"""Post-commit delivery of event promotion credit notifications."""
from datetime import datetime
from flask import current_app
from flask_mail import Message
from . import mail
from .models import db, EventPromotionEmail


def queue_promotion_email(grant, user, event, paid_guests):
    # Snapshot the issued amount and expiry. Retrying after the credit is spent
    # must still describe the original award, not its current balance.
    body = (
        f'Hi {user.first_name},\n\n'
        f'Your own spot for "{event.title}" on {event.date.strftime("%B %d, %Y")} '
        'is now FREE under this event\'s promotion. Since you already paid for '
        'your own spot with money or an Event Credit, we have added a credit to your account.\n\n'
        'You have received 1 Event Credit for your own spot.\n'
    )
    if paid_guests:
        body += ('Your plus-ones remain paid. This promotional credit does not include '
                 'your plus-ones.\n')
    body += (
        '\nYou can see this credit on your Profile page after signing in. '
        'Use it to cover your own spot when signing up for another paid event.\n\n'
        f'The credit is valid for 30 days and expires on {grant.expiry_date.strftime("%B %d, %Y")}.\n\n'
        'Your current RSVP is still confirmed; you do not need to sign up again.\n\n'
        'The Pick Up Volleyball Team'
    )
    db.session.add(EventPromotionEmail(grant_id=grant.id, event_id=event.id,
        recipient=user.email, subject='Your event spot is now free: 1 Event Credit added', body=body))


def deliver_promotion_emails(*, event_id=None, limit=100):
    """Send committed pending notifications; failed deliveries remain retryable.

    This function commits notification state, so callers must commit credits first.
    SMTP delivery cannot guarantee exactly-once email if the process stops after
    provider acceptance; retrying never creates another credit.
    """
    query = EventPromotionEmail.query.filter_by(sent_at=None)
    if event_id is not None:
        query = query.filter_by(event_id=event_id)
    ids = [row.id for row in query.order_by(EventPromotionEmail.id).limit(limit).all()]
    sent = failed = 0
    for notification_id in ids:
        row = EventPromotionEmail.query.filter_by(id=notification_id).populate_existing().with_for_update().first()
        if row is None or row.sent_at is not None:
            db.session.rollback()
            continue
        row.attempts += 1
        try:
            mail.send(Message(subject=row.subject, recipients=[row.recipient], body=row.body))
        except Exception as error:
            row.last_error = str(error)[:1024]
            failed += 1
            current_app.logger.exception('Promotion credit issued but email delivery failed')
        else:
            row.sent_at = datetime.utcnow()
            row.last_error = None
            sent += 1
        db.session.commit()
    return dict(sent=sent, failed=failed)
