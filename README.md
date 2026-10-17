# PulseNotify: Flight Price Monitor & Alert System

PulseNotify is a Django + DRF backend. Users set price alerts for flight routes. Every 60 seconds a Celery Beat job
checks the current price for every active alert, using the project's own mock price feed. When a price
is at or below an alert's threshold, an async Celery task writes a `NotificationLog` and marks the alert `triggered`.

```
POST /api/alerts/            → PriceAlert saved (responds instantly)
Celery Beat (every 60s)      → check_prices
  requests.get(/api/flights/price/?route=DEL-BOM)  → { "route": "DEL-BOM", "price": 4200 }
  4200 <= threshold?         → send_notification.delay(alert_id, 4200)
send_notification (worker)   → NotificationLog row + alert.status = triggered
GET /api/admin/summary/      → platform stats (admins only)
```

## Tech stack

Django 6.1 · Django REST Framework · SimpleJWT · Celery 5.6 (worker + beat) · Redis 7 (broker) ·
PostgreSQL 16 · Docker Compose

## Project layout

```
pulsenotify/
  __init__.py          # loads the Celery app
  celery.py            # Celery app, reads CELERY_* from Django settings
  settings/
    base.py            # shared config: apps, DRF/JWT, Celery + CELERY_BEAT_SCHEDULE
    local.py           # DEBUG=True, local Postgres
    production.py      # DEBUG=False, prod DB, secure cookies
pulse/
  models.py            # UserProfile, PriceAlert, NotificationLog
  signals.py           # post_save on User → auto-create UserProfile
  apps.py              # connects signals in AppConfig.ready()
  permissions.py       # custom IsAdminUser (profile.role == ADMIN)
  serializers.py
  views.py             # 7 endpoints + mock price feed
  tasks.py             # check_prices, send_notification (@shared_task)
  tests.py             # required tests + API / task tests
  management/commands/promote_admin.py
docker-compose.yml     # PostgreSQL + Redis
PulseNotify.postman_collection.json
```

## Setup

### 1. Clone and install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

```bash
cp .env.example .env      # then set SECRET_KEY and DB_PASSWORD
```

| Variable | Example | Purpose |
|---|---|---|
| `DJANGO_SETTINGS_MODULE` | `pulsenotify.settings.local` | Use `pulsenotify.settings.production` when deploying |
| `SECRET_KEY` | long random string | Django secret key |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `pulsenotify` / `postgres` / … | Used by Docker Compose *and* Django |
| `DB_HOST` / `DB_PORT` | `localhost` / `5433` | Postgres host port (5433 avoids clashing with another local Postgres on 5432) |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Redis broker |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Task results |
| `PRICE_FEED_URL` | `http://localhost:8000/api/flights/price/` | Endpoint that `check_prices` calls |

`.env` is in `.gitignore` and must never be committed.

### 3. Start PostgreSQL + Redis

```bash
docker compose up -d
```

### 4. Migrate

```bash
python manage.py migrate
```

## Running PulseNotify (three processes)

Open three terminals, activate the venv in each, and run:

```bash
# Terminal 1: Django API (also serves the mock price feed)
python manage.py runserver

# Terminal 2: Celery worker (runs check_prices and send_notification)
celery -A pulsenotify worker --loglevel=info

# Terminal 3: Celery Beat (queues check_prices every 60 seconds)
celery -A pulsenotify beat --loglevel=info
```

* **Terminal 3** logs `Sending due task check-flight-prices-every-minute (pulse.tasks.check_prices)` every 60 seconds.
* **Terminal 2** logs `check_prices` running, the current price per route, and `send_notification` tasks arriving whenever a
  threshold is hit.

> **macOS note:** on macOS, Celery's default prefork pool starts its workers with `spawn`, and the prefork pool fails
> under `spawn`. `settings/local.py` therefore sets `CELERY_WORKER_POOL = 'threads'` when running on macOS.
> Linux and production keep the default prefork pool.

