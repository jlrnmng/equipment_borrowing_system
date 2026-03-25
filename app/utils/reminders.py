import atexit
import os
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from flask import current_app

from app.utils.db import get_db
from app.utils.notifications import process_existing_notification

_scheduler = None


def sync_overdue_status(db):
    """Keep borrow status aligned with expected return date and unreturned items."""
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE borrow_records br
            SET br.status = 'overdue',
                br.updated_at = CURRENT_TIMESTAMP
            WHERE br.status = 'active'
              AND br.expected_return_date < CURDATE()
              AND EXISTS (
                  SELECT 1
                  FROM borrow_items bi
                  WHERE bi.borrow_id = br.borrow_id
                    AND bi.returned_at IS NULL
              )
            """
        )
        updated_to_overdue = cursor.rowcount or 0

        cursor.execute(
            """
            UPDATE borrow_records br
            SET br.status = 'active',
                br.updated_at = CURRENT_TIMESTAMP
            WHERE br.status = 'overdue'
              AND br.expected_return_date >= CURDATE()
              AND EXISTS (
                  SELECT 1
                  FROM borrow_items bi
                  WHERE bi.borrow_id = br.borrow_id
                    AND bi.returned_at IS NULL
              )
            """
        )
        restored_to_active = cursor.rowcount or 0

        cursor.execute(
            """
            SELECT COUNT(*) AS total_overdue
            FROM borrow_records br
            WHERE br.status = 'overdue'
              AND EXISTS (
                  SELECT 1
                  FROM borrow_items bi
                  WHERE bi.borrow_id = br.borrow_id
                    AND bi.returned_at IS NULL
              )
            """
        )
        total_overdue = int((cursor.fetchone() or {}).get('total_overdue', 0))

    return {
        'updated_to_overdue': updated_to_overdue,
        'restored_to_active': restored_to_active,
        'total_overdue': total_overdue,
    }


def _has_notification_today(db, borrow_id, notification_type):
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT 1
            FROM notifications
            WHERE borrow_id = %s
              AND notification_type = %s
              AND DATE(created_at) = CURDATE()
            LIMIT 1
            """,
            (borrow_id, notification_type),
        )
        return cursor.fetchone() is not None


