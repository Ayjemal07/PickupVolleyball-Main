# This file handles the routes

from flask import Blueprint, render_template, redirect, url_for, flash, session, abort, make_response
import urllib.parse

import traceback
from flask import request, jsonify # Import jsonify
from datetime import datetime, timedelta, date
from decimal import Decimal, InvalidOperation
from collections import defaultdict
import hmac
import re
import secrets
import math
from urllib.parse import quote
from sqlalchemy import update

import os
from flask import current_app
from werkzeug.utils import secure_filename
import uuid 
from flask_login import current_user, login_required


from .models import (
    Event, User, db, EventAttendee, EventCheckout, Subscription, CreditGrant,
    CreditTransaction, PaymentTransaction, PayPalWebhookEvent,
    MerchOrder, MerchOrderItem, MerchProduct, MerchProductImage, MerchVariant
)
from flask_mail import Message
from . import mail  

import os
from dotenv import load_dotenv
import requests

import base64
from .utils import generate_detailed_waiver, add_user_credit, cleanup_user_expired_credits
from .event_pricing import (quote as event_quote, free_spots_remaining, parse_free_spots,
                            normalize_email, record_free_claim, compensate_existing_bookings,
                            event_now, hours_until_event, issue_event_credit)


load_dotenv() # Load variables from .env file



main = Blueprint('main', __name__)

# PayPal credentials
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET")
PAYPAL_BASE = os.getenv("PAYPAL_BASE", "https://api-m.paypal.com")
PAYPAL_PLAN_ID_TIER1 = os.getenv("PAYPAL_PLAN_ID_TIER1")
PAYPAL_PLAN_ID_TIER2 = os.getenv("PAYPAL_PLAN_ID_TIER2")
RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS = 48

FREE_EVENT_COURTESY_MESSAGE = (
    "Payment is not required for this event. Courtesy of Pickup Volleyball."
)


def is_free_event(event):
    """
    Authoritative rule for determining whether an event is FREE.
    FREE events do not use PayPal or event credits.
    """
    try:
        return float(event.ticket_price or 0) <= 0
    except (TypeError, ValueError):
        return False


def get_event_ticket_price(form, existing_price=0.0):
    """
    Keep a regular price for plus-ones, including full-capacity promotions.
    """
    pricing_type = (form.get("pricing_type") or "").strip().lower()

    raw_price = form.get("ticket_price")

    if raw_price in (None, ""):
        price = float(existing_price or 0)
    else:
        price = float(raw_price)

    if not math.isfinite(price) or price <= 0 or round(price, 2) != price:
        raise ValueError(
            "Paid events need a positive ticket price with no more than two decimal places."
        )

    return price


def get_event_guest_policy(form, existing_limit=0):
    allowed = form.get('allow_guests') == 'on'
    if not allowed:
        return False, 0
    limit = int(form.get('guest_limit') or existing_limit or 0)
    if limit < 1:
        raise ValueError('Choose at least 1 plus-one, or turn off plus-ones.')
    return True, limit

def spend_user_credit(user, amount_needed=1):
    ok = user.spend_credits(amount_needed)
    return ok, "Success" if ok else "Insufficient credits"

def send_cancellation_credit_email(user, event, credits_num, expiry_date):
    """Sends email with specific wording required."""
    try:
        subject = "PUVB Event Cancellation"
        
        # Format: "Thursday 11/19 7:30pm"
        event_datetime_str = f"{event.date.strftime('%A %m/%d')} {event.start_time.strftime('%I:%M%p').lstrip('0').lower()}"
        
        body = f"""
        Cancellation details: '{event_datetime_str} {event.title} in {event.location}' has been canceled and will no longer be happening. 
        
        You will automatically receive credit(s) for the canceled event. 
        You will receive 1 credit for each spot you registered (Total: {credits_num}). 
        
        Credits will be valid for 30 days (Expires: {expiry_date.strftime('%m/%d/%Y')}). 
        
        We’re sorry for any inconvenience and look forward to having you join an event soon!
        """
        
        msg = Message(subject=subject, recipients=[user.email], body=body.strip())
        mail.send(msg)
    except Exception as e:
        print(f"Failed to send cancellation email: {e}")

def send_free_event_cancellation_email(
    email,
    event,
    first_name=None
):
    """
    Sends a cancellation notification for a FREE event.
    Does not mention refunds or event credits.
    """
    try:
        subject = "PUVB Event Cancellation"

        event_date_str = event.date.strftime('%A %m/%d')
        event_time_str = (
            event.start_time
            .strftime('%I:%M%p')
            .lstrip('0')
            .lower()
        )

        greeting = (
            f"Hi {first_name},"
            if first_name
            else "Hello,"
        )

        body = f"""
        {greeting}

        Cancellation details: '{event_date_str} {event_time_str}
        {event.title} in {event.location}' has been canceled
        and will no longer be happening.

        No additional event credit is due for free spots or
        primary spots already compensated by a promotional credit.

        We’re sorry for any inconvenience and look forward
        to having you join an event soon!
        """

        msg = Message(
            subject=subject,
            recipients=[email],
            body=body.strip()
        )

        mail.send(msg)

    except Exception as e:
        print(
            f"Failed to send FREE event cancellation email: {e}"
        )

def send_guest_cancellation_only_email(email, event):
    """Sends simple cancellation email to non-registered guests (no credits possible)."""
    try:
        subject = "PUVB Event Cancellation"
        event_date_str = event.date.strftime('%A %m/%d')
        event_time_str = event.start_time.strftime('%I:%M%p').lstrip("0").lower()
        
        body = f"""
        Cancellation details: '{event_date_str} {event_time_str} {event.title} in {event.location}' has been canceled and will no longer be happening.
        
        If you paid for this event, please reply to this email to coordinate a refund, or create an account to receive credits.
        
        We’re sorry for any inconvenience.
        """
        msg = Message(subject=subject, recipients=[email], body=body.strip())
        mail.send(msg)
    except Exception as e:
        print(f"Failed to send guest cancellation email: {e}")

def send_subscription_email(user_email):
    body = """
    Thank you for subscribing to Pick Up Volleyball events, we are excited to have you join and be part of our community! 
    
    You can view and sign up for all events using this link: https://pickupvolleyball.com/events

    Subscriptions will renew every 30 days starting from the time of purchase, with 4 new event credits being issued every cycle. Event credits cannot be used for guests, and any unused credits will not roll over. 
    See you out there!
    """

    msg = Message(
        subject="Subscription Receipt Email",
        recipients=[user_email],
        body=body.strip()
    )
    print(f"Attempting to send subscription email to: {user_email}") # <--- ADD THIS LINE
    mail.send(msg)
    print("Email send attempt completed.") # <--- AND THIS LINE


def send_rsvp_confirmation_email(user, event, guest_count):
    """Sends a confirmation email. (Fixed: Headers are now a dictionary)"""
    try:
        # --- 1. SETUP ---
        event_date_str = event.date.strftime('%A, %B %d, %Y')
        start_time_str = event.start_time.strftime('%I:%M %p')
        subject = f"Confirmation: You're signed up for {event.title}!"

        # --- 2. PLAIN TEXT BODY ---
        text_body = f"""
        Hi {user.first_name},

        You have successfully RSVP'd for an upcoming volleyball event. We're excited to see you there!

        Details:
        - Event: {event.title}
        - Date: {event_date_str}
        - Time: {start_time_str}
        - Location: {event.location}
        
        (Please view this email in an HTML-compatible viewer to see the map and full details.)
        
        - The Pick Up Volleyball Team
        """

        # --- 3. HTML BODY ---
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <p>Hi {user.first_name},</p>
                <p>You have successfully RSVP'd for an upcoming volleyball event. We're excited to see you there!</p>

                <p><strong>Registration details:</strong></p>
                <ul>
                    <li><strong>Date:</strong> {event_date_str}</li>
                    <li><strong>Time:</strong> {start_time_str}</li>
                    <li><strong>Location:</strong> {event.location}, {event.full_address or 'Not provided'}</li>
                    <li><strong>Total People in Your Party:</strong> {1 + guest_count}</li>
                </ul>

                <p><strong>Important Information:</strong></p>
                <ul>
                    <li>Remember to bring water, court shoes (shoes not worn outside), and any gear you may need!</li>
                    <li>If you need to cancel your spot or edit your guests, please do so from the event details page.</li>
                    <li>Use the map below if you have any trouble finding the gym!</li>
                </ul>

                <p>See you on the court!</p>
                <p>- The Pick Up Volleyball Team</p>
                <br>
        """

        # Check for Guide Image (FUMC)
        has_guide = False
        if (event.location == "First United Methodist Church") or \
           (event.full_address == "1838 SW Jefferson St, Portland, OR 97201"):
            html_body += '<img src="cid:fumc_guide" style="max-width: 100%; height: auto; display: block; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 8px;"><br>'
            has_guide = True

        # Add Logo Placeholder
        html_body += '<img src="cid:voll_logo" style="max-width: 150px; height: auto;">'
        html_body += "</body></html>"

        # --- 4. CONSTRUCT MESSAGE ---
        msg = Message(
            subject=subject, 
            recipients=[user.email],
            bcc=[current_app.config.get('MAIL_DEFAULT_SENDER') or current_app.config.get('MAIL_USERNAME')],
            body=text_body.strip(), 
            html=html_body
        )

        # --- 5. ATTACH IMAGES (Fixed Headers) ---
        # Guide
        if has_guide:
            guide_path = os.path.join(current_app.root_path, 'static', 'images', 'FUMC.Directions.Guide.jpeg')
            if os.path.exists(guide_path):
                with open(guide_path, 'rb') as fp:
                    # FIX: headers must be a dict {}
                    msg.attach("FUMC.Directions.Guide.jpeg", "image/jpeg", fp.read(), 'inline', headers={'Content-ID': '<fumc_guide>'})
        
        # Logo
        logo_path = os.path.join(current_app.root_path, 'static', 'images', 'voll-logo.png')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as fp:
                # FIX: headers must be a dict {}
                msg.attach("voll-logo.png", "image/png", fp.read(), 'inline', headers={'Content-ID': '<voll_logo>'})

        mail.send(msg)
        print(f"RSVP Confirmation email sent to {user.email} for event {event.id}")

    except Exception as e:
        print(f"Failed to send RSVP confirmation email: {e}")
        traceback.print_exc()


def send_guest_confirmation_email(guest_info, event, waiver_path):
    """Sends a confirmation email to a guest. (Fixed: Headers are now a dictionary)"""
    try:
        # --- 1. SETUP ---
        admin_email = current_app.config.get('MAIL_DEFAULT_SENDER') or current_app.config.get('MAIL_USERNAME')
        event_date_str = event.date.strftime('%A, %B %d, %Y')
        start_time_str = event.start_time.strftime('%I:%M %p')
        subject = f"Confirmation: You're signed up for {event.title}!"

        # --- 2. PLAIN TEXT BODY ---
        text_body = f"""
        Hi {guest_info['first_name']},
        
        This is a confirmation that you have successfully registered as a guest.
        
        Details:
        - Event: {event.title}
        - Date: {event_date_str}
        - Time: {start_time_str}
        - Location: {event.location}
        
        Your signed waiver is attached.
        
        - The Pick Up Volleyball Team
        """

        # --- 3. HTML BODY ---
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <p>Hi {guest_info['first_name']},</p>
                <p>This is a confirmation that you have successfully registered as a guest for an upcoming volleyball event.</p>

                <p><strong>Here are the details:</strong></p>
                <ul>
                    <li><strong>Event:</strong> {event.title}</li>
                    <li><strong>Date:</strong> {event_date_str}</li>
                    <li><strong>Time:</strong> {start_time_str}</li>
                    <li><strong>Location:</strong> {event.location}, {event.full_address or 'Not provided'}</li>
                </ul>
                <p>Your signed liability waiver is attached to this email for your records.</p>
        """

        # Check for Guide Image (FUMC)
        has_guide = False
        if (event.location == "First United Methodist Church") or \
           (event.full_address == "1838 SW Jefferson St, Portland, OR 97201"):
            html_body += '<p>Use the map below if you have any trouble finding the gym!</p>'
            html_body += '<img src="cid:fumc_guide" style="max-width: 100%; height: auto; display: block; margin-bottom: 20px; border: 1px solid #ddd; border-radius: 8px;"><br>'
            has_guide = True

        # Add Logo Placeholder
        html_body += """
                <p>See you on the court!</p>
                <p>- The Pick Up Volleyball Team</p>
                <br>
                <img src="cid:voll_logo" style="max-width: 150px; height: auto;">
            </body>
        </html>
        """

        # --- 4. CONSTRUCT MESSAGE ---
        msg = Message(
            subject=subject,
            sender=admin_email,
            recipients=[guest_info['email']],
            bcc=[admin_email],
            body=text_body.strip(),
            html=html_body
        )

        # --- 5. ATTACHMENTS ---
        # Waiver
        if waiver_path and os.path.exists(waiver_path):
            with open(waiver_path, 'rb') as fp:
                msg.attach("Liability_Waiver.pdf", "application/pdf", fp.read())

        # Guide Image (Fixed Headers)
        if has_guide:
            guide_path = os.path.join(current_app.root_path, 'static', 'images', 'FUMC.Directions.Guide.jpeg')
            if os.path.exists(guide_path):
                with open(guide_path, 'rb') as fp:
                    # FIX: headers must be a dict {}
                    msg.attach("FUMC.Directions.Guide.jpeg", "image/jpeg", fp.read(), 'inline', headers={'Content-ID': '<fumc_guide>'})
        
        # Logo Image (Fixed Headers)
        logo_path = os.path.join(current_app.root_path, 'static', 'images', 'voll-logo.png')
        if os.path.exists(logo_path):
            with open(logo_path, 'rb') as fp:
                # FIX: headers must be a dict {}
                msg.attach("voll-logo.png", "image/png", fp.read(), 'inline', headers={'Content-ID': '<voll_logo>'})

        mail.send(msg)
        print(f"Guest confirmation email sent to {guest_info['email']} (BCC: {admin_email})")

    except Exception as e:
        print(f"Failed to send guest confirmation email: {e}")
        traceback.print_exc()