### Try it

1. Register and create an alert with a high threshold, e.g. `DEL-BOM` at ₹9000. DEL-BOM prices range from ₹3000 to ₹7000, so it always triggers.
2. Within 60 seconds the worker logs `send_notification ... succeeded`.
3. A row appears in `pulse_notificationlog` and the alert's status becomes `triggered`.

### Making a user an admin

Change the user's role to `admin` in the `pulse_userprofile` table (PGAdmin), or run:

```bash
python manage.py promote_admin <username>
```

## API

All endpoints except register, login and the price feed need `Authorization: Bearer <access token>`.

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| POST | `/api/auth/register/` | Public | Create user (role is always `user`). **201**, or **400** if the username exists |
| POST | `/api/auth/login/` | Public | `authenticate()` credentials. **200** with JWT, or **401** |
| POST | `/api/alerts/` | JWT | Create alert; the owner comes from the token, never from the body. **201** |
| GET | `/api/alerts/` | JWT | Only the caller's alerts (empty list if none). **200** |
| DELETE | `/api/alerts/<id>/` | JWT | Soft delete (status → `inactive`). **200** `{"status": "inactive"}`, or **404** if the alert is missing or belongs to someone else |
| GET | `/api/flights/price/?route=DEL-BOM` | Public | Mock price feed. **200** `{"route", "price"}`, or **404** for an unknown route |
| GET | `/api/admin/summary/` | JWT, admin role | Platform stats. **200**, or **403** for regular users |

Supported mock routes: `DEL-BOM` (3000–7000), `BLR-HYD` (1500–4000), `DEL-BLR` (4000–9000), `BOM-GOA` (2000–5000).

Example requests:

```jsonc
// POST /api/auth/register/
{ "username": "alice", "password": "securepass", "email": "alice@example.com" }
// → 201 { "username": "alice", "access": "eyJ...", "refresh": "eyJ...", "role": "user" }

// POST /api/alerts/
{ "origin": "DEL", "destination": "BOM", "threshold_price": 4500 }

// GET /api/admin/summary/
{
  "total_alerts": 24, "active_alerts": 18, "triggered_alerts": 4, "total_notifications": 11,
  "top_routes": [ { "route": "DEL-BOM", "alert_count": 8 }, { "route": "BLR-HYD", "alert_count": 6 } ]
}
```

## Background jobs

* **`check_prices`** (Celery Beat, every 60s, registered in `CELERY_BEAT_SCHEDULE`): finds the distinct routes that have
  active alerts and calls the price feed once per route with `requests.get()`. Every active alert whose
  threshold is at or above the current price gets `send_notification.delay(...)`, so it never waits for the notification
  to be written.
* **`send_notification`** (async): atomically changes the alert from `active` to `triggered`, then writes the
  `NotificationLog`. If the alert is no longer active (already triggered or deactivated), it does nothing, so an alert
  can't produce duplicate notifications.

## Tests

```bash
python manage.py test
```

There are 25 tests. They include the three required test classes (`PriceThresholdTest`, `NotificationLogTest`,
`AlertScopingTest`) and also cover the profile signal, every endpoint's success and error cases, admin permissions and
aggregation, and both Celery tasks, with `requests.get` mocked.

## Postman

Import `PulseNotify.postman_collection.json` and run it with the Collection Runner, top to bottom. Every request has test
assertions, and tokens and the alert id are passed between requests through collection variables. Before running the **Admin**
folder, make a user an admin (see above) and set the `admin_username` and `admin_password` collection variables (defaults:
`admin` / `adminpass123`).

Scenarios: register (201) · duplicate register (400) · login (200) · bad login (401) · create alert (201) · create
without a token (401) · list alerts (200) · new user gets an empty list (200) · delete another user's alert (404) · delete a
non-existent alert (404) · delete own alert (200, inactive) · price for a valid route (200) · price for an unknown route (404) · summary as a
user (403) · admin login (200) · summary as admin (200).
