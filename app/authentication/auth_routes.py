# app/authentication/auth_routes.py

from flask import Blueprint, render_template, redirect, url_for, flash, session
from app.forms import UserLoginForm, UserRegistrationForm
from ..models import User, Subscription, db, CreditGrant
from flask_login import login_user, logout_user, login_required
from flask import request
from itsdangerous import URLSafeTimedSerializer
from flask_mail import Mail, Message
import re
from datetime import date, datetime, timedelta
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.units import inch
import base64
import io
from reportlab.lib.utils import ImageReader
from app.utils import generate_detailed_waiver, add_user_credit, cleanup_user_expired_credits

from app.forms import ProfileUpdateForm
import uuid
from werkzeug.utils import secure_filename
import os
from flask import current_app
from flask_login import current_user
mail = Mail()
serializer = URLSafeTimedSerializer(os.getenv('SECRET_KEY', 'fallback-secret-key-change-this'))


auth = Blueprint('auth', __name__, template_folder='auth_templates')

def send_underage_rejection_email(user_email):
    """Sends an email to users who are not old enough to register."""
    msg = Message(
        subject="Registration Rejected: Age Requirement",
        recipients=[user_email],
        body="Thank you for your interest in Pick Up Volleyball PDX. At this time, we are only able to accept registrations from individuals who are 18 years or older. We were unable to create your account as a result of this policy. You may have a parent/legal guardian create an account and register for events, in which case they will assume liability for you. Apologies for any inconvenience, and thank you for your understanding."
    )
    mail.send(msg)

def is_strong_password(password):
    return (
        len(password) >= 8 and
        re.search(r'[A-Z]', password) and
        re.search(r'[a-z]', password) and
        re.search(r'\d', password) and
        re.search(r'[!@#$%^&*(),.?":{}|<>]', password)
    )

@auth.route('/signin', methods=['GET', 'POST'])
def signin():
    form = UserLoginForm()
    try:
        if form.validate_on_submit():
            email = form.email.data
            password = form.password.data

            print(f"Attempting login with Email: {email}, Password: {password}")

            user = User.query.filter_by(email=email).first()

            if user and user.check_password(password):
                print(f"Found user: {user.email}")
                login_user(user)
                flash('Login successful!', 'success')
                session['role'] = user.role

                print(f"User {email} logged in successfully.")
                return render_template('signin.html', form=form, first_name=user.first_name)
            
            else:
                flash('Invalid email or password. ', 'error')
                print(f"No user found with email: {email}")
                return render_template('signin.html', form=form)
    except Exception as e:
        print(f"Error during login: {e}")
        flash('An error occurred during login. Please try again later.', 'error')

    return render_template('signin.html', form=form)


