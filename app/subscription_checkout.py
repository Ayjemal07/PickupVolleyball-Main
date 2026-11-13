"""Member checkout and recovery. Public functions commit their own DB transactions.

PayPal calls never run inside a member row lock. A durable unique checkout slot
and the same PayPal-Request-Id recover an interrupted create without another plan.
"""
import json
import re
import uuid
from datetime import timedelta
from urllib.parse import quote

from flask import current_app
from sqlalchemy.exc import IntegrityError
from .models import db, User, Subscription, PaymentTransaction
from .credit_models import SubscriptionCheckout, SubscriptionCreditReceipt
from .subscription_credits import (PayPal, PayPalUnavailable, ReviewRequired,
    plans, sync_subscription, resolve_owner, issue_payment, utcnow, credit_day)

OPEN = ('active', 'pending', 'suspended')
TERMINAL = ('CANCELLED', 'EXPIRED')


class CheckoutConflict(Exception):
    """A member-facing reason checkout cannot proceed."""


def _owned(details, uid):
    try:
        owner = resolve_owner(details)
        if owner.id != uid:
            raise ReviewRequired('Subscription belongs to another account')
    finally:
        db.session.rollback()


def _close_slot(sid):
    row = SubscriptionCheckout.query.filter_by(paypal_subscription_id=sid).first()
    if row:
        # Closing the new tier must not discard already-authorized cancellation
        # of the old tier. Keep that durable job retryable until it finishes.
        if row.state != 'cancel_pending':
            row.state = 'closed'
        row.slot_key, row.updated_at = None, utcnow()


def sync_one(sid, *, uid=None, api=None, finish=True, lookback_days=32):
    """Reconcile one subscription; receipts and credits commit atomically."""
    api = api or PayPal()
    details = api.subscription(sid)
    if uid is not None:
        _owned(details, uid)
    end = utcnow()
    txs = api.transactions(sid, end-timedelta(days=lookback_days), end)
    last_payment = (details.get('billing_info') or {}).get('last_payment') or {}
    last_payment_time = last_payment.get('time')
    from .subscription_credits import parse_time
    latest_paid_at = parse_time(last_payment_time) if last_payment_time else None
    # PayPal can show a new renewal in billing_info before its transaction is
    # listed. A transaction from the previous monthly cycle must not confirm
    # that new payment. Allow a day for the two PayPal timestamps to differ.
    matching_txs = ([tx for tx in txs if parse_time(tx['time']) >= latest_paid_at-timedelta(days=1)]
                    if latest_paid_at and end-timedelta(days=lookback_days) <= latest_paid_at <= end else txs)
    payment_history_pending = bool(latest_paid_at and
        end-timedelta(days=lookback_days) <= latest_paid_at <= end and not matching_txs)
    try:
        sub, user, _ = sync_subscription(details)
        if uid is not None and user.id != uid:
            raise ReviewRequired('Subscription ownership changed')
        results = [issue_payment(sub, user, details, tx) for tx in txs]
        paid_ids = [tx['id'] for tx in matching_txs if tx.get('status') == 'COMPLETED']
        paid = bool(paid_ids and db.session.query(SubscriptionCreditReceipt.id).join(
            PaymentTransaction, PaymentTransaction.id == SubscriptionCreditReceipt.payment_id).filter(
            PaymentTransaction.paypal_capture_id.in_(paid_ids),
            PaymentTransaction.subscription_id == sub.id,
            PaymentTransaction.status == 'completed',
            SubscriptionCreditReceipt.grant_id.isnot(None)).first())
        legacy_receipt = bool(paid_ids and db.session.query(SubscriptionCreditReceipt.id).join(
            PaymentTransaction, PaymentTransaction.id == SubscriptionCreditReceipt.payment_id).filter(
            PaymentTransaction.paypal_capture_id.in_(paid_ids),
            PaymentTransaction.subscription_id == sub.id,
            SubscriptionCreditReceipt.grant_id.is_(None)).first())
        user_id, status = user.id, sub.status
        if status in ('canceled', 'expired'):
            _close_slot(sid)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    upgrade_pending = finish_upgrade(sid, api=api, paid=paid, target_status=status) if finish else False
    return {'subscription_id': sid, 'user_id': user_id, 'status': status,
            'payment_confirmed': paid, 'pending': not paid and status in OPEN,
            'payment_history_pending': payment_history_pending,
            'upgrade_pending': upgrade_pending,
            'payment_review_required': legacy_receipt or any(r['action'].startswith('review_') for r in results),
            'payments': results,
            'credits_issued': sum(r.get('credits', 0) for r in results)}


