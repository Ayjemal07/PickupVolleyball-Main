"""Run every five minutes on the host: flask --app run credits maintain."""
import json
import os
import tempfile
import time
import inspect
from pathlib import Path
from datetime import timedelta
import click
from flask.cli import with_appcontext
from .credit_cli import credits
from .models import db, User, Subscription, CreditGrant, CreditTransaction, PaymentTransaction, PayPalWebhookEvent
from .credit_models import SubscriptionCheckout, SubscriptionCreditReceipt, CreditSystemState
from .subscription_checkout import sync_one, _create_remote
from .subscription_credits import (PayPal, PayPalUnavailable, ReviewRequired, utcnow, iso,
    credit_day, plans, setting, parse_time, sync_subscription, issue_payment)


def _reason(exc):
    return str(exc) if isinstance(exc, (ReviewRequired, PayPalUnavailable)) else type(exc).__name__


def _check_checkout_api():
    """Reject mixed releases before making remote calls or changing accounts."""
    try:
        inspect.signature(sync_one).bind('I-COMPATIBILITY', api=None, lookback_days=32)
    except (TypeError, ValueError) as exc:
        raise click.ClickException(
            'Incompatible app/subscription_checkout.py: sync_one must accept lookback_days. '
            'Install the matching release files and run verify_subscription_release.py.') from exc


class ReportWriteError(click.ClickException):
    """A local report failure, never a PayPal discovery failure."""


def _write_report(target, report):
    """Publish a complete snapshot; retry transient Windows replacement locks.

    Only the file replacement is retried, never the account processing. Keep
    the complete temporary snapshot if publishing fails after earlier commits.
    """
    target = Path(target)
    try:
        fd, temporary = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=target.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2, default=str)
    except OSError as exc:
        raise ReportWriteError(
            f'Could not write local report {target}: {exc}. '
            'Choose a writable report folder outside OneDrive or other synced folders.') from exc
    for delay in (0.1, 0.2, 0.4, 0.8, 1.6, None):
        try:
            os.replace(temporary, target)
            return
        except PermissionError as exc:
            if delay is not None:
                time.sleep(delay)
                continue
            failure = exc
        except OSError as exc:
            failure = exc
        raise ReportWriteError(
            f'Could not publish local report {target}: {failure}. '
            f'Latest complete snapshot preserved at {temporary}. '
            'Earlier maintenance changes may already be committed. '
            'Close programs holding the report or choose an output folder outside synced folders.') from failure


def _state(key, value):
    row = db.session.get(CreditSystemState, key)
    if row:
        row.value = value
    else:
        db.session.add(CreditSystemState(key=key, value=value))
    db.session.commit()


