#Models are used for creating classes that are used repeatedly 
# to populate databases.

#UUID stands for universally unique identifiers. 
#These are great for creating independent items that won't clash with other items
import uuid 

#imports date and time
from datetime import datetime, date


#Werkzeug is a security package. This allows us to make the password data that we store in our 
#database secret, so that if we log in to look at our database, 
#we can't see what users saved as their password!
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from flask_login import LoginManager
import secrets
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Text


db = SQLAlchemy()

# set variables for class instantiation
login_manager = LoginManager()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

class User(UserMixin, db.Model):
    id = db.Column(db.String(36), primary_key=True) 
    first_name=db.Column(db.String(150), nullable=False)
    last_name=db.Column(db.String(150), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    profile_image = db.Column(db.String(255), nullable=True)  
    role=db.Column(db.String(10), nullable=False, default='user')
    password_hash = db.Column(db.String(255))
    token = db.Column(db.String(50))
    g_auth_verify = db.Column(db.Boolean, default=False, nullable=False)
    address = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow) 


    @property
    def event_credits(self):
        """
        Calculates total available credits dynamically from the ledger.
        This is the Single Source of Truth.
        """
        today = date.today()
        # Sum balances of all grants that are not expired
        total = db.session.query(db.func.sum(CreditGrant.balance)).filter(
            CreditGrant.user_id == self.id,
            CreditGrant.balance > 0,
            CreditGrant.expiry_date >= today
        ).scalar()
        return total or 0

    @property
    def next_credit_expiry(self):
        """Returns the date of the credit that expires soonest."""
        today = date.today()
        next_grant = CreditGrant.query.filter(
            CreditGrant.user_id == self.id,
            CreditGrant.balance > 0,
            CreditGrant.expiry_date >= today
        ).order_by(CreditGrant.expiry_date.asc()).first()
        
        if next_grant:
            return next_grant.expiry_date.strftime('%b %d, %Y')
        return None

    def spend_credits(self, amount_needed):
        """
        FIFO Logic: Deducts credits from grants expiring soonest.
        Returns True if successful, False if insufficient funds.
        """
        today = date.today()
        
        # 1. Check total balance first
        if self.event_credits < amount_needed:
            return False

        # 2. Get active grants sorted by expiry (Soonest first)
        active_grants = CreditGrant.query.filter(
            CreditGrant.user_id == self.id,
            CreditGrant.balance > 0,
            CreditGrant.expiry_date >= today
        ).order_by(CreditGrant.expiry_date.asc()).all()

        remaining_to_pay = amount_needed

        for grant in active_grants:
            if remaining_to_pay <= 0:
                break
            
            if grant.balance >= remaining_to_pay:
                # This grant covers the rest
                grant.balance -= remaining_to_pay
                remaining_to_pay = 0
            else:
                # Take all from this grant and move to next
                remaining_to_pay -= grant.balance
                grant.balance = 0
        
        # If we successfully deducted everything
        if remaining_to_pay == 0:
            db.session.commit()
            return True
        else:
            db.session.rollback() # Should not happen if step 1 passed
            return False

    subscriptions = db.relationship('Subscription', back_populates='user', lazy=True, cascade="all, delete-orphan")


    def __init__(self, email, first_name, last_name, role, password='', token='', g_auth_verify=False):
        self.id = self.set_id() 
        self.email = email
        self.set_password(password)
        self.first_name = first_name
        self.last_name = last_name
        self.role = role
        self.token = self.set_token(24)


    def set_token(self, length):
        return secrets.token_hex(length)

    def set_id(self):
        return str(uuid.uuid4())

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'User {self.email} has been added to the database'
    



class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(Text, nullable=False)
    image_filename = db.Column(db.String(255))
    location = db.Column(db.String(100), nullable=False)
    full_address = db.Column(db.String(255), nullable=True)
    date = db.Column(db.Date, nullable=False) 
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    max_capacity = db.Column(db.Integer, nullable=False, default=28)
    rsvp_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(20), default='active') 
    cancellation_reason = db.Column(db.String(255), nullable=True)  # New field for cancellation reason
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    ticket_price = db.Column(db.Float, nullable=False, default=10.0)
    allow_guests = db.Column(db.Boolean, default=False)
    guest_limit = db.Column(db.Integer, nullable=True)
    attendees = db.relationship('EventAttendee', back_populates='event', cascade="all, delete-orphan")

    
    def __repr__(self):
        return f'<Event {self.title}>'
    


class EventAttendee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=False)
    
    # Allow user_id to be nullable for guests
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=True) 
    
    # Add Guest specific fields
    first_name = db.Column(db.String(150), nullable=True)
    last_name = db.Column(db.String(150), nullable=True)
    email = db.Column(db.String(150), nullable=True)
    waiver_pdf = db.Column(db.String(255), nullable=True) # To store the filename of the signed waiver

    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    guest_count = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship('User', backref='attendances')
    event = db.relationship('Event', back_populates='attendees')


# Add to models.py

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    paypal_subscription_id = db.Column(db.String(255), unique=True, nullable=False)
    tier = db.Column(db.Integer, nullable=False)  # e.g., 1 or 2
    credits_per_month = db.Column(db.Integer, nullable=False) # e.g., 4 or 8
    status = db.Column(db.String(50), default='active', nullable=False)  # 'active' or 'canceled'
    expiry_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', back_populates='subscriptions')

    def __repr__(self):
        return f'<Subscription {self.id} - Tier {self.tier} for User {self.user_id}>'



class CreditGrant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    
    # "balance" allows us to deduct partially from a grant (e.g. Sub gives 4, user uses 1, balance becomes 3)
    balance = db.Column(db.Integer, nullable=False, default=1) 

    # Labels: 'promo' (free first event), 'subscription', 'cancellation', 'rsvp change 48+ hrs in advance'
    source_type = db.Column(db.String(50), nullable=False) 
    
    # Optional: keeps track of original reason e.g. "Nov Subscription" or "Canceled Game 11/12"
    description = db.Column(db.String(255), nullable=True) 
    
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    expiry_date = db.Column(db.Date, nullable=False)

    user = db.relationship('User', backref='credit_grants')