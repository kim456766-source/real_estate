# Theo Property – Real Estate Management & Marketplace (Rwanda)

A public real estate website plus a staff dashboard.

- **Clients need no account.** They browse, filter, save favorites (kept on their device), send inquiries and book visits with their name and phone.
- **Owners need no account.** They submit a property with photos at `/properties/submit/`, get a reference number, and can check the status at `/properties/submit/status/`.
- **Each agent works only on their own listings.** The admin assigns each owner submission to an agent, who verifies it with the owner and advertises it on the website and on other platforms using the **share kit** (ready-made ad text, share buttons, photo ZIP download).
- **Administrators** control everything. Only staff (admins and agents) log in.

## Technology stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13, Django 6.1, Django ORM |
| API | Django REST Framework 3.18, django-filter, token + session auth |
| Frontend | HTML5, CSS3, JavaScript (Fetch API), Bootstrap 5.3, Font Awesome 6 |
| Charts / maps | Chart.js 4, Leaflet + OpenStreetMap (Google Maps/Places when a key is set) |
| Database | PostgreSQL (via `DATABASE_URL`); SQLite fallback for quick local work |
| Integrations | Brevo/SendGrid SMTP email, WhatsApp click-to-chat, Cloudinary (optional), MTN MoMo & Airtel Money (prepared) |

## Quick start (Windows)

```powershell
cd C:\Users\user\OneDrive\Desktop\real_estate
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env          # then edit SECRET_KEY etc.
python manage.py migrate
python manage.py seed_demo      # optional demo data
python manage.py runserver
```

Open http://127.0.0.1:8000. Staff login is at http://127.0.0.1:8000/accounts/login/ (also linked in the footer).
Demo staff accounts (password `TheoDemo#2026`): `admin`, `agent` (can publish directly), `agent2` (needs admin approval).
**Change or delete these accounts before going live.**

To create your own administrator: `python manage.py createsuperuser`.

## Switching to PostgreSQL

1. Install PostgreSQL and create the database:
   ```sql
   CREATE USER theo WITH PASSWORD 'strong-password';
   CREATE DATABASE theo_property OWNER theo;
   ```
2. In `.env`: `DATABASE_URL=postgres://theo:strong-password@localhost:5432/theo_property`
3. `python manage.py migrate` (and `seed_demo` if you want demo data).

## Project structure

```
config/          settings, root URLs
core/            audit log, site settings, role mixins, upload validation, seed_demo command, tests
accounts/        custom User (roles: admin, agent, owner, client), agent & owner profiles, auth views
properties/      Property, PropertyImage, Category, search filters, public listing/detail pages
inquiries/       property inquiries + Contact Us messages
appointments/    viewing requests (no account needed)
favorites/       saved properties, stored in the visitor session (AJAX toggle)
payments/        payment records + gateways.py (MTN MoMo / Airtel Money integration points)
notifications/   in-app notifications + email (services.notify)
blog/            news & advice articles
pages/           home, about, contact
dashboard/       management dashboard (role-scoped), reports, CSV exports, settings, audit log
api/             REST API (DRF)
templates/  static/  media/
```

## Roles & permissions

| Who | Account? | Can do |
|---|---|---|
| Client (visitor) | No | Search and filter, save favorites, send inquiries, book visits, contact by WhatsApp/phone |
| Property owner | No | Submit a property with photos, check its status with reference number + phone |
| Agent | Yes (created by admin, or self-registered and approved by the super admin) | Sees **only their own listings** and only their own clients' inquiries and visits; edits their listings, uses the share kit, answers inquiries, manages visits. Publishing needs admin approval unless "can publish directly" is enabled |
| Administrator | Yes | Everything: publish/reject/deactivate, assign agents, manage agents, payments, reports, settings, audit log |

Owner phone numbers and emails are never shown publicly. Clients always contact the agent, or the
company number if no agent has been assigned yet.

**Property flow:** owner submits → admins are notified → an admin assigns it to an agent → the agent
calls the owner to verify → it's published (by an admin, or by an agent allowed to publish) → the agent
shares it with the share kit → clients' inquiries and visit requests go to that agent.

## REST API (`/api/v1/`)