@credits.command('maintain')
@click.option('--output', required=True, type=click.Path(dir_okay=False), help='New private report filename.')
@click.option('--discover', is_flag=True, help='Discover PayPal-only agreements now (also done at least daily).')
@click.option('--lookback-days', default=32, type=click.IntRange(32, 365), show_default=True)
@with_appcontext
def maintain(output, discover=False, lookback_days=32):
    """Apply verified payments, recover checkouts, and periodically discover missing agreements."""
    _check_checkout_api()
    target = Path(output)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    report = {'started_at': iso(utcnow()), 'checkouts': [], 'subscriptions': [],
              'coverage_errors': [], 'inventory_checked': False, 'lookback_days': lookback_days}
    api = PayPal()
    def save():
        _write_report(target, report)
    save()
    _state('maintenance_started_at', report['started_at'])
    attempts = [r.id for r in SubscriptionCheckout.query.filter(
        SubscriptionCheckout.paypal_subscription_id.is_(None),
        SubscriptionCheckout.state.in_(['creating', 'pending', 'review'])).all()]
    db.session.rollback()
    for attempt_id in attempts:
        item = {'checkout_id': attempt_id}
        try:
            item['subscription_id'] = _create_remote(attempt_id, api)
            item['result'] = 'recovered'
        except Exception as exc:
            db.session.rollback()
            item.update(result='needs_review_or_retry', error=type(exc).__name__, reason=_reason(exc))
        report['checkouts'].append(item)
        save()
    ids = {s.paypal_subscription_id for s in Subscription.query.filter(db.or_(
        Subscription.status.in_(['active', 'pending', 'suspended']),
        Subscription.expiry_date >= credit_day(),
        Subscription.created_at >= utcnow()-timedelta(days=35))).all()}
    ids.update(r.paypal_subscription_id for r in SubscriptionCheckout.query.filter_by(state='cancel_pending').all()
               if r.paypal_subscription_id)
    ids.update(h.paypal_subscription_id for h in PayPalWebhookEvent.query.filter(
        PayPalWebhookEvent.status.in_(['failed', 'review', 'received'])).all()
               if h.paypal_subscription_id and h.paypal_subscription_id.startswith('I-'))
    last = db.session.get(CreditSystemState, 'inventory_checked_at')
    try:
        due = not last or utcnow()-parse_time(last.value) >= timedelta(days=1)
    except (ValueError, ReviewRequired):
        due = True
    db.session.rollback()
    if discover or due:
        try:
            inventory = api.inventory()
            for row in inventory:
                # Some inventory responses omit plan metadata. Fetch it rather
                # than quietly skipping an agreement we have never saved.
                if not row.get('plan_id'):
                    row = api.subscription(row['id'])
                if row.get('plan_id') in plans():
                    ids.add(row['id'])
            report['inventory_checked'] = True
            _state('inventory_checked_at', iso(utcnow()))
        except Exception as exc:
            db.session.rollback()
            report['coverage_errors'].append(_reason(exc))
        save()
    for sid in sorted(ids):
        try:
            item = sync_one(sid, api=api, lookback_days=lookback_days)
            item['result'] = 'checked'
        except Exception as exc:
            db.session.rollback()
            item = {'subscription_id': sid, 'result': 'needs_review_or_retry',
                    'error': type(exc).__name__, 'reason': _reason(exc)}
        report['subscriptions'].append(item)
        save()
    report['finished_at'] = iso(utcnow())
    report['needs_attention'] = len(report['coverage_errors']) + sum(
        i.get('result') == 'needs_review_or_retry' or i.get('upgrade_pending', False) or
        i.get('payment_review_required', False) or i.get('payment_history_pending', False)
        for i in report['subscriptions'] + report['checkouts'])
    report['credits_issued'] = sum(i.get('credits_issued', 0) for i in report['subscriptions'])
    save()
    _state('maintenance_finished_at', report['finished_at'])
    _state('maintenance_needs_attention', str(report['needs_attention']))
    click.echo(f"Checked {len(ids)} subscriptions; {report['needs_attention']} need review/retry. Report: {target}")
    if report['needs_attention']:
        raise click.ClickException('Review the maintenance report; verified repairs were committed.')


