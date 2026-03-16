# This file handles the routes

from flask import Blueprint, render_template, redirect, url_for, flash, session 
import urllib.parse

import traceback
from flask import request, jsonify # Import jsonify
from datetime import datetime, timedelta, date

import os
from flask import current_app
from werkzeug.utils import secure_filename
import uuid 
from flask_login import current_user, login_required


from .models import Event, User, db, EventAttendee, Subscription, CreditGrant
from flask_mail import Message
from . import mail  

import os
from dotenv import load_dotenv
import requests

import base64
from .utils import generate_detailed_waiver, add_user_credit, cleanup_user_expired_credits


load_dotenv() # Load variables from .env file



main = Blueprint('main', __name__)

# PayPal credentials
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_SECRET = os.getenv("PAYPAL_SECRET")
PAYPAL_BASE = "https://api-m.paypal.com"
PAYPAL_PLAN_ID_TIER1 = os.getenv("PAYPAL_PLAN_ID_TIER1")
PAYPAL_PLAN_ID_TIER2 = os.getenv("PAYPAL_PLAN_ID_TIER2")

def spend_user_credit(user, amount_needed=1):
    """
    FIFO Logic: Finds the credits expiring SOONEST and deducts from them.
    Returns True if successful, False if insufficient balance.
    """
    # 1. Get all active grants with positive balance, sorted by expiry date (ASC)
    active_grants = CreditGrant.query.filter(
        CreditGrant.user_id == user.id,
        CreditGrant.balance > 0,
        CreditGrant.expiry_date >= date.today()
    ).order_by(CreditGrant.expiry_date.asc()).all()

    # 2. Check if total available is enough
    total_available = sum(g.balance for g in active_grants)
    if total_available < amount_needed:
        return False, "Insufficient credits"

    # 3. Deduct from grants FIFO
    remaining_to_pay = amount_needed
    
    for grant in active_grants:
        if remaining_to_pay <= 0:
            break
            
        if grant.balance >= remaining_to_pay:
            # This grant covers the rest
            grant.balance -= remaining_to_pay
            remaining_to_pay = 0
        else:
            # Take everything from this grant and move to the next
            remaining_to_pay -= grant.balance
            grant.balance = 0
            
    db.session.commit()
    return True, "Success"

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
    
    You can view and sign up for all events using this link: https://pickupvolleyballpdx.com/events

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
        
        - The Pick Up Volleyball PDX Team
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
                <p>- The Pick Up Volleyball PDX Team</p>
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
            bcc=["noreply.pickupvbpdx@gmail.com"],
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
        admin_email = "noreply.pickupvbpdx@gmail.com"
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
        
        - The Pick Up Volleyball PDX Team
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
                <p>- The Pick Up Volleyball PDX Team</p>
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
    if request.method == 'POST' and user_role == 'admin':
        # This section remains unchanged...
        title = request.form.get('title')
        description = request.form.get('description')
        date_str = request.form.get('date')
        start_time = request.form.get('start_time')
        end_time = request.form.get('end_time')
        location = request.form.get('location')
        full_address = request.form.get('full_address') # Get the new field
        allow_guests = request.form.get('allow_guests') == 'on'
        guest_limit = int(request.form.get('guest_limit') or 0)
        ticket_price = float(request.form.get('ticket_price') or 0.0)
        max_capacity = int(request.form.get('max_capacity') or 28)


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
            max_capacity=max_capacity,
            date=datetime.strptime(date_str, "%Y-%m-%d").date(),
            image_filename=image_filename
        )
        db.session.add(new_event)
        db.session.commit()
        flash('Event added successfully!', 'success')
        return redirect(url_for('main.events'))

    now = datetime.now()
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
            "max_capacity": event.max_capacity,
            "rsvp_count": event.rsvp_count,
            "is_attending": event.is_attending,
            'image_filename': event.image_filename,
            'is_past': is_past
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
    if session.get('role') != 'admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('main.events'))
    event = Event.query.get_or_404(event_id)

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

        event.allow_guests = request.form.get('allow_guests') == 'on'
        event.guest_limit = int(request.form.get('guest_limit', event.guest_limit))
        event.ticket_price = float(request.form.get('ticket_price', event.ticket_price))
        event.max_capacity = int(request.form.get('max_capacity', event.max_capacity))
        
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
        flash('Event updated successfully!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error updating event: {e}', 'error')

    return redirect(url_for('main.events'))

