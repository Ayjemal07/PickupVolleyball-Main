"""Durable payment-to-credit receipts. Import before Flask-Migrate initializes."""
from datetime import datetime
from .models import db


class SubscriptionCreditReceipt(db.Model):
    __tablename__ = 'subscription_credit_receipt'
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment_transaction.id'), nullable=False, unique=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey('subscription.id'), nullable=False)
    grant_id = db.Column(db.Integer, db.ForeignKey('credit_grant.id'), nullable=True, unique=True)
    original_amount = db.Column(db.Integer, nullable=False)
    payment_time = db.Column(db.DateTime, nullable=False)
    mode = db.Column(db.String(50), nullable=False)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class CreditSystemState(db.Model):
    __tablename__ = 'credit_system_state'
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


class SubscriptionCheckout(db.Model):
    """Durable checkout intent, retained after completion for audit/recovery."""
    __tablename__ = 'subscription_checkout'
    id = db.Column(db.String(36), primary_key=True)  # Also PayPal-Request-Id.
    slot_key = db.Column(db.String(80), unique=True, nullable=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False, index=True)
    tier = db.Column(db.Integer, nullable=False)
    action = db.Column(db.String(20), nullable=False)
    paypal_subscription_id = db.Column(db.String(255), unique=True, nullable=True)
    replaces_subscription_id = db.Column(db.String(255), nullable=True)
    payload = db.Column(db.Text, nullable=False)
    state = db.Column(db.String(30), nullable=False, default='creating')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_error = db.Column(db.String(255), nullable=True)