@credits.command('doctor')
@click.option('--require-maintenance', is_flag=True, help='Fail if recovery did not finish recently or has unresolved items.')
@click.option('--max-age-minutes', default=15, type=click.IntRange(1, 1440), show_default=True)
@with_appcontext
def doctor(require_maintenance=False, max_age_minutes=15):
    """Check local prerequisites. Does not call PayPal or change accounts."""
    _check_checkout_api()
    from sqlalchemy import inspect
    required = {'subscription_checkout', 'subscription_credit_receipt', 'credit_system_state'}
    missing = required - set(inspect(db.engine).get_table_names())
    if missing:
        raise click.ClickException('Run the migration. Missing tables: ' + ', '.join(sorted(missing)))
    marker = db.session.get(CreditSystemState, 'atomic_issuance_started_at')
    if not marker:
        raise click.ClickException('Previous credit migration/cutover marker is missing. Do not enable checkout yet.')
    parse_time(marker.value)
    plans()
    for name in ('PAYPAL_CLIENT_ID', 'PAYPAL_SECRET', 'PAYPAL_WEBHOOK_ID'):
        if not setting(name):
            raise click.ClickException('Missing setting: ' + name)
    # A successful schema check alone cannot detect the legacy route bypass.
    import inspect as source_inspect
    from flask import current_app
    from . import credit_routes
    for name in ('create', 'confirm', 'cancel', 'refresh', 'status', 'webhook'):
        if not callable(getattr(credit_routes, name, None)):
            raise click.ClickException('Supporting credit_routes.py is incomplete: ' + name)
    expected = {'main.create_subscription': 'return create()',
                'main.upgrade_subscription': 'return create(upgrade=True)',
                'main.cancel_subscription': 'cancel(subscription_id)',
                'main.confirm_subscription': 'return confirm()',
                'main.refresh_subscription': 'return refresh()',
                'main.get_subscription_status': 'return status()',
                'main.paypal_webhook': 'return webhook()'}
    for endpoint, delegate in expected.items():
        view = current_app.view_functions.get(endpoint)
        try:
            source = source_inspect.getsource(source_inspect.unwrap(view)) if view else ''
        except (OSError, TypeError):
            source = ''
        if delegate not in source or '.credit_routes import' not in source:
            raise click.ClickException('Checkout route is missing or still uses legacy code: ' + endpoint)
    last = db.session.get(CreditSystemState, 'maintenance_finished_at')
    attention = db.session.get(CreditSystemState, 'maintenance_needs_attention')
    if require_maintenance:
        if not last or utcnow()-parse_time(last.value) > timedelta(minutes=max_age_minutes):
            raise click.ClickException('Maintenance has not finished recently; check scheduler/process/logs.')
        if not attention or attention.value != '0':
            raise click.ClickException('Maintenance has unresolved review/retry/coverage items; inspect its report.')
    click.echo('Schema/settings and checkout routes verified. Last maintenance: ' +
               (last.value if last else 'NEVER RECORDED') + '; needs attention: ' +
               (attention.value if attention else 'UNKNOWN') +
               '. Verify actual payment webhook delivery and the host scheduler.')