def finish_upgrade(sid, *, api, paid, target_status):
    """After verified new payment, cancel the old tier; failures are durable/retried."""
    row = SubscriptionCheckout.query.filter_by(paypal_subscription_id=sid).first()
    if not row or row.state in ('complete', 'closed'):
        db.session.rollback()
        return False
    retrying_cancel = row.state == 'cancel_pending'
    if not retrying_cancel and (not paid or target_status != 'active'):
        db.session.rollback()
        return False
    # A legacy payment receipt without a grant is not proof that the upgraded
    # pass was issued. This also protects the webhook call path.
    issued = db.session.query(SubscriptionCreditReceipt.id).join(
        PaymentTransaction, PaymentTransaction.id == SubscriptionCreditReceipt.payment_id).filter(
        PaymentTransaction.paypal_subscription_id == sid,
        PaymentTransaction.user_id == row.user_id,
        PaymentTransaction.status == 'completed',
        SubscriptionCreditReceipt.grant_id.isnot(None)).first()
    if not issued:
        db.session.rollback()
        # Never cancel without verified issuance. A saved cancellation job with
        # missing evidence still needs attention; it is not a successful pass.
        return retrying_cancel
    row_id, uid, old_sid = row.id, row.user_id, row.replaces_subscription_id
    row.state = 'cancel_pending' if old_sid else 'complete'
    row.updated_at, row.last_error = utcnow(), None
    db.session.commit()
    if not old_sid:
        return False
    try:
        old_details = api.subscription(old_sid)
        _owned(old_details, uid)
        if plans().get(old_details.get('plan_id'), (None,))[0] != 1:
            raise ReviewRequired('Upgrade source is not Tier 1')
        if old_details['status'] not in TERMINAL:
            if old_details['status'] not in ('ACTIVE', 'SUSPENDED', 'APPROVED', 'APPROVAL_PENDING'):
                raise ReviewRequired('Unexpected old subscription status')
            api.request('POST', '/v1/billing/subscriptions/' + quote(old_sid, safe='') + '/cancel',
                        json={'reason': 'Member approved and paid for Tier 2 upgrade'})
            # A successful cancel returns 204. Fetch again; never declare complete
            # from local state alone, including recovery after a lost HTTP response.
            old_details = api.subscription(old_sid)
            if old_details['status'] not in TERMINAL:
                raise PayPalUnavailable('Cancellation is not visible yet')
        sub, owner, _ = sync_subscription(old_details)
        if owner.id != uid:
            raise ReviewRequired('Upgrade owner conflict')
        _close_slot(old_sid)
        row = db.session.get(SubscriptionCheckout, row_id)
        row.state, row.last_error, row.updated_at = 'complete', None, utcnow()
        db.session.commit()
        return False
    except Exception as exc:
        db.session.rollback()
        row = db.session.get(SubscriptionCheckout, row_id)
        if row.state in ('complete', 'closed'):
            db.session.rollback()
            return False
        row.state, row.last_error, row.updated_at = 'cancel_pending', type(exc).__name__, utcnow()
        db.session.commit()
        current_app.logger.warning('Upgrade cancellation needs retry for checkout %s', row_id)
        return True


