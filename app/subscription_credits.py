"""Verified PayPal reconciliation. Callers own the database transaction/commit."""
import os
import re
import time
from datetime import datetime, timedelta, date, timezone
from decimal import Decimal
from urllib.parse import urlparse, quote
from zoneinfo import ZoneInfo
import requests
from flask import current_app
from .models import db, User, Subscription, CreditGrant, CreditTransaction, PaymentTransaction
from .credit_models import SubscriptionCreditReceipt, CreditSystemState


class ReviewRequired(Exception):
    pass


class PayPalUnavailable(Exception):
    pass


def setting(name, default=None):
    return current_app.config.get(name) or os.getenv(name) or default


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ReviewRequired('PayPal timestamp has no timezone')
    return result.astimezone(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace('+00:00', 'Z')


def credit_day(value=None):
    value = value or utcnow()
    return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(setting('CREDIT_TIMEZONE', 'America/Los_Angeles'))).date()


def plans():
    one, two = setting('PAYPAL_PLAN_ID_TIER1'), setting('PAYPAL_PLAN_ID_TIER2')
    if not one or not two or one == two:
        raise ReviewRequired('Configure two distinct PayPal plan IDs')
    return {one: (1, 4, Decimal('35.00')), two: (2, 8, Decimal('60.00'))}


