"""Event promotion integration checks; isolated SQLite and mocked PayPal/email."""
import os
import unittest
from datetime import datetime, timedelta, time
from decimal import Decimal
from unittest.mock import patch, Mock

os.environ['DATABASE_URI'] = 'sqlite:///:memory:'
import werkzeug
if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3.1.2'

from flask import g
from app import create_app
from app.models import db, User, Event, EventAttendee, EventCheckout, CreditGrant, CreditTransaction, EventPromotionClaim
from app.event_pricing import quote, free_spots_remaining


class FreeSpotsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config.update(TESTING=True, SECRET_KEY='test', MAIL_SUPPRESS_SEND=True)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        for name in ['admin', 'one', 'two', 'three']:
            user = User(f'{name}@example.test', name, 'Test', 'admin' if name == 'admin' else 'user')
            user.id = name
            db.session.add(user)
        self.event = Event(title='Promotion', description='Test', location='Gym',
                           date=(datetime.now()+timedelta(days=7)).date(),
                           start_time=time(18), end_time=time(20), ticket_price=12,
                           max_capacity=5, free_spots=2, allow_guests=True, guest_limit=4)
        db.session.add(self.event)
        db.session.commit()
        self.client = self.app.test_client()
        self.email = patch('app.views.send_rsvp_confirmation_email').start()
        self.guest_email = patch('app.views.send_guest_confirmation_email').start()
        self.promotion_mail = patch('app.event_notifications.mail.send').start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self, name):
        with self.client.session_transaction() as session:
            session['_user_id'] = name
            session['_fresh'] = True
            session['role'] = 'admin' if name == 'admin' else 'user'
        g.pop('_login_user', None)

    def free(self, name='one', guests=0):
        self.login(name)
        return self.client.post('/api/rsvp/free', json={'event_id': self.event.id, 'guest_count': guests})

    def test_free_limit_and_guests_then_paid(self):
        self.assertEqual(self.free(guests=1).status_code, 409)
        self.assertEqual(self.free().status_code, 200)
        self.assertEqual(self.free('two').status_code, 200)
        self.assertEqual(self.free('three').status_code, 409)
        self.assertEqual(quote(self.event, 1, email='other@example.test')['amount'], Decimal('12.00'))


    def test_mixed_booking_charges_only_overflow(self):
        pricing = quote(self.event, 4, email='guest@example.test')
        self.assertEqual(pricing, dict(free_spots=1, credits=0, amount=Decimal('36.00')))

    def test_promo_does_not_spend_member_credit(self):
        db.session.add(CreditGrant(user_id='one', balance=1, source_type='test',
                                  expiry_date=self.event.date))
        db.session.commit()
        self.assertEqual(self.free().status_code, 200)
        self.assertEqual(db.session.get(User, 'one').event_credits, 1)
        self.assertEqual(quote(self.event, 3, db.session.get(User, 'two'))['credits'], 0)

    def test_cancel_free_booking_reopens_quota_without_credit(self):
        self.free()
        response = self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['credits_issued'], 0)
        self.assertEqual(free_spots_remaining(self.event), 2)

    def test_cancel_mixed_booking_returns_only_paid_spots(self):
        db.session.add(EventAttendee(event_id=self.event.id, user_id='one', guest_count=3, free_spots=2))
        db.session.commit()
        self.login('one')
        policy = self.client.get(f'/api/rsvp/cancel-policy/{self.event.id}')
        self.assertEqual(policy.json['credits_to_issue'], 2)
        response = self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(response.json['credits_issued'], 2)

    def test_admin_can_enable_promotion_on_booked_event(self):
        db.session.add(EventAttendee(event_id=self.event.id, user_id='one'))
        db.session.commit()
        self.login('admin')
        response = self.client.post(f'/events/edit/{self.event.id}', data={
            'pricing_type': 'limited', 'free_spots': '3', 'ticket_price': '15', 'max_capacity': '5'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.event.free_spots, 3)
        self.assertEqual(self.event.ticket_price, 15)
        self.assertEqual(EventAttendee.query.one().creditable_spots, 0)
        self.assertEqual(db.session.get(User, 'one').event_credits, 1)

    def test_free_full_capacity_and_later_price_change_preserve_booking(self):
        self.login('admin')
        self.client.post(f'/events/edit/{self.event.id}', data={'pricing_type': 'free', 'allow_guests': 'on'})
        self.assertEqual(self.event.ticket_price, 12)
        self.assertEqual(self.free().status_code, 200)
        self.assertEqual(EventAttendee.query.one().free_spots, 1)
        self.login('admin')
        self.client.post(f'/events/edit/{self.event.id}', data={'pricing_type': 'paid', 'ticket_price': '15'})
        self.login('one')
        self.assertEqual(self.client.post(f'/api/rsvp/delete/{self.event.id}').json['credits_issued'], 0)

    def test_invalid_promo_is_rejected_and_non_admin_cannot_edit(self):
        self.login('admin')
        for value in ['-1', '6', '0', '1.5']:
            self.client.post(f'/events/edit/{self.event.id}', data={
                'pricing_type': 'limited', 'free_spots': value, 'ticket_price': '15'})
            self.assertEqual(self.event.free_spots, 2)
            self.assertEqual(self.event.ticket_price, 12)
        self.login('one')
        self.client.post(f'/events/edit/{self.event.id}', data={'pricing_type': 'free'})
        self.assertEqual(self.event.ticket_price, 12)

    def test_quote_endpoint_and_detail_display(self):
        self.login('one')
        response = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': 3})
        self.assertEqual(response.json['total'], '24.00')
        self.assertEqual(response.json['free_spots'], 1)
        response = self.client.get(f'/events/{self.event.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'2 free primary spot(s) left', response.data)

    def test_add_remove_guests_preserves_free_allocation(self):
        self.assertEqual(self.free().status_code, 200)
        result = self.client.post('/api/rsvp/update', json={'event_id': self.event.id, 'new_guest_count': 1})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(EventAttendee.query.one().free_spots, 1)
        result = self.client.post('/api/rsvp/update', json={'event_id': self.event.id, 'new_guest_count': 2})
        self.assertEqual(result.status_code, 400)
        result = self.client.post('/api/rsvp/update', json={'event_id': self.event.id, 'new_guest_count': 0})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(EventAttendee.query.one().free_spots, 1)

    def test_paypal_mixed_capture_and_duplicate_rejected(self):
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'ORDER-1'})
            response = self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 3})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(post.call_args.kwargs['json']['purchase_units'][0]['amount']['value'], '24.00')
            post.return_value = Mock(json=lambda: {'status': 'COMPLETED', 'purchase_units': [{
                'payments': {'captures': [{'id': 'CAP-1', 'amount': {'value': '24.00', 'currency_code': 'USD'}}]}}]})
            response = self.client.post('/api/orders/ORDER-1/capture', json={'event_id': self.event.id, 'guest_count': 2})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(EventAttendee.query.one().free_spots, 1)
            self.assertEqual(EventAttendee.query.one().creditable_spots, 2)
            self.assertTrue(db.session.get(EventCheckout, 'ORDER-1').completed)
            response = self.client.post('/api/orders/ORDER-1/capture', json={'event_id': self.event.id, 'guest_count': 2})
            self.assertEqual(response.status_code, 409)

    def test_quota_race_rejected_before_payment(self):
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'ORDER-1'})
            self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 3})
            self.free('two')
            self.free('three')
            self.login('one')
            post.reset_mock()
            response = self.client.post('/api/orders/ORDER-1/capture', json={'event_id': self.event.id, 'guest_count': 2})
            self.assertEqual(response.status_code, 409)
            post.assert_not_called()

    def guest_info(self):
        return dict(first_name='Guest', last_name='Test', email='guest@example.test',
                    address='123 Main', dob='1990-01-01', emergency_contact_name='Test',
                    emergency_contact_phone='5551234567', signature_data='data:image/png;base64,dGVzdA==')

    def test_guest_free_booking_counts_guests_and_enforces_limit(self):
        with patch('app.views.generate_detailed_waiver'):
            response = self.client.post('/api/rsvp/free/guest', json={
                'event_id': self.event.id, 'guest_count': 0, 'guest_info': self.guest_info()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(EventAttendee.query.one().free_spots, 1)
            guest = dict(self.guest_info(), email=' GUEST@EXAMPLE.TEST ')
            response = self.client.post('/api/rsvp/free/guest', json={
                'event_id': self.event.id, 'guest_count': 0, 'guest_info': guest})
            self.assertEqual(response.status_code, 409)

    def test_guest_mixed_payment_and_member_credit_after_quota_exhausted(self):
        with patch('app.views.generate_detailed_waiver'), patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'GUEST-ORDER'})
            response = self.client.post('/api/orders', json={
                'event_id': self.event.id, 'quantity': 3, 'is_guest_checkout': True, 'email': 'guest@example.test'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(db.session.get(EventCheckout, 'GUEST-ORDER').amount, Decimal('24.00'))
            post.return_value = Mock(json=lambda: {'status': 'COMPLETED', 'purchase_units': [{
                'payments': {'captures': [{'id': 'GUEST-CAP', 'amount': {'value': '12.00', 'currency_code': 'USD'}}]}}]})
            response = self.client.post('/api/orders/GUEST-ORDER/capture', json={
                'event_id': self.event.id, 'guest_count': 2, 'is_guest_checkout': True,
                'guest_info': self.guest_info()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(EventAttendee.query.one().free_spots, 1)
        self.free('two')
        db.session.add(CreditGrant(user_id='one', balance=1, source_type='test', expiry_date=self.event.date))
        db.session.commit()
        self.login('one')
        result = self.client.post('/api/rsvp/credit', json={'event_id': self.event.id})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(db.session.get(User, 'one').event_credits, 0)
        self.assertEqual(self.event.rsvp_count, 5)

    def test_event_cancellation_does_not_credit_promotional_spots(self):
        db.session.add_all([
            EventAttendee(event_id=self.event.id, user_id='one', guest_count=1, free_spots=2),
            EventAttendee(event_id=self.event.id, user_id='two', guest_count=1, free_spots=0)])
        db.session.commit()
        self.login('admin')
        with patch('app.views.send_free_event_cancellation_email'), patch('app.views.send_cancellation_credit_email'):
            result = self.client.post(f'/events/cancel/{self.event.id}', json={})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(db.session.get(User, 'one').event_credits, 0)
        self.assertEqual(db.session.get(User, 'two').event_credits, 2)

    def test_capacity_and_tampered_checkout_rejected(self):
        self.login('one')
        result = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': -1})
        self.assertEqual(result.status_code, 400)
        self.free()
        self.login('two')
        result = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': 5})
        self.assertEqual(result.status_code, 400)
        with patch('app.views.requests.post') as post:
            result = self.client.post('/api/orders/nonexistent/capture', json={'event_id': self.event.id})
            self.assertEqual(result.status_code, 409)
            post.assert_not_called()

    def test_paid_booking_with_credit_preserves_existing_policy(self):
        self.event.free_spots = 0
        db.session.add(CreditGrant(user_id='one', balance=1, source_type='test', expiry_date=self.event.date))
        db.session.commit()
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'CREDIT-ORDER'})
            response = self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 2})
            self.assertEqual(response.status_code, 200)
            snapshot = db.session.get(EventCheckout, 'CREDIT-ORDER')
            self.assertEqual((snapshot.credits, snapshot.amount), (1, Decimal('12.00')))
            post.return_value = Mock(json=lambda: {'status': 'COMPLETED', 'purchase_units': [{
                'payments': {'captures': [{'id': 'CREDIT-CAP', 'amount': {'value': '12.00', 'currency_code': 'USD'}}]}}]})
            response = self.client.post('/api/orders/CREDIT-ORDER/capture', json={'event_id': self.event.id, 'guest_count': 1})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(db.session.get(User, 'one').event_credits, 0)
            self.assertEqual(EventAttendee.query.one().creditable_spots, 2)

    def test_paid_guest_addition_can_cross_free_limit(self):
        self.free()
        rsvp = EventAttendee.query.one()
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'EDIT-ORDER'})
            response = self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 3,
                'is_edit': True, 'rsvp_id': rsvp.id, 'initial_guest_count': 999})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(db.session.get(EventCheckout, 'EDIT-ORDER').amount, Decimal('24.00'))
            post.return_value = Mock(json=lambda: {'status': 'COMPLETED', 'purchase_units': [{
                'payments': {'captures': [{'id': 'EDIT-CAP', 'amount': {'value': '12.00', 'currency_code': 'USD'}}]}}]})
            response = self.client.post('/api/orders/EDIT-ORDER/capture', json={'event_id': self.event.id,
                'guest_count': 2, 'is_edit': True, 'rsvp_id': rsvp.id})
            self.assertEqual(response.status_code, 200)
            self.assertEqual((rsvp.free_spots, rsvp.guest_count), (1, 2))

    def test_promotion_display_cannot_exceed_remaining_capacity(self):
        db.session.add(EventAttendee(event_id=self.event.id, user_id='one', guest_count=3))
        db.session.commit()
        self.assertEqual(free_spots_remaining(self.event), 1)

    def test_create_single_and_recurring_promotions(self):
        self.login('admin')
        form = dict(title='New event', description='Test', location='Gym',
                    date=self.event.date.isoformat(), start_time='18:00', end_time='20:00',
                    pricing_type='limited', free_spots='3', ticket_price='15', max_capacity='10')
        response = self.client.post('/events', data=form)
        self.assertEqual(response.status_code, 302)
        created = Event.query.filter_by(title='New event').one()
        self.assertEqual((created.free_spots, created.ticket_price), (3, 15))
        form.update(title='Recurring', recurring_start_date=self.event.date.isoformat(),
                    recurring_end_date=self.event.date.isoformat(), weekdays=[str(self.event.date.weekday())])
        response = self.client.post('/events/add_recurring', data=form)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Event.query.filter_by(title='Recurring').one().free_spots, 3)

    def test_cancel_rebook_cannot_redeem_twice_even_after_email_change(self):
        self.assertEqual(self.free().status_code, 200)
        self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(self.free().status_code, 409)
        db.session.get(User, 'one').email = 'changed@example.test'
        db.session.commit()
        self.assertEqual(self.free().status_code, 409)
        self.assertEqual(self.free('two').status_code, 200)

    def test_member_guest_email_share_one_promotion(self):
        self.free()
        guest = dict(self.guest_info(), email=' ONE@EXAMPLE.TEST ')
        with patch('app.views.generate_detailed_waiver'):
            result = self.client.post('/api/rsvp/free/guest', json={
                'event_id': self.event.id, 'guest_info': guest})
        self.assertEqual(result.status_code, 409)
        self.assertEqual(quote(self.event, 3, email=guest['email'])['amount'], Decimal('36.00'))

    def test_full_capacity_promotion_keeps_plus_ones_paid(self):
        self.login('admin')
        self.client.post(f'/events/edit/{self.event.id}', data={
            'pricing_type': 'free', 'ticket_price': '15', 'allow_guests': 'on'})
        self.assertEqual(self.event.free_spots, self.event.max_capacity)
        self.assertEqual(quote(self.event, 3, email='guest@example.test')['amount'], Decimal('30.00'))

    def enable_promotion(self, count):
        self.login('admin')
        return self.client.post(f'/events/edit/{self.event.id}', data={
            'pricing_type': 'limited', 'free_spots': str(count), 'ticket_price': '12',
            'allow_guests': 'on', 'guest_limit': '4'})

    def old_paid(self, user_id, guests=0, offset=0):
        attendee = EventAttendee(event_id=self.event.id, user_id=user_id, guest_count=guests,
            timestamp=datetime.now() - timedelta(hours=2) + timedelta(seconds=offset))
        db.session.add(attendee)
        db.session.commit()
        return attendee

    def test_first_x_includes_old_paid_primaries_with_30_day_credit(self):
        from app.subscription_credits import credit_day
        first = self.old_paid('one', guests=2)
        self.old_paid('two', offset=1)
        self.assertEqual(self.enable_promotion(3).status_code, 302)
        self.assertTrue(first.promotion_compensated)
        self.assertEqual(first.creditable_spots, 2)
        grants = CreditGrant.query.filter_by(source_type='event_promotion').all()
        self.assertEqual(len(grants), 2)
        self.assertTrue(all(g.balance == 1 and g.expiry_date == credit_day()+timedelta(days=30) for g in grants))
        self.assertEqual(CreditTransaction.query.filter_by(transaction_type='event_promotion').count(), 2)
        self.assertEqual(free_spots_remaining(self.event), 1)
        self.assertEqual(self.free('three').status_code, 200)
        self.assertEqual(free_spots_remaining(self.event), 0)

    def test_only_first_x_old_paid_receive_credit(self):
        self.old_paid('one')
        self.old_paid('two', offset=1)
        self.old_paid('three', offset=2)
        self.enable_promotion(2)
        self.assertEqual(db.session.get(User, 'one').event_credits, 1)
        self.assertEqual(db.session.get(User, 'two').event_credits, 1)
        self.assertEqual(db.session.get(User, 'three').event_credits, 0)
        self.assertEqual(free_spots_remaining(self.event), 0)

    def test_repeat_save_toggle_and_quota_increase_do_not_duplicate_credits(self):
        self.old_paid('one')
        self.old_paid('two', offset=1)
        self.enable_promotion(1)
        self.enable_promotion(1)
        self.client.post(f'/events/edit/{self.event.id}', data={'pricing_type': 'paid', 'ticket_price': '12'})
        self.enable_promotion(1)
        self.assertEqual(CreditGrant.query.count(), 1)
        self.enable_promotion(2)
        self.assertEqual(CreditGrant.query.count(), 2)
        self.assertEqual(db.session.get(User, 'one').event_credits, 1)

    def test_compensated_primary_cancellation_only_credits_plus_ones(self):
        self.old_paid('one', guests=2)
        self.enable_promotion(1)
        self.login('one')
        policy = self.client.get(f'/api/rsvp/cancel-policy/{self.event.id}')
        self.assertEqual(policy.json['credits_to_issue'], 2)
        result = self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(result.json['credits_issued'], 2)
        self.assertEqual(db.session.get(User, 'one').event_credits, 3)
        self.assertEqual(self.free('one').status_code, 409)

    def test_guest_prior_payment_requires_only_manual_refund(self):
        attendee = self.old_paid(None, guests=1)
        attendee.email = 'guest@example.test'
        db.session.commit()
        response = self.enable_promotion(2)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(attendee.promotion_compensated)
        self.assertEqual(EventPromotionClaim.query.one().benefit, 'manual_refund')
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(free_spots_remaining(self.event), 1)

    def test_free_primary_cannot_use_paypal_or_credit_and_plus_ones_are_paid(self):
        self.login('one')
        db.session.add(CreditGrant(user_id='one', balance=1, source_type='test', expiry_date=self.event.date))
        db.session.commit()
        with patch('app.views.requests.post') as post:
            response = self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 1})
            self.assertEqual(response.status_code, 400)
            post.assert_not_called()
        response = self.client.post('/api/rsvp/credit', json={'event_id': self.event.id})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': 3})
        self.assertEqual((response.json['total'], response.json['free_spots'], response.json['credits']), ('24.00', 1, 0))
        self.assertEqual(db.session.get(User, 'one').event_credits, 1)

    def test_pending_paid_checkout_cannot_capture_when_promotion_enabled(self):
        self.event.free_spots = 0
        db.session.commit()
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'PENDING'})
            self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 1})
            self.enable_promotion(2)
            self.login('one')
            post.reset_mock()
            response = self.client.post('/api/orders/PENDING/capture', json={'event_id': self.event.id})
            self.assertEqual(response.status_code, 409)
            post.assert_not_called()

    def test_invalid_admin_edit_issues_no_credit(self):
        attendee = self.old_paid('one')
        self.enable_promotion(99)
        self.assertFalse(attendee.promotion_compensated)
        self.assertEqual(CreditGrant.query.count(), 0)

    def test_admin_controls_plus_ones_for_free_promotions(self):
        self.login('admin')
        self.client.post(f'/events/edit/{self.event.id}', data={
            'pricing_type': 'free', 'ticket_price': '12'})
        self.assertFalse(self.event.allow_guests)
        self.assertEqual(self.event.guest_limit, 0)
        self.login('one')
        result = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': 2})
        self.assertEqual(result.status_code, 400)
        guest = self.client.post(f'/api/events/{self.event.id}/quote', json={
            'quantity': 2, 'is_guest_checkout': True, 'email': 'guest@example.test'})
        self.assertEqual(guest.status_code, 400)
        self.login('admin')
        self.client.post(f'/events/edit/{self.event.id}', data={
            'pricing_type': 'free', 'ticket_price': '12', 'allow_guests': 'on', 'guest_limit': '2'})
        self.login('one')
        result = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': 3})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['total'], '24.00')
        result = self.client.post(f'/api/events/{self.event.id}/quote', json={'quantity': 4})
        self.assertEqual(result.status_code, 400)

    def test_new_single_and_recurring_events_save_plus_one_policy(self):
        self.login('admin')
        form = dict(title='Plus one policy', description='Test', location='Gym',
                    date=self.event.date.isoformat(), start_time='18:00', end_time='20:00',
                    pricing_type='free', ticket_price='15', max_capacity='10',
                    allow_guests='on', guest_limit='2')
        self.client.post('/events', data=form)
        created = Event.query.filter_by(title='Plus one policy').one()
        self.assertEqual((created.allow_guests, created.guest_limit, created.free_spots), (True, 2, 10))
        form.update(title='Recurring policy', recurring_start_date=self.event.date.isoformat(),
                    recurring_end_date=self.event.date.isoformat(), weekdays=[str(self.event.date.weekday())])
        self.assertEqual(self.client.post('/events/add_recurring', data=form).status_code, 200)
        created = Event.query.filter_by(title='Recurring policy').one()
        self.assertEqual((created.allow_guests, created.guest_limit), (True, 2))

    def test_zero_or_negative_enabled_plus_one_limits_are_rejected(self):
        self.login('admin')
        for count in ['0', '-1', '1.5']:
            self.client.post(f'/events/edit/{self.event.id}', data={
                'pricing_type': 'free', 'ticket_price': '12', 'allow_guests': 'on', 'guest_limit': count})
            self.assertEqual(self.event.guest_limit, 4)

    def test_regular_payment_after_quota_exhausted_earns_no_promotion_credit(self):
        self.free('one')
        self.free('two')
        self.login('three')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'AFTER-QUOTA'})
            response = self.client.post('/api/orders', json={'event_id': self.event.id, 'quantity': 1})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(db.session.get(EventCheckout, 'AFTER-QUOTA').amount, Decimal('12.00'))
            post.return_value = Mock(json=lambda: {'status': 'COMPLETED', 'purchase_units': [{
                'payments': {'captures': [{'id': 'AFTER-QUOTA-CAP', 'amount': {'value': '12.00', 'currency_code': 'USD'}}]}}]})
            result = self.client.post('/api/orders/AFTER-QUOTA/capture', json={'event_id': self.event.id})
            self.assertEqual(result.status_code, 200)
        self.assertEqual(db.session.get(User, 'three').event_credits, 0)
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(EventPromotionClaim.query.count(), 2)