def _create_remote(attempt_id, api):
    row = db.session.get(SubscriptionCheckout, attempt_id)
    if row.paypal_subscription_id:
        sid = row.paypal_subscription_id
        db.session.rollback()
        return sid
    if utcnow() - row.created_at > timedelta(hours=48):
        # PayPal retains create idempotency keys for 72h. Never risk reissuing a
        # stale unknown request after that window. Operator must resolve its ID.
        row.state, row.last_error = 'review', 'Create result unknown; locate original PayPal subscription'
        db.session.commit()
        raise CheckoutConflict('An earlier checkout needs verification. Please contact support; do not start another payment.')
    payload, uid, tier = json.loads(row.payload), row.user_id, row.tier
    db.session.rollback()
    created = api.request('POST', '/v1/billing/subscriptions', request_id=attempt_id, json=payload)
    sid = created.get('id')
    if not re.fullmatch(r'I-[A-Za-z0-9]+', sid or ''):
        raise PayPalUnavailable('PayPal create response has no valid subscription ID')
    try:
        User.query.filter_by(id=uid).with_for_update().first()
        row = SubscriptionCheckout.query.filter_by(id=attempt_id).populate_existing().with_for_update().first()
        if row.paypal_subscription_id and row.paypal_subscription_id != sid:
            raise ReviewRequired('Create retry returned a different subscription')
        existing = Subscription.query.filter_by(paypal_subscription_id=sid).first()
        if existing:
            if existing.user_id != uid or existing.tier != tier:
                raise ReviewRequired('Created subscription ownership conflict')
            # A fast webhook may already have activated it. Never demote it.
        else:
            db.session.add(Subscription(user_id=uid, paypal_subscription_id=sid,
                tier=tier, credits_per_month=4 if tier == 1 else 8,
                status='pending', expiry_date=credit_day()-timedelta(days=1)))
        row.paypal_subscription_id, row.updated_at = sid, utcnow()
        if row.state == 'creating':
            row.state = 'pending'
        row.last_error = None
        db.session.commit()
        return sid
    except Exception:
        db.session.rollback()
        raise


def start_checkout(uid, tier, action, return_url, *, api=None):
    if tier not in (1, 2) or action not in ('signup', 'upgrade') or (action == 'upgrade' and tier != 2):
        raise CheckoutConflict('Invalid subscription choice.')
    api = api or PayPal()
    slot = f'{uid}:{tier}'
    row = SubscriptionCheckout.query.filter_by(slot_key=slot).first()
    if row and not row.paypal_subscription_id:
        if row.action != action:
            raise CheckoutConflict('Finish your earlier checkout choice first, then change plans.')
        attempt_id = row.id
        db.session.rollback()
        return _create_remote(attempt_id, api)
    # Only check this member's relevant agreements. No merchant-wide inventory
    # scan, emails, or other members' PayPal requests in the signup path.
    tiers = (1, 2) if action == 'upgrade' else (tier,)
    ids = [s.paypal_subscription_id for s in Subscription.query.filter(
        Subscription.user_id == uid, Subscription.tier.in_(tiers),
        Subscription.status.in_(OPEN)).all()]
    db.session.rollback()
    for sid in ids:
        sync_one(sid, uid=uid, api=api)
    try:
        user = User.query.filter_by(id=uid).populate_existing().with_for_update().first()
        if not user:
            raise ReviewRequired('User does not exist')
        row = SubscriptionCheckout.query.filter_by(slot_key=slot).first()
        if row and row.paypal_subscription_id:
            retired = Subscription.query.filter_by(
                paypal_subscription_id=row.paypal_subscription_id, user_id=uid).first()
            if retired and retired.status in ('canceled', 'expired'):
                _close_slot(row.paypal_subscription_id)
                db.session.flush()
                row = None
        same_tier = Subscription.query.filter(Subscription.user_id == uid,
            Subscription.tier == tier, Subscription.status.in_(OPEN)).all()
        # A canceled popup resumes the same pending agreement with one click.
        if len(same_tier) == 1 and same_tier[0].status == 'pending':
            sub = same_tier[0]
            if (row and row.paypal_subscription_id == sub.paypal_subscription_id
                    and row.action == action):
                sid = sub.paypal_subscription_id
                db.session.rollback()
                return sid
            raise CheckoutConflict('This tier has a pending checkout. Continue that checkout or cancel it on this page first.')
        if same_tier:
            raise CheckoutConflict('You already have this tier. Check your pass status and credit balance below.')
        if row:
            # Concurrent reservation or an upgrade still being finalized.
            if row.action != action:
                raise CheckoutConflict('An earlier checkout is still being processed.')
            attempt_id = row.id
            db.session.rollback()
            return _create_remote(attempt_id, api)
        old_sid = None
        if action == 'upgrade':
            old = Subscription.query.filter_by(user_id=uid, tier=1, status='active').all()
            if len(old) != 1:
                raise CheckoutConflict('Upgrade requires one active Tier 1 pass. Refresh your passes and try again.')
            old_sid = old[0].paypal_subscription_id
        plan_id = next(pid for pid, spec in plans().items() if spec[0] == tier)
        payload = {'plan_id': plan_id, 'custom_id': uid,
                   'subscriber': {'email_address': user.email},
                   'application_context': {'return_url': return_url, 'cancel_url': return_url,
                                           'shipping_preference': 'NO_SHIPPING', 'user_action': 'SUBSCRIBE_NOW'}}
        attempt_id = str(uuid.uuid4())
        db.session.add(SubscriptionCheckout(id=attempt_id, slot_key=slot, user_id=uid,
            tier=tier, action=action, replaces_subscription_id=old_sid,
            payload=json.dumps(payload), state='creating'))
        db.session.commit()  # Durable before any remote create.
    except IntegrityError:
        db.session.rollback()
        # Unique slot is the final guard on databases without SELECT FOR UPDATE.
        raise CheckoutConflict('A checkout for this tier is already starting. Please try again in a moment.') from None
    except Exception:
        db.session.rollback()
        raise
    return _create_remote(attempt_id, api)


