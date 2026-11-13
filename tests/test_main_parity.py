"""Main-specific price, data migration, branding, and capacity regressions."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from flask import Flask
from flask_migrate import Migrate, upgrade
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

import test_event_free_spots as fixtures
from app.models import db, EventAttendee, CreditGrant, User
from app.subscription_checkout import sync_one
from app.subscription_credits import plans, iso, utcnow
from app.credit_models import CreditSystemState
from test_subscription_renewal import FakePayPal


class MainBehaviorTests(unittest.TestCase):
    setUp = fixtures.FreeSpotsTests.setUp
    tearDown = fixtures.FreeSpotsTests.tearDown
    login = fixtures.FreeSpotsTests.login

    def test_capacity_cannot_drop_below_members_and_guests(self):
        db.session.add(EventAttendee(event_id=self.event.id, user_id='one', guest_count=2))
        db.session.commit()
        self.login('admin')
        response = self.client.post(f'/events/edit/{self.event.id}', data={
            'max_capacity': '2', 'pricing_type': 'paid', 'ticket_price': '12'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.event.max_capacity, 5)
        self.assertEqual(EventAttendee.query.one().guest_count, 2)
        self.client.post(f'/events/edit/{self.event.id}', data={
            'max_capacity': '3', 'pricing_type': 'paid', 'ticket_price': '12'})
        self.assertEqual(self.event.max_capacity, 3)

    def test_spending_and_rsvp_share_the_callers_transaction(self):
        user = db.session.get(User, 'one')
        db.session.add(CreditGrant(user_id=user.id, balance=1, source_type='test',
                                  expiry_date=self.event.date))
        db.session.commit()
        self.assertTrue(user.spend_credits(1, self.event.title))
        db.session.add(EventAttendee(event_id=self.event.id, user_id=user.id))
        db.session.flush()
        db.session.rollback()
        self.assertEqual(user.event_credits, 1)
        self.assertEqual(EventAttendee.query.count(), 0)

    def test_main_prices_issue_both_tiers_and_reject_pdx_amounts(self):
        self.app.config.update(PAYPAL_PLAN_ID_TIER1='P-MAIN-ONE', PAYPAL_PLAN_ID_TIER2='P-MAIN-TWO')
        self.assertEqual(plans(), {'P-MAIN-ONE': (1, 4, Decimal('35.00')),
                                  'P-MAIN-TWO': (2, 8, Decimal('60.00'))})
        db.session.add(CreditSystemState(key='atomic_issuance_started_at',
            value=iso(utcnow()-timedelta(days=2))))
        db.session.commit()
        for tier, price, wrong_price, credits in [(1, '35.00', '40.00', 4), (2, '60.00', '75.00', 8)]:
            sid = f'I-MAIN-TIER-{tier}'
            plan = 'P-MAIN-ONE' if tier == 1 else 'P-MAIN-TWO'
            when = utcnow()-timedelta(minutes=5)
            details = dict(id=sid, plan_id=plan, status='ACTIVE', custom_id='one',
                           billing_info={'last_payment': {'time': iso(when)}})
            payment = dict(id=f'MAIN-PAYMENT-{tier}', time=iso(when), status='COMPLETED',
                amount_with_breakdown={'gross_amount': {'value': price, 'currency_code': 'USD'}})
            api = FakePayPal(details, [payment])
            self.assertEqual(sync_one(sid, api=api, finish=False)['credits_issued'], credits)
            self.assertEqual(sync_one(sid, api=api, finish=False)['credits_issued'], 0)
            payment['id'] = f'WRONG-PRICE-{tier}'
            payment['amount_with_breakdown']['gross_amount']['value'] = wrong_price
            self.assertEqual(sync_one(sid, api=api, finish=False)['credits_issued'], 0)
        self.assertEqual(db.session.get(User, 'one').event_credits, 12)

    def test_public_pages_and_signup_prices_use_main_wording(self):
        for route in ['/', '/events', '/subscriptions', '/hosting', '/merch', '/merch/cart', '/faq']:
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, route)
            self.assertNotIn(b'Pick Up Volleyball PDX', response.data, route)
            self.assertNotIn(b'pickupvolleyballpdx', response.data, route)
            self.assertIn(b'https://www.instagram.com/pickupvolleyballsalem', response.data, route)
            self.assertIn(b'https://www.facebook.com/share/1KBhKKqgwp/', response.data, route)
            self.assertNotIn(b'facebook.com/groups/623654829419818', response.data, route)
        subscriptions = self.client.get('/subscriptions').get_data(as_text=True)
        for text in ['$35', '$60', '$8.75/ea.', '$7.50/ea.']:
            self.assertIn(text, subscriptions)
        for text in ['$40', '$75', '$9.38/ea.', '$10/ea.']:
            self.assertNotIn(text, subscriptions)


class MainMigrationTests(unittest.TestCase):
    def test_upgrade_existing_main_preserves_data_and_matches_pdx_schema(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='main-migration-', dir=root) as folder:
            app = Flask(__name__)
            app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///' + str(Path(folder)/'test.db'),
                              SQLALCHEMY_TRACK_MODIFICATIONS=False)
            db.init_app(app)
            Migrate(app, db)
            with app.app_context():
                directory = str(root/'migrations')
                upgrade(directory=directory, revision='36bfcce0f503')
                with db.engine.begin() as connection:
                    connection.execute(sa.text("INSERT INTO user (id,first_name,last_name,email,role,g_auth_verify) VALUES ('legacy','Legacy','Member','legacy@example.test','user',0)"))
                    connection.execute(sa.text("INSERT INTO event (id,title,description,location,date,start_time,end_time,max_capacity,ticket_price) VALUES (1,'Existing event','Keep me','Gym','2030-01-10','18:00:00','20:00:00',12,0)"))
                    connection.execute(sa.text("INSERT INTO event_attendee (id,event_id,user_id,guest_count) VALUES (1,1,'legacy',2)"))
                    connection.execute(sa.text("INSERT INTO subscription (id,user_id,paypal_subscription_id,tier,credits_per_month,status,expiry_date) VALUES (1,'legacy','I-LEGACY',1,4,'active','2030-01-01')"))
                    connection.execute(sa.text("INSERT INTO credit_grant (id,user_id,balance,source_type,expiry_date) VALUES (1,'legacy',3,'subscription','2030-01-01')"))
                    connection.execute(sa.text("INSERT INTO payment_transaction (id,user_id,subscription_id,paypal_capture_id,transaction_type,status,amount) VALUES (1,'legacy',1,'OLD-PAID','subscription_payment','completed',35)"))
                upgrade(directory=directory)
                upgrade(directory=directory)
                with db.engine.connect() as connection:
                    changes = compare_metadata(MigrationContext.configure(connection), db.metadata)
                    self.assertEqual(changes, [])
                    self.assertEqual(connection.execute(sa.text('SELECT balance FROM credit_grant WHERE id=1')).scalar_one(), 3)
                    self.assertEqual(connection.execute(sa.text('SELECT paypal_subscription_id FROM subscription WHERE id=1')).scalar_one(), 'I-LEGACY')
                    self.assertEqual(connection.execute(sa.text('SELECT paypal_capture_id FROM payment_transaction WHERE id=1')).scalar_one(), 'OLD-PAID')
                    self.assertEqual(connection.execute(sa.text('SELECT free_spots FROM event_attendee WHERE id=1')).scalar_one(), 3)
                    self.assertEqual(connection.execute(sa.text('SELECT email FROM event_promotion_claim')).scalar_one(), 'legacy@example.test')
                    self.assertEqual(connection.execute(sa.text('SELECT COUNT(*) FROM subscription_credit_receipt')).scalar_one(), 0)
                    self.assertEqual(connection.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one(), '20261111_promotion_emails')
                db.session.remove()
                db.engine.dispose()