@main.route('/events/delete/<int:event_id>', methods=['POST'])
def delete_event(event_id):
    event = Event.query.get_or_404(event_id)
    db.session.delete(event)
    db.session.commit()
    return jsonify({'message': 'Event deleted successfully'}), 200


@main.route('/events/cancel/<int:event_id>', methods=['POST'])
def cancel_event(event_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    event = Event.query.get_or_404(event_id)
    
    # --- SAFETY CHECK: Prevent double-crediting ---
    if event.status == 'canceled':
        return jsonify({'error': 'This event is already canceled.'}), 400

    data = request.get_json()
    
    # *** CRITICAL FIX: Update the status to canceled ***
    event.status = 'canceled' 
    event.cancellation_reason = data.get('cancellation_reason', 'Cancelled Event')
    
    attendees = EventAttendee.query.filter_by(event_id=event.id).all()
    
    credits_issued_count = 0
    expiry = date.today() + timedelta(days=30)
    for attendee in attendees:
        # Calculate spots (User + Guests)
        spots_registered = 1 + (attendee.guest_count or 0)
        
        if attendee.user_id:
            user = User.query.get(attendee.user_id)
            if user:
                new_grant = CreditGrant(
                    user_id=user.id,
                    balance=spots_registered,
                    source_type='cancellation',
                    description=f"Refund: {event.title}",
                    expiry_date=expiry
                )
                db.session.add(new_grant)
                credits_issued_count += 1
                
                # 2. Send the Email
                send_cancellation_credit_email(user, event, spots_registered, expiry)
        
        elif attendee.email:
             # Guest checkout (no account) - Just notify them
             send_guest_cancellation_only_email(attendee.email, event)

    try:
        db.session.commit()
        return jsonify({'message': 'Event canceled, credits issued, and emails sent successfully.'}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f'Error canceling event: {str(e)}'}), 500


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
        now = datetime.now()
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
    response = requests.post(f"{PAYPAL_BASE}/v1/oauth2/token", headers=headers, data=data, auth=auth)
    response.raise_for_status() # Raise an exception for HTTP errors
    return response.json()["access_token"]

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

    event = Event.query.get(event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404

    current_attendees = EventAttendee.query.filter_by(event_id=event.id).all()
    current_rsvp_count = sum(1 + (a.guest_count or 0) for a in current_attendees)
    
    # For edits, we only check against capacity if new people are being added.
    if is_edit:
        # Calculate how many people are being *added* in this edit.
        new_guest_count = requested_quantity - 1
        additional_attendees = new_guest_count - initial_guest_count
        if additional_attendees > 0 and (current_rsvp_count + additional_attendees > event.max_capacity):
             return jsonify({"error": f"Sorry, the event does not have enough space to add {additional_attendees} more person(s)."}), 400
    else: # For new RSVPs
        # Check if adding the requested number of people exceeds capacity.
        if current_rsvp_count + requested_quantity > event.max_capacity:
            # Calculate remaining spots for a more helpful error message.
            spots_left = event.max_capacity - current_rsvp_count
            return jsonify({"error": f"Sorry, this event is full or does not have enough space. Only {spots_left} spot(s) remaining."}), 400
    # --- Bug Fix End ---

    ticket_price = event.ticket_price
    # Calculate the amount to charge based on whether it's an edit or initial RSVP
    amount_to_charge = 0.0

    if is_guest_checkout:
        # Guests NEVER get free credits. Charge for every spot (themself + guests)
        amount_to_charge = requested_quantity * ticket_price
        user_id_for_paypal = "GUEST"
    else:
        # ... [Keep existing logged-in user calculation logic] ...
        user = current_user
        user_id_for_paypal = current_user.id

        if is_edit:
            # For edits, calculate the difference in guests (this logic remains the same)
            initial_quantity = 1 + initial_guest_count
            guest_difference = requested_quantity - initial_quantity
            amount_to_charge = guest_difference * ticket_price
            
            if amount_to_charge <= 0:
                return jsonify({"error": "No payment required for this change."}), 400
                
        else:
            payable_attendees = requested_quantity

            if user.event_credits > 0:
                payable_attendees -= 1
                
            payable_attendees = max(0, payable_attendees)
            amount_to_charge = payable_attendees * ticket_price

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
    return jsonify(response.json())

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

        event = Event.query.get(event_id)
        if not event:
            return jsonify({"error": "Event not found"}), 404

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
        current_attendees = EventAttendee.query.filter_by(event_id=event.id).all()
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
                success_message = f'Your RSVP for {event.title} has been updated!'
            else:
                # Initial RSVP for User
                attendee = EventAttendee(event_id=event_id, user_id=user.id, guest_count=new_guest_count)
                db.session.add(attendee)

                # Handle Credits
                cleanup_user_expired_credits(current_user)
                if user.event_credits > 0:
                    user.spend_credits(1)

                success_message = f"You're confirmed for {event.title}! See you there!"
                send_rsvp_confirmation_email(user, event, new_guest_count)

        all_attendees_for_event = EventAttendee.query.filter_by(event_id=event_id).all()
        event.rsvp_count = sum(1 + (a.guest_count or 0) for a in all_attendees_for_event)

        db.session.commit()
        order_data['success_message'] = success_message

    except Exception as e:
        traceback.print_exc()
        db.session.rollback()
        return jsonify({"error": "An internal server error occurred."}), 500

    return jsonify(order_data)

@main.route('/subscriptions')
def subscriptions():
    today = date.today()
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
            if sub.status == 'active':
                filtered_subscriptions.append(sub)
            # ONLY keep canceled subscriptions if their expiry_date is in the future or today
            elif sub.status == 'canceled' and sub.expiry_date and sub.expiry_date >= today:
                filtered_subscriptions.append(sub)

        # Determine which active tiers the user has
        active_subs = [sub for sub in filtered_subscriptions if sub.status == 'active']
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
                'renews_on': sub.expiry_date if sub.status == 'active' else None,
                'expires_on': sub.expiry_date if sub.status == 'canceled' else None,
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
        has_tier_2=has_tier_2
    )


