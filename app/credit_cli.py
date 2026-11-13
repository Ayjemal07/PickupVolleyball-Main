"""Run with: python -m flask --app run credits reconcile --help"""
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
import click
from flask.cli import with_appcontext
from .models import db, Subscription, PaymentTransaction, PayPalWebhookEvent, User
from .subscription_credits import PayPal, ReviewRequired, parse_time, plans, sync_subscription, issue_payment, utcnow, iso


@click.group('credits')
def credits():
    """Reconcile PayPal memberships, safely previewing changes by default."""


@credits.command('send-event-promotion-emails')
@click.option('--limit', default=100, type=click.IntRange(1, 1000), show_default=True)
@with_appcontext
def send_event_promotion_emails(limit):
    """Retry queued promotion emails without issuing any credits."""
    from .event_notifications import deliver_promotion_emails
    result = deliver_promotion_emails(limit=limit)
    click.echo(json.dumps(result))


def read_map(path):
    if path is None:
        return {}
    result = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(result, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in result.items()):
        raise click.ClickException('Mapping must be a JSON object of string keys and values')
    return result


@credits.command('reconcile')
@click.option('--since', help='UTC start date YYYY-MM-DD; default last 60 days.')
@click.option('--output', required=True, type=click.Path(dir_okay=False), help='New JSON report path; never overwritten.')
@click.option('--apply', is_flag=True, help='Commit verified changes. Default is rollback/dry run.')
@click.option('--known-only', is_flag=True, help='Skip PayPal inventory discovery; coverage will be incomplete.')
@click.option('--subscription', 'subscription_ids', multiple=True, help='Limit to specific I-... IDs; repeatable.')
@click.option('--owners', type=click.Path(exists=True, dir_okay=False), help='Verified I-... -> local user UUID mapping.')
@click.option('--approved-missing', type=click.Path(exists=True, dir_okay=False), help='Reviewed payment ID -> I-... mapping authorizing missing historical issuance.')
@click.option('--restore-days', type=click.IntRange(1, 365), help='Validity for explicitly approved missing expired cycles; requires --approved-missing.')
@with_appcontext
def reconcile(since, output, apply, known_only, subscription_ids, owners, approved_missing, restore_days):
    start = datetime.strptime(since, '%Y-%m-%d') if since else utcnow()-timedelta(days=60)
    end = utcnow()
    if start >= end:
        raise click.ClickException('--since must be in the past')
    if restore_days and not approved_missing:
        raise click.ClickException('--restore-days requires --approved-missing')
    owner_map, approvals = read_map(owners), read_map(approved_missing)
    for uid in owner_map.values():
        if not db.session.get(User, uid):
            raise click.ClickException('Owner mapping contains a user UUID that does not exist')
    target = Path(output).resolve()
    # Reserve output before any commits; private permissions where supported.
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    report = {'mode': 'apply' if apply else 'dry_run', 'started_at': iso(end),
              'since': iso(start), 'inventory_complete': False, 'coverage_errors': [],
              'subscriptions': [], 'limitations': [
                  'Historical deleted grants cannot be reconstructed from the database alone.',
                  'Old local completed payments are preserved, never automatically reissued.',
                  'Refunds/reversals, conflicting ownership, and ambiguous legacy issuance require review.',
                  'PayPal HTTP errors are never treated as cancellations or evidence of nonpayment.']}
    def checkpoint():
        temp = target.with_name(target.name + '.tmp')
        fd = os.open(temp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2, default=str)
        os.replace(temp, target)
    try:
        api = PayPal()
        if subscription_ids:
            ids = set(subscription_ids)
            report['coverage_errors'].append('Explicit subscription filter: not a full-account audit')
        else:
            ids = {s.paypal_subscription_id for s in Subscription.query.all()}
            ids.update(p.paypal_subscription_id for p in PaymentTransaction.query.all() if p.paypal_subscription_id)
            ids.update(h.paypal_subscription_id for h in PayPalWebhookEvent.query.all()
                       if h.paypal_subscription_id and h.paypal_subscription_id.startswith('I-'))
            ids.update(owner_map)
            db.session.rollback()
            if not known_only:
                try:
                    inventory = api.inventory()
                    ids.update(s['id'] for s in inventory if s.get('plan_id') in plans())
                    report['inventory_complete'] = True
                except Exception as exc:
                    report['coverage_errors'].append('Inventory unavailable: ' + str(exc))
            else:
                report['coverage_errors'].append('Known-only mode cannot find PayPal-only memberships')
        checkpoint()
        for number, sid in enumerate(sorted(ids), 1):
            click.echo(f'[{number}/{len(ids)}] Checking {sid}', err=True)
            item = {'paypal_subscription_id': sid, 'payments': []}
            try:
                details = api.subscription(sid)
                if details.get('plan_id') not in plans():
                    raise ValueError('Unknown plan; requires review')
                txs = api.transactions(sid, start, end)
                last_paid = (details.get('billing_info') or {}).get('last_payment') or {}
                if not txs and last_paid.get('time'):
                    paid_at = parse_time(last_paid['time'])
                    if start <= paid_at <= end:
                        raise ReviewRequired('PayPal shows a payment but transaction history is empty; review before changing credits')
                sub, user, status = sync_subscription(details, owner_map)
                item.update(user_id=user.id, email=user.email,
                            paypal_email=details.get('subscriber', {}).get('email_address'),
                            paypal_custom_id=details.get('custom_id'), status_change=status,
                            balance_before=user.event_credits)
                for tx in txs:
                    item['payments'].append(issue_payment(sub, user, details, tx,
                        approved_missing=approvals, restore_days=restore_days))
                db.session.flush()
                item['balance_after'] = user.event_credits
                if apply:
                    db.session.commit()
                else:
                    db.session.rollback()
                item['result'] = 'committed' if apply else 'preview'
            except Exception as exc:
                db.session.rollback()
                item['result'] = 'review_or_error_no_changes'
                # Service errors contain no raw HTTP bodies or auth values.
                item['error'] = str(exc) if type(exc).__name__ in ('ReviewRequired','PayPalUnavailable','ValueError') else type(exc).__name__
                item['payments'] = []
                item.pop('balance_after', None)
            report['subscriptions'].append(item)
            checkpoint()
        reviews = PayPalWebhookEvent.query.filter(PayPalWebhookEvent.status.in_(['failed','review','received'])).all()
        report['unresolved_webhooks'] = [dict(event_id=h.webhook_event_id, kind=h.event_type,
            subscription=h.paypal_subscription_id, status=h.status, notes=h.notes) for h in reviews]
        report['finished_at'] = iso(utcnow())
        report['summary'] = {
            'subscriptions_checked': len(report['subscriptions']),
            'subscriptions_with_errors': sum(s['result']=='review_or_error_no_changes' for s in report['subscriptions']),
            'payments_needing_review': sum(p['action'].startswith('review_') for s in report['subscriptions'] for p in s['payments']),
            'credits_to_issue' if not apply else 'credits_issued': sum(p.get('credits',0) for s in report['subscriptions'] for p in s['payments']),
            'ownership_source': 'PayPal custom_id, existing subscription, transaction owner, or explicit verified mapping; never email matching'}
        checkpoint()
        click.echo(json.dumps(report['summary'], indent=2))
        click.echo(f'Report: {target}')
        if report['coverage_errors'] or report['summary']['subscriptions_with_errors'] or report['summary']['payments_needing_review']:
            raise click.ClickException('Reconciliation finished with review/coverage items; inspect the report.')
    finally:
        db.session.rollback()


def register(app):
    app.cli.add_command(credits)
