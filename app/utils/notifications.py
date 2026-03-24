import smtplib
from email.message import EmailMessage

from flask import current_app

from app.utils.db import get_db


def _mail_is_configured():
    return bool(
        current_app.config.get('MAIL_NOTIFICATIONS_ENABLED', True)
        and current_app.config.get('MAIL_SERVER')
        and current_app.config.get('MAIL_PORT')
        and current_app.config.get('MAIL_USERNAME')
        and current_app.config.get('MAIL_PASSWORD')
    )


def _send_email(recipient_email, subject, body):
    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = current_app.config.get('MAIL_DEFAULT_SENDER')
    message['To'] = recipient_email
    message.set_content(body)

    mail_server = current_app.config.get('MAIL_SERVER')
    mail_port = int(current_app.config.get('MAIL_PORT', 587))
    use_tls = bool(current_app.config.get('MAIL_USE_TLS', True))
    use_ssl = bool(current_app.config.get('MAIL_USE_SSL', False))
    username = current_app.config.get('MAIL_USERNAME')
    password = current_app.config.get('MAIL_PASSWORD')

    if use_ssl:
        with smtplib.SMTP_SSL(mail_server, mail_port, timeout=20) as server:
            server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(mail_server, mail_port, timeout=20) as server:
            if use_tls:
                server.starttls()
            server.login(username, password)
            server.send_message(message)


def _queue_notification(
    member_id,
    borrow_id,
    notification_type,
    recipient_email,
    subject,
    message,
    channel='email',
):
    db = get_db()
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
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', 'queued')
            """,
            (
                member_id,
                borrow_id,
                notification_type,
                channel,
                recipient_email,
                subject,
                message,
            ),
        )
        notification_id = cursor.lastrowid
    db.commit()
    return notification_id


def _update_notification_success(notification_id):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE notifications
            SET status = 'sent',
                sent_at = NOW(),
                delivery_status = 'delivered',
                error_message = NULL
            WHERE notification_id = %s
            """,
            (notification_id,),
        )
    db.commit()


def _update_notification_failure(notification_id, error_message):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE notifications
            SET status = 'failed',
                delivery_status = 'failed',
                retry_count = retry_count + 1,
                error_message = %s
            WHERE notification_id = %s
            """,
            (error_message[:1000], notification_id),
        )
    db.commit()


def process_existing_notification(notification_id, recipient_email, subject, message):
    if not _mail_is_configured():
        return {'sent': False, 'notification_id': notification_id, 'error': 'Mail service is not configured.'}

    try:
        _send_email(recipient_email=recipient_email, subject=subject, body=message)
        _update_notification_success(notification_id)
        return {'sent': True, 'notification_id': notification_id}
    except Exception as exc:
        _update_notification_failure(notification_id, str(exc))
        return {'sent': False, 'notification_id': notification_id, 'error': str(exc)}


def queue_and_send_notification(
    member_id,
    borrow_id,
    notification_type,
    recipient_email,
    subject,
    message,
    channel='email',
):
    notification_id = _queue_notification(
        member_id=member_id,
        borrow_id=borrow_id,
        notification_type=notification_type,
        recipient_email=recipient_email,
        subject=subject,
        message=message,
        channel=channel,
    )

    if not _mail_is_configured():
        # Keep notification queued/pending until mail credentials are configured.
        return {'queued': True, 'sent': False, 'notification_id': notification_id}

    try:
        _send_email(recipient_email=recipient_email, subject=subject, body=message)
        _update_notification_success(notification_id)
        return {'queued': True, 'sent': True, 'notification_id': notification_id}
    except Exception as exc:
        _update_notification_failure(notification_id, str(exc))
        return {'queued': True, 'sent': False, 'notification_id': notification_id, 'error': str(exc)}


def build_welcome_message(member_name, member_code):
    return (
        f"Hello {member_name},\n\n"
        "Welcome to the QR Equipment Borrowing System.\n"
        f"Your member code is: {member_code}\n\n"
        "You can now use your account for equipment borrowing workflows.\n"
        "Please keep your member QR code safe.\n\n"
        "- DOST-CSPC ASOG TBI"
    )


def build_borrow_confirmation_message(member_name, transaction_code, expected_return_date, usage_area, total_items):
    return (
        f"Hello {member_name},\n\n"
        "Your borrow transaction has been recorded.\n"
        f"Transaction: {transaction_code}\n"
        f"Items borrowed: {total_items}\n"
        f"Usage area: {usage_area}\n"
        f"Expected return date: {expected_return_date}\n\n"
        "Reminder: Equipment is for in-facility use only unless officially authorized.\n\n"
        "- DOST-CSPC ASOG TBI"
    )


def build_return_confirmation_message(member_name, transaction_code, equipment_name, condition_returned, days_overdue):
    overdue_note = (
        f"This return was marked overdue by {days_overdue} day(s).\n"
        if days_overdue > 0
        else "Return was processed on time.\n"
    )
    return (
        f"Hello {member_name},\n\n"
        "Your return has been processed successfully.\n"
        f"Transaction: {transaction_code}\n"
        f"Equipment: {equipment_name}\n"
        f"Returned condition: {condition_returned}\n"
        f"{overdue_note}\n"
        "- DOST-CSPC ASOG TBI"
    )