def format_event_line(event):
    if not event:
        return "Unknown event"

    date_part = event.date.strftime('%A, %B %d, %Y').replace(' 0', ' ')
    start = event.start_time.strftime('%I:%M %p').lstrip('0')
    end = event.end_time.strftime('%I:%M %p').lstrip('0')

    return f"{date_part}, {start}–{end} at {event.location} — {event.title}"


def build_weekly_admin_audit_report(start_date, end_date):
    users = User.query.order_by(User.first_name.asc(), User.last_name.asc()).all()

    lines = []
    lines.append(f"PUVB Weekly Audit Report")
    lines.append(f"Reporting Period: {start_date.strftime('%B %d, %Y')} – {end_date.strftime('%B %d, %Y')}")
    lines.append("")
    lines.append("=" * 72)
    lines.append("")

    for user in users:
        active_subs = Subscription.query.filter(
            Subscription.user_id == user.id,
            Subscription.status == 'active'
        ).all()

        active_credits = CreditGrant.query.filter(
            CreditGrant.user_id == user.id,
            CreditGrant.balance > 0,
            CreditGrant.expiry_date >= date.today()
        ).order_by(CreditGrant.expiry_date.asc()).all()

        credit_transactions = CreditTransaction.query.filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.timestamp >= start_date,
            CreditTransaction.timestamp < end_date + timedelta(days=1)
        ).order_by(CreditTransaction.timestamp.asc()).all()

        payments = PaymentTransaction.query.filter(
            PaymentTransaction.user_id == user.id,
            PaymentTransaction.created_at >= start_date,
            PaymentTransaction.created_at < end_date + timedelta(days=1)
        ).order_by(PaymentTransaction.created_at.asc()).all()

        if not active_subs and not active_credits and not credit_transactions and not payments:
            continue

        lines.append(f"User: {user.first_name} {user.last_name}")
        lines.append(f"Email: {user.email}")
        lines.append(f"User ID: {user.id}")
        lines.append("")

        if active_subs:
            sub_summary = ", ".join(
                [f"Tier {sub.tier} ({sub.status}, renews/expires {sub.expiry_date.strftime('%B %d, %Y')})"
                 for sub in active_subs]
            )
        else:
            sub_summary = "None"

        lines.append(f"Subscription: {sub_summary}")
        lines.append(f"Current Credit Balance: {user.event_credits}")

        if active_credits:
            lines.append("Credit Expiration Breakdown:")
            for credit in active_credits:
                lines.append(
                    f"- {credit.balance} credit(s), {credit.source_type}, "
                    f"expires {credit.expiry_date.strftime('%B %d, %Y')} "
                    f"({credit.description or 'No description'})"
                )
        else:
            lines.append("Credit Expiration Breakdown: No active credits")

        lines.append("")
        lines.append("Credit Usage / Credit Activity:")

        weekly_credit_activity = False
        for tx in credit_transactions:
            weekly_credit_activity = True
            sign = "+" if tx.amount > 0 else ""
            lines.append(
                f"- {sign}{tx.amount} credit(s) — {tx.transaction_type} — "
                f"{tx.description or 'No description'} "
                f"({tx.timestamp.strftime('%B %d, %Y %I:%M %p')})"
            )

        if not weekly_credit_activity:
            lines.append("- No credit activity this week")

        lines.append("")
        lines.append("Event Purchases:")

        event_payments = [p for p in payments if p.transaction_type == 'event_purchase']
        if event_payments:
            for p in event_payments:
                lines.append(
                    f"- ${p.amount:.2f} {p.currency} PayPal purchase — "
                    f"{format_event_line(p.event)} "
                    f"PayPal Capture: {p.paypal_capture_id or 'N/A'}"
                )
        else:
            lines.append("- No event purchases this week")

        lines.append("")
        lines.append("Subscription Purchases / Renewals:")

        sub_payments = [
            p for p in payments
            if p.transaction_type in ('subscription_payment', 'subscription_created')
        ]

        if sub_payments:
            for p in sub_payments:
                lines.append(
                    f"- {p.description or 'Subscription activity'} — "
                    f"Status: {p.status} — "
                    f"PayPal Sub: {p.paypal_subscription_id or 'N/A'} — "
                    f"PayPal Email: {p.paypal_payer_email or 'N/A'} — "
                    f"Platform Email: {p.platform_email or user.email} — "
                    f"custom_id: {p.custom_id or 'N/A'}"
                )
        else:
            lines.append("- No subscription purchases or renewals this week")

        lines.append("")
        lines.append("Admin Flags:")
        mismatch_payments = [
            p for p in payments
            if p.paypal_payer_email and p.platform_email
            and p.paypal_payer_email.lower() != p.platform_email.lower()
        ]

        if mismatch_payments:
            for p in mismatch_payments:
                lines.append(
                    f"- PayPal email differs from platform email: "
                    f"{p.paypal_payer_email} vs {p.platform_email}. "
                    f"Matched by custom_id: {p.custom_id or 'N/A'}"
                )
        else:
            lines.append("- No PayPal/platform email mismatches detected")

        lines.append("")
        lines.append("-" * 72)
        lines.append("")

    return "\n".join(lines)


def send_weekly_admin_audit_report():
    admin_emails = [
        email.strip()
        for email in os.getenv('ADMIN_EMAILS', '').split(',')
        if email.strip()
    ]

    if not admin_emails:
        raise ValueError("ADMIN_EMAILS is not configured")

    today = date.today()
    start_date = today - timedelta(days=7)
    end_date = today - timedelta(days=1)

    body = build_weekly_admin_audit_report(start_date, end_date)

    msg = Message(
        subject=f"PUVB Weekly Audit Report — {start_date.strftime('%b %d')}–{end_date.strftime('%b %d, %Y')}",
        recipients=admin_emails,
        body=body
    )

    mail.send(msg)

MERCH_SHIPPING_RATE = Decimal('7.99')

MERCH_FULFILLMENT_METHODS = {'shipping'}
MERCH_RESERVATION_MINUTES = 30


def merch_admin():
    return current_user.is_authenticated and current_user.role == 'admin'


def merch_admin_token():
    if 'merch_admin_token' not in session:
        session['merch_admin_token'] = secrets.token_urlsafe(32)
    return session['merch_admin_token']


def valid_merch_admin_token():
    supplied = request.form.get('admin_token', '')
    expected = session.get('merch_admin_token', '')
    return bool(expected) and hmac.compare_digest(supplied, expected)


def release_merch_reservation(order, status='expired'):
    if not order.inventory_reserved:
        return
    for item in order.items:
        if item.variant_id:
            released = db.session.execute(
                update(MerchVariant)
                .where(MerchVariant.id == item.variant_id,
                       MerchVariant.stock_reserved >= item.quantity)
                .values(stock_reserved=(
                    MerchVariant.stock_reserved - item.quantity
                ))
            )
            if released.rowcount != 1:
                raise ValueError('Reserved merchandise stock is inconsistent.')
    order.inventory_reserved = False
    order.reservation_expires_at = None
    order.status = status


def expire_merch_reservations():
    expired = (MerchOrder.query
               .filter(MerchOrder.status == 'pending',
                       MerchOrder.inventory_reserved.is_(True),
                       MerchOrder.reservation_expires_at < datetime.utcnow())
               .with_for_update()
               .all())
    for order in expired:
        release_merch_reservation(order)
    if expired:
        db.session.commit()


def merch_tracking_url(order):
    if not order.tracking_number:
        return None
    number = quote(order.tracking_number, safe='')
    return {
        'USPS': f'https://tools.usps.com/go/TrackConfirmAction?tLabels={number}',
        'UPS': f'https://www.ups.com/track?tracknum={number}',
        'FedEx': f'https://www.fedex.com/fedextrack/?trknbr={number}',
    }.get(order.tracking_carrier)

def serialize_merch_catalog():
    return [
        {
            'id': product.id,
            'name': product.name,
            'description': product.description,
            'price': f'{product.price:.2f}',
            'image': product.image,
            'detail_image': product.detail_image,
            'detail_position': product.detail_position,
            'gallery_images': [image.image for image in product.gallery_images],
            'has_stock': any(
                variant.active and variant.available > 0
                for variant in product.variants
            ),
            'variants': [
                {
                    'id': variant.id,
                    'size': variant.size,
                    'color': variant.color,
                    'available': variant.available,
                    'image': variant.image or product.image,
                }
                for variant in product.variants if variant.active
            ],
        }
        for product in (MerchProduct.query.filter_by(active=True)
                        .order_by(MerchProduct.created_at,
                                  MerchProduct.id).all())
    ]


def validate_merch_cart(raw_items):
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError('Your cart is empty.')

    validated_items = []
    quantities = defaultdict(int)
    subtotal = Decimal('0.00')

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError('One of the cart items is invalid.')

        product_id = str(raw_item.get('product_id') or '').strip()
        try:
            variant_id = int(raw_item.get('variant_id'))
        except (TypeError, ValueError):
            raise ValueError('Choose a size and color for each item.')

        variant = db.session.get(MerchVariant, variant_id)
        product = variant.product if variant else None
        if (not product or product.id != product_id or not product.active
                or not variant.active):
            raise ValueError(
                'One of the products is no longer available.'
            )

        try:
            quantity = int(raw_item.get('quantity'))
        except (TypeError, ValueError):
            raise ValueError(
                f'Choose a valid quantity for {product.name}.'
            )

        if quantity < 1 or quantity > 10:
            raise ValueError(
                f'Quantity for {product.name} must be '
                "between 1 and 10."
            )

        quantities[variant_id] += quantity
        if quantities[variant_id] > 10:
            raise ValueError(
                f'Maximum quantity is 10 for {product.name} '
                f'({variant.color}, {variant.size}).'
            )

    for variant_id, quantity in quantities.items():
        variant = db.session.get(MerchVariant, variant_id)
        product = variant.product
        if quantity > variant.available:
            raise ValueError(
                f'Only {variant.available} left for {product.name} '
                f'({variant.color}, {variant.size}).'
            )
        subtotal += product.price * quantity
        validated_items.append({
            'product_id': product.id,
            'variant_id': variant.id,
            'name': product.name,
            'size': variant.size,
            'color': variant.color,
            'quantity': quantity,
            'unit_price': product.price,
        })

    return (
        validated_items,
        subtotal.quantize(Decimal('0.01'))
    )


@main.route('/admin/send-weekly-audit', methods=['POST'])
@login_required
def send_weekly_audit_now():
    if current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        send_weekly_admin_audit_report()
        return jsonify({'success': True, 'message': 'Weekly audit report sent.'}), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/')
def home():
    return render_template('home.html', embedded=False)

@main.route('/about')
def about():
    return render_template('about.html')