@credits.command('audit-member')
@click.option('--name', 'names', multiple=True, help='Exact full local name; repeatable. Never used to link PayPal ownership.')
@click.option('--user', 'user_ids', multiple=True, help='Exact local UUID; repeatable.')
@click.option('--subscription', 'extra_ids', multiple=True, help='Also inspect a known PayPal I-... ID.')
@click.option('--discover', is_flag=True, help='Scan PayPal for missing agreements and unverified name/email candidates.')
@click.option('--days', default=60, type=click.IntRange(1, 365), show_default=True)
@click.option('--output', required=True, type=click.Path(dir_okay=False))
@with_appcontext
def audit_member(names, user_ids, extra_ids, discover, days, output):
    """Private READ-ONLY evidence report. No credits, cancellations, or owner guesses."""
    if not names and not user_ids:
        raise click.ClickException('Specify --name or --user.')
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    report = {'mode': 'read_only', 'started_at': iso(utcnow()), 'days': days,
              'members': [], 'subscriptions': [], 'unverified_candidates': [], 'errors': []}
    def save():
        _write_report(output, report)
    selected = set(user_ids)
    for name in names:
        matches = User.query.filter(db.func.lower(User.first_name + ' ' + User.last_name) == name.strip().lower()).all()
        if not matches:
            report['errors'].append('No local account with exact full name: ' + name)
        if len(matches) > 1:
            report['errors'].append('Duplicate local name: ' + name + '; inspect each UUID independently')
        selected.update(u.id for u in matches)
    local_ids, emails, full_names = set(extra_ids), set(), set()
    start, end = utcnow()-timedelta(days=days), utcnow()
    for uid in sorted(selected):
        user = db.session.get(User, uid)
        if not user:
            report['errors'].append('No local user with UUID: ' + uid)
            continue
        emails.add(user.email.lower())
        full_names.add((user.first_name + ' ' + user.last_name).strip().lower())
        subs = Subscription.query.filter_by(user_id=uid).all()
        payments = PaymentTransaction.query.filter_by(user_id=uid, transaction_type='subscription_payment').order_by(PaymentTransaction.created_at).all()
        local_ids.update(s.paypal_subscription_id for s in subs)
        local_ids.update(p.paypal_subscription_id for p in payments if p.paypal_subscription_id)
        local_ids.update(c.paypal_subscription_id for c in SubscriptionCheckout.query.filter_by(user_id=uid).all() if c.paypal_subscription_id)
        report['members'].append({'user_id': uid, 'name': user.first_name + ' ' + user.last_name,
            'email': user.email, 'available_credits': int(user.event_credits),
            'subscriptions': [dict(id=s.paypal_subscription_id, status=s.status, tier=s.tier,
                                   paid_through=str(s.expiry_date)) for s in subs],
            'payments': [dict(payment_id=p.paypal_capture_id, subscription_id=p.paypal_subscription_id,
                status=p.status, amount=p.amount, receipt=[dict(grant_id=r.grant_id, mode=r.mode,
                    original_amount=r.original_amount, payment_time=iso(r.payment_time)) for r in
                    SubscriptionCreditReceipt.query.filter_by(payment_id=p.id).all()]) for p in payments],
            'grants': [dict(id=g.id, balance=g.balance, source=g.source_type, description=g.description,
                issued_at=iso(g.issued_at), expires=str(g.expiry_date)) for g in
                CreditGrant.query.filter_by(user_id=uid).order_by(CreditGrant.issued_at).all()],
            'ledger': [dict(amount=t.amount, kind=t.transaction_type, at=iso(t.timestamp), description=t.description)
                for t in CreditTransaction.query.filter(CreditTransaction.user_id == uid,
                    CreditTransaction.timestamp >= start).order_by(CreditTransaction.timestamp).all()]})
    db.session.rollback()
    save()
    api, details_cache = PayPal(), {}
    if discover:
        try:
            for row in api.inventory():
                try:
                    details = api.subscription(row['id'])
                    if details.get('plan_id') not in plans():
                        continue
                    sid = details['id']
                    subscriber = details.get('subscriber') or {}
                    name = subscriber.get('name') or {}
                    remote_name = (name.get('given_name', '') + ' ' + name.get('surname', '')).strip().lower()
                    if details.get('custom_id') in selected or sid in local_ids:
                        local_ids.add(sid)
                        details_cache[sid] = details
                    elif subscriber.get('email_address', '').lower() in emails or remote_name in full_names:
                        report['unverified_candidates'].append({'subscription_id': sid,
                            'paypal_custom_id': details.get('custom_id'), 'paypal_name': remote_name,
                            'paypal_email': subscriber.get('email_address'),
                            'reason': 'Name/email resemblance only; verify ownership before supplying --owners'})
                except Exception as exc:
                    report['errors'].append({'subscription_id': row.get('id'), 'reason': _reason(exc)})
                save()
        except ReportWriteError:
            raise
        except Exception as exc:
            report['errors'].append('PayPal discovery incomplete: ' + _reason(exc))
            save()
    for sid in sorted(local_ids):
        item = {'subscription_id': sid}
        try:
            details = details_cache.get(sid) or api.subscription(sid)
            txs = api.transactions(sid, start, end)
            item.update(paypal_status=details.get('status'), paypal_custom_id=details.get('custom_id'),
                        paypal_plan_id=details.get('plan_id'), billing_info=details.get('billing_info'))
            sub, user, status = sync_subscription(details)
            if user.id not in selected:
                raise ReviewRequired('This subscription resolves to a user outside the selected members')
            item.update(user_id=user.id, status_change=status, balance_before=int(user.event_credits),
                        payments=[issue_payment(sub, user, details, tx) for tx in txs])
            item['balance_if_applied'] = int(user.event_credits)
            item['webhooks'] = [dict(event_id=h.webhook_event_id, kind=h.event_type, status=h.status, notes=h.notes)
                for h in PayPalWebhookEvent.query.filter_by(paypal_subscription_id=sid).all()]
        except Exception as exc:
            item.update(error=type(exc).__name__, reason=_reason(exc))
        finally:
            db.session.rollback()  # Even a proposed grant/receipt/status change is discarded.
        report['subscriptions'].append(item)
        save()
    report['finished_at'] = iso(utcnow())
    save()
    click.echo('Read-only member audit saved: ' + str(output))
    if report['errors'] or any('error' in s or any(p['action'].startswith('review_') for p in s.get('payments', []))
                              for s in report['subscriptions']):
        raise click.ClickException('Audit contains review/coverage items; inspect the report. No changes were committed.')
