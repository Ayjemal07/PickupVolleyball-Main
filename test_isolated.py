"""Run Main regressions without loading .env or contacting live services."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    os.environ.update(DATABASE_URI='sqlite:///:memory:', SECRET_KEY='isolated-test',
        PAYPAL_CLIENT_ID='test-client', PAYPAL_SECRET='test-secret',
        PAYPAL_BASE='https://api-m.sandbox.paypal.com',
        PAYPAL_PLAN_ID_TIER1='P-TEST-ONE', PAYPAL_PLAN_ID_TIER2='P-TEST-TWO',
        MAIL_USERNAME='test@example.test', MAIL_DEFAULT_SENDER='test@example.test',
        MAIL_PASSWORD='test-password',
        INSTAGRAM_URL='https://www.instagram.com/pickupvolleyballsalem',
        FACEBOOK_URL='https://www.facebook.com/share/1KBhKKqgwp/?mibextid=wwXIfr')
    with patch('dotenv.load_dotenv', return_value=False), \
         patch('requests.sessions.Session.request', side_effect=RuntimeError('Live HTTP disabled in isolated tests')), \
         patch('smtplib.SMTP', side_effect=RuntimeError('Live SMTP disabled in isolated tests')):
        suite = unittest.defaultTestLoader.discover(str(root/'tests'))
        return not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