@main.route('/events', methods=['GET', 'POST'])
def events():
    user_role = session.get('role', 'user')  # Default to 'user'
    is_authenticated = current_user.is_authenticated # Get the login status

    # Get list of image filenames
    image_folder = os.path.join(current_app.root_path, 'static', 'images')
    image_files = [f for f in os.listdir(image_folder) if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))]

    # For admin: Handle event creation if the form is submitted
    if request.method == 'POST' and current_user.is_authenticated and current_user.role == 'admin':
        # This section remains unchanged...
        title = request.form.get('title')
        description = request.form.get('description')
        date_str = request.form.get('date')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')
        location = request.form.get('location')
        full_address = request.form.get('full_address') # Get the new field
        try:
            allow_guests, guest_limit = get_event_guest_policy(request.form)
            ticket_price = get_event_ticket_price(request.form)
            max_capacity = int(request.form.get('max_capacity') or 28)
            free_spots = parse_free_spots(request.form, max_capacity)
        except ValueError as error:
            flash(str(error), 'error')
            return redirect(url_for('main.events'))


        # --- CORRECTED IMAGE HANDLING LOGIC ---
        image_filename = None
        # Priority 1: A new file was uploaded.
        image_file = request.files.get('eventImage')
        if image_file and image_file.filename != '':
            filename = secure_filename(image_file.filename)
            unique_filename = str(uuid.uuid4()) + "_" + filename
            save_path = os.path.join(current_app.root_path, 'static/images', unique_filename)
            image_file.save(save_path)
            image_filename = unique_filename
        # Priority 2: No new file, but an existing image was selected.
        elif request.form.get('existingImage'):
            image_filename = request.form.get('existingImage')

        # Create datetime objects for start and end times
        try:
            start_datetime = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M")
            end_datetime = datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M")
        except ValueError:
            flash("Invalid date or time format.", "error")
            return redirect(url_for('main.events'))

        # Validate start and end times
        if start_datetime >= end_datetime:
            flash("Start time must be before end time.", "error")
            return redirect(url_for('main.events'))

        new_event = Event(
            title=title,
            description=description,
            start_time=start_datetime.time(), # Store only time
            end_time=end_datetime.time(),     # Store only time
            location=location,
            full_address = full_address,
            status='active',
            allow_guests=allow_guests,
            guest_limit=guest_limit,
            ticket_price=ticket_price,
            free_spots=free_spots,
            max_capacity=max_capacity,
            date=datetime.strptime(date_str, "%Y-%m-%d").date(),
            image_filename=image_filename
        )
        db.session.add(new_event)
        db.session.commit()
        flash('Event added successfully!', 'success')
        return redirect(url_for('main.events'))

    now = event_now()
    two_months_ago = now.date() - timedelta(days=60)

    # 1. Automatically delete events older than two months
    events_to_delete = Event.query.filter(Event.date < two_months_ago).all()
    for event in events_to_delete:
        EventAttendee.query.filter_by(event_id=event.id).delete() # Clean up RSVPs
        db.session.delete(event)
    if events_to_delete:
        db.session.commit()

    # 2. Fetch all events that are not yet deleted
    displayable_events = Event.query.filter(Event.date >= two_months_ago).order_by(Event.date.asc()).all()

    upcoming_events = []
    past_events = []

    for event in displayable_events:
        # Combine the event's date and end_time into a single datetime object
        event_end_datetime = datetime.combine(event.date, event.end_time)

        # If the event's end time is in the future, it's an upcoming event
        if event_end_datetime >= now:
            upcoming_events.append(event)
        else:
            # Otherwise, it's a past event
            past_events.append(event)
            
    # 3. Sort past events descending to get the most recent, and limit to 2
    past_events.sort(key=lambda x: datetime.combine(x.date, x.end_time), reverse=True)

    past_events_display = past_events

    # 4. Process event data for the template
    user_id = session.get('user_id') or (current_user.id if current_user.is_authenticated else None)

    db.session.commit()


    def process_event_list(event_list):
        with db.session.no_autoflush:
            for event in event_list:
                event.formatted_date = event.date.strftime('%b %d, %Y')
                attendees = EventAttendee.query.filter_by(event_id=event.id).all()
                event.rsvp_count = sum(1 + (a.guest_count or 0) for a in attendees)
                event.is_attending = any(a.user_id == user_id for a in attendees)
                if user_id:
                    event.is_attending = any(a.user_id == user_id for a in attendees)
                else:
                    event.is_attending = False
    
    
    process_event_list(upcoming_events)
    process_event_list(past_events_display)

    def create_event_dict(event, is_past=False):
        return {
            "id": event.id,   
            "title": event.title,
            # Combine date and time before formatting
            "start": datetime.combine(event.date, event.start_time).isoformat(),
            "end": datetime.combine(event.date, event.end_time).isoformat(),
            "location": event.location,
            "full_address": event.full_address,
            "description": event.description,
            "formatted_date": event.formatted_date,
            "status": event.status,
            "cancellation_reason": event.cancellation_reason,
            "allow_guests": event.allow_guests,
            "guest_limit": event.guest_limit,
            "ticket_price": event.ticket_price,
            "free_spots": event.free_spots,
            "free_spots_remaining": free_spots_remaining(event),
            "max_capacity": event.max_capacity,
            "rsvp_count": event.rsvp_count,
            "is_attending": event.is_attending,
            'image_filename': event.image_filename,
            'is_past': is_past,
            "is_free": is_free_event(event),
            "pricing_type": "free" if event.free_spots == event.max_capacity else ("limited" if event.free_spots else "paid")
        }

    upcoming_events_data = [create_event_dict(e, is_past=False) for e in upcoming_events]
    past_events_data = [create_event_dict(e, is_past=True) for e in past_events_display]
    
    # Combine data for the FullCalendar view
    all_events_for_calendar = upcoming_events_data + past_events_data

    user_event_credits = 0
    soonest_expiry = None
    
    if current_user.is_authenticated:
        # 1. Clean up old credits first to ensure accuracy
        cleanup_user_expired_credits(current_user)
        
        # 2. Get the total valid credits (Sums up CreditGrant table)
        user_event_credits = current_user.event_credits

        # 3. Get the expiry date for display
        soonest_expiry = current_user.next_credit_expiry

    # Note: We pass has_active_subscription just for UI/Badge purposes, 
    # NOT for permission to use credits. Credits are valid if > 0.
    has_active_subscription = False
    if current_user.is_authenticated:
        active_sub = Subscription.query.filter(
            Subscription.user_id == current_user.id,
            Subscription.status == 'active'
        ).first()
        if active_sub:
            has_active_subscription = True

    print("PayPal Client ID:", PAYPAL_CLIENT_ID)
    return render_template('events.html', 
                           upcoming_events_data=upcoming_events_data,
                           past_events_data=past_events_data,
                           all_events_for_calendar=all_events_for_calendar,
                           user_role=user_role,
                           user_event_credits=user_event_credits,
                           has_active_subscription=has_active_subscription,
                           image_files=image_files,
                           is_authenticated=is_authenticated,
                           paypal_client_id=PAYPAL_CLIENT_ID,
                           soonest_credit_expiry=soonest_expiry)


#Route to add an event
@main.route('/events/add', methods=['POST'])
def add_event():
    user_role = session.get('role', 'user')
    
    if user_role != 'admin':
        return {'error': 'Unauthorized'}, 403

    data = request.get_json() # Use get_json() for data sent via fetch with JSON.stringify
    title = data.get('title')
    description = data.get('description')
    date_str = data.get('date')
    start_time_str = data.get('startTime')
    end_time_str = data.get('endTime')
    location = data.get('location')

    try:
        event_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_time_obj = datetime.strptime(start_time_str, "%H:%M").time()
        end_time_obj = datetime.strptime(end_time_str, "%H:%M").time()

    except ValueError:
        return jsonify({'error': 'Invalid date or time format'}), 400

    new_event = Event(
        title=title,
        description=description,
        date=event_date,
        start_time=start_time_obj,
        end_time=end_time_obj,
        location=location,
        status='active',

    )
    db.session.add(new_event)
    db.session.commit()

    return jsonify({'message': 'Event added successfully'}), 201

#Route to edit an event
@main.route('/events/edit/<int:event_id>', methods=['POST'])
@login_required
def edit_event(event_id):
    if current_user.role != 'admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('main.events'))
    event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first_or_404()

    try:
        # Update event fields from form data
        event.title = request.form.get('title', event.title)
        event.description = request.form.get('description', event.description)
        event.location = request.form.get('location', event.location)
        event.full_address = request.form.get('full_address', event.full_address) 

        
        date_str = request.form.get('date')
        if date_str:
            event.date = datetime.strptime(date_str, "%Y-%m-%d").date()
        
        start_time_str = request.form.get('start_time')
        if start_time_str:
            event.start_time = datetime.strptime(start_time_str, "%H:%M").time()
        
        end_time_str = request.form.get('end_time')
        if end_time_str:
            event.end_time = datetime.strptime(end_time_str, "%H:%M").time()

        event.allow_guests, event.guest_limit = get_event_guest_policy(request.form, event.guest_limit)
        requested_capacity = int(request.form.get('max_capacity', event.max_capacity))
        if requested_capacity < 1:
            raise ValueError('Capacity must be at least 1.')
        if requested_capacity != event.max_capacity:
            attendees = EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all()
            registered_count = sum(1 + (a.guest_count or 0) for a in attendees)
            if requested_capacity < registered_count:
                raise ValueError(f'Capacity cannot be below the {registered_count} people already registered.')
        event.max_capacity = requested_capacity

        new_ticket_price = get_event_ticket_price(
            request.form,
            event.ticket_price
        )

        event.free_spots = parse_free_spots(request.form, event.max_capacity, event.free_spots)
        event.ticket_price = new_ticket_price
        # Flush the admin changes before locking reads refresh booking records.
        db.session.flush()
        promotion_credits, guest_refunds = compensate_existing_bookings(event)
        
        # Handle image update
        image_file = request.files.get('eventImage')
        if image_file and image_file.filename != '':
                filename = secure_filename(image_file.filename)
                unique_filename = str(uuid.uuid4()) + "_" + filename
                save_path = os.path.join(current_app.root_path, 'static/images', unique_filename)
                image_file.save(save_path)
                event.image_filename = unique_filename
        elif request.form.get('existingImage'):
             event.image_filename = request.form.get('existingImage')

        db.session.commit()
        flash(f'Event updated successfully! {promotion_credits} promotional credit(s) issued '
              f'(valid for 30 days). {guest_refunds} guest primary spot(s) require a manual refund.', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error updating event: {e}', 'error')
        return redirect(url_for('main.events'))

    # Credit issuance must be durable before telling members they received it.
    from .event_notifications import deliver_promotion_emails
    try:
        delivery = deliver_promotion_emails(event_id=event.id)
        if delivery['failed']:
            flash('Credits were saved. Some promotion emails could not be sent and remain queued for retry.', 'warning')
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Promotion credits saved; notification delivery needs retry')
        flash('Credits were saved. Promotion emails remain queued for retry.', 'warning')

    return redirect(url_for('main.events'))

@main.route('/events/delete/<int:event_id>', methods=['POST'])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return jsonify({'message': 'Event deleted successfully'}), 200


@main.route('/events/cancel/<int:event_id>', methods=['POST'])
@login_required
def cancel_event(event_id):
    if current_user.role != 'admin':
        return jsonify(error='Unauthorized'), 403
    notifications = []
    try:
        event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first()
        if not event:
            return jsonify(error='Event not found.'), 404
        if event.status == 'canceled':
            return jsonify(error='This event is already canceled.'), 400
        event.status = 'canceled'
        event.cancellation_reason = (request.get_json(silent=True) or {}).get(
            'cancellation_reason', 'Cancelled Event')
        attendees = EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all()
        credits_issued = 0
        for attendee in attendees:
            spots = attendee.creditable_spots
            if attendee.user_id:
                user = attendee.user
                if spots:
                    grant = issue_event_credit(user.id, event, spots, 'cancellation', f'Refund: {event.title}')
                    credits_issued += spots
                    notifications.append(('credit', user, spots, grant.expiry_date))
                else:
                    notifications.append(('free', user.email, user.first_name))
            elif attendee.email:
                notifications.append(('guest', attendee.email))
        # The canceled status and grants commit together. Retried requests cannot
        # issue the same credits again, even when notification delivery fails.
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Unable to cancel event')
        return jsonify(error='Unable to cancel event.'), 500

    for notification in notifications:
        try:
            if notification[0] == 'credit':
                send_cancellation_credit_email(notification[1], event, notification[2], notification[3])
            elif notification[0] == 'free':
                send_free_event_cancellation_email(notification[1], event, first_name=notification[2])
            else:
                send_guest_cancellation_only_email(notification[1], event)
        except Exception:
            current_app.logger.exception('Event canceled but notification failed')
    return jsonify(message=f'Event canceled. {credits_issued} credit(s) issued.',
                   is_free_event=is_free_event(event), credits_issued=credits_issued), 200


#event details page
@main.route('/events/<int:event_id>')
def event_details(event_id):
    event = Event.query.get_or_404(event_id)
    event.formatted_date = event.date.strftime('%b %d, %Y')
    attendees = EventAttendee.query.filter_by(event_id=event.id).all()
    event.rsvp_count = sum(1 + (a.guest_count or 0) for a in attendees)
    user_id = session.get('user_id') or (current_user.id if current_user.is_authenticated else None)
    if user_id:
        is_attending = any(a.user_id == user_id for a in attendees)
    else:
        is_attending = False
    is_full = event.rsvp_count >= event.max_capacity

    try:
        # Combine the event's date and end_time into a single datetime object
        event_end_datetime = datetime.combine(event.date, event.end_time)
        now = event_now()
        event_passed = event_end_datetime < now
    except AttributeError:
        # Safety fallback if date or time fields are somehow missing
        event_passed = True

    maps_link = None
    if event.full_address:
        maps_link = f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote_plus(event.full_address)}"

    event_dict = {
        "id": event.id,
        "title": event.title,
        "location": event.location,
        "full_address": event.full_address,
        "description": event.description,
        "start": datetime.combine(event.date, event.start_time).isoformat(),
        "end": datetime.combine(event.date, event.end_time).isoformat(),
        "formatted_date": event.formatted_date,
        "status": event.status,
        "cancellation_reason": event.cancellation_reason,
        "allow_guests": event.allow_guests,
        "guest_limit": event.guest_limit,
        "ticket_price": float(event.ticket_price),
        "free_spots": event.free_spots,
        "free_spots_remaining": free_spots_remaining(event),
        "max_capacity": event.max_capacity,
        "rsvp_count": event.rsvp_count,
        "image_filename": event.image_filename,
        "is_attending": is_attending
    }

    user_event_credits = 0
    has_active_subscription = False
    soonest_expiry = None

    if current_user.is_authenticated:
        cleanup_user_expired_credits(current_user)
        user_event_credits = current_user.event_credits
        soonest_expiry = current_user.next_credit_expiry
        
        # Optional: Check subscription purely for UI purposes
        active_sub = Subscription.query.filter(
            Subscription.user_id == current_user.id,
            Subscription.status == 'active'
        ).first()
        if active_sub:
            has_active_subscription = True

    return render_template(
        'event_details.html', 
        event=event, 
        event_data=event_dict, 
        attendees=attendees,
        is_attending=is_attending,
        maps_link=maps_link,
        user_event_credits=user_event_credits,
        soonest_credit_expiry=soonest_expiry,
        has_active_subscription=has_active_subscription, 
        paypal_client_id=PAYPAL_CLIENT_ID,
        is_full=is_full,
        event_passed=event_passed,
        is_authenticated=current_user.is_authenticated,
        user_role=current_user.role if current_user.is_authenticated else 'user'
    )


