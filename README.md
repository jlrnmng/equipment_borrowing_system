# QR Equipment Borrowing System

A Flask-based web application for managing equipment borrowing in a facility using QR codes. Staff and admins can register staff accounts, normal users can sign up manually as members, and staff can manage equipment and transactions.

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup & Installation](#setup--installation)
- [Running the App](#running-the-app)
- [Creating the First Admin Account](#creating-the-first-admin-account)
- [Environment Variables](#environment-variables)
- [Database](#database)
- [Security](#security)
- [Development Roadmap](#development-roadmap)

---

## Features

**Currently implemented (through Day 3 morning):**
- Staff login / logout with bcrypt-hashed passwords
- Google OAuth login flow (domain-restricted)
- Manual login reserved for bootstrap admin account
- Public member manual signup (`/signup`) with QR code generation
- Staff account registration by admin or staff (`/staff/register`)
- Equipment management: add, list, detail, and edit
- Equipment code generation + inventory/status tracking
- CSRF protection on all forms
- Session timeout after 30 minutes of inactivity
- No back-button bypass after logout (cache headers)
- Dashboard with live stats — active members, available equipment, active borrowings, overdue items
- Recent activity feed

**Planned (see `2-week_development_plan.txt`):**
- Borrow / return transactions via QR scanning
- In-facility usage enforcement (working hours, usage area)
- Overdue tracking and automated email reminders
- Violation logging
- Reports and CSV export
- Gmail API notifications (database ready)
- Google Calendar integration (database ready)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3 · Flask 3 |
| Auth | Flask-Login · bcrypt |
| Forms | Flask-WTF · WTForms |
| Database | MySQL (XAMPP) · PyMySQL |
| Frontend | Jinja2 · Bootstrap 5 · Bootstrap Icons |
| Config | python-dotenv |

---

## Project Structure

```
equipment_borrowing_system/
│
├── app/
│   ├── __init__.py          # App factory (Flask-Login, CSRF, OAuth, session timeout)
│   ├── forms.py             # WTForms form classes
│   ├── models/
│   │   ├── staff.py         # Staff model (UserMixin, bcrypt helpers)
│   │   ├── member.py        # Member model helpers
│   │   └── equipment.py     # Equipment model helpers
│   ├── routes/
│   │   ├── auth.py          # /login, /logout, /auth/google, /signup
│   │   ├── dashboard.py     # /dashboard
│   │   ├── equipment.py     # equipment CRUD pages
│   │   ├── members.py       # compatibility redirect to /signup
│   │   └── staff_admin.py   # /staff/register
│   ├── templates/
│   │   ├── base.html        # Shared <head>, Bootstrap, CSS
│   │   ├── layouts/
│   │   │   └── main.html    # Authenticated layout (sidebar + topbar)
│   │   ├── auth/
│   │   │   ├── login.html
│   │   │   ├── request_access.html
│   │   │   └── signup.html
│   │   ├── dashboard/
│   │   │   └── index.html
│   │   ├── equipment/       # add, list, detail, edit
│   │   └── staff/
│   │       └── register.html
│   ├── static/              # CSS, JS, images, generated QRs
│   └── utils/
│       ├── db.py            # get_db() / close_db() per-request connection
│       └── qr.py            # member QR generation helper
│
├── config/
│   └── config.py            # DevelopmentConfig / ProductionConfig
│
├── database/
│   ├── migrations/
│   │   └── equipment_borrowing.sql   # Full schema (10 tables)
│   └── seeds/
│       └── test_data.sql             # 5 members, 10 equipment, 1 staff
│
├── scripts/
│   └── create_admin.py      # One-time CLI to create the first admin account
│
├── tests/                   # (to be populated)
│
├── .env                     # Local environment variables (not in Git)
├── .gitignore
├── requirements.txt
├── main.py                  # Entry point
├── workflow.txt             # System workflow documentation
└── 2-week_development_plan.txt
```

---

## Prerequisites

- Python 3.10+
- [XAMPP](https://www.apachefriends.org/) (for MySQL) or any MySQL 8+ server
- Git

---

## Setup & Installation

**1. Clone the repository**
```bash
git clone <https://github.com/jlrnmng/equipment_borrowing_system>
cd equipment_borrowing_system
```

**2. Create and activate a virtual environment**
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies**
```bash
pip install -r requirements.txt
```

**4. Configure environment variables**

Copy the example and fill in your values:
```bash
copy .env .env.local   # Windows
# or
cp .env .env.local     # macOS/Linux
```

Edit `.env`:
```
DB_HOST=localhost
DB_PORT=3306
DB_NAME=equipment_borrowing
DB_USER=root
DB_PASSWORD=

FLASK_APP=main.py
FLASK_ENV=development
SECRET_KEY=replace-with-a-long-random-string
```

> **Important:** Change `SECRET_KEY` to a long random string before deploying.  
> Generate one with: `python -c "import secrets; print(secrets.token_hex(32))"`

**5. Set up the database**

Start XAMPP and ensure MySQL is running, then:

```bash
# Create the database in phpMyAdmin or via MySQL CLI
mysql -u root -p -e "CREATE DATABASE equipment_borrowing CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# Run the schema migration
mysql -u root -p equipment_borrowing < database/migrations/equipment_borrowing.sql

# (Optional) Load test data
mysql -u root -p equipment_borrowing < database/seeds/test_data.sql
```

**6. Create the admin account**
```bash
python scripts/create_admin.py
```

---

## Running the App

```bash
python main.py
```

Then open **http://127.0.0.1:5000** in your browser.

Account flow:
- Member manual signup: **/signup**
- Staff login page: **/login**
- Staff registration (admin/staff only): **/staff/register**

---

## Creating the First Admin Account

```bash
python scripts/create_admin.py
```

You will be prompted for:
- Full name
- Email address
- Password (minimum 8 characters)

The script checks for duplicate emails and generates a unique `STAFF###` code automatically.

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `DB_HOST` | MySQL host | `localhost` |
| `DB_PORT` | MySQL port | `3306` |
| `DB_NAME` | Database name | `equipment_borrowing` |
| `DB_USER` | MySQL username | `root` |
| `DB_PASSWORD` | MySQL password | *(empty)* |
| `FLASK_APP` | Flask app module | `main.py` |
| `FLASK_ENV` | `development` or `production` | `development` |
| `SECRET_KEY` | Flask session signing key | *(must be changed)* |

---

## Database

The schema contains **10 tables** with OAuth 2.0 and Gmail API fields already in place for future use:

| Table | Purpose |
|---|---|
| `staff` | Staff/admin accounts (with Google OAuth fields) |
| `members` | Registered members with QR codes |
| `equipment` | Equipment inventory with QR codes |
| `borrow_records` | Borrowing transaction headers |
| `borrow_items` | Individual items per transaction |
| `violations` | Overdue, damage, and unauthorized use records |
| `notifications` | Email/SMS queue with Gmail API tracking |
| `activity_log` | Full audit trail |
| `google_calendar_events` | Calendar reminders (Phase 2) |
| `app_settings` | OAuth credentials and system config |

See [`database/database_documentation.md`](database/database_documentation.md) for full field-level documentation.

---

## Security

- **Passwords** — hashed with bcrypt, never stored in plain text
- **CSRF** — all forms protected via Flask-WTF
- **Session timeout** — 30-minute inactivity timeout enforced server-side
- **Back-button bypass** — `Cache-Control: no-store` on all authenticated responses
- **Open redirect** — `next` parameter validated to reject absolute URLs
- **SQL injection** — all queries use parameterized `%s` placeholders
- **Inactive staff** — blocked from logging in regardless of correct password

---

## Development Roadmap

See [`2-week_development_plan.txt`](2-week_development_plan.txt) for the full sprint plan.

| Phase | Status |
|---|---|
| Day 1 — Database & project setup | ✅ Done |
| Day 2 Morning — Staff authentication & dashboard | ✅ Done |
| Day 2 Afternoon — Member manual signup & QR codes | ✅ Done |
| Day 3 Morning — Equipment management | ✅ Done |
| Day 3 Afternoon — Member QR scanning | 🔲 Next |
| Day 4 — Borrow transaction module | 🔲 Planned |
| Day 5 — Return module & email notifications | 🔲 Planned |
| Week 2 — Overdue tracking, reports, polish | 🔲 Planned |
| Post-launch — Calendar, advanced notifications, HTML emails | 🔲 Phase 2 |
