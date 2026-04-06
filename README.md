# QR Equipment Borrowing System

A Flask-based web application for managing equipment borrowing in a facility using QR codes. Staff/admin users manage inventory and transactions, while members can sign in with Google, complete their profile, submit borrow requests for staff approval, and submit return requests for staff physical-check approval.

---

## Table of Contents

- [Features](#features)
- [Recent UI Updates](#recent-ui-updates)
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

**Currently implemented:**
- Staff login / logout with bcrypt-hashed passwords
- Google OAuth login flow (domain-restricted)
- Manual login reserved for bootstrap admin account
- Member Google signup/login with profile completion gate before member dashboard
- Staff account registration by admin or staff (`/staff/register`)
- Equipment management: add, list, detail, and edit
- Equipment code generation + inventory/status tracking
- Borrow transactions with member eligibility checks and equipment assignment
- Member self-service borrow request dashboard (search equipment, submit request, view request history)
- Member dashboard camera scanner: scan equipment QR to add items instantly to a borrow request
- Unified authenticated shell: role-aware sidebar/topbar for both member and staff sessions
- Member dashboard redesign with quick-action cards, stats summary, and clearer section hierarchy
- Member borrow flow moved to a guided modal (search, QR scan, select items, submit request)
- Member active borrowing, return request, and request history sections are easier to scan and navigate
- Admin dashboard queue for pending member requests with approve/reject actions
- Approved member requests are converted into active borrow transactions automatically
- Pending member borrow/return requests auto-expire after 30 minutes (kept in history for reporting/audit)
- Equipment QR management: auto-generate on add/edit, regenerate on detail page, and downloadable QR image
- Member QR management: auto-repair missing QR on profile view + regenerate action
- Member-initiated return request workflow with condition/feedback capture
- Admin queue for pending return requests with physical-check approve/reject actions
- Approved return requests process actual returns and run overdue/damage violation checks
- Notification queue pipeline for borrow/return/reminder/overdue emails
- Automated reminder scheduler (due-tomorrow reminders + overdue warnings)
- Extended member profile requirements: phone, ID number, startup/agency, college department, program, year level
- Shared UI polish stylesheet for consistent shell/auth/dashboard presentation, responsive quick actions, and improved table/readability states
- CSRF protection on all forms
- Session timeout after 30 minutes of inactivity
- No back-button bypass after logout (cache headers)
- Dashboard with live stats — today's transactions, available equipment, active borrowings, overdue items
- Dashboard quick search across members, equipment, and transactions
- Recent activity feed
- **5 Essential Reports with filtering & CSV export:**
  - Active borrowings list (filter by member, equipment, facility area)
  - Overdue items report (filter by days overdue, member)
  - Member borrowing history (filter by member, date range)
  - Equipment usage report (usage stats, borrowing patterns, average duration)
  - Violation log (filter by violation type, member, date range)

**Planned (see `2-week_development_plan.txt`):**
- Gmail API native integration (database ready; SMTP queue fallback implemented)
- Google Calendar integration (database ready)

---

## Recent UI Updates

The member-side experience was updated to improve navigation and reduce friction for common tasks.

- Role-aware navigation now uses the shared authenticated layout, so members and staff get a consistent shell.
- Member dashboard now includes:
  - At-a-glance stats (member code, borrow count/limit, status, pending requests)
  - Quick actions for search, requests, returns, notifications, and profile
  - Cleaner history/return tables with clearer status badges
- Borrow request flow is now guided inside a modal for faster completion:
  - Search by equipment code/name/category
  - Optional QR scan input
  - Item selection with condition choice
  - Expected return date + usage area + notes
- Styling refresh focused on readability and responsiveness:
  - Better spacing and visual hierarchy
  - Improved empty states and table headers
  - Mobile-friendly action buttons and layout behavior

For a full breakdown, see `UI_IMPROVEMENTS.md`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3 · Flask 3 |
| Auth | Flask-Login · bcrypt |
| Forms | Flask-WTF · WTForms |
| Database | MySQL (XAMPP) · PyMySQL |
| Frontend | Jinja2 · Bootstrap 5 · Bootstrap Icons |
| Scheduling | APScheduler |
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
│   │   ├── member.py        # Member model helpers + member auth object
│   │   ├── member_request.py # Member self-service borrow request model
│   │   ├── member_return_request.py # Member-initiated return request model
│   │   └── equipment.py     # Equipment model helpers
│   ├── routes/
│   │   ├── auth.py          # auth + member profile completion + member dashboard + QR APIs
│   │   ├── borrow.py        # borrow/return/overdue/notifications APIs + pages
│   │   ├── dashboard.py     # /dashboard + quick search + pending request queue
│   │   ├── equipment.py     # equipment CRUD pages + equipment QR regenerate endpoint
│   │   ├── members.py       # signup + member QR scan/profile QR regenerate
│   │   └── staff_admin.py   # /staff/register
│   ├── templates/
│   │   ├── base.html        # Shared <head>, Bootstrap, CSS
│   │   ├── layouts/
│   │   │   └── main.html    # Authenticated layout (sidebar + topbar)
│   │   ├── auth/
│   │   │   ├── login.html
│   │   │   ├── request_access.html
│   │   │   ├── signup.html
│   │   │   ├── member_complete_profile.html
│   │   │   └── member_dashboard.html
│   │   ├── borrow/          # new, return, overdue, notifications, receipts
│   │   ├── dashboard/
│   │   │   └── index.html
│   │   ├── equipment/       # add, list, detail, edit
│   │   ├── members/         # register, scan
│   │   └── staff/
│   │       └── register.html
│   ├── static/              # CSS, JS, images, generated QRs
│   └── utils/
│       ├── db.py            # get_db() / close_db() per-request connection
│       ├── notifications.py # notification queue + SMTP sender helpers
│       └── qr.py            # member/equipment QR generation + payload extraction helpers
│
├── config/
│   └── config.py            # DevelopmentConfig / ProductionConfig
│
├── database/
│   ├── migrations/
│   │   └── equipment_borrowing.sql   # Full schema (10 tables)
│   └── seeds/
│       └── test_data.sql             # test seed data
│
├── migrations/
│   ├── 2026_03_16_equipment_label_fields.sql
│   ├── 2026_03_17_equipment_usage_restrictions.sql
│   ├── 2026_03_31_member_borrow_requests.sql
│   ├── 2026_03_31_member_profile_academic_fields.sql
│   ├── 2026_03_31_equipment_qr_path.sql
│   ├── 2026_03_31_member_return_requests.sql
│   └── 2026_03_31_request_status_expired.sql
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
git clone <your-repository-url>
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

Create a local `.env` file and set values for your environment.
Never commit real credentials to Git.

Example `.env` template:
```
DB_HOST=<db-host>
DB_PORT=<db-port>
DB_NAME=<db-name>
DB_USER=<db-user>
DB_PASSWORD=<db-password>

FLASK_APP=main.py
FLASK_ENV=development
SECRET_KEY=<long-random-secret-key>

# Google OAuth
GOOGLE_CLIENT_ID=<google-client-id>
GOOGLE_CLIENT_SECRET=<google-client-secret>
GOOGLE_REDIRECT_URI=<google-redirect-uri>
GOOGLE_ALLOWED_DOMAIN=<allowed-domain>

# Mail / notifications
MAIL_NOTIFICATIONS_ENABLED=true
MAIL_SERVER=<smtp-host>
MAIL_PORT=<smtp-port>
MAIL_USE_TLS=true
MAIL_USE_SSL=false
MAIL_USERNAME=<smtp-username>
MAIL_PASSWORD=<smtp-password-or-app-password>
MAIL_DEFAULT_SENDER=<sender-email>

# Reminder automation
REMINDER_AUTOMATION_ENABLED=true
REMINDER_JOB_INTERVAL_MINUTES=15
REMINDER_PROCESS_PENDING_ON_RUN=true
REQUEST_EXPIRY_MINUTES=30
```

> **Important:**
> - Do not commit `.env`, credentials, OAuth secrets, or private keys.
> - Rotate any credential immediately if it was ever pushed to a public repo.
> - Keep only placeholder values in documentation and sample config files.

**5. Set up the database**

Start XAMPP and ensure MySQL is running, then:

```bash
# Create the database in phpMyAdmin or via MySQL CLI
mysql -u root -p -e "CREATE DATABASE equipment_borrowing CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# Run the schema migration
mysql -u root -p equipment_borrowing < database/migrations/equipment_borrowing.sql

# Run incremental app migrations (skip files already applied in existing DBs)
mysql -u root -p equipment_borrowing < migrations/2026_03_17_equipment_usage_restrictions.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_member_borrow_requests.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_member_profile_academic_fields.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_equipment_qr_path.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_member_return_requests.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_request_status_expired.sql

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
- Member Google signup entry: **/signup**
- Member profile completion: **/member/complete-profile**
- Member self-service dashboard: **/member/dashboard**
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
| `MAIL_NOTIFICATIONS_ENABLED` | Enable/disable notification sending | `true` |
| `MAIL_SERVER` | SMTP server host | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USE_TLS` | Use STARTTLS for SMTP | `true` |
| `MAIL_USE_SSL` | Use SSL SMTP transport | `false` |
| `MAIL_USERNAME` | SMTP username | *(empty)* |
| `MAIL_PASSWORD` | SMTP password/app password | *(empty)* |
| `MAIL_DEFAULT_SENDER` | Sender email/display address | `MAIL_USERNAME` or `no-reply@localhost` |
| `REMINDER_AUTOMATION_ENABLED` | Enable background reminder scheduler | `true` |
| `REMINDER_JOB_INTERVAL_MINUTES` | Scheduler run interval in minutes | `15` |
| `REMINDER_PROCESS_PENDING_ON_RUN` | Process queued notifications every reminder cycle | `true` |
| `REQUEST_EXPIRY_MINUTES` | Minutes before pending borrow/return requests auto-expire | `30` |

---

## Database

The schema includes core borrowing tables plus request/approval tables for self-service member flow:

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
| `member_borrow_requests` | Member-submitted borrow requests pending review |
| `member_borrow_request_items` | Equipment items attached to each member request |
| `member_return_requests` | Member-submitted return requests pending physical-check review |

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
| Day 3 Afternoon — Member QR scanning | ✅ Done |
| Day 4 — Borrow transaction module | ✅ Done |
| Day 5 — Return module & email notifications | ✅ Done |
| Day 6 Morning — Overdue tracking | ✅ Done |
| Day 6 Afternoon — Reminder automation | ✅ Done |
| Day 7 Morning — Dashboard metrics + search | ✅ Done |
| Day 7 Afternoon — Reports & CSV export | ✅ Done |
| Member self-service request + approval flow | ✅ Done |
| Post-launch — Calendar, advanced notifications, HTML emails | 🔲 Phase 2 |
