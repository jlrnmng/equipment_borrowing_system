# QR Equipment Borrowing System

Flask-based web application for managing equipment borrowing with QR support, role-based access, and request approval workflows.

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Run](#run)
- [Important Routes](#important-routes)
- [Environment Variables](#environment-variables)
- [Database](#database)
- [Deployment Notes (Hostinger)](#deployment-notes-hostinger)
- [Security](#security)
- [Roadmap](#roadmap)

## Features

### Implemented

- Staff/admin authentication with secure bcrypt password hashing
- Google OAuth login for members (domain-restricted)
- Mandatory member profile completion before member dashboard access
- Equipment management: add, edit, list, detail, QR generation/regeneration
- Member borrow request flow with multi-item selection
- Optional accessory add-on suggestions when desktop equipment is selected
- Member return requests per item plus Return All action
- Staff approval/rejection queue for member borrow and return requests
- Auto-expiry for pending member requests (configurable)
- Overdue tracking and violation handling
- Notification queue and reminder automation scheduler
- Realtime updates via Socket.IO
- Reports with filters and CSV export

### Planned

- Google Calendar integration
- Expanded notification/channel features

See [2-week_development_plan.txt](2-week_development_plan.txt) for planned and phased work.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask |
| Auth | Flask-Login, bcrypt, Authlib |
| Forms | Flask-WTF, WTForms |
| Database | MySQL, PyMySQL |
| Frontend | Jinja2, Bootstrap 5, Bootstrap Icons |
| Realtime | Flask-SocketIO |
| Scheduling | APScheduler |
| Config | python-dotenv |

## Project Structure

```text
equipment_borrowing_system/
|- app/
|  |- routes/
|  |- models/
|  |- templates/
|  |- static/
|  |- utils/
|- config/
|- database/
|- migrations/
|- scripts/
|- tests/
|- main.py
|- requirements.txt
```

## Prerequisites

- Python 3.10+
- MySQL 8+ (or XAMPP MySQL)
- Git

## Setup

1. Clone repository

```bash
git clone <your-repository-url>
cd equipment_borrowing_system
```

2. Create and activate virtual environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

4. Create local .env

```env
DB_HOST=localhost
DB_PORT=3306
DB_NAME=equipment_borrowing
DB_USER=root
DB_PASSWORD=

FLASK_APP=main.py
FLASK_ENV=development
SECRET_KEY=<your-secret>

GOOGLE_CLIENT_ID=<google-client-id>
GOOGLE_CLIENT_SECRET=<google-client-secret>
GOOGLE_REDIRECT_URI=<google-redirect-uri>
GOOGLE_ALLOWED_DOMAIN=my.cspc.edu.ph

MAIL_NOTIFICATIONS_ENABLED=true
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USE_SSL=false
MAIL_USERNAME=<smtp-user>
MAIL_PASSWORD=<smtp-password>
MAIL_DEFAULT_SENDER=<sender-email>

REMINDER_AUTOMATION_ENABLED=true
REMINDER_JOB_INTERVAL_MINUTES=15
REMINDER_PROCESS_PENDING_ON_RUN=true
REQUEST_EXPIRY_MINUTES=30

SOCKETIO_CORS_ALLOWED_ORIGINS=*
SOCKETIO_MESSAGE_QUEUE=
```

5. Initialize database

```bash
mysql -u root -p -e "CREATE DATABASE equipment_borrowing CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -u root -p equipment_borrowing < database/migrations/equipment_borrowing.sql
```

6. Apply incremental migrations

```bash
mysql -u root -p equipment_borrowing < migrations/2026_03_16_equipment_label_fields.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_17_equipment_usage_restrictions.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_equipment_qr_path.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_member_borrow_requests.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_member_profile_academic_fields.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_member_return_requests.sql
mysql -u root -p equipment_borrowing < migrations/2026_03_31_request_status_expired.sql
mysql -u root -p equipment_borrowing < migrations/2026_04_13_member_approval_workflow.sql
mysql -u root -p equipment_borrowing < migrations/2026_04_16_equipment_image_path.sql
mysql -u root -p equipment_borrowing < migrations/2026_04_21_notifications_in_app_delivery_backfill.sql
mysql -u root -p equipment_borrowing < migrations/2026_04_21_notifications_read_tracking.sql
```

7. Create first admin account

```bash
python scripts/create_admin.py
```

## Run

```bash
python main.py
```

Open http://127.0.0.1:5000

## Important Routes

- Member signup: /signup
- Member dashboard: /member/dashboard
- Member complete profile: /member/complete-profile
- Staff login: /login
- Staff register: /staff/register

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| DB_HOST | MySQL host | localhost |
| DB_PORT | MySQL port | 3306 |
| DB_NAME | Database name | equipment_borrowing |
| DB_USER | MySQL username | root |
| DB_PASSWORD | MySQL password | empty |
| FLASK_APP | Flask app module | main.py |
| FLASK_ENV | Environment | development |
| SECRET_KEY | Flask session signing key | required |
| MAIL_NOTIFICATIONS_ENABLED | Enable notification sending | true |
| MAIL_SERVER | SMTP server host | smtp.gmail.com |
| MAIL_PORT | SMTP port | 587 |
| MAIL_USE_TLS | Use STARTTLS | true |
| MAIL_USE_SSL | Use SSL transport | false |
| MAIL_USERNAME | SMTP username | empty |
| MAIL_PASSWORD | SMTP password/app password | empty |
| MAIL_DEFAULT_SENDER | Default sender | MAIL_USERNAME or fallback |
| REMINDER_AUTOMATION_ENABLED | Enable scheduler | true |
| REMINDER_JOB_INTERVAL_MINUTES | Scheduler interval | 15 |
| REMINDER_PROCESS_PENDING_ON_RUN | Process queue per run | true |
| REQUEST_EXPIRY_MINUTES | Pending request expiry | 30 |
| SOCKETIO_CORS_ALLOWED_ORIGINS | Allowed realtime origins | * |
| SOCKETIO_MESSAGE_QUEUE | Optional Redis URL | empty |

## Database

Core and workflow tables include:

- staff
- members
- equipment
- borrow_records
- borrow_items
- violations
- notifications
- activity_log
- app_settings
- member_borrow_requests
- member_borrow_request_items
- member_return_requests
- google_calendar_events (phase-ready)

Full schema documentation: [database/database_documentation.md](database/database_documentation.md)

## Deployment Notes (Hostinger)

- Deploy as a separate subdomain/app instance.
- Keep Socket.IO client transport on default negotiation to allow polling fallback.
- Use websocket workers and reverse proxy upgrades only when hosting tier supports it.

## Security

- Passwords hashed with bcrypt
- CSRF protection enabled
- Session timeout enforced
- Parameterized SQL queries used
- Logout response disables cache on authenticated pages