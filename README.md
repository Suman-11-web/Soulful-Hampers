# Soulful Hampers

A Flask storefront with a guest shopping bag, manual UPI payments, order tracking and a private studio dashboard. HTML templates, CSS and vanilla JavaScript run without a frontend build step. Product listings and best sellers start empty. The category photographs and three editable review entries come from the supplied design reference.

## Open the local site

Run from the project directory with Python 3.9 or newer.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m flask --app app setup-code
python app.py
```

Open [the storefront](http://127.0.0.1:5055/) or [the studio dashboard](http://127.0.0.1:5055/admin/). The current workspace already has a first-use code. On a fresh installation, the setup command writes one to `instance/setup-code.txt`. Paste the code on the studio setup screen and choose a username and password. The code is single-use. To recover access on the server, run `python -m flask --app app set-admin`.

## Configure the shop

1. In **Store settings**, upload the shop’s UPI QR, enter its payee name and set the business WhatsApp number with its country code. The same screen controls the homepage text, artwork, delivery information, contact email and social profiles.
2. A QR can also be supplied as `static/payment/qr.png`, `qr.jpg` or `qr.jpeg` in that folder. A dashboard upload takes priority. Until a QR is configured, checkout directs visitors to the enquiry form. A configured WhatsApp number enables the direct chat buttons.
3. In **Products**, create a listing with a category, rupee price and one to eight photos. The first photo is the cover. Use **Available to order** and **Show in Our Best Sellers** to control visibility and purchasing. Removing a listing preserves order history.
4. In **Reviews**, edit, hide or replace the three entries reproduced from the reference before launching the client’s shop.

## Buyer and studio workflow

Buyers add products to their bag, review the exact total, scan the merchant QR and enter the 12-digit UPI reference with their delivery details. Mobile buyers can save the QR to open it in their UPI app. Checkout holds the displayed price for 24 hours. A submitted payment reference starts under manual review.

The success page provides the order ID, an order enquiry form and a prefilled WhatsApp chat when configured. The buyer chooses whether to send the WhatsApp message. The studio can verify the transaction, change the payment and fulfilment statuses and add internal notes in **Orders**. Guest tracking on the homepage accepts an order ID or buyer mobile number and returns dates and statuses. The address, payment reference and internal notes are available to the studio.

**Sales reports** supports date ranges, verified revenue, product rankings and CSV downloads. **Enquiries** collects custom requests, bulk gifting briefs and order questions.

## Hosting and data

The local preview listens on loopback. For a client deployment, use a Python WSGI host with HTTPS and a persistent disk. The WSGI application factory is `app:create_app()`. Set `SOULFUL_DATA` to a private persistent directory for the SQLite database and uploaded photos, and set `HTTPS_ONLY=1` behind HTTPS. Keep that directory outside the public static root. The application creates a persistent signing key there or accepts a deployment-provided `SECRET_KEY`.

Back up the database using SQLite’s backup mechanism together with the uploads directory. Use the same data directory when running Flask setup or recovery commands. Edit `PORT` to change the local preview port. All order dates and reports use India Standard Time.

## Verify

```sh
python -m unittest discover -s tests -v
```

The tests use temporary directories and exercise admin setup, uploads, catalogue edits, guest checkout, resubmission, duplicate UTRs, tracking, reports and enquiries.
