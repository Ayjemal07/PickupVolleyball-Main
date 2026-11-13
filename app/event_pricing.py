"""Shared pricing rules for members, guests, and RSVP changes."""
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import current_app
from sqlalchemy import or_
from .models import db, EventAttendee, EventPromotionClaim, CreditGrant, CreditTransaction


def event_timezone():
    return ZoneInfo(current_app.config.get('EVENT_TIMEZONE') or 'America/Los_Angeles')


def event_now():
    return datetime.now(event_timezone()).replace(tzinfo=None)


def hours_until_event(event, now=None):
    start = datetime.combine(event.date, event.start_time).replace(tzinfo=event_timezone())
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=event_timezone())
    return (start.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds() / 3600


def issue_event_credit(user_id, event, amount, source_type, description):
    """Caller commits this grant and its audit entry with the RSVP change."""
    from .subscription_credits import credit_day
    grant = CreditGrant(user_id=user_id, balance=amount, source_type=source_type,
                        description=description, expiry_date=credit_day() + timedelta(days=30))
    db.session.add(grant)
    db.session.add(CreditTransaction(user_id=user_id, amount=amount,
                                    transaction_type=source_type, description=description))
    return grant


def normalize_email(email):
    value = str(email or '').strip().lower()
    if len(value) > 150 or '@' not in value or any(c.isspace() for c in value):
        raise ValueError('Enter a valid email to check promotional eligibility.')
    return value


def promotion_claim(event, email, user_id=None):
    identity = EventPromotionClaim.email == normalize_email(email)
    if user_id:
        identity = or_(identity, EventPromotionClaim.user_id == user_id)
    return EventPromotionClaim.query.filter(EventPromotionClaim.event_id == event.id, identity).populate_existing().with_for_update().first()


def record_free_claim(event, email, user_id=None):
    if promotion_claim(event, email, user_id):
        raise ValueError('This email has already redeemed its promotion for this event.')
    db.session.add(EventPromotionClaim(event_id=event.id, email=normalize_email(email),
                                      user_id=user_id, benefit='free'))


def free_spots_remaining(event, *, lock=False):
    # A locking read sees bookings committed while waiting for the event lock,
    # including on MySQL's default REPEATABLE READ isolation level.
    query = EventAttendee.query.filter_by(event_id=event.id)
    if lock:
        query = query.populate_existing().with_for_update()
    attendees = query.all()
    open_spots = max(0, event.max_capacity - sum(1 + (a.guest_count or 0) for a in attendees))
    if event.ticket_price <= 0:
        return open_spots
    claimed = sum((a.free_spots or 0) + int(bool(a.promotion_compensated)) for a in attendees)
    return min(open_spots, max(0, (event.free_spots or 0) - claimed))


def compensate_existing_bookings(event):
    """Apply the first-X promotion to existing primaries, oldest booking first.

    Caller holds the event lock and commits credits, claims, and event changes
    together. Guest allocations are recorded for manual refunds only.
    """
    if event.status != 'active' or hours_until_event(event) <= 0:
        return 0, 0
    attendees = EventAttendee.query.filter_by(event_id=event.id).order_by(
        EventAttendee.timestamp, EventAttendee.id).populate_existing().with_for_update().all()
    allocated = sum((a.free_spots or 0) + int(bool(a.promotion_compensated)) for a in attendees)
    remaining = max(0, (event.free_spots or 0) - allocated)
    credits_issued = guests_to_refund = 0
    for attendee in attendees:
        if not remaining:
            break
        if attendee.free_spots or attendee.promotion_compensated:
            continue
        email = attendee.user.email if attendee.user_id else attendee.email
        if not email or promotion_claim(event, email, attendee.user_id):
            continue
        db.session.add(EventPromotionClaim(event_id=event.id, email=normalize_email(email),
            user_id=attendee.user_id, benefit='credit' if attendee.user_id else 'manual_refund'))
        attendee.promotion_compensated = True
        remaining -= 1
        if attendee.user_id:
            description = f'Primary spot promotion credit for {event.title}'
            grant = issue_event_credit(attendee.user_id, event, 1, 'event_promotion', description)
            db.session.flush()
            from .event_notifications import queue_promotion_email
            queue_promotion_email(grant, attendee.user, event, attendee.guest_count or 0)
            credits_issued += 1
        else:
            guests_to_refund += 1
        db.session.flush()
    return credits_issued, guests_to_refund


def quote(event, quantity, user=None, rsvp=None, email=None):
    """Allocate free spots first; a member credit covers their own paid spot."""
    additional = max(0, quantity - (1 + (rsvp.guest_count or 0) if rsvp else 0))
    if event.ticket_price <= 0 and quantity > 1 and additional > 0:
        raise ValueError('An admin must set the regular ticket price before plus-ones can be added.')
    email = user.email if user else email
    eligible = not rsvp and email and not promotion_claim(event, email, user.id if user else None)
    free = int(bool(additional and eligible and free_spots_remaining(event, lock=True) > 0))
    paid = additional - free
    credits = int(bool(not rsvp and free == 0 and paid and event.ticket_price > 0
                       and user and user.event_credits > 0))
    return dict(free_spots=free, credits=credits,
                amount=Decimal(str(event.ticket_price)).quantize(Decimal('0.01')) * (paid - credits))


def parse_free_spots(form, capacity, existing=0):
    if capacity < 1:
        raise ValueError('Capacity must be at least 1.')
    mode = form.get('pricing_type')
    if mode == 'free':
        return capacity
    if mode == 'limited':
        count = int(form.get('free_spots') or 0)
        if not 1 <= count <= capacity:
            raise ValueError('Free spots must be between 1 and the event capacity.')
        return count
    return 0 if mode == 'paid' else existing