class PayPal:
    def __init__(self):
        self.base = setting('PAYPAL_BASE', 'https://api-m.paypal.com').rstrip('/')
        if self.base not in ('https://api-m.paypal.com', 'https://api-m.sandbox.paypal.com'):
            raise ReviewRequired('Unrecognized PAYPAL_BASE')
        self.token = None

    def request(self, method, path, *, request_id=None, **kwargs):
        if path.startswith('https://'):
            parsed = urlparse(path)
            if parsed.scheme != 'https' or parsed.netloc != urlparse(self.base).netloc or not parsed.path.startswith('/v1/'):
                raise PayPalUnavailable('Unsafe PayPal pagination URL')
            url = path
        else:
            url = self.base + path
        # Newly-created subscriptions can take a few seconds to appear in
        # PayPal's read APIs. Retry only transient responses and network
        # failures; subscription creation always carries its stable
        # PayPal-Request-Id, so an interrupted create cannot double-charge.
        for attempt in range(3):
            try:
                if not self.token:
                    response = requests.post(self.base + '/v1/oauth2/token',
                        auth=(setting('PAYPAL_CLIENT_ID'), setting('PAYPAL_SECRET')),
                        data={'grant_type': 'client_credentials'}, timeout=(10, 40))
                    response.raise_for_status()
                    self.token = response.json()['access_token']
                headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'}
                if request_id:
                    headers['PayPal-Request-Id'] = request_id
                response = requests.request(method, url,
                    headers=headers,
                    timeout=(10, 40), **kwargs)
                if response.status_code == 401 and attempt == 0:
                    self.token = None
                    continue
                if response.status_code in (404, 429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                response.raise_for_status()
                if response.status_code == 204:
                    return {}
                return response.json()
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                raise PayPalUnavailable(f'PayPal request failed ({status or type(exc).__name__})') from None
            except requests.RequestException as exc:
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                # Never include response bodies, credentials or bearer tokens in reports.
                raise PayPalUnavailable(f'PayPal request failed ({status or type(exc).__name__})') from None
            except (ValueError, KeyError) as exc:
                raise PayPalUnavailable(f'PayPal request failed ({type(exc).__name__})') from None
        raise PayPalUnavailable('PayPal authentication failed')

    def pages(self, path, field, params=None):
        seen_urls, seen_ids = set(), set()
        output = []
        while path:
            if path in seen_urls or len(seen_urls) >= 10000:
                raise PayPalUnavailable('Invalid or excessive pagination')
            seen_urls.add(path)
            data = self.request('GET', path, params=params)
            params = None
            # PayPal sometimes returns {} for a date range without subscription
            # transactions. Only an empty first transaction page is accepted;
            # keep rejecting malformed inventory and later pagination pages.
            if field == 'transactions' and data == {} and len(seen_urls) == 1:
                return []
            if field not in data or not isinstance(data[field], list):
                raise PayPalUnavailable('Unexpected PayPal list response')
            for row in data[field]:
                identifier = row.get('id')
                if not identifier or identifier in seen_ids:
                    raise PayPalUnavailable('Missing/repeated ID in paginated response')
                seen_ids.add(identifier)
                output.append(row)
            next_links = [l['href'] for l in data.get('links', []) if l.get('rel') == 'next']
            path = next_links[0] if next_links else None
            if not path and data.get('total_items', 0) > len(output):
                raise PayPalUnavailable('Incomplete PayPal transaction response')
        return output

    def inventory(self):
        return self.pages('/v1/billing/subscriptions', 'subscriptions', {'page_size': 20})

    def subscription(self, sid):
        if not re.fullmatch(r'I-[A-Za-z0-9]+', sid or ''):
            raise ReviewRequired('Invalid subscription ID')
        data = self.request('GET', '/v1/billing/subscriptions/' + quote(sid, safe=''))
        if data.get('id') != sid:
            raise ReviewRequired('Subscription ID mismatch')
        return data

    def transactions(self, sid, start, end):
        result = {}
        while start < end:
            finish = min(start + timedelta(days=30), end)
            rows = self.pages('/v1/billing/subscriptions/' + quote(sid, safe='') + '/transactions',
                              'transactions', {'start_time': iso(start), 'end_time': iso(finish)})
            for row in rows:
                # Inclusive window boundaries may repeat a transaction.
                result[row['id']] = row
            start = finish
        return sorted(result.values(), key=lambda row: (row['time'], row['id']))

    def verify(self, data, headers):
        wid = setting('PAYPAL_WEBHOOK_ID')
        if not wid:
            raise PayPalUnavailable('PAYPAL_WEBHOOK_ID is required')
        names = {'auth_algo': 'PAYPAL-AUTH-ALGO', 'cert_url': 'PAYPAL-CERT-URL',
                 'transmission_id': 'PAYPAL-TRANSMISSION-ID',
                 'transmission_sig': 'PAYPAL-TRANSMISSION-SIG',
                 'transmission_time': 'PAYPAL-TRANSMISSION-TIME'}
        payload = {key: headers.get(header) for key, header in names.items()}
        if not all(payload.values()):
            return False
        payload.update(webhook_id=wid, webhook_event=data)
        return self.request('POST', '/v1/notifications/verify-webhook-signature', json=payload).get('verification_status') == 'SUCCESS'


def resolve_owner(details, owners=None):
    sid = details['id']
    sub = Subscription.query.filter_by(paypal_subscription_id=sid).first()
    candidates = set()
    if sub:
        candidates.add(sub.user_id)
    custom = details.get('custom_id')
    if custom:
        if not db.session.get(User, custom):
            raise ReviewRequired('PayPal custom_id does not identify an existing user')
        candidates.add(custom)
    # Explicit owner map is operator-verified; never infer ownership from email.
    if owners and sid in owners:
        candidates.add(owners[sid])
    if not candidates:
        candidates.update(p.user_id for p in PaymentTransaction.query.filter_by(
            paypal_subscription_id=sid).all() if p.user_id)
    if len(candidates) != 1:
        raise ReviewRequired('Missing or conflicting owner; verify a subscription-to-user mapping')
    uid = candidates.pop()
    user = User.query.filter_by(id=uid).populate_existing().with_for_update().first()
    if not user:
        raise ReviewRequired('Mapped user no longer exists')
    return user


def sync_subscription(details, owners=None):
    spec = plans().get(details.get('plan_id'))
    if not spec:
        raise ReviewRequired('Unknown PayPal plan; no automatic tier assignment')
    if str(details.get('quantity', '1')) != '1' or details.get('plan_overridden'):
        raise ReviewRequired('Quantity or customized plan requires review')
    user = resolve_owner(details, owners)
    sub = Subscription.query.filter_by(paypal_subscription_id=details['id']).populate_existing().with_for_update().first()
    mapping = {'ACTIVE': 'active', 'APPROVAL_PENDING': 'pending', 'APPROVED': 'pending',
               'SUSPENDED': 'suspended', 'CANCELLED': 'canceled', 'EXPIRED': 'expired'}
    if details.get('status') not in mapping:
        raise ReviewRequired('Unknown PayPal subscription status')
    before = sub.status if sub else None
    status_key = 'paypal_status:' + details['id']
    remote_time = details.get('status_update_time')
    status_stamp = db.session.get(CreditSystemState, status_key)
    # A delayed response/webhook must not overwrite a newer verified status.
    if sub is not None and remote_time and status_stamp and parse_time(remote_time) < parse_time(status_stamp.value):
        return sub, user, {'before': before, 'after': sub.status}
    if sub is None:
        sub = Subscription(user_id=user.id, paypal_subscription_id=details['id'],
                           tier=spec[0], credits_per_month=spec[1],
                           status=mapping[details['status']], expiry_date=credit_day()-timedelta(days=1))
        db.session.add(sub)
    elif sub.user_id != user.id:
        raise ReviewRequired('Subscription ownership conflict')
    sub.status = mapping[details['status']]
    sub.tier, sub.credits_per_month = spec[:2]
    if remote_time:
        normalized_time = iso(parse_time(remote_time))
        if status_stamp:
            status_stamp.value = normalized_time
        else:
            db.session.add(CreditSystemState(key=status_key, value=normalized_time))
    db.session.flush()
    return sub, user, {'before': before, 'after': sub.status}


def normalize_payment(transaction):
    pid = transaction.get('id')
    if not pid or not isinstance(pid, str) or len(pid) > 50:
        raise ReviewRequired('Missing/invalid payment transaction ID')
    paid_at = parse_time(transaction['time'])
    if paid_at > utcnow() + timedelta(minutes=5):
        raise ReviewRequired('Payment timestamp is in the future')
    amount = transaction['amount_with_breakdown']['gross_amount']
    value = Decimal(amount['value'])
    if not value.is_finite() or value <= 0:
        raise ReviewRequired('Invalid payment amount')
    return pid, paid_at, value, amount['currency_code']


def issue_payment(sub, user, details, transaction, *, approved_missing=None, restore_days=None, webhook_id=None):
    """One verified payment. Does not commit. Caller must rollback on any exception.

    approved_missing maps a reviewed payment ID to its subscription ID. It can
    restore a reviewed legacy payment without an issuance receipt, but never
    overrides a receipt with a grant or conflicting ownership.
    """
    pid, paid_at, amount, currency = normalize_payment(transaction)
    result = {'payment_id': pid, 'subscription_id': sub.paypal_subscription_id,
              'user_id': user.id, 'email': user.email, 'paid_at': iso(paid_at)}
    existing = PaymentTransaction.query.filter_by(paypal_capture_id=pid).populate_existing().with_for_update().first()
    if existing and (existing.user_id != user.id or existing.paypal_subscription_id != sub.paypal_subscription_id
                     or existing.transaction_type != 'subscription_payment'
                     or existing.subscription_id not in (None, sub.id)):
        raise ReviewRequired('Existing payment has a different owner/subscription/type')
    if transaction.get('status') != 'COMPLETED':
        result.update(action='review_payment_status', status=transaction.get('status'))
        return result
    tier, credits, expected_price = plans()[details['plan_id']]
    if currency != 'USD' or amount != expected_price:
        result.update(action='review_amount', amount=str(amount), currency=currency)
        return result
    approved = (approved_missing or {}).get(pid) == sub.paypal_subscription_id
    receipt = None
    if existing:
        receipt = SubscriptionCreditReceipt.query.filter_by(payment_id=existing.id).first()
        if receipt and receipt.subscription_id != sub.id:
            raise ReviewRequired('Existing receipt belongs to another subscription')
        if existing.status != 'completed':
            result['action'] = 'review_local_payment_status'
            return result
        sub.expiry_date = max(sub.expiry_date, credit_day(paid_at) + timedelta(days=30))
        if receipt and receipt.grant_id is not None:
            result['action'] = 'already_recorded'
            return result
        # A legacy payment row proves payment, not that credits were delivered.
        # Keep it reviewable on every retry; never report this as successful
        # issuance, or automatically replace grants that were spent/expired.
        if not approved:
            if not receipt:
                db.session.add(SubscriptionCreditReceipt(payment_id=existing.id,
                    subscription_id=sub.id, grant_id=None, original_amount=credits,
                    payment_time=paid_at, mode='legacy_payment_recorded'))
            result.update(action='review_legacy_payment_without_grant',
                          reason='Completed local payment has no linked credit grant; verify legacy issuance/spending before approving restoration')
            return result
        if receipt and receipt.mode not in ('legacy_payment_recorded',):
            raise ReviewRequired('Receipt has lost its grant; investigate deletion before restoring')
    boundary = db.session.get(CreditSystemState, 'atomic_issuance_started_at')
    if not boundary:
        raise ReviewRequired('Credit migration/cutover marker missing')
    cutover = parse_time(boundary.value)
    # Old failed requests can have committed a grant without a payment row.
    # For recent cycles, cleanup could not have deleted an unexpired grant.
    recent = paid_at >= utcnow() - timedelta(days=27)
    possible_grants = CreditGrant.query.outerjoin(SubscriptionCreditReceipt,
        SubscriptionCreditReceipt.grant_id == CreditGrant.id).filter(
        SubscriptionCreditReceipt.id.is_(None), CreditGrant.user_id == user.id,
        CreditGrant.source_type != 'promo', CreditGrant.issued_at >= paid_at-timedelta(days=2)).count()
    spending = CreditTransaction.query.filter(CreditTransaction.user_id == user.id,
        CreditTransaction.amount < 0, CreditTransaction.timestamp >= paid_at-timedelta(days=2)).count()
    safe_recent = recent and not possible_grants and not spending
    if paid_at < cutover and not approved and not safe_recent:
        result.update(action='review_legacy_issuance', possible_grants=possible_grants,
                      spending_entries=spending)
        return result
    expiry = credit_day(paid_at) + timedelta(days=30)
    mode = 'approved_repair' if approved else ('new_payment' if paid_at >= cutover else 'recent_missing_repair')
    if expiry < credit_day():
        if not approved or restore_days is None:
            result['action'] = 'review_expired_missing_cycle'
            return result
        expiry = credit_day() + timedelta(days=restore_days)
        mode = 'approved_historical_restoration'
    payer_email = transaction.get('payer_email') or details.get('subscriber', {}).get('email_address')
    payment = existing or PaymentTransaction(user_id=user.id, subscription_id=sub.id,
        paypal_capture_id=pid, paypal_subscription_id=sub.paypal_subscription_id,
        paypal_webhook_event_id=webhook_id, platform_email=user.email,
        paypal_payer_email=payer_email, custom_id=details.get('custom_id'),
        amount=float(amount), currency=currency, transaction_type='subscription_payment',
        status='completed', description=f'Tier {tier} verified payment; {mode}')
    if existing and existing.subscription_id is None:
        existing.subscription_id = sub.id
    grant = CreditGrant(user_id=user.id, balance=credits, source_type='subscription',
        description=f'Tier {tier}; PayPal {pid}; {mode}', expiry_date=expiry)
    db.session.add_all([payment, grant])
    db.session.flush()
    if receipt:
        receipt.grant_id, receipt.original_amount = grant.id, credits
        receipt.payment_time, receipt.mode, receipt.issued_at = paid_at, mode, utcnow()
    else:
        db.session.add(SubscriptionCreditReceipt(payment_id=payment.id, subscription_id=sub.id,
            grant_id=grant.id, original_amount=credits, payment_time=paid_at, mode=mode))
    db.session.add_all([
        CreditTransaction(user_id=user.id, amount=credits, transaction_type='earned',
            description=f'Tier {tier}; payment {pid}; grant {grant.id}; {mode}')])
    # This is paid-through, not the next PayPal billing date. Never revive canceled status.
    sub.expiry_date = max(sub.expiry_date, expiry)
    db.session.flush()
    result.update(action='issue_credits', credits=credits, expires=expiry.isoformat(), mode=mode)
    return result