class FreeSpotsMigrationTests(unittest.TestCase):
    def test_upgrade_backfills_only_old_free_bookings_and_downgrades(self):
        import importlib.util
        from pathlib import Path
        import sqlalchemy as sa
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path = Path(__file__).resolve().parents[1] / 'migrations/versions/20261111_event_free_spots.py'
        spec = importlib.util.spec_from_file_location('free_spots_migration', path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = sa.create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(sa.text('CREATE TABLE event (id INTEGER PRIMARY KEY, ticket_price FLOAT)'))
            connection.execute(sa.text('CREATE TABLE event_attendee (id INTEGER PRIMARY KEY, event_id INTEGER, guest_count INTEGER, user_id VARCHAR(36), email VARCHAR(150))'))
            connection.execute(sa.text('CREATE TABLE user (id VARCHAR(36) PRIMARY KEY, email VARCHAR(150))'))
            connection.execute(sa.text("INSERT INTO user VALUES ('one', ' ONE@example.test ')"))
            connection.execute(sa.text('INSERT INTO event VALUES (1, 0), (2, 12)'))
            connection.execute(sa.text("INSERT INTO event_attendee VALUES (1, 1, 2, 'one', NULL), (2, 2, 1, 'one', NULL)"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(connection.execute(sa.text('SELECT free_spots FROM event_attendee ORDER BY id')).scalars().all(), [3, 0])
                self.assertIn('event_checkout', sa.inspect(connection).get_table_names())
                claims_spec = importlib.util.spec_from_file_location('claims_migration', path.with_name('20261111_event_promotion_claims.py'))
                claims = importlib.util.module_from_spec(claims_spec)
                claims_spec.loader.exec_module(claims)
                claims.upgrade()
                self.assertEqual(connection.execute(sa.text('SELECT email FROM event_promotion_claim')).scalar(), 'one@example.test')
                credit_spec = importlib.util.spec_from_file_location('credit_migration', path.with_name('20261111_promotion_credits.py'))
                credit_migration = importlib.util.module_from_spec(credit_spec)
                credit_spec.loader.exec_module(credit_migration)
                credit_migration.upgrade()
                self.assertEqual(connection.execute(sa.text('SELECT promotion_compensated FROM event_attendee ORDER BY id')).scalars().all(), [0, 0])
                connection.execute(sa.text('CREATE TABLE credit_grant (id INTEGER PRIMARY KEY)'))
                email_spec = importlib.util.spec_from_file_location('email_migration', path.with_name('20261111_promotion_emails.py'))
                email_migration = importlib.util.module_from_spec(email_spec)
                email_spec.loader.exec_module(email_migration)
                email_migration.upgrade()
                self.assertIn('event_promotion_email', sa.inspect(connection).get_table_names())
                email_migration.downgrade()
                self.assertNotIn('event_promotion_email', sa.inspect(connection).get_table_names())
                credit_migration.downgrade()
                claims.downgrade()
                migration.downgrade()
                self.assertNotIn('event_checkout', sa.inspect(connection).get_table_names())
                self.assertNotIn('free_spots', [c['name'] for c in sa.inspect(connection).get_columns('event')])
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
