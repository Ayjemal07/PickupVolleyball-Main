# Merch operations

## First use

1. Before starting the updated app, apply the database migrations with the same environment and `DATABASE_URI` used by the app: `python -m flask --app run.py db upgrade`. Check the result with `python -m flask --app run.py db current`; it should report `20261027_merch_gallery`.
2. Sign in with an account whose `User.role` is `admin`, open **Merch**, and use **Edit listing** for each seeded product.
3. Enter the physical stock on hand for each size and color. The migration seeds the four current products with **zero** stock to avoid accepting orders for unverified inventory.
4. Check the primary and hover photos, price, description, and whether each product and variant is for sale.
5. Make a sandbox PayPal purchase before accepting live orders. The merch checkout uses the existing `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET`, and `PAYPAL_BASE` settings and the Orders v2 one-time capture flow.

## Daily workflow

If the app reports `Unknown column 'merch_orders.inventory_reserved'`, the code is running against the older database schema. Stop the app, run the migration command above, and restart it. The migration adds the product and variant tables plus inventory and tracking columns to the existing order tables.

- Use **Add product** to create a listing. Each size/color combination has its own stock count and can have its own color photo. Selecting a variant changes the shopper's product and cart image. Hiding a product or variant preserves order history.
- In **Edit listing → Photos**, upload several additional images at once. The card shows the primary image, the hover/close-up image, then your additional photos. Shoppers can use the arrows or dots on the card; you can reorder or remove additional photos later. Each listing supports up to 12 additional photos.
- **On hand** is physical inventory. Open PayPal checkouts temporarily reserve units for 30 minutes; **available** is on hand minus reserved. A successful capture lowers on-hand stock. Canceling PayPal releases the hold immediately; expired unpaid reservations release stock when the merch page, quote, checkout, or admin orders page is opened.
- Use **Orders & tracking** to see paid orders and shipping addresses. After buying the label and shipping the package, enter the carrier and tracking number. Select **Email customer now** if the site's mail service is configured and you want the customer to receive the tracking details. The page records whether that email was sent.
- If an order shows **Capturing**, payment confirmation was interrupted. Check the PayPal order ID in PayPal, then use **Retry PayPal confirmation**. Its stock remains reserved while this state is unresolved.
- Uploaded product photos are written to `app/static/images/merch/uploads`. Deployment storage for this directory must persist across releases and instances. Keep a backup of uploads and the database.

## Current boundaries

- Shipping is a flat $7.99 per order to United States addresses. The app does not buy labels or calculate tax.
- One tracking number is stored per order. Split shipments need an additional design.
- Tracking is stored in the site and can be emailed to the customer; it is not submitted to PayPal's package-tracking API.
- Refunds, returns, exchanges, and restocking are handled outside this admin page. Add a documented policy before launch.