def _queue_notification_row(db, row, notification_type, subject, message):
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO notifications (
                member_id,
                borrow_id,
                notification_type,
                channel,
                recipient_email,
                subject,
                message,
                status,
                delivery_status
            ) VALUES (%s, %s, %s, 'email', %s, %s, %s, 'pending', 'queued')
            """,
            (
                row['member_id'],
                row['borrow_id'],
                notification_type,
                row['recipient_email'],
                subject,
                message,
            ),
        )
        return cursor.lastrowid


def _build_due_soon_message(row):
    member_name = f"{(row.get('first_name') or '').strip()} {(row.get('last_name') or '').strip()}".strip() or 'Member'
    return (
        f"Hello {member_name},\n\n"
        "This is a reminder that your borrowed equipment is due tomorrow.\n"
        f"Transaction: {row.get('transaction_code')}\n"
        f"Expected return date: {row.get('expected_return_date')}\n"
        f"Unreturned item(s): {row.get('unreturned_items')}\n\n"
        "Please return items on time to avoid overdue violations.\n"
        "Reminder: Equipment is for in-facility use only unless officially authorized.\n\n"
        "- DOST-CSPC ASOG TBI"
    )


def _build_overdue_warning_message(row):
    member_name = f"{(row.get('first_name') or '').strip()} {(row.get('last_name') or '').strip()}".strip() or 'Member'
    return (
        f"Hello {member_name},\n\n"
        "Your borrowed equipment is now overdue.\n"
        f"Transaction: {row.get('transaction_code')}\n"
        f"Expected return date: {row.get('expected_return_date')}\n"
        f"Days overdue: {row.get('days_overdue')}\n"
        f"Unreturned item(s): {row.get('unreturned_items')}\n\n"
        "Please return the item(s) immediately and coordinate with staff if you need assistance.\n\n"
        "- DOST-CSPC ASOG TBI"
    )


def queue_due_and_overdue_notifications(db):
    tomorrow = date.today() + timedelta(days=1)
    queued_due = 0
    queued_overdue = 0

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT br.borrow_id, br.transaction_code, br.expected_return_date,
                   m.member_id, m.first_name, m.last_name,
                   COALESCE(NULLIF(TRIM(m.email), ''), NULLIF(TRIM(m.google_email), '')) AS recipient_email,
                   COUNT(CASE WHEN bi.returned_at IS NULL THEN 1 END) AS unreturned_items
            FROM borrow_records br
            INNER JOIN members m ON m.member_id = br.member_id
            INNER JOIN borrow_items bi ON bi.borrow_id = br.borrow_id
            WHERE br.status = 'active'
              AND br.expected_return_date = %s
              AND bi.returned_at IS NULL
              AND COALESCE(NULLIF(TRIM(m.email), ''), NULLIF(TRIM(m.google_email), '')) IS NOT NULL
            GROUP BY br.borrow_id, br.transaction_code, br.expected_return_date,
                     m.member_id, m.first_name, m.last_name, recipient_email
            """,
            (tomorrow,),
        )
        due_rows = cursor.fetchall()

    for row in due_rows:
        if _has_notification_today(db, row['borrow_id'], 'reminder'):
            continue

        subject = f"Return Reminder (Due Tomorrow) - {row.get('transaction_code')}"
        _queue_notification_row(
            db=db,
            row=row,
            notification_type='reminder',
            subject=subject,
            message=_build_due_soon_message(row),
        )
        queued_due += 1

    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT br.borrow_id, br.transaction_code, br.expected_return_date,
                   DATEDIFF(CURDATE(), br.expected_return_date) AS days_overdue,
                   m.member_id, m.first_name, m.last_name,
                   COALESCE(NULLIF(TRIM(m.email), ''), NULLIF(TRIM(m.google_email), '')) AS recipient_email,
                   COUNT(CASE WHEN bi.returned_at IS NULL THEN 1 END) AS unreturned_items
            FROM borrow_records br
            INNER JOIN members m ON m.member_id = br.member_id
            INNER JOIN borrow_items bi ON bi.borrow_id = br.borrow_id
            WHERE br.status = 'overdue'
              AND br.expected_return_date < CURDATE()
              AND bi.returned_at IS NULL
              AND COALESCE(NULLIF(TRIM(m.email), ''), NULLIF(TRIM(m.google_email), '')) IS NOT NULL
            GROUP BY br.borrow_id, br.transaction_code, br.expected_return_date,
                     m.member_id, m.first_name, m.last_name, recipient_email
            """
        )
        overdue_rows = cursor.fetchall()

    for row in overdue_rows:
        if _has_notification_today(db, row['borrow_id'], 'overdue'):
            continue

        subject = f"Overdue Warning - {row.get('transaction_code')}"
        _queue_notification_row(
            db=db,
            row=row,
            notification_type='overdue',
            subject=subject,
            message=_build_overdue_warning_message(row),
        )
        queued_overdue += 1

    return {'queued_due_reminders': queued_due, 'queued_overdue_warnings': queued_overdue}


def process_pending_notifications(limit=50):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT notification_id, recipient_email, subject, message
            FROM notifications
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

    processed = 0
    sent = 0
    failed = 0

    for row in rows:
        result = process_existing_notification(
            notification_id=row['notification_id'],
            recipient_email=row['recipient_email'],
            subject=row['subject'],
            message=row['message'],
        )
        processed += 1
        if result.get('sent'):
            sent += 1
        else:
            failed += 1

    return {'processed': processed, 'sent': sent, 'failed': failed}


def run_reminder_cycle():
    """Single execution cycle for overdue sync + reminder queue + optional sender."""
    db = get_db()
    try:
        sync_summary = sync_overdue_status(db)
        queued_summary = queue_due_and_overdue_notifications(db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    sent_summary = {'processed': 0, 'sent': 0, 'failed': 0}
    if current_app.config.get('REMINDER_PROCESS_PENDING_ON_RUN', True):
        sent_summary = process_pending_notifications(limit=50)

    return {
        'sync': sync_summary,
        'queued': queued_summary,
        'delivery': sent_summary,
    }


def _scheduler_runner(flask_app):
    with flask_app.app_context():
        try:
            result = run_reminder_cycle()
            flask_app.logger.info('Reminder automation cycle finished: %s', result)
        except Exception:
            flask_app.logger.exception('Reminder automation cycle failed.')


def start_scheduler(flask_app):
    global _scheduler

    if _scheduler is not None:
        return _scheduler

    interval_minutes = max(1, int(flask_app.config.get('REMINDER_JOB_INTERVAL_MINUTES', 15)))

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _scheduler_runner,
        'interval',
        minutes=interval_minutes,
        id='reminder-automation-cycle',
        replace_existing=True,
        args=[flask_app],
    )
    _scheduler.start()

    flask_app.logger.info('Reminder scheduler started (every %s minute(s)).', interval_minutes)

    def _shutdown_scheduler():
        global _scheduler
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)
        _scheduler = None

    atexit.register(_shutdown_scheduler)
    return _scheduler


def maybe_start_scheduler(flask_app):
    if not flask_app.config.get('REMINDER_AUTOMATION_ENABLED', True):
        flask_app.logger.info('Reminder scheduler disabled by config.')
        return None

    # Avoid duplicate scheduler jobs in Flask reloader parent process.
    if flask_app.debug and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        return None

    return start_scheduler(flask_app)
