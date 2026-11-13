# PickupMain parity release — November 12, 2026

This release brings Main to PDX commit `03130b5` (per-email free event
promotions and credit notifications), with Main's subscription prices and
Southern Oregon wording. It retains Main's uncommitted capacity and credit
transaction fixes. PDX's source and pricing are unchanged.

## Main settings

- Tier 1: $35/month, 4 credits, $8.75/event.
- Tier 2: $60/month, 8 credits, $7.50/event.
- Cards, signup/upgrade dialogs and server-side payment verification agree.
- Keep Main's existing `.env`, especially its database, PayPal plan IDs,
  credentials, webhook ID and email settings. No PDX `.env` is included.
- Emails use Main's configured mail sender instead of a hardcoded PDX address.
- Main uses the supplied Instagram account, `pickupvolleyballsalem`, and
  Facebook share link `https://www.facebook.com/share/1KBhKKqgwp/?mibextid=wwXIfr`.
  `INSTAGRAM_URL` and `FACEBOOK_URL` can override these defaults. When Instagram
  is deliberately set to an empty string, the icon is hidden and coaching/contact
  enquiries use the configured mail account.
- The existing legal entity name in the liability agreement is preserved.
  The venue-specific directions rule for the existing Portland church is also
  preserved; it only applies when an event matches that venue.

## Database upgrade

Main and PDX have different migration histories. Main's original two migrations
are retained. The merchandise migration now follows Main's `36bfcce0f503`
revision, followed by the applicable forward migrations from PDX (ten in total).
Do not replace Main's history with PDX's older migrations or stamp a PDX revision.

Before starting the updated application on a database, take a database backup
and upgrade that database. From the Main project with its virtual environment
active:

```text
python -m pip install -r requirements.txt
python verify_subscription_release.py
python -m flask --app run db current
python -m flask --app run db upgrade
python -m flask --app run credits doctor
```

`db upgrade` changes the database selected by Main's `.env`; use the intended
host and database. The expected final revision is `20261111_promotion_emails`.
The migrations preserve existing accounts, subscriptions, balances and payments;
they add durable receipts/checkout state, merchandise tables and event promotion
state. Historical credit grants are not recreated. The cutover marker prevents
automatic reissuance of ambiguous legacy payments.

The included migration regression upgrades a temporary database from Main's
original head, preserves sample existing data, checks legacy free bookings,
checks the final schema against the models and reruns upgrade safely.
Production database-engine locking and a real payment have not been tested by
these SQLite checks.

## Automated checks

```text
python test_isolated.py
```

This runner blocks `.env` loading, actual HTTP and SMTP, and uses temporary
SQLite data plus simulated PayPal/email responses. Do not run synthetic
payments or test webhooks against a live database.

The original 75 PDX tests passed on both snapshots. Main also passed five
regressions for $35/$60 payment issuance and deduplication, public branding and
price text, capacity reduction, atomic credit spending and data-preserving
migration: **80 tests passed** in total.

`verify_subscription_release.py` is refreshed for this Main release and checks
the release source files without importing the app or accessing any database.

## Production activation

Updating this Windows folder does not publish the website. Deploy the complete
application and new migrations together, retain the server's Main `.env` and
uploaded files, run the migration and doctor on that host, and restart its
application service. This code sync does not run a live migration, change PayPal
plans or schedule jobs.

PDX's subscription maintenance/recovery commands and the
`schedule_subscription_maintenance.py` wrapper are included. Configure a Main
host job if automatic recovery is desired; copying the script does not create
one. Existing failed promotion notifications can be retried with
`python -m flask --app run credits send-event-promotion-emails` after migration;
that command sends email and updates delivery status.

Merch remains available at `/merch` and hidden from navigation, matching PDX's
current launch state. Inventory is site-specific; the migration seeds the same
catalogue with zero stock. No PDX customers, orders, signed waivers or database
contents are transferred.