@auth.route('/register', methods=['GET', 'POST'])
def register():
    form = UserRegistrationForm()

    if form.validate_on_submit():
        # --- Data Collection ---
        email = form.email.data
        password = form.password.data
        first_name = form.first_name.data
        last_name = form.last_name.data
        
        # New waiver fields
        address = request.form.get('address')
        dob = request.form.get('dob')
        emergency_contact_name = request.form.get('emergency_contact_name')
        emergency_contact_phone = request.form.get('emergency_contact_phone')
        signature_data = request.form.get('signature_data') # Get signature image data

         # --- Age Calculation & Validation ---
        try:
            today = date.today()
            birthdate = date.fromisoformat(dob)
            age = today.year - birthdate.year - ((today.month, today.day) < (birthdate.month, birthdate.day))
        except (ValueError, TypeError):
            flash('Please enter a valid date of birth.', 'error')
            return render_template('register.html', form=form)

        if age < 18:
            flash('Sorry, you must be 18 years or older to create an account.', 'error')
            send_underage_rejection_email(email) # Send the rejection email
            return render_template('register.html', form=form) # Stop and show the form again


        # --- Validation ---
        if User.query.filter_by(email=form.email.data).first():
            flash('Email already registered. Please log in.', 'error')
            # 👇 CHANGE THIS LINE
            return render_template('register.html', form=form)
        
        if not is_strong_password(form.password.data):
            flash('Password must be at least 8 characters long...', 'error')
            # 👇 AND CHANGE THIS LINE
            return render_template('register.html', form=form)

        if form.password.data != form.confirm_password.data:
            flash('Passwords do not match.', 'error')
            # 👇 ALSO CHANGE THIS LINE (if you have it)
            return render_template('register.html', form=form)
    

        #  Profile Image Handling ---
        profile_image_filename = 'default-avatar.jpeg'  # Default image
        cropped_image_data = request.form.get('cropped_image_data')

        if cropped_image_data:
            try:
                # Handle the cropped image data if it exists
                header, encoded = cropped_image_data.split(",", 1)
                image_data = base64.b64decode(encoded)
                filename = f"{uuid.uuid4().hex}.png"
                upload_path = os.path.join(current_app.root_path, 'static', 'profile_images', filename)
                os.makedirs(os.path.dirname(upload_path), exist_ok=True)
                with open(upload_path, 'wb') as f:
                    f.write(image_data)
                profile_image_filename = filename
            except Exception as e:
                flash('There was an error processing the cropped image.', 'error')
                print(f"Error saving cropped image: {e}")
        
        elif 'profileImage' in request.files:
            # Fallback to the original file if no crop data is sent
            image = request.files['profileImage']
            if image and image.filename:
                ext = os.path.splitext(image.filename)[1]
                filename = f"{uuid.uuid4().hex}{ext}"
                upload_path = os.path.join(current_app.root_path, 'static', 'profile_images', filename)
                os.makedirs(os.path.dirname(upload_path), exist_ok=True)
                image.save(upload_path)
                profile_image_filename = filename

        # Checks a comma-separated list of admins from your .env file
        admin_emails = os.getenv('ADMIN_EMAILS', '').split(',')
        role = 'admin' if email in admin_emails else 'user'
        user = User(
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=role
        )
        user.address=address
        user.profile_image = profile_image_filename or 'default-avatar.jpeg' # Set the profile image here instead
        user.set_password(password)
        db.session.add(user)
        db.session.commit() # Commit here to get the user.id
        # GIVE THE FREE CREDIT IMMEDIATELY
        add_user_credit(
            user=user, 
            amount=1, 
            source_type='promo', 
            description='New Member Welcome Gift', 
            days_valid=365
        )

        try:
            header, encoded = signature_data.split(",", 1)
            signature_image_data = base64.b64decode(encoded)
        except (ValueError, TypeError):
            flash('Invalid signature data. Please sign again.', 'error')
            return render_template('register.html', form=form)


        waiver_filename = f"waiver_{user.id}.pdf"
        waiver_path = os.path.join(current_app.root_path, 'static', 'waivers', waiver_filename)
        os.makedirs(os.path.dirname(waiver_path), exist_ok=True)
        
        waiver_data_dict = { "name": f"{first_name} {last_name}", "address": address, "dob": dob, "emergency_name": emergency_contact_name, "emergency_phone": emergency_contact_phone }
        
        generate_detailed_waiver(waiver_data_dict, signature_image_data, waiver_path)

        # ... (your existing email logic) ...
        with open(waiver_path, 'rb') as f:
            pdf_bytes = f.read()

        def send_waiver_email(user_email, pdf_data, user_name, user_id):
            admin_email = "noreply.pickupvbpdx@gmail.com"
            msg = Message(
                subject=f"Waiver Confirmation for {user_name}",
                sender=admin_email,
                recipients=[user_email],
                bcc=[admin_email],
                body=(f"Hi {user_name},\n\n"
                                    f"Attached is your signed waiver and liability agreement for "
                                    f"Pick Up Volleyball PDX. Keep this for your records.\n\n"
                                    f"See you on the court!")
                    )
            msg.attach(f"waiver_{user_id}.pdf", "application/pdf", pdf_data)
            mail.send(msg)
        try:
            send_waiver_email(user.email, pdf_bytes, user.first_name, user.id)
        except Exception as e:
            print(f"Mail failed but user was created: {e}")

        # --- Final Steps & Redirect ---
        login_user(user)
        session['role'] = role
        flash('Account created successfully! Check your email for your signed waiver.', 'success')
        
        # 3. Redirect instead of rendering the template
        return redirect(url_for('auth.profile'))

    return render_template('register.html', form=form)