| Endpoint | Access |
|---|---|
| `GET properties/` `GET properties/<slug>/` | public; filters: `purpose, property_type, district, location, min_price, max_price, bedrooms, bathrooms, min_size, max_size, availability, q, sort` |
| `GET categories/` | public |
| `POST inquiries/` | public; `GET` lists your own |
| `POST appointments/` | public (name, phone, property, scheduled_for); `GET` lists your own |
| `GET/POST/DELETE favorites/` | authenticated (the website itself uses session favorites) |
| `POST auth/token/` | exchange username/password for a token (`Authorization: Token <key>`) |

Browse the API in the browser at http://127.0.0.1:8000/api/v1/.

## External services: how to turn them on

| Service | What to do |
|---|---|
| Email (Brevo) | Set `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`. In development emails are printed to the terminal. |
| Google Maps / Places | Set `GOOGLE_MAPS_API_KEY` (enable Maps JavaScript API + Places API). The address field then autocompletes and fills latitude/longitude. Without a key, OpenStreetMap is used. |
| Cloudinary | `pip install django-cloudinary-storage`, set `CLOUDINARY_URL`. Uploads go to Cloudinary automatically. |
| WhatsApp | Set `SITE_WHATSAPP_NUMBER` (digits only, e.g. 250788000000). Agents' phone numbers are used on property pages. |
| MTN MoMo / Airtel Money | Get merchant credentials, add them to `.env`, `pip install requests`, and complete the marked TODOs in `payments/gateways.py`. A mobile-money payment **cannot** be marked successful until the provider confirms it; cash/bank payments need staff verification. |

## Security built in

- **Database injection:** every query goes through the Django ORM (parameterised, no raw SQL); list filters and sorting only accept known values.
- **Stored HTML / script injection:** every form and API write is cleaned before saving (`core/security.py` `clean_text`): HTML tags and hidden control characters are removed and NUL bytes are refused. Templates also auto-escape everything.
- **Half-saved data:** `ATOMIC_REQUESTS` puts each request in one database transaction, so any error rolls the whole request back. Payment actions have their own transaction.
- **Uploads:** photos are opened with Pillow to prove they are real JPG/PNG/WEBP, with a size limit and a 40-megapixel limit, then re-saved as clean JPEG.
- **Brute force and spam:**
  - Login locks for 15 minutes after 5 wrong passwords per username (20 per connection).
  - Inquiries and visit requests are limited to 10 per hour per connection; owner submissions and agent applications have their own limits plus honeypots.
- **Open redirects:** `?next=` links are only followed when they stay on this website.
- **CSV / Excel formula injection:** exported cells starting with `= + - @` are neutralised.
- **Errors:** visitors and the API get friendly 400/403/404/500 pages or clean JSON errors, never stack traces. Details are written to `logs/errors.log`, and blocked attacks to `logs/security.log`. Set `ADMIN_ERROR_EMAIL` to get crash emails in production.
- **Other protections:**
  - CSRF tokens with a friendly "please try again" page.
  - Private pages (dashboard, login, notifications, owner form) are never cached, and logout clears the browser cache, so pressing Back after logging out can't show them again.
  - Role-based access on every dashboard view.
  - HTTPS/HSTS/secure cookies when `DEBUG=False`.
  - DENY framing, nosniff, a strict referrer policy and an audit log of admin actions.
- **Behind a proxy:** set `TRUST_X_FORWARDED_FOR=True` only when the site runs behind your own proxy (Nginx, Render, Railway…). Otherwise visitors could fake their IP address to dodge the limits.

## Tests

```powershell
python manage.py test
```
51 tests cover security (injection, redirects, brute force, fake images, CSV), the public pages without login, owner submissions (photos, spam protection, rate limit, status check), admin assigns / agent publishes / share kit, agents seeing only their own listings, role restrictions,
inquiries, visits, session favorites, payment rules and the API.

## Deployment checklist

1. `DEBUG=False`, strong `SECRET_KEY`, real `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
2. PostgreSQL `DATABASE_URL`, SMTP email, Cloudinary (or other storage for media)
3. `pip install gunicorn`, `python manage.py collectstatic`, `python manage.py migrate`
4. Run `python manage.py check --deploy`
5. Remove the demo accounts
