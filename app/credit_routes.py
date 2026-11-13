"""Handlers used by the existing main blueprint routes."""
from datetime import timedelta
from flask import request, jsonify, current_app, url_for, session
import time
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from .models import db, PayPalWebhookEvent
from .credit_models import SubscriptionCheckout
from .subscription_checkout import (start_checkout, sync_one, finish_upgrade,
    cancel_member_subscription, member_snapshot, refresh_member, CheckoutConflict)
from .subscription_credits import (PayPal, PayPalUnavailable, ReviewRequired,
    sync_subscription, resolve_owner, issue_payment, parse_time, utcnow)


def _error(exc):
    db.session.rollback()
    if isinstance(exc, CheckoutConflict):
        return jsonify(error=str(exc)), 409
    if isinstance(exc, ReviewRequired):
        return jsonify(error='This pass needs account or payment verification. Please contact support before purchasing again.'), 409
    # PayPalUnavailable messages contain only a safe status/exception category,
    # never response bodies, credentials, tokens, or member data. Log the
    # category so transient PayPal errors can be distinguished from config bugs.
    if isinstance(exc, PayPalUnavailable):
        current_app.logger.warning('Subscription request failed: %s: %s',
                                   type(exc).__name__, str(exc)[:160])
    else:
        current_app.logger.warning('Subscription request failed: %s', type(exc).__name__)
    return jsonify(error='We could not verify this request with PayPal. Please refresh your pass status and try again shortly.'), 503


def create(upgrade=False):
    try:
        tier = (request.get_json(silent=True) or {}).get('tier')
        if str(tier) not in ('1', '2'):
            return jsonify(error='Invalid tier.'), 400
        sid = start_checkout(current_user.id, int(tier), 'upgrade' if upgrade else 'signup',
                             url_for('main.subscriptions', _external=True))
        session.pop('subscription_refresh', None)
        return jsonify(id=sid), 201
    except Exception as exc:
        return _error(exc)


def confirm():
    sid = (request.get_json(silent=True) or {}).get('subscription_id')
    try:
        result = sync_one(sid, uid=current_user.id)
        snapshot = member_snapshot(current_user.id)
        return jsonify(success=True, **result, event_credits=snapshot['event_credits']), 200
    except Exception as exc:
        return _error(exc)


def cancel(sid):
    try:
        cancel_member_subscription(current_user.id, sid)
        session.pop('subscription_refresh', None)
        return jsonify(success=True), 200
    except Exception as exc:
        return _error(exc)


def refresh():
    try:
        cached = session.get('subscription_refresh', {})
        if cached.get('uid') == current_user.id and time.time()-cached.get('at', 0) < 30:
            return jsonify(**member_snapshot(current_user.id), verified=cached.get('verified', False),
                           payment_review_required=cached.get('payment_review_required', False),
                           payment_history_pending=cached.get('payment_history_pending', False), cached=True)
        result = refresh_member(current_user.id)
        session['subscription_refresh'] = {'uid': current_user.id, 'at': time.time(), 'verified': result['verified'],
                                          'payment_review_required': result['payment_review_required'],
                                          'payment_history_pending': result['payment_history_pending']}
        return jsonify(result)
    except Exception as exc:
        return _error(exc)


def status():
    response = jsonify(member_snapshot(current_user.id))
    response.headers['Cache-Control'] = 'no-store'
    return response


def record_failure(eid, kind, sid, status, note):
    """Persist retry/review evidence only after rolling back the financial transaction."""
    db.session.rollback()
    try:
        row = PayPalWebhookEvent.query.filter_by(webhook_event_id=eid).with_for_update().first()
        if row and row.status == 'processed':
            db.session.rollback()
            return
        if not row:
            row = PayPalWebhookEvent(webhook_event_id=eid, event_type=kind,
                                    paypal_subscription_id=sid)
            db.session.add(row)
        row.status, row.notes = status, note[:255]
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Could not persist webhook failure status')


def webhook():
    data = request.get_json(silent=True) or {}
    eid, kind = data.get('id'), data.get('event_type')
    resource = data.get('resource') or {}
    if not isinstance(eid, str) or len(eid)>255 or not isinstance(kind, str) or len(kind)>100:
        return jsonify(error='Invalid webhook'), 400
    sid = resource.get('billing_agreement_id') if kind.startswith('PAYMENT.SALE.') else resource.get('id')
    try:
        api = PayPal()
        if not api.verify(data, request.headers):
            return jsonify(error='Webhook signature verification failed'), 401
        row = PayPalWebhookEvent.query.filter_by(webhook_event_id=eid).first()
        if row and row.status == 'processed':
            db.session.rollback()
            return jsonify(status='already_processed'), 200
        db.session.rollback()
        supported = kind.startswith('BILLING.SUBSCRIPTION.') or kind == 'PAYMENT.SALE.COMPLETED'
        if not supported:
            # Refunds/reversals require accounting review, never fresh credits.
            record_failure(eid, kind, sid, 'review' if kind.startswith('PAYMENT.SALE.') else 'ignored',
                           'Review non-completed sale event' if kind.startswith('PAYMENT.SALE.') else 'Unrelated event')
            return jsonify(status='recorded'), 200
        details = api.subscription(sid)
        transaction = None
        if kind == 'PAYMENT.SALE.COMPLETED':
            paid_at = parse_time(resource['create_time'])
            txs = api.transactions(sid, paid_at-timedelta(days=1), min(paid_at+timedelta(days=1), utcnow()))
            transaction = next((tx for tx in txs if tx['id'] == resource.get('id')), None)
            if transaction is None:
                raise PayPalUnavailable('Payment not visible in subscription history yet')
        sub, user, _ = sync_subscription(details)
        result = issue_payment(sub, user, details, transaction, webhook_id=eid) if transaction else {'action': 'status_synced'}
        row = PayPalWebhookEvent.query.filter_by(webhook_event_id=eid).with_for_update().first()
        if not row:
            row = PayPalWebhookEvent(webhook_event_id=eid, event_type=kind, paypal_subscription_id=sid)
            db.session.add(row)
        row.status = 'review' if result['action'].startswith('review_') else 'processed'
        row.notes = result['action']
        response_status = row.status
        target_status = sub.status
        db.session.commit()
        if transaction and result['action'] in ('issue_credits', 'already_recorded'):
            finish_upgrade(sid, api=api, paid=True, target_status=target_status)
        return jsonify(status=response_status), 200
    except ReviewRequired as exc:
        record_failure(eid, kind, sid, 'review', str(exc))
        return jsonify(status='review_required'), 200
    except (PayPalUnavailable, IntegrityError):
        record_failure(eid, kind, sid, 'failed', 'Temporary failure; retry required')
        return jsonify(status='retry_required'), 503
    except Exception:
        current_app.logger.exception('Subscription webhook processing failed')
        record_failure(eid, kind, sid, 'failed', 'Processing exception; retry required')
        return jsonify(status='retry_required'), 503