# New endpoint to fetch user's RSVP for an event
@main.route("/api/user_rsvp/<int:event_id>", methods=['GET'])
@login_required 
def get_user_rsvp(event_id):
    user_id = current_user.id
    rsvp = EventAttendee.query.filter_by(event_id=event_id, user_id=user_id).first()
    
    if rsvp:
        user = User.query.get(user_id)
        return jsonify({
            'rsvp_id': rsvp.id,
            'guest_count': rsvp.guest_count,
            'first_name': user.first_name,
            'last_name': user.last_name
        }), 200
    else:
        return jsonify({'error': 'RSVP not found for this user and event'}), 404

@main.route('/hosting')
def hosting():
    return render_template('hosting.html')


@main.route('/faq')
def faq():
    return render_template('faq.html')


@main.route('/contactus')
def contactus():
    return render_template('contactus.html')


def get_access_token():
    auth = (PAYPAL_CLIENT_ID, PAYPAL_SECRET)
    headers = { "Accept": "application/json", "Accept-Language": "en_US" }
    data = { "grant_type": "client_credentials" }
    response = requests.post(f"{PAYPAL_BASE}/v1/oauth2/token", headers=headers, data=data, auth=auth, timeout=(10, 40))
    response.raise_for_status() # Raise an exception for HTTP errors
    return response.json()["access_token"]

def booking_quote(event, data):
    """Validate booking identity and derive quantities from the stored RSVP."""
    guest_checkout = bool(data.get('is_guest_checkout', False))
    if not guest_checkout and not current_user.is_authenticated:
        raise ValueError('Please sign in or use guest checkout.')
    quantity = data.get('quantity', 1)
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError('Quantity must be a positive whole number.')
    if event.status != 'active' or datetime.combine(event.date, event.start_time) <= event_now():
        raise ValueError('Registration is closed for this event.')
    rsvp = None
    if data.get('is_edit'):
        if guest_checkout:
            raise ValueError('Guest checkout cannot edit an RSVP.')
        rsvp = EventAttendee.query.filter_by(event_id=event.id, user_id=current_user.id).populate_existing().with_for_update().first()
        if not rsvp or (data.get('rsvp_id') and rsvp.id != int(data['rsvp_id'])):
            raise ValueError('Existing RSVP not found.')
    elif not guest_checkout and EventAttendee.query.filter_by(event_id=event.id, user_id=current_user.id).populate_existing().with_for_update().first():
        raise ValueError('You are already registered for this event.')
    _, error = validate_guest_count_for_event(event, quantity - 1,
                                              rsvp.guest_count if rsvp else None)
    if error:
        raise ValueError(error)
    occupied = sum(1 + (a.guest_count or 0) for a in EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all())
    additional = quantity - (1 + (rsvp.guest_count or 0) if rsvp else 0)
    if additional > 0 and occupied + additional > event.max_capacity:
        raise ValueError('This event does not have enough open spots.')
    email = normalize_email((data.get('guest_info') or {}).get('email') or data.get('email')) if guest_checkout else current_user.email
    return event_quote(event, quantity, None if guest_checkout else current_user, rsvp, email=email), rsvp


@main.route('/api/events/<int:event_id>/quote', methods=['POST'])
def quote_event_registration(event_id):
    event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first_or_404()
    try:
        data = request.get_json(silent=True) or {}
        pricing, rsvp = booking_quote(event, data)
        removed_paid = 0
        if rsvp:
            quantity = data.get('quantity', 1)
            remaining_paid = max(0, quantity - min(rsvp.free_spots or 0, quantity)
                                 - int(bool(rsvp.promotion_compensated)))
            removed_paid = max(0, rsvp.creditable_spots - remaining_paid)
        pricing['removed_paid_spots'] = removed_paid
        pricing['credits_to_return'] = removed_paid if hours_until_event(event, event_now()) >= RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS else 0
        return jsonify(**pricing, total=str(pricing['amount']))
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400


@main.route("/api/orders", methods=["POST"])
def create_order():
    data = request.get_json()
    event_id = data.get("event_id")
    requested_quantity = data.get("quantity", 1) # Total quantity (1 attendee + guests)
    is_edit = data.get("is_edit", False)
    rsvp_id = data.get("rsvp_id")
    initial_guest_count = data.get("initial_guest_count", 0)

    # Guest specific data
    is_guest_checkout = data.get("is_guest_checkout", False)

    event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first()
    if not event:
        return jsonify({"error": "Event not found"}), 404

    try:
        pricing, stored_rsvp = booking_quote(event, data)
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    if stored_rsvp:
        initial_guest_count = stored_rsvp.guest_count or 0
    
    if is_free_event(event):
        return jsonify({
            "error": (
                "Payment is not required for this FREE event."
            )
        }), 400

    user_id_for_paypal = "GUEST" if is_guest_checkout else current_user.id
    amount_to_charge = pricing['amount']
    if amount_to_charge <= 0:
        return jsonify({"error": "No payment is required for this RSVP."}), 400


    access_token = get_access_token()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "amount": {
                "currency_code": "USD",
                "value": f"{amount_to_charge:.2f}"
            },
            # Pass "GUEST" in the custom_id if it's a guest
            "custom_id": f"{event_id}-{user_id_for_paypal}-{is_edit}-{rsvp_id if rsvp_id else ''}-{initial_guest_count}"
        }]
    }
    response = requests.post(f"{PAYPAL_BASE}/v2/checkout/orders", headers=headers, json=body)
    response.raise_for_status()
    order = response.json()
    db.session.add(EventCheckout(id=order['id'], event_id=event.id,
        email=normalize_email(data.get('email')) if is_guest_checkout else normalize_email(current_user.email),
        user_id=None if is_guest_checkout else current_user.id,
        rsvp_id=stored_rsvp.id if stored_rsvp else None, quantity=requested_quantity,
        free_spots=pricing['free_spots'], credits=pricing['credits'], amount=amount_to_charge))
    db.session.commit()
    return jsonify(order)

@main.route("/api/orders/<order_id>/capture", methods=["POST"])
def capture_order(order_id):
    try:
        data = request.get_json()
        event_id = data.get("event_id")
        new_guest_count = int(data.get("guest_count", 0))
        is_edit = data.get("is_edit", False)
        rsvp_id = data.get("rsvp_id")
        is_guest_checkout = data.get("is_guest_checkout", False)
        guest_info = data.get("guest_info", {})
        if not is_guest_checkout and not current_user.is_authenticated:
            return jsonify(error='Please sign in.'), 401

        event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first()
        if not event:
            return jsonify({"error": "Event not found"}), 404
        
        checkout = EventCheckout.query.populate_existing().with_for_update().get(order_id)
        owner = None if is_guest_checkout else (current_user.id if current_user.is_authenticated else None)
        if (not checkout or checkout.completed or checkout.event_id != event.id
                or checkout.email != normalize_email(guest_info.get('email') if is_guest_checkout else current_user.email)
                or checkout.user_id != owner or checkout.quantity != 1 + new_guest_count
                or bool(checkout.rsvp_id) != bool(is_edit)):
            return jsonify(error='Invalid or already processed checkout. Please refresh.'), 409
        pricing, stored_rsvp = booking_quote(event, dict(data, quantity=1 + new_guest_count))
        if (checkout.rsvp_id != (stored_rsvp.id if stored_rsvp else None)
                or checkout.free_spots != pricing['free_spots']
                or checkout.credits != pricing['credits'] or checkout.amount != pricing['amount']):
            return jsonify(error='Available free spots or pricing changed. Refresh before paying. You have not been charged.'), 409
        if pricing['credits'] and not current_user.spend_credits(1, event.title, commit=False):
            db.session.rollback()
            return jsonify(error='Your credit balance changed. Please refresh.'), 409

        # --- 1. PRE-VALIDATION (Check BEFORE charging money) ---
        if is_guest_checkout:
            if not guest_info.get('signature_data'):
                return jsonify({"error": "Signature required"}), 400
        elif is_edit:
            # If editing, ensure the RSVP actually exists before proceeding
            existing_rsvp_check = EventAttendee.query.get(rsvp_id)
            if not existing_rsvp_check:
                return jsonify({"error": "Existing RSVP not found"}), 404
        
        # 1. Calculate how many spots are currently taken in the DB
        current_attendees = EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all()
        current_rsvp_count = sum(1 + (a.guest_count or 0) for a in current_attendees)

        spots_needed = 0
        
        if is_edit:
            # If editing an existing RSVP, we only care if they are INCREASING the count
            existing_rsvp = EventAttendee.query.get(rsvp_id)
            if existing_rsvp:
                # Calculate the difference (New Guest Count - Old Guest Count)
                # The primary user spot cancels out, so we just check guests
                additional_spots = new_guest_count - (existing_rsvp.guest_count or 0)
                spots_needed = max(0, additional_spots)
        else:
            # New Booking: 1 spot for the user + N spots for guests
            spots_needed = 1 + new_guest_count

        # 2. Check if there is enough room
        if spots_needed > 0 and (current_rsvp_count + spots_needed > event.max_capacity):
            return jsonify({
                "error": "We're sorry, but the event filled up while you were processing payment. You have not been charged."
            }), 400

        # --- PayPal Capture ---
        access_token = get_access_token()
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}
        capture_response = requests.post(f"{PAYPAL_BASE}/v2/checkout/orders/{order_id}/capture", headers=headers)
        capture_response.raise_for_status()
        order_data = capture_response.json()

        if order_data.get("status") != "COMPLETED":
            return jsonify({"error": f"PayPal transaction not completed: {order_data.get('status')}"}), 400
        
        # --- Payment Audit: Event Purchase ---
        purchase_unit = (order_data.get("purchase_units") or [{}])[0]
        payments = purchase_unit.get("payments") or {}
        captures = payments.get("captures") or [{}]
        capture = captures[0]

        capture_id = capture.get("id")
        amount_info = capture.get("amount") or purchase_unit.get("amount") or {}

        try:
            amount_value = float(amount_info.get("value") or 0)
        except (TypeError, ValueError):
            amount_value = 0.0

        currency_code = amount_info.get("currency_code") or "USD"

        paypal_payer_email = (
            order_data.get("payer", {}).get("email_address")
            or order_data.get("payment_source", {}).get("paypal", {}).get("email_address")
        )

        platform_email = (
            current_user.email
            if current_user.is_authenticated and not is_guest_checkout
            else guest_info.get("email")
        )

        audit_user_id = (
            current_user.id
            if current_user.is_authenticated and not is_guest_checkout
            else None
        )

        if capture_id:
            existing_payment = PaymentTransaction.query.filter_by(
                paypal_capture_id=capture_id
            ).first()

            if existing_payment:
                return jsonify({
                    "error": "This PayPal payment has already been processed. Please refresh the page."
                }), 409

        event_payment = PaymentTransaction(
            user_id=audit_user_id,
            event_id=event.id,
            paypal_order_id=order_id,
            paypal_capture_id=capture_id,
            platform_email=platform_email,
            paypal_payer_email=paypal_payer_email,
            custom_id=purchase_unit.get("custom_id"),
            amount=amount_value,
            currency=currency_code,
            transaction_type='event_purchase',
            status='completed',
            description=f'Event purchase for {event.title}'
        )

        db.session.add(event_payment)
        checkout.completed = True

        # --- RSVP Creation ---
        if is_guest_checkout:
            # === PATH A: GUEST ===
            signature_data = guest_info.get('signature_data')

            # Generate Waiver
            header, encoded = signature_data.split(",", 1)
            signature_image_data = base64.b64decode(encoded)
            waiver_filename = f"guest_waiver_{uuid.uuid4()}.pdf"
            waiver_path = os.path.join(current_app.root_path, 'static', 'waivers', waiver_filename)
            
            waiver_data_dict = {
                "name": f"{guest_info['first_name']} {guest_info['last_name']}",
                "address": guest_info['address'],
                "dob": guest_info['dob'],
                "emergency_name": guest_info['emergency_contact_name'],
                "emergency_phone": guest_info['emergency_contact_phone']
            }
            generate_detailed_waiver(waiver_data_dict, signature_image_data, waiver_path)

            # Create Guest Record
            attendee = EventAttendee(
                event_id=event_id,
                user_id=None, # Explicitly None for guests
                guest_count=new_guest_count,
                first_name=guest_info['first_name'],
                last_name=guest_info['last_name'],
                email=guest_info['email'],
                waiver_pdf=waiver_filename
            )
            db.session.add(attendee)
            send_guest_confirmation_email(guest_info, event, waiver_path)
            success_message = f"You're confirmed for {event.title}!An email with your waiver has been sent."

        else:
            user = current_user
            
            if is_edit:
                attendee = EventAttendee.query.get(rsvp_id)
                attendee.guest_count = new_guest_count
                attendee.free_spots += pricing['free_spots']
                success_message = f'Your RSVP for {event.title} has been updated!'
            else:
                # Initial RSVP for User
                attendee = EventAttendee(event_id=event_id, user_id=user.id, guest_count=new_guest_count)
                db.session.add(attendee)

                success_message = f"You're confirmed for {event.title}! See you there!"
                send_rsvp_confirmation_email(user, event, new_guest_count)

        if not is_edit:
            attendee.free_spots = pricing['free_spots']
            if pricing['free_spots']:
                record_free_claim(event, guest_info.get('email') if is_guest_checkout else current_user.email,
                                  None if is_guest_checkout else current_user.id)
        db.session.flush()
        all_attendees_for_event = EventAttendee.query.filter_by(event_id=event_id).all()
        event.rsvp_count = sum(1 + (a.guest_count or 0) for a in all_attendees_for_event)

        db.session.commit()
        order_data['success_message'] = success_message

    except (ValueError, TypeError) as error:
        db.session.rollback()
        return jsonify(error=str(error)), 400
    except Exception as e:
        traceback.print_exc()
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500

    return jsonify(order_data)

