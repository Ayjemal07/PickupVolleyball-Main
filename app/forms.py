"""
This file is used to make sure our users only 
submit the right kinds of data when they're logging in. 
"""

"""package with select modules that we pull from called wtforms 
- this is also from the same origin as flask_wtf. StringField is used to ensure
user only uses string, PasswordField prevents showing it on the screen etc.
"""
from flask_wtf import FlaskForm 
from wtforms import BooleanField, StringField, PasswordField, SubmitField, IntegerField,FileField
from wtforms.validators import DataRequired, Email, Length
from wtforms.validators import DataRequired, Optional, EqualTo
from flask_wtf.file import FileAllowed
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError
from wtforms import DateField
import re

# Password complexity check
def is_strong_password(password):
    return (
        len(password) >= 8 and
        re.search(r'[A-Z]', password) and
        re.search(r'[a-z]', password) and
        re.search(r'\d', password) and
        re.search(r'[!@#$%^&*(),.?":{}|<>]', password)
    )

def password_strength_check(form, field):
    """Custom validator for password strength."""
    if not is_strong_password(field.data):
        raise ValidationError('Password must be at least 8 characters and include uppercase, lowercase, a number, and a symbol.')

class UserLoginForm(FlaskForm):
    email = StringField('Email', validators = [DataRequired(), Email()])
    password = PasswordField('Password', validators = [DataRequired()])
    submit = SubmitField('Sign In')



class UserRegistrationForm(FlaskForm):

    first_name = StringField('First Name', validators=[DataRequired("First name is required.")])
    last_name = StringField('Last Name', validators=[DataRequired("Last name is required.")])
    profileImage = FileField('Upload Profile Image (Optional)')
    dob = DateField('Date of Birth', format='%Y-%m-%d', validators=[DataRequired("Date of birth is required.")])
    email = StringField('Email', validators=[DataRequired("Email is required."), Email()])
    address = StringField('Mailing Address', validators=[DataRequired("Mailing address is required.")])
    emergency_contact_name = StringField('Emergency Contact Name', validators=[DataRequired("Emergency contact name is required.")])
    emergency_contact_phone = StringField('Emergency Contact Phone', validators=[DataRequired("Emergency contact phone number is required.")])
    
    password = PasswordField('Password', validators=[
        DataRequired("Password is required."),
        password_strength_check
    ])
    confirm_password = PasswordField('Confirm Password', validators=[
        DataRequired("Please confirm your password."),
        EqualTo('password', message='Passwords must match.')
    ])
    waiver_agree = BooleanField('I agree to the Waiver', validators=[DataRequired("You must agree to the waiver to register.")])



class ProfileUpdateForm(FlaskForm):
    first_name = StringField('First Name', validators=[Optional()])
    last_name = StringField('Last Name', validators=[Optional()])
    profile_image = FileField('Profile Image', validators=[
        Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')
    ])
    
    current_password = PasswordField('Current Password', validators=[Optional()])
    new_password = PasswordField('New Password', validators=[Optional(), password_strength_check])
    confirm_password = PasswordField('Confirm Password', validators=[
        Optional(), EqualTo('new_password', message='Passwords must match')
    ])