def cancel_member_subscription(uid, sid, *, api=None):
    api = api or PayPal()
    details = api.subscription(sid)
    _owned(details, uid)
    if details['status'] not in TERMINAL:
        api.request('POST', '/v1/billing/subscriptions/' + quote(sid, safe='') + '/cancel',
                    json={'reason': 'Member requested cancellation'})
        details = api.subscription(sid)
        if details['status'] not in TERMINAL:
            raise PayPalUnavailable('Cancellation is not confirmed yet')
    try:
        sub, owner, _ = sync_subscription(details)
        if owner.id != uid:
            raise ReviewRequired('Cancellation ownership conflict')
        _close_slot(sid)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def member_snapshot(uid):
    user = db.session.get(User, uid)
    subs = Subscription.query.filter_by(user_id=uid).all()
    checkouts = SubscriptionCheckout.query.filter_by(user_id=uid).all()
    return {'event_credits': int(user.event_credits),
            'has_active_subscription': any(s.status == 'active' for s in subs),
            'pending_subscription': any(s.status == 'pending' for s in subs) or
                                    any(c.state in ('creating', 'pending') for c in checkouts),
            'checkout_review_required': any(c.state == 'review' for c in checkouts),
            'upgrade_pending': any(c.state == 'cancel_pending' for c in checkouts),
            'subscriptions': [dict(id=s.paypal_subscription_id, tier=s.tier, status=s.status) for s in subs]}


def refresh_member(uid, *, api=None):
    api = api or PayPal()
    # Repeated page checks reconcile known IDs only. Whole-account discovery is
    # the operator's existing credits reconcile command, not a customer operation.
    # The member page only needs to recheck agreements that can still renew or
    # are awaiting approval. Locally canceled/expired agreements were already
    # confirmed when canceled; the maintenance/reconcile command handles their
    # historical payment audit without slowing every page view.
    ids = [s.paypal_subscription_id for s in Subscription.query.filter(
        Subscription.user_id == uid, Subscription.status.in_(OPEN)).all()]
    db.session.rollback()
    errors, results = [], []
    for sid in ids:
        try:
            results.append(sync_one(sid, uid=uid, api=api))
        except (ReviewRequired, PayPalUnavailable):
            db.session.rollback()
            errors.append(sid)
    result = member_snapshot(uid)
    result.update(verified=not errors, verification_errors=errors,
                  payment_history_pending=any(r['payment_history_pending'] for r in results),
                  payment_review_required=result['checkout_review_required'] or
                                          any(r['payment_review_required'] for r in results))
    return result