@main.route('/subscriptions')
def subscriptions():
    from .subscription_checkout import member_snapshot
    from .subscription_credits import credit_day
    today = credit_day()
    subscriptions_data = []
    total_monthly_credits = 0
    has_tier_1 = False
    has_tier_2 = False

    if current_user.is_authenticated:
        # Fetch all of the user's subscriptions
        # After: Sorts 'active' before 'canceled' (alphabetically Z-A), then by tier
        user_subscriptions = Subscription.query.filter_by(user_id=current_user.id).all()
        user_subscriptions.sort(key=lambda sub: (0, sub.tier) if sub.status == 'active' else (1, sub.tier))

        #Filter the list: Only keep active subscriptions or canceled ones that expire today or later.
        filtered_subscriptions = []
        for sub in user_subscriptions:
            # ALWAYS keep active subscriptions
            if sub.status in ('active', 'pending', 'suspended'):
                filtered_subscriptions.append(sub)
            # ONLY keep canceled subscriptions if their expiry_date is in the future or today
            elif sub.status in ('canceled', 'expired') and sub.expiry_date and sub.expiry_date >= today:
                filtered_subscriptions.append(sub)

        # Determine which active tiers the user has
        active_subs = [sub for sub in filtered_subscriptions if sub.status in ('active', 'suspended')]
        has_tier_1 = any(sub.tier == 1 for sub in active_subs)
        has_tier_2 = any(sub.tier == 2 for sub in active_subs)

        #Populate the data for the template using the FILTERED list
        for sub in filtered_subscriptions:
            subscriptions_data.append({
                'id': sub.id,
                'paypal_subscription_id': sub.paypal_subscription_id,
                'tier': sub.tier,
                'status': sub.status,
                'credits': sub.credits_per_month,
                # The dates passed to the template now only exist for relevant items
                'paid_through': sub.expiry_date if PaymentTransaction.query.filter_by(subscription_id=sub.id, transaction_type='subscription_payment', status='completed').first() else None,
                'expires_on': sub.expiry_date if sub.status in ('canceled', 'expired') else None,
            })
        
        total_monthly_credits = current_user.event_credits

    return render_template(
        'subscriptions.html',
        is_authenticated=current_user.is_authenticated,
        paypal_client_id=PAYPAL_CLIENT_ID,
        today=today,
        subscriptions=subscriptions_data,
        total_monthly_credits=total_monthly_credits,
        has_tier_1=has_tier_1,
        has_tier_2=has_tier_2,
        subscription_state=member_snapshot(current_user.id) if current_user.is_authenticated else {}
    )


@main.route("/api/paypal/confirm-subscription", methods=["POST"])
@login_required
def confirm_subscription():
    from .credit_routes import confirm
    return confirm()


