# Event free spots

In the admin event creation or Edit Event form, choose:

- **Paid event:** every spot uses the ticket price or the existing member credit policy.
- **Free primary spots up to full capacity:** each eligible email can redeem one primary spot; plus-ones pay the regular price.
- **First N spots free, then paid:** enter the free spot limit and the price for the remaining spots.

The same choices are available for recurring events; each event has its own limit.
Creation and editing also ask whether to allow plus-ones and the maximum per
booking, including free promotions. New events default to no plus-ones. Enabling
them requires a positive whole-number limit, enforced for both members and guests.
Only the primary attendee can redeem a free spot. Each email can redeem once per
event across member and guest checkout. Email matching ignores case and surrounding
spaces. Member account IDs also prevent re-redemption after changing email.
Plus-ones always pay the regular price and never consume promotional free spots.
A member credit is only used when the member's own spot is paid.
Free primary spots never consume subscription or other Event Credits, even when
the member pays cash for plus-ones in the same checkout.

Enabling or increasing a promotion includes existing bookings in booking order.
Within the first-X allocation, each previously paid member primary spot receives
one event credit expiring 30 days from issuance. This also restores a primary spot
originally covered by an event credit. Plus-ones do not earn promotional credits.
Guest primary spots within that allocation are counted toward the limit and
reported to the admin for manual refunds; no money refund is automated.
The allocation includes both these compensated spots and newly booked free spots.
Once exhausted, later bookings are paid and do not earn a promotional credit.

Members receiving a promotional credit are emailed after their credit is saved.
The email states that their own spot is now free, they received **1 Event Credit**
for their own spot, any plus-ones remain paid, and their RSVP remains confirmed.
It explains how to find the credit in Profile, use it for another paid event,
and gives the exact expiry date, 30 days after issuance. The Profile ledger shows
the grant with an **Event promotion** label, remaining balance, and expiry.

Email notifications are queued in the same transaction as the credit. Delivery
failure never removes or reissues the credit. Saving the event retries pending
emails; operators can also retry them without issuing credits using:

```sh
python -m flask --app run credits send-event-promotion-emails
```

Successful notifications are not resent by normal retries. SMTP cannot guarantee
exactly-once email delivery if a process stops after provider acceptance but
before saving its sent status; credit issuance remains protected against duplicates.

Eligible new primary spots are automatically FREE while an allocation remains;
the server refuses payment for a primary-only free booking and charges only for
plus-ones in mixed bookings. A pending paid checkout cannot capture after its
primary spot becomes free: the user must refresh to review the corrected price.

Canceled free or compensated spots
become available to other emails again, but the original email/account cannot
redeem again for that event. Removing guests does not change the primary free spot.
Cancellation credits are issued only for paid/credit-covered spots that have not
already been compensated, under the existing cancellation cutoff policy. Saving,
disabling, and re-enabling a promotion does not issue duplicate benefits.

## Cancellations and RSVP changes

| Action | Credits issued |
| --- | --- |
| Admin cancels an event | Remaining paid spots only, regardless of the 48-hour cutoff |
| Member selects Not Going at least 48 hours before start | Remaining paid spots only |
| Member selects Not Going less than 48 hours before start | None; any earlier promotional credit remains |
| Member removes plus-ones at least 48 hours before start | Removed paid plus-ones only |
| Member removes plus-ones less than 48 hours before start | None |

Free spots and primary spots already compensated by a promotional credit never
earn a second cancellation credit. Guest removals update the reservation and
credit ledger in the same transaction, so later cancellations cannot credit
removed guests again. The change preview displays the expected credits.
Reduced guest limits or disabled plus-ones do not prevent reducing an existing
reservation. Canceled or started events reject RSVP changes and pending payments.
Editing a canceled or past event cannot issue promotional compensation.

Event cancellation status and credit grants commit together, before notification
emails. Repeated requests and email failures cannot reissue credits. Cancellation
credits have a matching transaction audit entry and expire after 30 days.
The 48-hour threshold uses elapsed time in America/Los_Angeles, including daylight
saving changes; deployments may override the event zone with EVENT_TIMEZONE in
Flask configuration. Exactly 48 hours qualifies for cancellation credits.

No automatic guest money refunds are implemented. Guest identity relies on the
submitted email; email ownership verification and detecting multiple different
emails belonging to the same person are outside the current implementation.

## Deployment

Deploy the updated models, pricing module, views, templates, JavaScript, and migration
together. Before starting the updated application, run:

```sh
python -m flask --app run db upgrade
```

Migration `20261111_event_free_spots` adds event and RSVP free spot counts, backfills
existing free bookings, and creates checkout pricing snapshots. The subsequent
`20261111_promotion_claims` migration adds durable redemptions and backfills existing
free booking emails. Existing free bookings keep their allocations; the new one-per-email
rule applies to future redemptions. `20261111_promotion_credits` records compensated
primary spots so cancellations cannot refund them again. `20261111_promotion_emails`
adds the durable notification queue. For promotions already
enabled before this update, saving the event applies compensation to eligible
existing bookings. Legacy events with a zero regular price require
an admin to set a positive price before new plus-one bookings. Checkouts opened
before deployment must be restarted. Pending checkouts whose price or allocation
changes are rejected before payment capture; users must refresh to review pricing.

## Verification

```sh
python -m unittest discover -s tests -v
node --check app/static/js/events.js
```

Integration tests use SQLite with mocked PayPal, email, and guest waiver generation.
They do not contact payment services. Concurrent claims are serialized using event
row locks and current booking reads; SQLite tests simulate intervening bookings
but do not exercise production MySQL row locking.
The overlap regression suite covers repeated requests, the exact 48-hour boundary,
guest reductions followed by cancellation, database rollback, notification failure,
closed events, and stale payment quotes. All 64 local tests passed for this audit.
