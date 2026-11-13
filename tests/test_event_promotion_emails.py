"""Promotion notifications and preservation of subscription credit balances."""
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch
import test_event_free_spots as fixtures
from app.models import (db, User, CreditGrant, CreditTransaction, EventAttendee,
                        EventPromotionEmail, EventPromotionClaim)
from app.event_notifications import deliver_promotion_emails
from app.subscription_credits import credit_day


class PromotionEmailTests(unittest.TestCase):
    setUp = fixtures.FreeSpotsTests.setUp
    tearDown = fixtures.FreeSpotsTests.tearDown
    login = fixtures.FreeSpotsTests.login
    free = fixtures.FreeSpotsTests.free
    old_paid = fixtures.FreeSpotsTests.old_paid
    enable_promotion = fixtures.FreeSpotsTests.enable_promotion

    def subscription_grant(self, user_id='one'):
        grant = CreditGrant(user_id=user_id, balance=4, source_type='subscription',
                            expiry_date=credit_day()+timedelta(days=30))
        db.session.add(grant)
        db.session.commit()
        return grant

    def test_quota_and_full_capacity_free_primary_never_spend_subscription_credit(self):
        for limit, user_id in [(1, 'one'), (self.event.max_capacity, 'two')]:
            with self.subTest(limit=limit):
                self.event.free_spots = limit
                grant = self.subscription_grant(user_id)
                self.assertEqual(self.free(user_id).status_code, 200)
                self.assertEqual(grant.balance, 4)
                self.assertEqual(db.session.get(User, user_id).event_credits, 4)
        self.assertEqual(CreditTransaction.query.filter_by(transaction_type='spent').count(), 0)

    def test_free_primary_with_paid_guests_does_not_spend_subscription_credit(self):
        grant = self.subscription_grant()
        self.login('one')
        with patch('app.views.get_access_token', return_value='test'), patch('app.views.requests.post') as post:
            post.return_value = Mock(json=lambda: {'id': 'FREE-WITH-GUESTS'})
            response = self.client.post('/api/orders', json=dict(event_id=self.event.id, quantity=3, credits=1))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(post.call_args.kwargs['json']['purchase_units'][0]['amount']['value'], '24.00')
            post.return_value = Mock(json=lambda: {'status': 'COMPLETED', 'purchase_units': [{
                'payments': {'captures': [{'id': 'GUESTS-CAP', 'amount': {'value': '24.00', 'currency_code': 'USD'}}]}}]})
            result = self.client.post('/api/orders/FREE-WITH-GUESTS/capture', json=dict(event_id=self.event.id, guest_count=2, credits=1))
            self.assertEqual(result.status_code, 200)
        self.assertEqual(grant.balance, 4)
        self.assertEqual(CreditTransaction.query.filter_by(transaction_type='spent').count(), 0)

    def test_credit_endpoint_cannot_spend_subscription_on_available_free_primary(self):
        grant = self.subscription_grant()
        self.login('one')
        response = self.client.post('/api/rsvp/credit', json=dict(event_id=self.event.id))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(grant.balance, 4)
        self.assertEqual(EventAttendee.query.count(), 0)

    def test_legacy_zero_price_event_cannot_spend_credit_after_prior_redemption(self):
        from app.event_pricing import quote
        grant = self.subscription_grant()
        self.event.ticket_price = 0
        db.session.add(EventPromotionClaim(event_id=self.event.id, email='one@example.test',
                                          user_id='one', benefit='free'))
        db.session.commit()
        self.login('one')
        self.assertEqual(quote(self.event, 1, db.session.get(User, 'one'))['credits'], 0)
        result = self.client.post('/api/rsvp/credit', json=dict(event_id=self.event.id))
        self.assertEqual(result.status_code, 400)
        self.assertEqual(grant.balance, 4)

    def test_promotion_email_matches_one_primary_credit_and_expiry_in_profile(self):
        self.old_paid('one', guests=2)
        self.enable_promotion(2)
        self.promotion_mail.assert_called_once()
        message = self.promotion_mail.call_args.args[0]
        grant = CreditGrant.query.filter_by(source_type='event_promotion').one()
        self.assertEqual(message.recipients, ['one@example.test'])
        for text in ['Promotion', 'now FREE', '1 Event Credit for your own spot',
                     'plus-ones remain paid', 'Profile page', 'another paid event', 'valid for 30 days',
                     grant.expiry_date.strftime('%B %d, %Y'), 'still confirmed']:
            self.assertIn(text, message.body)
        self.assertEqual(grant.balance, 1)
        self.assertEqual(grant.expiry_date, credit_day()+timedelta(days=30))
        notification = EventPromotionEmail.query.one()
        self.assertEqual(notification.grant_id, grant.id)
        self.assertIsNotNone(notification.sent_at)
        self.login('one')
        profile = self.client.get('/profile')
        self.assertEqual(profile.status_code, 200)
        self.assertIn(b'Event promotion', profile.data)
        self.assertIn(grant.description.encode(), profile.data)
        self.assertIn(grant.expiry_date.strftime('%b %d, %Y').encode(), profile.data)

    def test_repeated_admin_edits_do_not_duplicate_credits_or_emails(self):
        self.old_paid('one')
        self.enable_promotion(2)
        self.enable_promotion(2)
        self.assertEqual(CreditGrant.query.count(), 1)
        self.assertEqual(EventPromotionEmail.query.count(), 1)
        self.promotion_mail.assert_called_once()

    def test_email_failure_preserves_credit_then_retry_only_sends_email(self):
        self.old_paid('one')
        self.promotion_mail.side_effect = RuntimeError('mail unavailable')
        self.enable_promotion(2)
        notification = EventPromotionEmail.query.one()
        self.assertIsNone(notification.sent_at)
        self.assertEqual(notification.attempts, 1)
        self.assertEqual(CreditGrant.query.one().balance, 1)
        self.promotion_mail.side_effect = None
        result = deliver_promotion_emails()
        self.assertEqual(result, dict(sent=1, failed=0))
        self.assertIsNotNone(notification.sent_at)
        self.assertEqual(CreditGrant.query.count(), 1)
        self.assertEqual(EventPromotionClaim.query.count(), 1)
        self.assertEqual(deliver_promotion_emails(), dict(sent=0, failed=0))

    def test_retry_email_keeps_original_award_after_credit_was_spent(self):
        self.old_paid('one')
        self.promotion_mail.side_effect = RuntimeError('mail unavailable')
        self.enable_promotion(2)
        CreditGrant.query.one().balance = 0
        db.session.commit()
        self.promotion_mail.side_effect = None
        deliver_promotion_emails()
        self.assertIn('1 Event Credit for your own spot', self.promotion_mail.call_args.args[0].body)
        self.assertEqual(CreditGrant.query.one().balance, 0)

    def test_failed_event_save_sends_no_email_and_issues_no_credit(self):
        self.old_paid('one')
        self.login('admin')
        with patch.object(db.session, 'commit', side_effect=RuntimeError('database unavailable')):
            result = self.client.post(f'/events/edit/{self.event.id}', data={
                'pricing_type': 'limited', 'free_spots': '2', 'ticket_price': '12'})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(EventPromotionEmail.query.count(), 0)
        self.promotion_mail.assert_not_called()

    def test_guests_and_nonqualifying_paid_members_receive_no_promotion_email(self):
        guest = self.old_paid(None)
        guest.email = 'guest@example.test'
        db.session.commit()
        self.old_paid('one', offset=1)
        self.enable_promotion(1)
        self.assertEqual(CreditGrant.query.count(), 0)
        self.assertEqual(EventPromotionEmail.query.count(), 0)
        self.promotion_mail.assert_not_called()

    def test_retry_command_sends_pending_email_without_awarding_credit_again(self):
        self.old_paid('one')
        self.promotion_mail.side_effect = RuntimeError('mail unavailable')
        self.enable_promotion(2)
        self.promotion_mail.side_effect = None
        result = self.app.test_cli_runner().invoke(args=['credits', 'send-event-promotion-emails'])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn('"sent": 1', result.output)
        self.assertEqual(CreditGrant.query.count(), 1)