@main.route('/api/paypal/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    from .credit_routes import create
    return create()

# In views.py, update the cancel_subscription route

@main.route('/cancel_subscription/<string:subscription_id>', methods=['POST'])
@login_required
def cancel_subscription(subscription_id):
    from .credit_routes import cancel
    response = cancel(subscription_id)
    if response[1] == 200:
        flash('Subscription canceled successfully. Your credits remain until the expiry date.', 'success')
    return response


@main.route('/api/user/subscription/refresh', methods=['POST'])
@login_required
def refresh_subscription():
    from .credit_routes import refresh
    return refresh()


@main.route('/api/user/subscription_status', methods=['GET'])
@login_required
def get_subscription_status():
    from .credit_routes import status
    return status()


#upgrade subscription:
@main.route('/api/subscription/upgrade', methods=['POST'])
@login_required
def upgrade_subscription():
    from .credit_routes import create
    return create(upgrade=True)


@main.route('/api/paypal/webhook', methods=['POST'])
def paypal_webhook():
    from .credit_routes import webhook
    return webhook()


@main.route("/api/rsvp/credit", methods=['POST'])
@login_required
def rsvp_with_credit():
    data = request.get_json()
    event_id = data.get('event_id')
    user = current_user

    try:
        event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first()
        
        if not event:
            return jsonify({'error': 'Event not found.'}), 404

        if event.status != 'active' or datetime.combine(event.date, event.start_time) <= event_now():
            return jsonify(error='Registration is closed for this event.'), 400
        
        if is_free_event(event) or event_quote(event, 1, current_user)['free_spots']:
            db.session.rollback()

            return jsonify({
                "error": (
                    "This is a FREE event. "
                    "No event credit should be used."
                )
            }), 400
        
        if EventAttendee.query.filter_by(event_id=event.id, user_id=user.id).populate_existing().with_for_update().first():
            db.session.rollback()
            return jsonify({'success': True, 'message': 'You are already registered.'}), 200
        current_attendees = EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all()
        current_rsvp_count = sum(1 + (a.guest_count or 0) for a in current_attendees)

        if current_rsvp_count + 1 > event.max_capacity:
            # Rollback acts as the "Unlock" here
            db.session.rollback()
            return jsonify({'error': 'Sorry, this event is now full.'}), 400
        if user.event_credits < 1:
             db.session.rollback()
             return jsonify({'error': 'Insufficient credits.'}), 400
        if not user.spend_credits(1, event.title, commit=False):
            db.session.rollback()
            return jsonify({'error': 'Insufficient credits.'}), 400

        new_attendee = EventAttendee(event_id=event.id, user_id=user.id, guest_count=0)
        db.session.add(new_attendee)
        event.rsvp_count = current_rsvp_count + 1
        db.session.commit()
        
        try:
            send_rsvp_confirmation_email(user, event, 0)
        except Exception:
            current_app.logger.exception('RSVP saved but confirmation email failed')
        
        remaining = user.event_credits 
        message = f"Successfully RSVP'd! Used 1 credit. {remaining} remaining."
        session['flashMessage'] = message
        session['flashEventId'] = event_id
        return jsonify({'success': True, 'message': message}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error in RSVP: {e}") # specific error logging
        return jsonify({'error': 'An error occurred processing your request.'}), 500

def validate_guest_count_for_event(event, guest_count, existing_guest_count=None):
    if isinstance(guest_count, (bool, float)):
        return None, 'Guest count must be a whole number.'
    try:
        guest_count = int(guest_count)
    except (TypeError, ValueError):
        return None, "Guest count must be a whole number."

    if guest_count < 0:
        return None, "Guest count cannot be negative."

    # A stricter admin policy must not prevent removing existing guests.
    if existing_guest_count is not None and guest_count <= existing_guest_count:
        return guest_count, None
    if guest_count > 0 and not event.allow_guests:
        return None, "Guests are not allowed for this event."

    if (
        event.allow_guests
        and event.guest_limit is not None
        and guest_count > event.guest_limit
    ):
        return None, (
            f"You may bring up to {event.guest_limit} guest(s)."
        )

    return guest_count, None


@main.route("/api/rsvp/free", methods=["POST"])
@login_required
def rsvp_free_event():
    data = request.get_json(silent=True) or {}
    event_id = data.get("event_id")

    if not event_id:
        return jsonify({"error": "Event ID is required."}), 400

    try:
        # Lock the event row while capacity is checked.
        event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first()

        if not event:
            return jsonify({"error": "Event not found."}), 404

        if event.status != "active":
            db.session.rollback()
            return jsonify({
                "error": "This event is not available for registration."
            }), 400

        event_start = datetime.combine(
            event.date,
            event.start_time
        )

        if event_start <= event_now():
            db.session.rollback()
            return jsonify({
                "error": "Registration is closed for this event."
            }), 400

        guest_count, guest_error = validate_guest_count_for_event(
            event,
            data.get("guest_count", 0)
        )

        if guest_error:
            db.session.rollback()
            return jsonify({"error": guest_error}), 400

        existing_rsvp = EventAttendee.query.filter_by(
            event_id=event.id,
            user_id=current_user.id
        ).populate_existing().with_for_update().first()

        if existing_rsvp:
            db.session.rollback()
            return jsonify({
                "error": "You are already registered for this event."
            }), 409

        attendees = EventAttendee.query.filter_by(
            event_id=event.id
        ).populate_existing().with_for_update().all()

        current_rsvp_count = sum(
            1 + (attendee.guest_count or 0)
            for attendee in attendees
        )

        spots_needed = 1 + guest_count

        if current_rsvp_count + spots_needed > event.max_capacity:
            db.session.rollback()
            return jsonify({
                "error": (
                    "Sorry, this event does not have enough open spots."
                )
            }), 400

        pricing = event_quote(event, spots_needed, current_user)
        if pricing['amount'] > 0 or pricing['credits'] or pricing['free_spots'] != 1:
            db.session.rollback()
            return jsonify(error='Not enough free spots remain. Please refresh to review pricing.'), 409
        attendee = EventAttendee(
            event_id=event.id,
            user_id=current_user.id,
            guest_count=guest_count,
            free_spots=pricing['free_spots']
        )

        db.session.add(attendee)

        # Send the new attendee INSERT to the database before recounting.
        record_free_claim(event, current_user.email, current_user.id)
        db.session.flush()

        all_attendees = EventAttendee.query.filter_by(
            event_id=event.id
        ).populate_existing().with_for_update().all()

        event.rsvp_count = sum(
            1 + (registered.guest_count or 0)
            for registered in all_attendees
        )

        db.session.commit()

        send_rsvp_confirmation_email(
            current_user,
            event,
            guest_count
        )

        return jsonify({
            "success": True,
            "message": (
                f"You're confirmed for {event.title}! "
                f"{FREE_EVENT_COURTESY_MESSAGE}"
            )
        }), 200

    except Exception as error:
        db.session.rollback()
        traceback.print_exc()

        return jsonify({
            "error": f"An error occurred: {str(error)}"
        }), 500
    
@main.route('/api/rsvp/free/guest', methods=['POST'])
def rsvp_free_guest():
    data = request.get_json(silent=True) or {}

    event_id = data.get('event_id')
    guest_info = data.get('guest_info') or {}

    if not event_id:
        return jsonify({
            'error': 'Event ID is required.'
        }), 400

    required_fields = [
        'first_name',
        'last_name',
        'email',
        'address',
        'dob',
        'emergency_contact_name',
        'emergency_contact_phone',
        'signature_data'
    ]

    missing_fields = [
        field
        for field in required_fields
        if not str(guest_info.get(field) or '').strip()
    ]

    if missing_fields:
        return jsonify({
            'error': 'Please complete all required fields.'
        }), 400

    try:
        dob = datetime.strptime(
            guest_info['dob'],
            '%Y-%m-%d'
        ).date()
    except (TypeError, ValueError):
        return jsonify({
            'error': 'Invalid date of birth.'
        }), 400

    today = date.today()

    age = (
        today.year
        - dob.year
        - (
            (today.month, today.day)
            < (dob.month, dob.day)
        )
    )

    if age < 18:
        return jsonify({
            'error': (
                'You must be 18 years or older '
                'to register.'
            )
        }), 400

    try:
        event = (
            Event.query
            .with_for_update()
            .get(event_id)
        )

        if not event:
            return jsonify({
                'error': 'Event not found.'
            }), 404

        if event.status != 'active':
            db.session.rollback()

            return jsonify({
                'error': (
                    'This event is not available '
                    'for registration.'
                )
            }), 400

        event_start = datetime.combine(
            event.date,
            event.start_time
        )

        if event_start <= event_now():
            db.session.rollback()

            return jsonify({
                'error': (
                    'Registration is closed '
                    'for this event.'
                )
            }), 400

        guest_count, guest_error = (
            validate_guest_count_for_event(
                event,
                data.get('guest_count', 0)
            )
        )

        if guest_error:
            db.session.rollback()

            return jsonify({
                'error': guest_error
            }), 400

        email = guest_info['email'].strip()

        # Prevent an accidental duplicate submission.
        existing_guest_rsvp = EventAttendee.query.filter(
            EventAttendee.event_id == event.id,
            EventAttendee.user_id.is_(None),
            db.func.lower(EventAttendee.email)
                == email.lower()
        ).populate_existing().with_for_update().first()

        if existing_guest_rsvp:
            db.session.rollback()

            return jsonify({
                'error': (
                    'This email is already registered '
                    'for this event.'
                )
            }), 409

        attendees = EventAttendee.query.filter_by(
            event_id=event.id
        ).populate_existing().with_for_update().all()

        current_rsvp_count = sum(
            1 + (attendee.guest_count or 0)
            for attendee in attendees
        )

        spots_needed = 1 + guest_count

        if (
            current_rsvp_count + spots_needed
            > event.max_capacity
        ):
            db.session.rollback()

            return jsonify({
                'error': (
                    'This event does not have '
                    'enough open spots.'
                )
            }), 400

        pricing = event_quote(event, spots_needed, email=guest_info['email'])
        if pricing['amount'] > 0 or pricing['free_spots'] != 1:
            db.session.rollback()
            return jsonify(error='Not enough free spots remain. Please refresh to review pricing.'), 409

        signature_data = guest_info['signature_data']

        if ',' not in signature_data:
            db.session.rollback()

            return jsonify({
                'error': 'Invalid signature data.'
            }), 400

        try:
            _, encoded_signature = (
                signature_data.split(',', 1)
            )

            signature_image_data = base64.b64decode(
                encoded_signature
            )

        except Exception:
            db.session.rollback()

            return jsonify({
                'error': 'Invalid signature data.'
            }), 400

        waiver_filename = (
            f'guest_waiver_{uuid.uuid4()}.pdf'
        )

        waiver_directory = os.path.join(
            current_app.root_path,
            'static',
            'waivers'
        )

        os.makedirs(
            waiver_directory,
            exist_ok=True
        )

        waiver_path = os.path.join(
            waiver_directory,
            waiver_filename
        )

        waiver_data = {
            'name': (
                f"{guest_info['first_name'].strip()} "
                f"{guest_info['last_name'].strip()}"
            ),
            'address': guest_info['address'].strip(),
            'dob': guest_info['dob'],
            'emergency_name': (
                guest_info[
                    'emergency_contact_name'
                ].strip()
            ),
            'emergency_phone': (
                guest_info[
                    'emergency_contact_phone'
                ].strip()
            )
        }

        generate_detailed_waiver(
            waiver_data,
            signature_image_data,
            waiver_path
        )

        attendee = EventAttendee(
            event_id=event.id,
            user_id=None,
            guest_count=guest_count,
            free_spots=pricing['free_spots'],
            first_name=guest_info['first_name'].strip(),
            last_name=guest_info['last_name'].strip(),
            email=email,
            waiver_pdf=waiver_filename
        )

        db.session.add(attendee)

        # Send the new attendee INSERT to the database before recounting.
        record_free_claim(event, guest_info['email'])
        db.session.flush()

        all_attendees = EventAttendee.query.filter_by(
            event_id=event.id
        ).populate_existing().with_for_update().all()

        event.rsvp_count = sum(
            1 + (registered.guest_count or 0)
            for registered in all_attendees
        )

        db.session.commit()

        send_guest_confirmation_email(
            guest_info,
            event,
            waiver_path
        )

        return jsonify({
            'success': True,
            'message': (
                f"You're confirmed for {event.title}! "
                f"{FREE_EVENT_COURTESY_MESSAGE}"
            )
        }), 200

    except Exception as error:
        db.session.rollback()
        traceback.print_exc()

        return jsonify({
            'error': (
                f'An error occurred: {str(error)}'
            )
        }), 500


def rsvp_cancellation_policy(event, rsvp):
    hours = hours_until_event(event, event_now())
    can_cancel = event.status == 'active' and hours > 0
    eligible = can_cancel and hours >= RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS and rsvp.creditable_spots > 0
    credits = rsvp.creditable_spots if eligible else 0
    if not can_cancel:
        message = 'This event is canceled or has already started. RSVP changes are closed.'
    elif rsvp.creditable_spots == 0:
        message = 'This removes everyone in your RSVP. No additional credit is due for free or already compensated spots.'
    elif eligible:
        message = f'This removes everyone in your RSVP. You will receive {credits} event credit(s) for paid spots not already compensated.'
    else:
        message = f'This removes everyone in your RSVP. No event credits will be issued because cancellation is less than {RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS} hours before the event.'
    return dict(can_cancel=can_cancel, is_free_event=rsvp.creditable_spots == 0,
                credit_eligible=eligible, credits_to_issue=credits,
                hours_until_event=round(hours, 1), confirmation_message=message)


@main.route('/api/rsvp/delete/<int:event_id>', methods=['POST'])
@login_required
def delete_rsvp(event_id):
    event = Event.query.filter_by(id=event_id).populate_existing().with_for_update().first_or_404()
    rsvp = EventAttendee.query.filter_by(event_id=event.id, user_id=current_user.id).populate_existing().with_for_update().first()
    if not rsvp:
        return jsonify(error='You are not registered for this event.'), 404
    policy = rsvp_cancellation_policy(event, rsvp)
    if not policy['can_cancel']:
        return jsonify(error=policy['confirmation_message']), 400
    try:
        credits = policy['credits_to_issue']
        if credits:
            issue_event_credit(current_user.id, event, credits, 'rsvp_cancellation',
                               f'Credit for canceling RSVP: {event.title}')
        db.session.delete(rsvp)
        db.session.flush()
        attendees = EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all()
        event.rsvp_count = sum(1 + (a.guest_count or 0) for a in attendees)
        db.session.commit()
        message = f'Your RSVP has been canceled. {credits} event credit(s) issued, valid for 30 days.' if credits else (
            'Your RSVP has been canceled. No additional credits were issued for free or already compensated spots.'
            if policy['is_free_event'] else
            f'Your RSVP has been canceled. No credits were issued because cancellation is less than {RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS} hours before the event.')
        return jsonify(success=True, message=message, credit_eligible=policy['credit_eligible'],
                       credits_issued=credits, new_credit_balance=current_user.event_credits), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Unable to cancel RSVP')
        return jsonify(error='Unable to cancel RSVP.'), 500


@main.route('/api/rsvp/cancel-policy/<int:event_id>', methods=['GET'])
@login_required
def get_rsvp_cancel_policy(event_id):
    event = Event.query.get_or_404(event_id)
    rsvp = EventAttendee.query.filter_by(event_id=event.id, user_id=current_user.id).first()
    if not rsvp:
        return jsonify(error='You are not registered for this event.'), 404
    policy = rsvp_cancellation_policy(event, rsvp)
    if not policy['can_cancel']:
        return jsonify(error=policy['confirmation_message'], **policy), 400
    return jsonify(policy), 200


@main.route('/api/rsvp/update', methods=['POST'])
@login_required
def update_rsvp():
    data = request.get_json(silent=True) or {}
    if data.get('event_id') is None or data.get('new_guest_count') is None:
        return jsonify(error='Event ID and new guest count are required.'), 400
    try:
        event = Event.query.filter_by(id=data['event_id']).populate_existing().with_for_update().first()
        if not event:
            return jsonify(error='Event not found.'), 404
        hours = hours_until_event(event, event_now())
        if event.status != 'active' or hours <= 0:
            return jsonify(error='This event is closed for RSVP changes.'), 400
        rsvp = EventAttendee.query.filter_by(event_id=event.id, user_id=current_user.id).populate_existing().with_for_update().first()
        if not rsvp:
            return jsonify(error='No existing RSVP found to update.'), 404
        count, error = validate_guest_count_for_event(event, data['new_guest_count'], rsvp.guest_count or 0)
        if error:
            return jsonify(error=error), 400
        pricing, _ = booking_quote(event, dict(quantity=1 + count, is_edit=True, rsvp_id=rsvp.id))
        if pricing['amount'] > 0:
            return jsonify(error='Payment is required to add guests to this event.'), 400
        before = rsvp.creditable_spots
        old_count = rsvp.guest_count or 0
        rsvp.free_spots = min(rsvp.free_spots or 0, 1 + count)
        rsvp.guest_count = count
        removed_paid = max(0, before - rsvp.creditable_spots)
        credits = removed_paid if hours >= RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS else 0
        if credits:
            issue_event_credit(current_user.id, event, credits, 'rsvp_change',
                               f'Credit for removing paid guests: {event.title}')
        db.session.flush()
        attendees = EventAttendee.query.filter_by(event_id=event.id).populate_existing().with_for_update().all()
        event.rsvp_count = sum(1 + (a.guest_count or 0) for a in attendees)
        db.session.commit()
        message = 'Your RSVP has been updated.'
        if credits:
            message += f' {credits} event credit(s) issued for removed paid guests, valid for 30 days.'
        elif removed_paid:
            message += f' No credits issued because this change is less than {RSVP_CANCELLATION_CREDIT_CUTOFF_HOURS} hours before the event.'
        elif count < old_count:
            message += ' No credits issued for removed free spots.'
        return jsonify(success=True, message=message, credits_issued=credits,
                       new_credit_balance=current_user.event_credits), 200
    except (ValueError, TypeError) as error:
        db.session.rollback()
        return jsonify(error=str(error)), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Unable to update RSVP')
        return jsonify(error='Unable to update RSVP.'), 500


# Add this new route to your views.py file

@main.route('/events/add_recurring', methods=['POST'])
@login_required # Make sure to import login_required from flask_login
def add_recurring_events():
    if not current_user.is_authenticated or current_user.role != 'admin':
        return jsonify({'error': 'Unauthorized access'}), 403

    try:
        # 1. Parse all form data
        form = request.form
        start_date_str = form.get('recurring_start_date')
        end_date_str = form.get('recurring_end_date')
        weekdays = form.getlist('weekdays') # Gets a list of selected weekday values ('0', '1', etc.)

        # Convert strings to proper data types
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        selected_weekdays = [int(day) for day in weekdays]
        
        start_time_obj = datetime.strptime(form.get('start_time'), "%H:%M").time()
        end_time_obj = datetime.strptime(form.get('end_time'), "%H:%M").time()

        # 2. Validate inputs
        if not selected_weekdays:
            return jsonify({'error': 'Please select at least one day of the week.'}), 400
        if start_date > end_date:
            return jsonify({'error': 'Start date cannot be after the end date.'}), 400
        if start_time_obj >= end_time_obj:
            return jsonify({'error': 'Start time must be before end time.'}), 400

        # 3. Calculate all the dates that match the criteria
        dates_to_create = []
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() in selected_weekdays:
                dates_to_create.append(current_date)
            current_date += timedelta(days=1)

        if not dates_to_create:
            return jsonify({'error': 'No matching dates found in the selected range.'}), 400
            
        # --- Handle image upload (same logic as single event) ---
        image_filename = None
        image_file = request.files.get('eventImage')
        if image_file and image_file.filename != '':
            filename = secure_filename(image_file.filename)
            unique_filename = str(uuid.uuid4()) + "_" + filename
            save_path = os.path.join(current_app.root_path, 'static/images', unique_filename)
            image_file.save(save_path)
            image_filename = unique_filename
        elif form.get('existingImage'):
            image_filename = form.get('existingImage')


        allow_guests, guest_limit = get_event_guest_policy(form)
        # 4. Loop through the calculated dates and create events
        for event_date in dates_to_create:
            new_event = Event(
                title=form.get('title'),
                description=form.get('description'),
                start_time=start_time_obj,
                end_time=end_time_obj,
                location=form.get('location'),
                full_address=form.get('full_address'),
                allow_guests=allow_guests,
                guest_limit=guest_limit,
                ticket_price=get_event_ticket_price(form),
                free_spots=parse_free_spots(form, int(form.get('max_capacity') or 28)),
                max_capacity=int(form.get('max_capacity') or 28),
                date=event_date,
                image_filename=image_filename,
                status='active'
            )
            db.session.add(new_event)
        
        # 5. Commit all new events to the database at once
        db.session.commit()

        flash(f'{len(dates_to_create)} recurring events created successfully!', 'success')
        return jsonify({'success': True, 'message': f'{len(dates_to_create)} events were successfully created!'})

    except Exception as e:
        db.session.rollback()
        traceback.print_exc() # Log the full error to your console
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500
    


@main.route('/execute-guest-payment', methods=['POST'])
def execute_guest_payment():
    data = request.json
    guest_info = data.get('guest_info', {})
    event_id = data.get('event_id')
    guest_dob_str = guest_info.get('dob') # 'YYYY-MM-DD'
    if not guest_dob_str:
        return jsonify({'error': 'Date of Birth is required.'}), 400

    try:
        dob = datetime.strptime(guest_dob_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Invalid Date of Birth format.'}), 400

    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    if age < 18:
        return jsonify({'error': 'You must be 18 years or older to register for an event.'}), 400
    

    guest_first_name = guest_info.get('first_name')
    guest_last_name = guest_info.get('last_name')

    if not guest_first_name or not guest_last_name:
        # This shouldn't happen if frontend validation works, but is a safe guard
        return jsonify({'error': 'Guest first and last name are required.'}), 400

    event = Event.query.get(event_id)
    if not event:
        return jsonify({'error': 'Event not found.'}), 404
    


@main.route('/merch')
def merch():
    expire_merch_reservations()
    return render_template(
        'merch.html',
        products=serialize_merch_catalog(),
        is_merch_admin=merch_admin(),
        admin_products=(MerchProduct.query.order_by(MerchProduct.name).all()
                        if merch_admin() else [])
    )


@main.route('/merch/cart')
def merch_cart():
    return render_template(
        'merch_cart.html',
        shipping_rate=f'{MERCH_SHIPPING_RATE:.2f}',
        paypal_client_id=PAYPAL_CLIENT_ID
    )


@main.route('/api/merch/quote', methods=['POST'])
def merch_quote():
    data = request.get_json(silent=True) or {}
    expire_merch_reservations()
    try:
        items, subtotal = validate_merch_cart(data.get('items'))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({
        'items': [{
            'productId': item['product_id'],
            'variantId': item['variant_id'],
            'name': item['name'],
            'size': item['size'],
            'color': item['color'],
            'quantity': item['quantity'],
            'unitPrice': f"{item['unit_price']:.2f}",
            'available': db.session.get(MerchVariant,
                                        item['variant_id']).available,
            'image': url_for('static', filename='images/' + (
                db.session.get(MerchVariant, item['variant_id']).image
                or db.session.get(MerchProduct, item['product_id']).image)),
        } for item in items],
        'subtotal': f'{subtotal:.2f}',
        'shipping': f'{MERCH_SHIPPING_RATE:.2f}',
        'total': f'{subtotal + MERCH_SHIPPING_RATE:.2f}',
    })


def save_merch_image(file, current=None):
    if not file or not file.filename:
        if current:
            return current
        raise ValueError('Add a product image.')

    content = file.read(8 * 1024 * 1024 + 1)
    if len(content) > 8 * 1024 * 1024:
        raise ValueError('Each product image must be 8 MB or smaller.')
    if content.startswith(b'\xff\xd8\xff'):
        extension = '.jpg'
    elif content.startswith(b'\x89PNG\r\n\x1a\n'):
        extension = '.png'
    elif content[:4] == b'RIFF' and content[8:12] == b'WEBP':
        extension = '.webp'
    else:
        raise ValueError('Use a JPEG, PNG, or WebP product image.')

    folder = current_app.config.get('MERCH_UPLOAD_FOLDER') or os.path.join(
        current_app.root_path, 'static', 'images', 'merch', 'uploads')
    os.makedirs(folder, exist_ok=True)
    filename = f'{uuid.uuid4().hex}{extension}'
    with open(os.path.join(folder, filename), 'wb') as output:
        output.write(content)
    return f'merch/uploads/{filename}'


def save_merch_product(product=None):
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name or len(name) > 150:
        raise ValueError('Enter a product name under 150 characters.')
    if len(description) > 3000:
        raise ValueError('Keep the description under 3,000 characters.')
    try:
        raw_price = Decimal(request.form.get('price', ''))
    except (InvalidOperation, ValueError):
        raise ValueError('Enter a valid price.')
    if (not raw_price.is_finite()
            or raw_price.as_tuple().exponent < -2):
        raise ValueError('Enter a price with at most two decimal places.')
    price = raw_price.quantize(Decimal('0.01'))
    if price <= 0 or price > Decimal('9999.99'):
        raise ValueError('Price must be between $0.01 and $9,999.99.')

    if product is None:
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:80]
        if not slug or db.session.get(MerchProduct, slug):
            raise ValueError('Use a distinct product name.')
        product = MerchProduct(id=slug)

    existing = []
    labels = set()
    current_variants = (MerchVariant.query.filter_by(product_id=product.id)
                        .populate_existing().with_for_update().all())
    for variant in current_variants:
        raw_stock = request.form.get(f'stock_{variant.id}', '')
        try:
            stock = int(raw_stock)
        except (TypeError, ValueError):
            raise ValueError('Enter a whole number for each stock quantity.')
        if stock < variant.stock_reserved or stock > 1000000:
            raise ValueError(
                f'Stock for {variant.color} / {variant.size} must be '
                f'at least its {variant.stock_reserved} reserved units.'
            )
        existing.append((variant, stock,
                         request.form.get(f'active_{variant.id}') == 'on',
                         request.files.get(f'image_{variant.id}')))
        labels.add((variant.size.casefold(), variant.color.casefold()))

    sizes = request.form.getlist('new_size[]')
    colors = request.form.getlist('new_color[]')
    stocks = request.form.getlist('new_stock[]')
    new_images = request.files.getlist('new_image[]')
    if not (len(sizes) == len(colors) == len(stocks)) or len(sizes) > 100:
        raise ValueError('Invalid variant rows.')
    new_images += [None] * (len(sizes) - len(new_images))
    new_variants = []
    for size, color, raw_stock, image_file in zip(
            sizes, colors, stocks, new_images):
        size, color = size.strip(), color.strip()
        if (not size and not color and not raw_stock.strip()
                and not (image_file and image_file.filename)):
            continue
        if not size or not color or len(size) > 20 or len(color) > 40:
            raise ValueError('Each variant needs a size and color.')
        try:
            stock = int(raw_stock)
        except (TypeError, ValueError):
            raise ValueError('Enter a whole number for each stock quantity.')
        if stock < 0 or stock > 1000000:
            raise ValueError('Stock must be between 0 and 1,000,000.')
        label = (size.casefold(), color.casefold())
        if label in labels:
            raise ValueError(f'{color} / {size} is already listed.')
        labels.add(label)
        new_variants.append((MerchVariant(
            size=size, color=color, stock_on_hand=stock, active=True),
            image_file))
    if not labels:
        raise ValueError('Add at least one size and color.')

    gallery_rows = []
    for gallery_image in product.gallery_images:
        if request.form.get(f'gallery_remove_{gallery_image.id}') == 'on':
            continue
        try:
            sort_order = int(request.form.get(
                f'gallery_order_{gallery_image.id}',
                str(gallery_image.sort_order)))
        except (TypeError, ValueError):
            raise ValueError('Enter a whole number for each photo order.')
        if sort_order < 1 or sort_order > 1000:
            raise ValueError('Photo order must be between 1 and 1000.')
        gallery_rows.append((sort_order, gallery_image.id, gallery_image))
    new_gallery_files = [file for file in request.files.getlist('gallery_images')
                         if file and file.filename]
    if len(gallery_rows) + len(new_gallery_files) > 12:
        raise ValueError('A listing can have up to 12 additional photos.')

    primary = save_merch_image(request.files.get('image'), product.image)
    detail_file = request.files.get('detail_image')
    detail = save_merch_image(detail_file, product.detail_image or primary) \
        if detail_file and detail_file.filename else (product.detail_image or primary)
    product.name = name
    product.description = description
    product.price = price
    product.image = primary
    product.detail_image = detail
    product.detail_position = product.detail_position or 'center'
    product.active = request.form.get('active') == 'on'
    for gallery_image in list(product.gallery_images):
        if request.form.get(f'gallery_remove_{gallery_image.id}') == 'on':
            db.session.delete(gallery_image)
    gallery_rows.sort(key=lambda row: (row[0], row[1]))
    for order, (_, _, gallery_image) in enumerate(gallery_rows, 1):
        gallery_image.sort_order = order
    for order, image_file in enumerate(new_gallery_files,
                                       len(gallery_rows) + 1):
        product.gallery_images.append(MerchProductImage(
            image=save_merch_image(image_file), sort_order=order))
    for variant, stock, active, image_file in existing:
        variant.stock_on_hand = stock
        variant.active = active
        if image_file and image_file.filename:
            variant.image = save_merch_image(image_file)
    for variant, image_file in new_variants:
        if image_file and image_file.filename:
            variant.image = save_merch_image(image_file)
        product.variants.append(variant)
    db.session.add(product)
    db.session.commit()
    return product


@main.route('/merch/admin/products/new', methods=['GET', 'POST'])
@login_required
def merch_product_new():
    if not merch_admin():
        abort(403)
    if request.method == 'POST':
        if not valid_merch_admin_token():
            abort(403)
        try:
            product = save_merch_product()
            flash('Product added. Set stock before accepting orders.', 'success')
            return redirect(url_for('main.merch_product_edit',
                                    product_id=product.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
    return render_template('merch_product_edit.html', product=None,
                           admin_token=merch_admin_token())


@main.route('/merch/admin/products/<product_id>/edit',
            methods=['GET', 'POST'])
@login_required
def merch_product_edit(product_id):
    if not merch_admin():
        abort(403)
    expire_merch_reservations()
    product = MerchProduct.query.get_or_404(product_id)
    if request.method == 'POST':
        if not valid_merch_admin_token():
            abort(403)
        try:
            save_merch_product(product)
            flash('Product updated.', 'success')
            return redirect(url_for('main.merch_product_edit',
                                    product_id=product.id))
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
    return render_template('merch_product_edit.html', product=product,
                           admin_token=merch_admin_token())


@main.route('/merch/admin/orders')
@login_required
def merch_admin_orders():
    if not merch_admin():
        abort(403)
    expire_merch_reservations()
    page = max(1, request.args.get('page', 1, type=int) or 1)
    pagination = (MerchOrder.query.order_by(MerchOrder.created_at.desc())
                  .paginate(page=page, per_page=30, error_out=False))
    return render_template('merch_admin_orders.html', orders=pagination.items,
                           pagination=pagination,
                           admin_token=merch_admin_token(),
                           tracking_url=merch_tracking_url)


@main.route('/merch/admin/orders/<int:order_id>/tracking', methods=['POST'])
@login_required
def merch_admin_tracking(order_id):
    if not merch_admin() or not valid_merch_admin_token():
        abort(403)
    order = MerchOrder.query.get_or_404(order_id)
    if order.status != 'completed' or order.fulfillment_method != 'shipping':
        flash('Only paid shipping orders can be marked shipped.', 'danger')
        return redirect(url_for('main.merch_admin_orders'))
    carrier = request.form.get('carrier', '').strip()
    number = request.form.get('tracking_number', '').strip()
    if (carrier not in {'USPS', 'UPS', 'FedEx', 'Other'}
            or not re.fullmatch(r'[A-Za-z0-9 -]{4,120}', number)):
        flash('Choose a carrier and enter a valid tracking number.', 'danger')
        return redirect(url_for('main.merch_admin_orders'))
    changed = (carrier != order.tracking_carrier
               or number != order.tracking_number)
    order.tracking_carrier = carrier
    order.tracking_number = number
    order.shipped_at = order.shipped_at or datetime.utcnow()
    if changed:
        order.tracking_notified_at = None
    db.session.commit()

    if request.form.get('notify_customer') == 'on':
        tracking_link = merch_tracking_url(order)
        body = (f'Your Pick Up Volleyball order {order.order_number} has shipped.\n\n'
                f'Carrier: {carrier}\nTracking number: {number}\n')
        if tracking_link:
            body += f'Track your package: {tracking_link}\n'
        try:
            mail.send(Message(
                subject=f'Your order {order.order_number} has shipped',
                recipients=[order.customer_email], body=body
            ))
            order.tracking_notified_at = datetime.utcnow()
            db.session.commit()
            flash('Tracking saved and customer emailed.', 'success')
        except Exception:
            current_app.logger.exception('Merch tracking email failed')
            flash('Tracking saved, but the email could not be sent.', 'warning')
    else:
        flash('Tracking saved.', 'success')
    return redirect(url_for('main.merch_admin_orders'))


@main.route('/merch/admin/orders/<int:order_id>/retry-capture',
            methods=['POST'])
@login_required
def merch_admin_retry_capture(order_id):
    if not merch_admin() or not valid_merch_admin_token():
        abort(403)
    order = MerchOrder.query.get_or_404(order_id)
    if order.status != 'capturing' or not order.paypal_order_id:
        flash('This order does not need payment confirmation.', 'warning')
        return redirect(url_for('main.merch_admin_orders'))
    result = make_response(capture_merch_order(order.paypal_order_id))
    if result.status_code == 200:
        flash('PayPal payment confirmed and stock finalized.', 'success')
    else:
        flash('PayPal could not confirm this order. Review it in PayPal '
              'before taking any further action.', 'warning')
    return redirect(url_for('main.merch_admin_orders'))

@main.route('/api/merch/orders', methods=['POST'])
def create_merch_order():
    data = request.get_json(silent=True) or {}
    customer = data.get('customer') or {}
    shipping_address = data.get('shipping_address') or {}

    customer_name = str(
        customer.get('name') or ''
    ).strip()

    customer_email = str(
        customer.get('email') or ''
    ).strip().lower()

    customer_phone = str(
        customer.get('phone') or ''
    ).strip()

    fulfillment_method = str(
        customer.get('fulfillment_method') or ''
    ).strip()

    if not customer_name or len(customer_name) > 150:
        return jsonify({
            'error': 'Enter your full name.'
        }), 400

    if (
        not customer_email
        or '@' not in customer_email
        or len(customer_email) > 150
    ):
        return jsonify({
            'error': 'Enter a valid email address.'
        }), 400

    if len(customer_phone) > 40:
        return jsonify({
            'error': 'Enter a valid phone number.'
        }), 400

    if fulfillment_method not in MERCH_FULFILLMENT_METHODS:
        return jsonify({
            'error': 'Shipping is the only available delivery method.'
        }), 400

    try:
        expire_merch_reservations()
        validated_items, subtotal = validate_merch_cart(
            data.get('items')
        )
    except ValueError as exc:
        return jsonify({
            'error': str(exc)
        }), 400

    try:
        expected_subtotal = Decimal(str(data.get('expected_subtotal')))
    except (InvalidOperation, TypeError):
        return jsonify({'error': 'Refresh your cart before checkout.'}), 409
    if (not expected_subtotal.is_finite()
            or expected_subtotal != subtotal):
        return jsonify({
            'error': 'A product price changed. Refresh your cart before paying.'
        }), 409

    shipping_name = None
    shipping_address_line_1 = None
    shipping_address_line_2 = None
    shipping_city = None
    shipping_state = None
    shipping_postal_code = None
    shipping_country = None

    if fulfillment_method == 'shipping':
        shipping_name = str(
            shipping_address.get('name') or customer_name
        ).strip()

        shipping_address_line_1 = str(
            shipping_address.get('address_line_1') or ''
        ).strip()

        shipping_address_line_2 = str(
            shipping_address.get('address_line_2') or ''
        ).strip()

        shipping_city = str(
            shipping_address.get('city') or ''
        ).strip()

        shipping_state = str(
            shipping_address.get('state') or ''
        ).strip().upper()

        shipping_postal_code = str(
            shipping_address.get('postal_code') or ''
        ).strip()

        shipping_country = str(
            shipping_address.get('country') or 'US'
        ).strip().upper()

        if not shipping_name or len(shipping_name) > 150:
            return jsonify({
                'error': 'Enter the recipient name.'
            }), 400

        if (
            not shipping_address_line_1
            or len(shipping_address_line_1) > 200
        ):
            return jsonify({
                'error': 'Enter a valid street address.'
            }), 400

        if len(shipping_address_line_2) > 200:
            return jsonify({
                'error': 'Address line 2 is too long.'
            }), 400

        if not shipping_city or len(shipping_city) > 100:
            return jsonify({
                'error': 'Enter a valid city.'
            }), 400

        if not shipping_state or len(shipping_state) > 50:
            return jsonify({
                'error': 'Enter a valid state.'
            }), 400

        if (
            not shipping_postal_code
            or len(shipping_postal_code) > 20
        ):
            return jsonify({
                'error': 'Enter a valid ZIP code.'
            }), 400

        if shipping_country != 'US':
            return jsonify({
                'error': (
                    'Shipping is currently available only '
                    'within the United States.'
                )
            }), 400

        shipping_amount = MERCH_SHIPPING_RATE
    else:
        shipping_amount = Decimal('0.00')

    total = (
        subtotal + shipping_amount
    ).quantize(Decimal('0.01'))

    order_number = (
        f"MERCH-{datetime.utcnow():%Y%m%d}-"
        f"{uuid.uuid4().hex[:8].upper()}"
    )

    user_id = (
        current_user.id
        if current_user.is_authenticated
        else None
    )

    merch_order = MerchOrder(
        order_number=order_number,
        user_id=user_id,
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone or None,
        fulfillment_method=fulfillment_method,
        shipping_name=shipping_name,
        shipping_address_line_1=shipping_address_line_1,
        shipping_address_line_2=shipping_address_line_2 or None,
        shipping_city=shipping_city,
        shipping_state=shipping_state,
        shipping_postal_code=shipping_postal_code,
        shipping_country=shipping_country,
        subtotal=subtotal,
        shipping_amount=shipping_amount,
        total=total,
        currency='USD',
        status='pending',
        inventory_reserved=True,
        cancel_token=secrets.token_urlsafe(32),
        reservation_expires_at=(datetime.utcnow() + timedelta(
            minutes=MERCH_RESERVATION_MINUTES)),
    )
    for item in validated_items:
        merch_order.items.append(MerchOrderItem(
            product_id=item['product_id'],
            variant_id=item['variant_id'],
            product_name=item['name'],
            size=item['size'],
            color=item['color'],
            quantity=item['quantity'],
            unit_price=item['unit_price'],
        ))

    try:
        for item in validated_items:
            reserved = db.session.execute(
                update(MerchVariant)
                .where(MerchVariant.id == item['variant_id'],
                       MerchVariant.active.is_(True),
                       MerchVariant.product.has(MerchProduct.active.is_(True)),
                       MerchVariant.stock_on_hand -
                       MerchVariant.stock_reserved >= item['quantity'])
                .values(stock_reserved=(
                    MerchVariant.stock_reserved + item['quantity']
                ))
            )
            if reserved.rowcount != 1:
                raise ValueError(
                    'An item just sold out. Refresh the merch page.'
                )
        db.session.add(merch_order)
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return jsonify({'error': str(exc)}), 409

    try:
        access_token = get_access_token()

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}',
            'PayPal-Request-Id': f'merch-create-{order_number}'
        }

        paypal_body = {
            'intent': 'CAPTURE',
            'purchase_units': [{
                'reference_id': order_number,
                'custom_id': order_number,
                'description': (
                    'Pickup Volleyball merchandise'
                ),
                'items': [{
                    'name': item['name'],
                    'description': f"{item['color']} / {item['size']}",
                    'sku': f"{item['product_id']}-{item['variant_id']}",
                    'quantity': str(item['quantity']),
                    'category': 'PHYSICAL_GOODS',
                    'unit_amount': {
                        'currency_code': 'USD',
                        'value': f"{item['unit_price']:.2f}"
                    }
                } for item in validated_items],
                'shipping': {
                    'name': {'full_name': shipping_name},
                    'address': {
                        'address_line_1': shipping_address_line_1,
                        **({'address_line_2': shipping_address_line_2}
                           if shipping_address_line_2 else {}),
                        'admin_area_2': shipping_city,
                        'admin_area_1': shipping_state,
                        'postal_code': shipping_postal_code,
                        'country_code': shipping_country,
                    }
                },
                'amount': {
                    'currency_code': 'USD',
                    'value': f'{total:.2f}',
                    'breakdown': {
                        'item_total': {
                            'currency_code': 'USD',
                            'value': f'{subtotal:.2f}'
                        },
                        'shipping': {
                            'currency_code': 'USD',
                            'value': f'{shipping_amount:.2f}'
                        }
                    }
                }
            }],
            'payment_source': {
                'paypal': {
                    'experience_context': {
                        'shipping_preference': 'SET_PROVIDED_ADDRESS'
                    }
                }
            }
        }

        paypal_response = requests.post(
            f'{PAYPAL_BASE}/v2/checkout/orders',
            headers=headers,
            json=paypal_body,
            timeout=20
        )

        paypal_response.raise_for_status()
        paypal_data = paypal_response.json()
        paypal_order_id = paypal_data.get('id')

        if not paypal_order_id:
            raise ValueError(
                'PayPal did not return an order ID.'
            )

        merch_order.paypal_order_id = paypal_order_id
        db.session.commit()

        return jsonify({
            'id': paypal_order_id,
            'cancel_token': merch_order.cancel_token,
        })

    except Exception:
        db.session.rollback()

        pending_order = db.session.get(MerchOrder, merch_order.id)
        if pending_order and pending_order.status == 'pending':
            release_merch_reservation(pending_order, status='failed')
            db.session.commit()

        current_app.logger.exception(
            'Unable to create merchandise order'
        )

        return jsonify({
            'error': (
                'Checkout could not be started. '
                'Please try again.'
            )
        }), 502


@main.route('/api/merch/orders/<paypal_order_id>/cancel', methods=['POST'])
def cancel_merch_order(paypal_order_id):
    order = MerchOrder.query.filter_by(paypal_order_id=paypal_order_id).first()
    supplied = (request.get_json(silent=True) or {}).get('cancel_token', '')
    if (not order or not order.cancel_token
            or not hmac.compare_digest(str(supplied), order.cancel_token)):
        return jsonify({'error': 'Checkout not found.'}), 404
    if order.status == 'cancelled':
        return jsonify({'success': True})
    if order.status != 'pending':
        return jsonify({
            'error': 'Payment confirmation is in progress. Contact us before retrying.'
        }), 409
    release_merch_reservation(order, status='cancelled')
    db.session.commit()
    return jsonify({'success': True})


@main.route(
    '/api/merch/orders/<paypal_order_id>/capture',
    methods=['POST']
)
def capture_merch_order(paypal_order_id):
    merch_order = MerchOrder.query.filter_by(
        paypal_order_id=paypal_order_id
    ).first()

    if not merch_order:
        return jsonify({
            'error': 'Merchandise order not found.'
        }), 404

    # Makes repeated successful capture requests safe.
    if merch_order.status == 'completed':
        return jsonify({
            'success': True,
            'order_number': merch_order.order_number
        })

    if merch_order.status not in {'pending', 'capturing'}:
        return jsonify({
            'error': 'This order cannot be captured.'
        }), 409

    if (merch_order.status == 'pending'
            and merch_order.inventory_reserved
            and merch_order.reservation_expires_at <= datetime.utcnow()):
        release_merch_reservation(merch_order)
        db.session.commit()
        return jsonify({
            'error': 'Your cart reservation expired. Please start checkout again.'
        }), 409

    if merch_order.status == 'pending' and merch_order.inventory_reserved:
        merch_order.status = 'capturing'
        merch_order.reservation_expires_at = datetime.utcnow() + timedelta(
            minutes=5)
        db.session.commit()

    try:
        access_token = get_access_token()

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {access_token}',
            'PayPal-Request-Id': f'merch-capture-{merch_order.order_number}'
        }

        capture_response = requests.post(
            (
                f'{PAYPAL_BASE}/v2/checkout/orders/'
                f'{paypal_order_id}/capture'
            ),
            headers=headers,
            timeout=20
        )

        capture_response.raise_for_status()
        order_data = capture_response.json()

        if order_data.get('status') != 'COMPLETED':
            return jsonify({
                'error': 'PayPal did not complete the payment.'
            }), 400

        purchase_units = order_data.get(
            'purchase_units'
        ) or []

        if not purchase_units:
            return jsonify({
                'error': 'PayPal returned incomplete payment data.'
            }), 502

        captures = (
            purchase_units[0]
            .get('payments', {})
            .get('captures')
            or []
        )

        if not captures:
            return jsonify({
                'error': 'PayPal capture information is missing.'
            }), 502

        capture = captures[0]
        capture_id = capture.get('id')
        amount = capture.get('amount') or {}

        try:
            captured_total = Decimal(
                str(amount.get('value'))
            ).quantize(Decimal('0.01'))
        except (InvalidOperation, TypeError):
            captured_total = Decimal('-1.00')

        if amount.get('currency_code') != merch_order.currency:
            current_app.logger.error(
                'Merch currency mismatch for %s',
                merch_order.order_number
            )

            return jsonify({
                'error': 'Payment currency did not match.'
            }), 409

        expected_total = Decimal(
            merch_order.total
        ).quantize(Decimal('0.01'))

        if captured_total != expected_total:
            current_app.logger.error(
                (
                    'Merch amount mismatch for %s: '
                    'expected %s, captured %s'
                ),
                merch_order.order_number,
                expected_total,
                captured_total
            )

            return jsonify({
                'error': 'Payment amount did not match.'
            }), 409

        if not capture_id:
            return jsonify({
                'error': 'PayPal capture ID is missing.'
            }), 502

        duplicate_capture = MerchOrder.query.filter(
            MerchOrder.paypal_capture_id == capture_id,
            MerchOrder.id != merch_order.id
        ).first()

        if duplicate_capture:
            return jsonify({
                'error': 'This payment was already processed.'
            }), 409

        paypal_email = (
            order_data.get('payer', {}).get(
                'email_address'
            )
            or order_data.get(
                'payment_source', {}
            ).get(
                'paypal', {}
            ).get(
                'email_address'
            )
        )

        merch_order = (MerchOrder.query.filter_by(id=merch_order.id)
                       .with_for_update().populate_existing().first())
        if merch_order.status == 'completed':
            return jsonify({
                'success': True,
                'order_number': merch_order.order_number
            })
        if merch_order.inventory_reserved:
            for item in merch_order.items:
                changed = db.session.execute(
                    update(MerchVariant)
                    .where(MerchVariant.id == item.variant_id,
                           MerchVariant.stock_reserved >= item.quantity,
                           MerchVariant.stock_on_hand >= item.quantity)
                    .values(
                        stock_reserved=(MerchVariant.stock_reserved -
                                        item.quantity),
                        stock_on_hand=(MerchVariant.stock_on_hand -
                                       item.quantity),
                    )
                )
                if changed.rowcount != 1:
                    raise ValueError('Reserved stock could not be finalized.')
            merch_order.inventory_reserved = False
            merch_order.reservation_expires_at = None

        merch_order.paypal_capture_id = capture_id
        merch_order.paypal_payer_email = paypal_email
        merch_order.status = 'completed'
        merch_order.completed_at = datetime.utcnow()

        db.session.commit()

        return jsonify({
            'success': True,
            'order_number': merch_order.order_number
        })

    except (requests.RequestException, ValueError):
        db.session.rollback()

        current_app.logger.exception(
            'Unable to capture merchandise order %s',
            merch_order.order_number
        )

        return jsonify({
            'error': (
                'Payment could not be confirmed. '
                'Please contact us before retrying.'
            )
        }), 502
    





    

