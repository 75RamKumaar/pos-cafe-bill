# Cafe POS — Django Offline POS + Khata

A local Django 5 application for a tea shop, cafe, or wholesale counter.
Designed for single-shop, offline LAN deployments.

---

## Features

| Area | Detail |
|------|--------|
| **POS Counter** | Fast keyboard-driven and touch billing (56px targets, one-hand friendly) |
| **Payment Modes** | Cash, UPI, Card, Khata (credit), and Split payments |
| **Cash Handling** | Change due calculator, quick tender buttons, receipt printing |
| **Favorites Strip** | Top 8 best-selling products from the last 30 days displayed above categories |
| **Khata Ledger** | Customer dues ledger, partial payments, protected customer deletion |
| **Stock Tracking** | Optional per-item stock tracking; untracked items never block sales |
| **Bill Management** | Safe bill voiding with stock restoration, Khata reversal, and audit logging |
| **Menu Management** | Add/edit products with image upload, price, cost price, stock tracking |
| **Expense Tracking** | Log daily expenses with category and date |
| **Dashboard** | Real-time net sales (accounting for discounts), profit, top items, expense tracking |
| **Reports & Export** | Sales reports, payment breakdown from `BillPayment`, CSV export |
| **Audit Log** | Timestamped audit log tracking billing, voiding, and Khata actions |
| **Roles & Auth** | Owner (full access) / Cashier (billing only; requires Owner authorization to void) |
| **Security & Throttling** | Login lockout (locks username+IP for 5 minutes after 5 failures), secure cookies |
| **Backups** | `python manage.py backup_db` — timestamped SQLite snapshots in `backups/` |
| **Demo Data** | `python manage.py seed_demo` — products, customers, bills |
| **Static Files** | Standalone high-contrast CSS/JS, vendored Chart.js offline, WhiteNoise serving |

---

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url>
cd cafe_pos_django_v2

# 2. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
copy .env.example .env         # Windows
# cp .env.example .env         # Linux / macOS
# Starts out-of-the-box in development mode (DJANGO_DEBUG=True).
# In production, set DJANGO_DEBUG=False and generate a secret key:
# python manage.py gen_secret

# 5. Apply database migrations (do not run makemigrations; all migrations are tracked)
python manage.py migrate

# 6. Seed demo data (optional)
python manage.py seed_demo

# 7. Create users and assign roles (see User Roles section)
python manage.py createsuperuser

# 8. Collect static files
python manage.py collectstatic --noinput

# 9. Run the server
python manage.py runserver 0.0.0.0:8000
```

Open **http://127.0.0.1:8000/** in your browser and sign in.

---

## Environment Variables (`.env`)

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | *(included in example)* | Secret key. In production, generate with `python manage.py gen_secret` |
| `DJANGO_DEBUG` | `True` *(in .env.example)* | Set `False` in production |
| `DJANGO_ALLOWED_HOSTS` | `127.0.0.1,localhost` | Comma-separated list of allowed hostnames/IPs |
| `DJANGO_HTTPS` | `False` | Set `1` or `True` to enable HTTPS redirect, secure cookies, and HSTS |

> **Startup check:** When `DJANGO_DEBUG=False`, Django will halt with `ImproperlyConfigured` if `DJANGO_SECRET_KEY` is under 32 characters or starts with `"replace-with"`.

---

## User Roles & Setup

Access control is strictly enforced at every view using Django groups:

| Role / Group | Permissions |
|--------------|-------------|
| **Owner** | Full system access — dashboard, sales reports, CSV export, menu management, customer ledger, expense management, settings, audit log, and direct bill voiding. |
| **Cashier** | Billing counter, product search, receipt reprint, customer search, and Khata payments. Bill voiding requires an Owner's credential authorization. |

Superusers automatically inherit Owner-level privileges.

### Creating Owner and Cashier Users

**Option A — Via Django Admin:**
1. Log in to `/admin/` using your superuser account.
2. Navigate to **Authentication and Authorization -> Users -> Add user**.
3. Under **Groups**, assign the user to `Owner` or `Cashier` (groups are created automatically during migration).

**Option B — Via Management Shell:**
```bash
python manage.py shell
```
```python
from django.contrib.auth.models import User, Group

# Create Cashier
cashier = User.objects.create_user(username="cashier1", password="password123")
cashier.groups.add(Group.objects.get(name="Cashier"))

# Create Owner
owner = User.objects.create_user(username="owner1", password="password123")
owner.groups.add(Group.objects.get(name="Owner"))
```

---

## Security & Login Throttling

- **Login Throttling:** Brute-force protection locks any `username + IP` combination for 5 minutes after 5 consecutive failed login attempts using Django's cache framework (`LocMemCache`). Successful login clears the failure counter.
- **Session & Cookies:** `SESSION_COOKIE_SAMESITE = "Lax"`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_AGE = 43200` (12 hours).
- **Security Headers:** `SECURE_CONTENT_TYPE_NOSNIFF = True` and `X_FRAME_OPTIONS = "DENY"`.
- **Secret Key Generation:** Run `python manage.py gen_secret` to generate a cryptographically strong secret key.

---

## Media Files & Production Deployment

- **Development (`DEBUG=True`):** Uploaded product images under `media/` are served automatically via Django's `serve()` view.
- **Production (`DEBUG=False`):** Django does not serve media files in production for performance and security reasons. Point a reverse proxy (such as **Nginx** or **Caddy**) directly at the `media/` directory.

### Nginx Example
```nginx
location /media/ {
    alias /path/to/cafe_pos_django_v2/media/;
    expires 30d;
}
```

### Static Files
Static files are compressed and cached via **WhiteNoise**. After making static file changes:
```bash
python manage.py collectstatic --noinput
```

---

## Database Backups

```bash
python manage.py backup_db
```

Creates a timestamped snapshot of `db.sqlite3` in the `backups/` directory and removes snapshots older than 30 days.

### Automate on Windows (Task Scheduler)
- **Program:** `C:\path\to\cafe_pos_django_v2\.venv\Scripts\python.exe`
- **Arguments:** `manage.py backup_db`
- **Start in:** `C:\path\to\cafe_pos_django_v2`

### Automate on Linux (Cron)
```
0 2 * * * cd /path/to/cafe_pos_django_v2 && /path/to/.venv/bin/python manage.py backup_db >> backup.log 2>&1
```

---

## Running Tests

Run the full test suite with:

```bash
python manage.py test pos
```

Tests cover:
- Role-based access control (Anonymous, Cashier, Owner)
- Split billing, Cash tendered & change due, Khata transactions
- Stock tracking and fractional decimal quantities
- Bill voiding and authorization checks
- Login throttling (lockout after 5 failures and release after expiry with mocked clock)
- Report and dashboard financial calculations
