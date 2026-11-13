"""Isolated renewal regression: no live database or PayPal requests."""
import os
import unittest
from datetime import timedelta

from flask import Flask

from app.models import db, User
from app.credit_models import CreditSystemState
from app.subscription_checkout import sync_one
from app.subscription_credits import iso, utcnow


class FakePayPal:
    def __init__(self, details, transactions):
        self.details = details
        self.rows = transactions

    def subscription(self, sid):
        assert sid == self.details['id']
        return self.details

    def transactions(self, sid, start, end):
        assert sid == self.details['id']
        return self.rows


def payment(pid, when):
    return {'id': pid, 'time': iso(when), 'status': 'COMPLETED',
            'amount_with_breakdown': {'gross_amount': {'value': '35.00', 'currency_code': 'USD'}}}


class RenewalReconciliationTest(unittest.TestCase):
    def test_old_receipt_does_not_confirm_unlisted_renewal(self):
        app = Flask(__name__)
        app.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
                          PAYPAL_PLAN_ID_TIER1='P-TEST-ONE',
                          PAYPAL_PLAN_ID_TIER2='P-TEST-TWO')
        db.init_app(app)
        with app.app_context():
            db.create_all()
            member = User('member@example.test', 'Test', 'Member', 'user')
            db.session.add_all([member, CreditSystemState(
                key='atomic_issuance_started_at', value=iso(utcnow()-timedelta(days=60)))])
            db.session.commit()
            sid = 'I-TESTRENEWAL'
            previous = utcnow()-timedelta(days=29)
            current = utcnow()-timedelta(minutes=10)
            details = {'id': sid, 'plan_id': 'P-TEST-ONE', 'status': 'ACTIVE',
                       'custom_id': member.id,
                       'billing_info': {'last_payment': {'time': iso(previous)}}}
            api = FakePayPal(details, [payment('OLD-SALE', previous)])

            first = sync_one(sid, api=api, finish=False)
            self.assertEqual(first['credits_issued'], 4)
            self.assertTrue(first['payment_confirmed'])

            details['billing_info']['last_payment']['time'] = iso(previous+timedelta(hours=6))
            same_payment = sync_one(sid, api=api, finish=False)
            self.assertTrue(same_payment['payment_confirmed'])
            self.assertFalse(same_payment['payment_history_pending'])

            details['billing_info']['last_payment']['time'] = iso(current)
            missing = sync_one(sid, api=api, finish=False)
            self.assertEqual(missing['credits_issued'], 0)
            self.assertFalse(missing['payment_confirmed'])
            self.assertTrue(missing['payment_history_pending'])

            api.rows.append(payment('NEW-SALE', current))
            recovered = sync_one(sid, api=api, finish=False)
            self.assertEqual(recovered['credits_issued'], 4)
            self.assertTrue(recovered['payment_confirmed'])
            self.assertFalse(recovered['payment_history_pending'])
            self.assertEqual(sync_one(sid, api=api, finish=False)['credits_issued'], 0)
            self.assertEqual(member.event_credits, 8)
            db.session.remove()
            db.drop_all()


if __name__ == '__main__':
    unittest.main()