@main.route("/api/paypal/confirm-subscription", methods=["POST"])
@login_required
def confirm_subscription():
    data = request.get_json()
    paypal_sub_id = data.get('subscription_id')

    if not paypal_sub_id:
        return jsonify({'error': 'Subscription ID is missing.'}), 400

    try:
        # Check if this subscription already exists in our DB to prevent duplicates
        existing_sub = Subscription.query.filter_by(paypal_subscription_id=paypal_sub_id).first()
        if existing_sub:
            return jsonify({'success': True, 'message': 'Subscription already confirmed.'}), 200

        # Fetch subscription details from PayPal to get the Plan ID, which tells us the tier
        access_token = get_access_token()
        headers = {"Authorization": f"Bearer {access_token}"}
        sub_details_response = requests.get(f"{PAYPAL_BASE}/v1/billing/subscriptions/{paypal_sub_id}", headers=headers)
        sub_details_response.raise_for_status()
        paypal_sub_data = sub_details_response.json()
        plan_id = paypal_sub_data.get('plan_id')

        # Determine tier and credits from the Plan ID
        if plan_id == PAYPAL_PLAN_ID_TIER1:
            tier = 1
            credits_to_add = 4
        elif plan_id == PAYPAL_PLAN_ID_TIER2:
            tier = 2
            credits_to_add = 8
        else:
            return jsonify({'error': 'Unknown subscription plan from PayPal.'}), 400

        # Create the new subscription record in our database
        new_sub = Subscription(
            user_id=current_user.id,
            paypal_subscription_id=paypal_sub_id,
            tier=tier,
            credits_per_month=credits_to_add,
            status='active',
            expiry_date=date.today() + timedelta(days=30)
        )
        db.session.add(new_sub)

        add_user_credit(
            user=current_user, 
            amount=credits_to_add, 
            source_type='subscription', 
            description=f'Subscription Tier {tier} Credits'
        )
        
        db.session.commit()
        
        try:
            send_subscription_email(current_user.email)
        except Exception as e:
            print(f"Failed to send subscription confirmation email: {e}")
        
        flash('Your subscription is now active! Thank you for joining.', 'success')
        return jsonify({'success': True, 'message': 'Subscription activated successfully!'}), 200

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f'An internal server error occurred: {str(e)}'}), 500


