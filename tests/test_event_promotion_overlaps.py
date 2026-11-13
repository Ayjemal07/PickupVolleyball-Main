"""Cancellation, cutoff, and RSVP-edit overlap regression tests."""
import unittest
from datetime import datetime, timedelta, time
from unittest.mock import patch, Mock

import test_event_free_spots as fixtures
from app.models import db, EventAttendee, CreditGrant, CreditTransaction, User
from app.event_pricing import hours_until_event


class PromotionOverlapTests(unittest.TestCase):
    setUp_base = fixtures.FreeSpotsTests.setUp
    tearDown = fixtures.FreeSpotsTests.tearDown
    login = fixtures.FreeSpotsTests.login
    free = fixtures.FreeSpotsTests.free
    enable_promotion = fixtures.FreeSpotsTests.enable_promotion

    def setUp(self):
        self.setUp_base()
        self.start = datetime(2030, 1, 10, 18)
        self.event.date = self.start.date()
        self.event.start_time = self.start.time()
        db.session.commit()
        self.now = patch('app.views.event_now', return_value=self.start-timedelta(hours=48)).start()
        patch('app.views.send_cancellation_credit_email').start()
        patch('app.views.send_free_event_cancellation_email').start()
        patch('app.views.send_guest_cancellation_only_email').start()

    def booking(self, free=0, compensated=False, guests=2):
        attendee = EventAttendee(event_id=self.event.id, user_id='one', free_spots=free,
            promotion_compensated=compensated, guest_count=guests)
        db.session.add(attendee)
        if compensated:
            db.session.add(CreditGrant(user_id='one', balance=1, source_type='event_promotion',
                                      expiry_date=self.event.date))
        db.session.commit()
        return attendee

    def balance(self):
        return db.session.get(User, 'one').event_credits

    def cancel_admin(self):
        self.login('admin')
        return self.client.post(f'/events/cancel/{self.event.id}', json={})

    def test_admin_cancel_compensated_primary_only_refunds_paid_guests_once(self):
        self.booking(compensated=True)
        result = self.cancel_admin()
        self.assertEqual((result.status_code, result.json['credits_issued']), (200, 2))
        self.assertEqual(self.balance(), 3)
        self.assertEqual(CreditTransaction.query.filter_by(transaction_type='cancellation').one().amount, 2)
        self.assertEqual(self.cancel_admin().status_code, 400)
        self.assertEqual(self.balance(), 3)
        self.login('one')
        self.assertEqual(self.client.post(f'/api/rsvp/delete/{self.event.id}').status_code, 400)
        self.assertEqual(self.balance(), 3)

    def test_admin_cancellation_bypasses_48_hour_cutoff_for_paid_spots(self):
        self.booking(free=1)
        self.now.return_value = self.start-timedelta(minutes=5)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 2)
        self.assertEqual(self.balance(), 2)

    def test_fully_free_booking_admin_cancellation_issues_zero(self):
        self.booking(free=3)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 0)
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(CreditTransaction.query.count(), 0)

    def test_not_going_cutoff_matches_preview_at_exact_second(self):
        attendee = self.booking(free=1)
        self.login('one')
        for seconds, expected in [(48*3600-1, 0), (48*3600, 2), (48*3600+1, 2)]:
            with self.subTest(seconds=seconds):
                self.now.return_value = self.start-timedelta(seconds=seconds)
                policy = self.client.get(f'/api/rsvp/cancel-policy/{self.event.id}')
                self.assertEqual(policy.json['credits_to_issue'], expected)
        # Actually cancel just inside the cutoff, then retry the request.
        self.now.return_value = self.start-timedelta(hours=48, seconds=-1)
        result = self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(result.json['credits_issued'], 0)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.client.post(f'/api/rsvp/delete/{self.event.id}').status_code, 404)

    def test_not_going_at_exactly_48_hours_compensated_primary_not_credited_twice(self):
        self.booking(compensated=True)
        self.login('one')
        result = self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(result.json['credits_issued'], 2)
        self.assertEqual(self.balance(), 3)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 0)
        self.assertEqual(self.balance(), 3)

    def test_not_going_inside_48_hours_keeps_existing_promotion_credit_only(self):
        self.booking(compensated=True)
        self.now.return_value = self.start-timedelta(hours=2)
        self.login('one')
        result = self.client.post(f'/api/rsvp/delete/{self.event.id}')
        self.assertEqual(result.json['credits_issued'], 0)
        self.assertEqual(self.balance(), 1)

    def test_free_primary_not_going_never_awards_credit(self):
        self.booking(free=1, guests=0)
        self.login('one')
        self.assertEqual(self.client.post(f'/api/rsvp/delete/{self.event.id}').json['credits_issued'], 0)
        self.assertEqual(self.balance(), 0)

    def test_guest_reduction_and_admin_cancel_only_credit_each_paid_spot_once(self):
        attendee = self.booking(compensated=True, guests=3)
        self.login('one')
        payload = dict(event_id=self.event.id, new_guest_count=1)
        result = self.client.post('/api/rsvp/update', json=payload)
        self.assertEqual((result.status_code, result.json['credits_issued']), (200, 2))
        self.assertEqual(self.event.rsvp_count, 2)
        self.assertEqual(self.client.post('/api/rsvp/update', json=payload).json['credits_issued'], 0)
        self.assertEqual(self.balance(), 3)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 1)
        self.assertEqual(self.balance(), 4)  # Original primary plus three paid guests.

    def test_late_guest_removal_receives_no_credit_now_or_on_later_admin_cancel(self):
        self.booking(free=1, guests=3)
        self.now.return_value = self.start-timedelta(hours=47)
        self.login('one')
        result = self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=1))
        self.assertEqual(result.json['credits_issued'], 0)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 1)
        self.assertEqual(self.balance(), 1)

    def test_legacy_free_guest_removal_earns_no_credit(self):
        self.booking(free=3, guests=2)
        self.login('one')
        result = self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=0))
        self.assertEqual(result.json['credits_issued'], 0)
        self.assertEqual(EventAttendee.query.one().free_spots, 1)
        self.assertEqual(self.balance(), 0)

    def test_lowered_guest_policy_cannot_block_removing_existing_guests(self):
        self.booking(free=1, guests=3)
        self.event.allow_guests = False
        self.event.guest_limit = 0
        self.event.max_capacity = 2  # Even over-capacity bookings may shrink.
        db.session.commit()
        self.login('one')
        result = self.client.post(f'/api/events/{self.event.id}/quote', json=dict(quantity=3, is_edit=True))
        self.assertEqual(result.status_code, 200)
        result = self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=2))
        self.assertEqual((result.status_code, result.json['credits_issued']), (200, 1))
        result = self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=3))
        self.assertEqual(result.status_code, 400)

    def test_admin_cancel_then_edit_promotion_cannot_issue_second_primary_credit(self):
        self.booking(guests=0)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 1)
        self.enable_promotion(5)
        self.assertEqual(self.balance(), 1)
        self.assertEqual(CreditGrant.query.count(), 1)

    def test_notification_failure_does_not_reissue_admin_credits(self):
        self.booking(guests=0)
        with patch('app.views.send_cancellation_credit_email', side_effect=RuntimeError('email unavailable')):
            self.assertEqual(self.cancel_admin().status_code, 200)
        self.assertEqual(self.cancel_admin().status_code, 400)
        self.assertEqual(self.balance(), 1)

    def test_failed_admin_commit_rolls_back_status_and_credits_together(self):
        self.booking(guests=0)
        with patch.object(db.session, 'commit', side_effect=RuntimeError('database unavailable')):
            self.assertEqual(self.cancel_admin().status_code, 500)
        self.assertEqual(self.event.status, 'active')
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(CreditTransaction.query.count(), 0)
        self.assertEqual(self.cancel_admin().json['credits_issued'], 1)

    def test_canceled_and_started_events_block_policy_and_mutations(self):
        self.booking(free=1)
        self.login('one')
        for status, now in [('canceled', self.start-timedelta(days=3)), ('active', self.start)]:
            with self.subTest(status=status):
                self.event.status = status
                db.session.commit()
                self.now.return_value = now
                self.assertEqual(self.client.get(f'/api/rsvp/cancel-policy/{self.event.id}').status_code, 400)
                self.assertEqual(self.client.post(f'/api/rsvp/delete/{self.event.id}').status_code, 400)
                self.assertEqual(self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=1)).status_code, 400)
        self.assertEqual(CreditGrant.query.count(), 0)

    def test_admin_cancellation_requires_actual_admin_not_stale_session_role(self):
        self.booking(guests=0)
        self.login('one')
        with self.client.session_transaction() as session:
            session['role'] = 'admin'
        self.assertEqual(self.client.post(f'/events/cancel/{self.event.id}', json={}).status_code, 403)
        self.assertEqual(self.event.status, 'active')
        self.assertEqual(CreditGrant.query.count(), 0)

    def test_guest_counts_must_be_whole_numbers_without_credit_side_effects(self):
        self.booking(free=1)
        self.login('one')
        for count in [1.5, True, -1, 'bad']:
            result = self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=count))
            self.assertEqual(result.status_code, 400)
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(EventAttendee.query.one().guest_count, 2)

    def test_cutoff_uses_elapsed_hours_across_portland_daylight_saving(self):
        self.event.date = datetime(2030, 3, 11).date()
        self.event.start_time = time(12)
        # The spring clock change means the same wall time two days earlier is
        # only 47 actual hours before the event.
        self.assertEqual(hours_until_event(self.event, datetime(2030, 3, 9, 12)), 47)
        self.assertEqual(hours_until_event(self.event, datetime(2030, 3, 9, 11)), 48)

    def test_guest_change_preview_matches_48_hour_credit_rule(self):
        self.booking(compensated=True, guests=2)
        self.login('one')
        for hours, expected in [(48, 1), (47, 0)]:
            self.now.return_value = self.start-timedelta(hours=hours)
            preview = self.client.post(f'/api/events/{self.event.id}/quote', json=dict(quantity=2, is_edit=True))
            self.assertEqual(preview.status_code, 200)
            self.assertEqual((preview.json['removed_paid_spots'], preview.json['credits_to_return']), (1, expected))

    def test_canceled_event_cannot_capture_previously_created_guest_payment(self):
        attendee = self.booking(free=1, guests=0)
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'GUEST-ADD'})
            result = self.client.post('/api/orders', json=dict(event_id=self.event.id, quantity=2,
                is_edit=True, rsvp_id=attendee.id))
            self.assertEqual(result.status_code, 200)
            self.cancel_admin()
            self.login('one')
            post.reset_mock()
            result = self.client.post('/api/orders/GUEST-ADD/capture', json=dict(event_id=self.event.id,
                guest_count=1, is_edit=True, rsvp_id=attendee.id))
            self.assertEqual(result.status_code, 400)
            post.assert_not_called()

    def test_guest_removal_invalidates_earlier_paypal_quote_before_capture(self):
        attendee = self.booking(free=1, guests=1)
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'GUEST-ADD'})
            self.client.post('/api/orders', json=dict(event_id=self.event.id, quantity=3,
                is_edit=True, rsvp_id=attendee.id))
            self.client.post('/api/rsvp/update', json=dict(event_id=self.event.id, new_guest_count=0))
            post.reset_mock()
            result = self.client.post('/api/orders/GUEST-ADD/capture', json=dict(event_id=self.event.id,
                guest_count=2, is_edit=True, rsvp_id=attendee.id))
            self.assertEqual(result.status_code, 409)
            post.assert_not_called()
            self.assertEqual(self.balance(), 1)
