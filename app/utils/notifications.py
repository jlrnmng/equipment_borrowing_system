from app.utils.db import get_db


def _queue_notification(
    member_id,
    borrow_id,
    notification_type,
    recipient_email,
    subject,
    message,
    channel='in_app',
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
                delivery_status,
                sent_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'sent', 'delivered', NOW())
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
    _update_notification_success(notification_id)
    return {'sent': True, 'notification_id': notification_id}


def queue_and_send_notification(
    member_id,
    borrow_id,
    notification_type,
    recipient_email,
    subject,
    message,
    channel='in_app',
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

    return {'queued': False, 'sent': True, 'notification_id': notification_id}


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


def build_borrow_request_review_message(member_name, request_code, status, review_notes=None):
    status_text = (status or '').strip().lower()
    if status_text == 'approved':
        headline = 'Your borrow request has been approved.'
    elif status_text == 'rejected':
        headline = 'Your borrow request has been rejected.'
    else:
        headline = f"Your borrow request status is now: {status_text or 'updated'}."

    note_block = f"\nReview notes: {review_notes}\n" if review_notes else ''
    return (
        f"Hello {member_name},\n\n"
        f"{headline}\n"
        f"Request: {request_code}\n"
        f"Status: {status_text or '-'}\n"
        f"{note_block}\n"
        "- DOST-CSPC ASOG TBI"
    )


def build_return_request_review_message(member_name, return_request_code, status, review_notes=None):
    status_text = (status or '').strip().lower()
    if status_text == 'approved':
        headline = 'Your return request has been approved and processed.'
    elif status_text == 'rejected':
        headline = 'Your return request has been rejected.'
    else:
        headline = f"Your return request status is now: {status_text or 'updated'}."

    note_block = f"\nReview notes: {review_notes}\n" if review_notes else ''
    return (
        f"Hello {member_name},\n\n"
        f"{headline}\n"
        f"Return request: {return_request_code}\n"
        f"Status: {status_text or '-'}\n"
        f"{note_block}\n"
        "- DOST-CSPC ASOG TBI"
    )