@auth.route('/signout', methods=['POST'])
@login_required
def signout():
    logout_user()
    session.pop('role', None)  # ✅ Ensure role is removed
    flash('You have been logged out.', 'success')
    return redirect(url_for('main.home'))


@auth.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    cleanup_user_expired_credits(current_user)

    # Fetch active grants for the ledger view
    active_credits = CreditGrant.query.filter(
        CreditGrant.user_id == current_user.id,
        CreditGrant.balance > 0,
        CreditGrant.expiry_date >= date.today()
    ).order_by(CreditGrant.expiry_date.asc()).all()

    # Calculate Total
    total_credits = sum(c.balance for c in active_credits)
    form = ProfileUpdateForm(obj=current_user)

    if request.method == 'POST':
        if 'update_details_submit' in request.form:
            # Check for new cropped image data first
            cropped_image_data = request.form.get('cropped_image_data')

            if cropped_image_data:
                try:
                    # Strip the header and decode the base64 string
                    header, encoded = cropped_image_data.split(",", 1)
                    image_data = base64.b64decode(encoded)

                    # Create a unique filename
                    filename = f"{uuid.uuid4().hex}.png"
                    upload_path = os.path.join(current_app.root_path, 'static', 'profile_images', filename)
                    
                    # Save the decoded data as a file
                    with open(upload_path, 'wb') as f:
                        f.write(image_data)

                    current_user.profile_image = filename
                    flash('Profile image updated successfully!', 'success')

                except Exception as e:
                    flash('There was an error processing the cropped image.', 'error')
                    print(f"Error saving cropped image: {e}")

            db.session.commit()
            return redirect(url_for('auth.profile'))

        # Check if the "Save New Password" button was clicked
        elif 'change_password_submit' in request.form:
            # Only process if the new_password field has data
            if form.new_password.data:
                # Check if the new passwords match
                if form.new_password.data == form.confirm_password.data:
                    # Check for password strength
                    if is_strong_password(form.new_password.data):
                        current_user.set_password(form.new_password.data)
                        db.session.commit()
                        flash('Password updated successfully!', 'success')
                    else:
                        flash('New password does not meet complexity requirements.', 'error')
                else:
                    flash('New passwords do not match.', 'error')
            else:
                flash('Please enter a new password.', 'error')
            return redirect(url_for('auth.profile'))
        
    # Fetch all active subscriptions for the current user
    active_subscriptions = Subscription.query.filter(
        Subscription.user_id == current_user.id,
        Subscription.status == 'active',
        Subscription.expiry_date >= date.today()
    ).all()

    # For a GET request, pre-populate the (disabled) name fields
    form.first_name.data = current_user.first_name
    form.last_name.data = current_user.last_name
    today = date.today()

    return render_template('profile.html', form=form, today=today, 
                           active_subscriptions=active_subscriptions, 
                           active_credits=active_credits,
                           total_credits=total_credits,)


@auth.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']
        user = User.query.filter_by(email=email).first()
        if user:
            token = serializer.dumps(email, salt='password-reset-salt')
            reset_url = url_for('auth.reset_password', token=token, _external=True, _scheme='https')
            msg = Message(subject="Reset Your Password",
                          sender="noreply.pickupvbpdx@gmail.com",
                          recipients=[email],
                          body=f'Click the link to reset your password: {reset_url}')
            mail.send(msg)
            flash('If your email exists in our system, a password reset link has been sent. Please check your inbox.', 'success')
        else:
            flash('No account with that email.', 'error')
        return redirect(url_for('auth.forgot_password'))
    return render_template('forgot_password.html')


@auth.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    try:
        email = serializer.loads(token, salt='password-reset-salt', max_age=3600)
    except Exception as e:
        flash('The password reset link is invalid or expired.', 'error')
        return redirect(url_for('auth.signin'))

    if request.method == 'POST':
        user = User.query.filter_by(email=email).first()
        if user:
            new_password = request.form['password']
            if not is_strong_password(new_password):
                flash('Password must meet complexity requirements.', 'error')
                return render_template('reset_password.html', token=token)
            user.set_password(new_password)
            db.session.commit()
            flash('Your password has been reset. Please log in.', 'success')
            return redirect(url_for('auth.signin'))
    return render_template('reset_password.html', token=token)