@main.route('/api/paypal/create-subscription', methods=['POST'])
@login_required
def create_subscription():
    try:
        # 1. Get tier from the frontend request
        request_data = request.get_json()
        tier = request_data.get('tier')

        # 2. Select the correct Plan ID based on the tier
        if tier == '1':
            plan_id = PAYPAL_PLAN_ID_TIER1
        elif tier == '2':
            plan_id = PAYPAL_PLAN_ID_TIER2
        else:
            return jsonify({'error': 'Invalid subscription tier provided.'}), 400
        
        access_token = get_access_token()
        url = f"{PAYPAL_BASE}/v1/billing/subscriptions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
        data = {
            "plan_id": plan_id,
            "subscriber": {
                "email_address": current_user.email
            },
            "application_context": {
                "return_url": url_for('main.subscriptions', _external=True),
                "cancel_url": url_for('main.subscriptions', _external=True)
            }
        }
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            subscription = response.json()
            return jsonify({'id': subscription['id']}), 201
        else:
            return jsonify({'error': 'Failed to create subscription'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# In views.py, update the cancel_subscription route

@main.route('/cancel_subscription/<string:subscription_id>', methods=['POST'])
@login_required
def cancel_subscription(subscription_id):
    # Find the subscription in our database
    sub_to_cancel = Subscription.query.filter_by(
        paypal_subscription_id=subscription_id, 
        user_id=current_user.id
    ).first()


    # Security check: ensure the sub belongs to the current user
    if not sub_to_cancel or sub_to_cancel.user_id != current_user.id:
        return jsonify({'error': 'Subscription not found or you do not have permission to cancel it.'}), 404
    
    if sub_to_cancel.status == 'canceled':
        return jsonify({'error': 'This subscription has already been canceled.'}), 400

    try:
        # Cancel the subscription with PayPal
        access_token = get_access_token()
        url = f"{PAYPAL_BASE}/v1/billing/subscriptions/{sub_to_cancel.paypal_subscription_id}/cancel"
        #Add explicit headers
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        response = requests.post(url, headers=headers, json={'reason': 'User requested cancellation'})

        if response.status_code == 204:
            # Update the status in our database
            sub_to_cancel.status = 'canceled'
            db.session.commit()
            flash('Subscription canceled successfully. Your credits remain until the expiry date.', 'success')
            return jsonify({'success': True}), 200
        else:
            return jsonify({'error': 'Failed to cancel subscription with PayPal.'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@main.route('/api/user/subscription_status', methods=['GET'])
@login_required  # Ensure user is logged in (import from flask_login if needed)
def get_subscription_status():
    # CORRECT LOGIC: Can the user USE credits? Check expiry date only.
    valid_sub = Subscription.query.filter(
        Subscription.user_id == current_user.id,
        Subscription.expiry_date >= date.today()
    ).first()
    
    return jsonify({
        'event_credits': current_user.event_credits,
        'has_active_subscription': valid_sub is not None
    })


#upgrade subscription:
@main.route('/api/subscription/upgrade', methods=['POST'])
@login_required
def upgrade_subscription():
    data = request.get_json()
    target_tier = data.get('tier')

    if target_tier != '2':
        return jsonify({'error': 'Upgrade path not supported.'}), 400

    try:
        # Step 1: Find the user's active Tier 1 subscription
        old_subscription = Subscription.query.filter_by(
            user_id=current_user.id, 
            tier=1, 
            status='active'
        ).first()

        if not old_subscription:
            return jsonify({'error': 'No active Tier 1 subscription found to upgrade.'}), 404

        # Step 2: Cancel the old subscription in PayPal
        access_token = get_access_token()
        cancel_url = f"{PAYPAL_BASE}/v1/billing/subscriptions/{old_subscription.paypal_subscription_id}/cancel"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        
        cancel_response = requests.post(cancel_url, headers=headers, json={'reason': 'User upgraded to a new plan.'})
        

        if cancel_response.status_code != 204:
            # Log the full error for debugging
            print(f"Upgrade Failed: PayPal cancel return {cancel_response.status_code} - {cancel_response.text}")
            return jsonify({'error': 'Failed to cancel your current subscription. Upgrade aborted to prevent double billing.'}), 500

        # Step 3: Only NOW do we mark it canceled in our DB
        old_subscription.status = 'canceled'
        db.session.commit()

        # Step 4: Create the new PayPal subscription for Tier 2
        create_url = f"{PAYPAL_BASE}/v1/billing/subscriptions"
        create_data = {
            "plan_id": PAYPAL_PLAN_ID_TIER2, 
            "subscriber": {"email_address": current_user.email},
            "application_context": {
                "return_url": url_for('main.subscriptions', _external=True),
                "cancel_url": url_for('main.subscriptions', _external=True)
            }
        }
        create_response = requests.post(create_url, headers=headers, json=create_data)
        create_response.raise_for_status() 
        
        new_paypal_sub = create_response.json()
        return jsonify({'id': new_paypal_sub['id']}), 201

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f'An error occurred during the upgrade process: {str(e)}'}), 500


@main.route('/api/paypal/webhook', methods=['POST'])
def paypal_webhook():
    data = request.get_json()
    event_type = data.get('event_type')
    resource = data.get('resource')

    if not resource or not event_type:
        return jsonify(status="ignored", reason="Missing resource or event_type"), 200

    subscription_id = None
    
    if "PAYMENT.SALE" in event_type:
        subscription_id = resource.get('billing_agreement_id')
    else:
        subscription_id = resource.get('id')

    if not subscription_id:
        return jsonify(status="ignored", reason="Could not determine Subscription ID"), 200
    # --- CRITICAL FIX END ---

    # Find the subscription in our database
    subscription = Subscription.query.filter_by(paypal_subscription_id=subscription_id).first()
    
    if not subscription:
        # If we can't find the subscription, we can't issue credits.
        print(f"Received webhook for unknown subscription ID: {subscription_id}")
        return jsonify(status="ignored", reason="Subscription not found in DB"), 200

    user = subscription.user

    # Handle Successful Payment (Renewal)
    if event_type == "PAYMENT.SALE.COMPLETED":
        # Check if the state is completed to be safe
        if resource.get('state') == 'completed':
            add_user_credit(
                user=user,
                amount=subscription.credits_per_month,
                source_type='subscription',
                description='Subscription Renewal',
                days_valid=35
            )
            # ----------------------

            subscription.expiry_date = date.today() + timedelta(days=30)
            if subscription.status in ('suspended', 'expired'):
                subscription.status = 'active'

            # 2. ONLY set status to 'active' if it's currently 'active', 'suspended', or 'expired'.
            # This prevents an explicitly 'canceled' subscription from being revived.
            if subscription.status in ('suspended', 'expired'):
                subscription.status = 'active'
            
            db.session.commit()
            print(f"Webhook: Payment received. Renewed credits for {user.email}. Total: {user.event_credits}")
        else:
            print(f"WARNING: Payment received but state is '{resource.get('state')}'. Credits NOT issued.")
    # Handle Reactivation or Updates (Keep your existing logic if needed)
    elif event_type == "BILLING.SUBSCRIPTION.RE-ACTIVATED":
        subscription.status = 'active'
        subscription.expiry_date = date.today() + timedelta(days=30)
        db.session.commit()
        print(f"Webhook: Subscription re-activated for {user.email}")

    # Handle Cancellation
    elif event_type == "BILLING.SUBSCRIPTION.CANCELLED":
        subscription.status = 'canceled'
        db.session.commit()
        print(f"Webhook: Subscription {subscription_id} for user {user.email} was cancelled.")
        
    # Handle Expiry/Suspension
    elif event_type == "BILLING.SUBSCRIPTION.EXPIRED" or event_type == "BILLING.SUBSCRIPTION.SUSPENDED":
        subscription.status = 'canceled'
        db.session.commit()
        print(f"Webhook: Subscription {subscription_id} for user {user.email} expired/suspended.")

    return jsonify(status="success"), 200


@main.route("/api/rsvp/credit", methods=['POST'])
@login_required
def rsvp_with_credit():
    data = request.get_json()
    event_id = data.get('event_id')
    user = current_user

    try:
        event = Event.query.with_for_update().get(event_id)
        
        if not event:
            return jsonify({'error': 'Event not found.'}), 404
        
        current_attendees = EventAttendee.query.filter_by(event_id=event.id).all()
        current_rsvp_count = sum(1 + (a.guest_count or 0) for a in current_attendees)

        if current_rsvp_count + 1 > event.max_capacity:
            # Rollback acts as the "Unlock" here
            db.session.rollback()
            return jsonify({'error': 'Sorry, this event is now full.'}), 400
        if user.event_credits < 1:
             db.session.rollback()
             return jsonify({'error': 'Insufficient credits.'}), 400
        user.spend_credits(1) 

        new_attendee = EventAttendee(event_id=event.id, user_id=user.id, guest_count=0)
        db.session.add(new_attendee)
        db.session.commit()
        
        send_rsvp_confirmation_email(user, event, 0)
        
        remaining = user.event_credits 
        message = f"Successfully RSVP'd! Used 1 credit. {remaining} remaining."
        session['flashMessage'] = message
        session['flashEventId'] = event_id
        return jsonify({'success': True, 'message': message}), 200

    except Exception as e:
        db.session.rollback()
        print(f"Error in RSVP: {e}") # specific error logging
        return jsonify({'error': 'An error occurred processing your request.'}), 500


@main.route('/api/rsvp/delete/<int:event_id>', methods=['POST'])
@login_required
def delete_rsvp(event_id):
    user = current_user
    event = Event.query.get_or_404(event_id)

    # Check if the event is in the past
    event_end_datetime = datetime.combine(event.date, event.end_time)
    if event_end_datetime < datetime.now():
        return jsonify({'error': 'Cannot cancel RSVP for an event that has already passed.'}), 400

    # Find the user's RSVP
    rsvp = EventAttendee.query.filter_by(event_id=event.id, user_id=user.id).first()

    if not rsvp:
        return jsonify({'error': 'You are not currently RSVP\'d to this event.'}), 404

    try:
        db.session.delete(rsvp)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Your RSVP has been successfully canceled.'}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500
    

@main.route('/api/rsvp/update', methods=['POST'])
@login_required
def update_rsvp():
    data = request.get_json()
    event_id = data.get('event_id')
    new_guest_count = data.get('new_guest_count')

    if event_id is None or new_guest_count is None:
        return jsonify({'error': 'Event ID and new guest count are required.'}), 400

    # Find the existing RSVP for the current user and event
    rsvp = EventAttendee.query.filter_by(event_id=event_id, user_id=current_user.id).first()

    if not rsvp:
        return jsonify({'error': 'No existing RSVP found to update.'}), 404

    try:
        rsvp.guest_count = int(new_guest_count)
        db.session.commit()
        return jsonify({
            'success': True, 
            'message': 'Your RSVP has been successfully updated!'
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

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


        # 4. Loop through the calculated dates and create events
        for event_date in dates_to_create:
            new_event = Event(
                title=form.get('title'),
                description=form.get('description'),
                start_time=start_time_obj,
                end_time=end_time_obj,
                location=form.get('location'),
                full_address=form.get('full_address'),
                allow_guests=form.get('allow_guests') == 'on',
                guest_limit=int(form.get('guest_limit') or 0),
                ticket_price=float(form.get('ticket_price') or 0.0),
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
    



    

