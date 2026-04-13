from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from flask import flash
from flask_login import current_user, login_required

from app.models.equipment import Equipment
from app.models.member import Member
from app.models.member_request import MemberBorrowRequest
from app.models.member_return_request import MemberReturnRequest
from app.realtime import emit_app_data_changed
from app.utils.request_expiry import expire_stale_requests
from app.utils.db import get_db
from app.utils.notifications import (
    build_borrow_confirmation_message,
    build_borrow_request_review_message,
    build_return_confirmation_message,
    build_return_request_review_message,
    queue_and_send_notification,
)
from app.utils.reminders import run_reminder_cycle, process_pending_notifications as process_pending_notifications_service

borrow_bp = Blueprint('borrow', __name__)


def _is_authorized_borrower():
    return current_user.role in ('admin', 'staff')


def _is_within_working_hours(now=None):
    now = now or datetime.now()
    # Monday=0 to Friday=4, 08:00-16:30
    if now.weekday() > 4:
        return False
    current_minutes = now.hour * 60 + now.minute
    return 8 * 60 <= current_minutes <= (16 * 60 + 30)


def _compute_overdue_with_grace(expected_return_date, now=None):
    """Determine overdue status with a 30-minute grace period after 4:30 PM cutoff."""
    if not expected_return_date:
        return False, 0

    now = now or datetime.now()
    overdue_cutoff = datetime.combine(expected_return_date, datetime.min.time()).replace(hour=17, minute=0, second=0, microsecond=0)

    if now <= overdue_cutoff:
        return False, 0

    days_overdue = max(1, (now.date() - expected_return_date).days)
    return True, days_overdue


def _get_member_from_query():
    member_code = (request.args.get('member_code') or '').strip().upper()
    query = (request.args.get('query') or '').strip()

    if member_code:
        return Member.get_by_member_code(member_code)

    if query:
        if query.upper().startswith('MEM'):
            return Member.get_by_member_code(query.upper())
        if '@' in query:
            return Member.get_by_email_or_google_email(query.lower())

    return None


def _member_overdue_count(member_id):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM borrow_records
            WHERE member_id = %s
              AND status IN ('active', 'overdue')
                            AND (
                                        status = 'overdue'
                                 OR expected_return_date < CURDATE()
                                 OR (expected_return_date = CURDATE() AND CURTIME() > '17:00:00')
                            )
            """,
            (member_id,),
        )
        row = cursor.fetchone()
    return int((row or {}).get('cnt', 0))


def _build_member_eligibility(member_row):
    if not member_row:
        return {
            'eligible': False,
            'checks': {
                'member_exists': False,
                'active_status': False,
                'within_borrow_limit': False,
                'no_overdue_items': False,
            },
            'message': 'Member not found.',
        }

    status = (member_row.get('status') or '').lower()
    current_borrow_count = int(member_row.get('current_borrow_count') or 0)
    max_borrow_limit = int(member_row.get('max_borrow_limit') or 0)
    overdue_count = _member_overdue_count(member_row['member_id'])

    checks = {
        'member_exists': True,
        'active_status': status == 'active',
        'within_borrow_limit': current_borrow_count < max_borrow_limit,
        'no_overdue_items': overdue_count == 0,
    }

    messages = []
    if not checks['active_status']:
        messages.append('Member status is not active.')
    if not checks['within_borrow_limit']:
        messages.append('Member has reached max borrow limit.')
    if not checks['no_overdue_items']:
        messages.append('Member has overdue borrowing records.')

    return {
        'eligible': all(checks.values()),
        'checks': checks,
        'message': 'Eligible for borrowing.' if not messages else ' '.join(messages),
        'current_borrow_count': current_borrow_count,
        'max_borrow_limit': max_borrow_limit,
        'overdue_count': overdue_count,
    }


def _serialize_member(row):
    middle_name = (row.get('middle_name') or '').strip()
    full_name = f"{row.get('first_name', '').strip()} {middle_name} {row.get('last_name', '').strip()}".replace('  ', ' ').strip()
    return {
        'member_id': row.get('member_id'),
        'member_code': row.get('member_code'),
        'full_name': full_name,
        'email': row.get('email') or row.get('google_email'),
        'startup': row.get('startup'),
        'status': row.get('status'),
    }


def _fetch_equipment_by_ids(equipment_ids):
    if not equipment_ids:
        return []

    placeholders = ','.join(['%s'] * len(equipment_ids))
    query = (
        "SELECT equipment_id, equipment_code, equipment_name, category, status, condition_status, "
        "location, requires_supervision, restricted_areas "
        f"FROM equipment WHERE equipment_id IN ({placeholders})"
    )

    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(query, tuple(equipment_ids))
        return cursor.fetchall()


def _generate_transaction_code(db):
    prefix = datetime.now().strftime('BRW-%Y%m%d')
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT transaction_code
            FROM borrow_records
            WHERE transaction_code LIKE %s
            ORDER BY borrow_id DESC
            LIMIT 1
            """,
            (f"{prefix}-%",),
        )
        row = cursor.fetchone()

    if row and row.get('transaction_code'):
        last_code = row['transaction_code']
        try:
            sequence = int(last_code.split('-')[-1]) + 1
        except Exception:
            sequence = 1
    else:
        sequence = 1
    return f"{prefix}-{sequence:04d}"


def _parse_expected_return_date(value):
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d').date()
    except Exception:
        return None
    return parsed


def _log_activity(db, action, description, member_id=None):
    with db.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO activity_log (staff_id, member_id, action, table_name, description)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (current_user.id, member_id, action, 'borrow_records', description),
        )


def _fetch_receipt_data(transaction_code):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT br.borrow_id, br.transaction_code, br.borrow_date, br.expected_return_date,
                   br.status, br.total_items, br.usage_area, br.requires_supervision,
                   br.borrowed_during_working_hours,
                   m.member_code, m.first_name, m.middle_name, m.last_name, m.email, m.startup,
                   s.full_name AS processed_by_name
            FROM borrow_records br
            INNER JOIN members m ON m.member_id = br.member_id
            LEFT JOIN staff s ON s.staff_id = br.processed_by
            WHERE br.transaction_code = %s
            LIMIT 1
            """,
            (transaction_code,),
        )
        header = cursor.fetchone()

        if not header:
            return None, []

        cursor.execute(
            """
            SELECT bi.borrow_item_id, bi.condition_borrowed,
                   e.equipment_code, e.equipment_name, e.category, e.location
            FROM borrow_items bi
            INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
            WHERE bi.borrow_id = %s
            ORDER BY bi.borrow_item_id ASC
            """,
            (header['borrow_id'],),
        )
        items = cursor.fetchall()

    return header, items


def _condition_rank(value):
    order = {
        'excellent': 4,
        'good': 3,
        'fair': 2,
        'poor': 1,
    }
    return order.get((value or '').strip().lower(), 0)


def _is_condition_worse(borrowed, returned):
    return _condition_rank(returned) < _condition_rank(borrowed)


def _fetch_member_active_borrowed_items(member_id):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT bi.borrow_item_id, bi.borrow_id, bi.condition_borrowed, bi.borrowed_at,
                   br.transaction_code, br.borrow_date, br.expected_return_date, br.status AS borrow_status,
                   e.equipment_id, e.equipment_code, e.equipment_name, e.category, e.location
            FROM borrow_items bi
            INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
            INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
            WHERE br.member_id = %s
              AND br.status IN ('active', 'overdue')
              AND bi.returned_at IS NULL
            ORDER BY br.borrow_date ASC, bi.borrow_item_id ASC
            """,
            (member_id,),
        )
        return cursor.fetchall()


def _fetch_return_receipt_data(return_record):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT m.member_code, m.first_name, m.middle_name, m.last_name,
                   e.equipment_code, e.equipment_name,
                   br.transaction_code, br.expected_return_date,
                   s.full_name AS returned_by_name
            FROM borrow_items bi
            INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
            INNER JOIN members m ON m.member_id = br.member_id
            INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
            LEFT JOIN staff s ON s.staff_id = %s
            WHERE bi.borrow_item_id = %s
            LIMIT 1
            """,
            (current_user.id, return_record['borrow_item_id']),
        )
        return cursor.fetchone()


def _finalize_return_item(db, item_row, condition_returned, notes, source_return_request_id=None, source_review_notes=None):
    expected_return_date = item_row.get('expected_return_date')
    is_overdue, days_overdue = _compute_overdue_with_grace(expected_return_date, now=datetime.now())
    is_damaged = _is_condition_worse(item_row.get('condition_borrowed'), condition_returned)

    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE borrow_items
            SET condition_returned = %s,
                returned_at = NOW(),
                notes = %s
            WHERE borrow_item_id = %s
            """,
            (condition_returned, notes, item_row['borrow_item_id']),
        )

        cursor.execute(
            """
            UPDATE equipment
            SET status = 'available',
                condition_status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE equipment_id = %s
            """,
            (condition_returned, item_row['equipment_id']),
        )

        cursor.execute(
            """
            UPDATE members
            SET current_borrow_count = GREATEST(current_borrow_count - 1, 0),
                updated_at = CURRENT_TIMESTAMP
            WHERE member_id = %s
            """,
            (item_row['member_id'],),
        )

        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM borrow_items WHERE borrow_id = %s AND returned_at IS NULL",
            (item_row['borrow_id'],),
        )
        remaining = int((cursor.fetchone() or {}).get('cnt', 0))

        if remaining == 0:
            cursor.execute(
                """
                UPDATE borrow_records
                SET status = 'returned',
                    actual_return_date = NOW(),
                    returned_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE borrow_id = %s
                """,
                (current_user.id, item_row['borrow_id']),
            )
        else:
            new_status = 'overdue' if is_overdue else 'active'
            cursor.execute(
                """
                UPDATE borrow_records
                SET status = %s,
                    returned_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE borrow_id = %s
                """,
                (new_status, current_user.id, item_row['borrow_id']),
            )

        if is_overdue:
            cursor.execute(
                """
                INSERT INTO violations (
                    member_id, borrow_id, equipment_id, violation_type,
                    days_overdue, description, status
                ) VALUES (%s, %s, %s, 'overdue', %s, %s, 'pending')
                """,
                (
                    item_row['member_id'],
                    item_row['borrow_id'],
                    item_row['equipment_id'],
                    days_overdue,
                    f"Returned {days_overdue} day(s) late for transaction {item_row['transaction_code']}.",
                ),
            )

        if is_damaged:
            cursor.execute(
                """
                INSERT INTO violations (
                    member_id, borrow_id, equipment_id, violation_type,
                    description, status
                ) VALUES (%s, %s, %s, 'damage', %s, 'pending')
                """,
                (
                    item_row['member_id'],
                    item_row['borrow_id'],
                    item_row['equipment_id'],
                    f"Condition changed from {item_row['condition_borrowed']} to {condition_returned}.",
                ),
            )

        if source_return_request_id:
            cursor.execute(
                """
                UPDATE member_return_requests
                SET status = 'approved',
                    final_condition = %s,
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    review_notes = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE return_request_id = %s
                """,
                (condition_returned, current_user.id, source_review_notes, source_return_request_id),
            )

    _log_activity(
        db,
        action='RETURN_PROCESSED',
        description=(
            f"Processed return for item {item_row['borrow_item_id']} in transaction "
            f"{item_row['transaction_code']}."
        ),
        member_id=item_row['member_id'],
    )

    return {
        'borrow_item_id': item_row['borrow_item_id'],
        'days_overdue': days_overdue,
        'is_damaged': is_damaged,
        'condition_borrowed': item_row.get('condition_borrowed'),
        'condition_returned': condition_returned,
    }

@borrow_bp.route('/borrow/requests/<int:request_id>/approve', methods=['POST'])
@login_required
def approve_member_request(request_id):
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    expire_stale_requests(expiry_minutes=current_app.config.get('REQUEST_EXPIRY_MINUTES', 30))

    request_row, request_items = MemberBorrowRequest.get_request_detail(request_id)
    if not request_row:
        flash('Borrow request not found.', 'warning')
        return redirect(url_for('dashboard.index'))

    if request_row.get('status') != 'pending':
        flash('This request is already reviewed.', 'warning')
        return redirect(url_for('dashboard.index'))

    member_row = Member.get_by_member_code((request_row.get('member_code') or '').strip().upper())
    eligibility = _build_member_eligibility(member_row)
    if not member_row or not eligibility.get('eligible'):
        flash(f"Cannot approve request {request_row.get('request_code')}: {eligibility.get('message', 'Member is not eligible.')}", 'danger')
        return redirect(url_for('dashboard.index'))

    if not request_items:
        flash('Cannot approve a request without equipment items.', 'danger')
        return redirect(url_for('dashboard.index'))

    unavailable = [item for item in request_items if (item.get('status') or '').lower() != 'available']
    if unavailable:
        codes = ', '.join((item.get('equipment_code') or str(item.get('equipment_id'))) for item in unavailable)
        flash(f'Cannot approve request because these items are unavailable: {codes}', 'danger')
        return redirect(url_for('dashboard.index'))

    review_notes = (request.form.get('review_notes') or '').strip() or None
    expected_return_date = request_row.get('expected_return_date')
    if not expected_return_date or expected_return_date < datetime.now().date():
        flash('Cannot approve request with an invalid or past expected return date.', 'danger')
        return redirect(url_for('dashboard.index'))

    db = get_db()
    try:
        transaction_code = _generate_transaction_code(db)
        requires_supervision = any(bool(item.get('requires_supervision')) for item in request_items)
        borrowed_during_working_hours = _is_within_working_hours()

        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO borrow_records (
                    transaction_code,
                    member_id,
                    expected_return_date,
                    status,
                    total_items,
                    usage_area,
                    requires_supervision,
                    borrowed_during_working_hours,
                    processed_by,
                    notes
                ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s, %s)
                """,
                (
                    transaction_code,
                    member_row['member_id'],
                    expected_return_date,
                    len(request_items),
                    request_row.get('usage_area'),
                    requires_supervision,
                    borrowed_during_working_hours,
                    current_user.id,
                    request_row.get('notes'),
                ),
            )
            borrow_id = cursor.lastrowid

            for item in request_items:
                cursor.execute(
                    """
                    INSERT INTO borrow_items (borrow_id, equipment_id, condition_borrowed)
                    VALUES (%s, %s, %s)
                    """,
                    (borrow_id, item['equipment_id'], item.get('condition_requested') or 'good'),
                )

            for item in request_items:
                cursor.execute(
                    "UPDATE equipment SET status = 'borrowed', updated_at = CURRENT_TIMESTAMP WHERE equipment_id = %s",
                    (item['equipment_id'],),
                )

            cursor.execute(
                """
                UPDATE members
                SET current_borrow_count = current_borrow_count + %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE member_id = %s
                """,
                (len(request_items), member_row['member_id']),
            )

            cursor.execute(
                """
                UPDATE member_borrow_requests
                SET status = 'approved',
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    review_notes = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                """,
                (current_user.id, review_notes, request_id),
            )

        _log_activity(
            db,
            action='BORROW_REQUEST_APPROVED',
            description=f"Approved member request {request_row.get('request_code')} and created transaction {transaction_code}.",
            member_id=member_row['member_id'],
        )
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Failed to approve member borrow request %s', request_id)
        flash('Unable to approve request due to an unexpected error.', 'danger')
        return redirect(url_for('dashboard.index'))

    # Notify member after admin approval. If mail is not configured, this is queued as pending.
    recipient_email = (request_row.get('email') or request_row.get('google_email') or '').strip()
    if recipient_email:
        try:
            member_name = f"{request_row.get('first_name', '')} {request_row.get('last_name', '')}".strip() or request_row.get('member_code')
            notify_result = queue_and_send_notification(
                member_id=member_row['member_id'],
                borrow_id=borrow_id,
                notification_type='borrow_confirmation',
                recipient_email=recipient_email,
                subject=f'Borrow Request Approved - {transaction_code}',
                message=build_borrow_confirmation_message(
                    member_name=member_name,
                    transaction_code=transaction_code,
                    expected_return_date=str(expected_return_date),
                    usage_area=request_row.get('usage_area') or '-',
                    total_items=len(request_items),
                ),
            )
            if not notify_result.get('sent'):
                flash('Borrow approval saved, but email was not delivered (queued or failed). Check Notification Queue.', 'warning')
        except Exception:
            current_app.logger.exception('Failed to queue/send approval notification for borrow request %s', request_id)

    flash(f"Request {request_row.get('request_code')} approved. Borrow transaction {transaction_code} created.", 'success')
    emit_app_data_changed(
        reason='member_borrow_request_approved',
        member_id=member_row['member_id'],
        include_staff=True,
        include_members=True,
    )
    return redirect(url_for('dashboard.index'))


@borrow_bp.route('/borrow/requests/<int:request_id>/reject', methods=['POST'])
@login_required
def reject_member_request(request_id):
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    expire_stale_requests(expiry_minutes=current_app.config.get('REQUEST_EXPIRY_MINUTES', 30))

    request_row, _ = MemberBorrowRequest.get_request_detail(request_id)
    if not request_row:
        flash('Borrow request not found.', 'warning')
        return redirect(url_for('dashboard.index'))

    if request_row.get('status') != 'pending':
        flash('This request is already reviewed.', 'warning')
        return redirect(url_for('dashboard.index'))

    review_notes = (request.form.get('review_notes') or '').strip() or None
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE member_borrow_requests
                SET status = 'rejected',
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    review_notes = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                """,
                (current_user.id, review_notes, request_id),
            )

        _log_activity(
            db,
            action='BORROW_REQUEST_REJECTED',
            description=f"Rejected member request {request_row.get('request_code')}.",
            member_id=request_row.get('member_id'),
        )
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Failed to reject member borrow request %s', request_id)
        flash('Unable to reject request due to an unexpected error.', 'danger')
        return redirect(url_for('dashboard.index'))

    flash(f"Request {request_row.get('request_code')} has been rejected.", 'info')
    emit_app_data_changed(
        reason='member_borrow_request_rejected',
        member_id=request_row.get('member_id'),
        include_staff=True,
        include_members=True,
    )

    recipient_email = (request_row.get('email') or request_row.get('google_email') or '').strip()
    if recipient_email:
        try:
            member_name = f"{request_row.get('first_name', '')} {request_row.get('last_name', '')}".strip() or request_row.get('member_code')
            notify_result = queue_and_send_notification(
                member_id=request_row.get('member_id'),
                borrow_id=None,
                notification_type='borrow_request_rejected',
                recipient_email=recipient_email,
                subject=f"Borrow Request Rejected - {request_row.get('request_code')}",
                message=build_borrow_request_review_message(
                    member_name=member_name,
                    request_code=request_row.get('request_code') or '-',
                    status='rejected',
                    review_notes=review_notes,
                ),
            )
            if not notify_result.get('sent'):
                flash('Borrow rejection saved, but email was not delivered (queued or failed). Check Notification Queue.', 'warning')
        except Exception:
            current_app.logger.exception('Failed to queue/send rejection notification for borrow request %s', request_id)

    return redirect(url_for('dashboard.index'))


@borrow_bp.route('/borrow/return-requests/<int:return_request_id>/approve', methods=['POST'])
@login_required
def approve_member_return_request(return_request_id):
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    expire_stale_requests(expiry_minutes=current_app.config.get('REQUEST_EXPIRY_MINUTES', 30))

    request_row = MemberReturnRequest.get_request_detail(return_request_id)
    if not request_row:
        flash('Return request not found.', 'warning')
        return redirect(url_for('dashboard.index'))

    if request_row.get('status') != 'pending':
        flash('This return request is already reviewed.', 'warning')
        return redirect(url_for('dashboard.index'))

    if request_row.get('returned_at') is not None:
        flash('This item is already returned.', 'warning')
        return redirect(url_for('dashboard.index'))

    if (request_row.get('borrow_status') or '').lower() not in ('active', 'overdue'):
        flash('Associated borrow transaction is not active.', 'danger')
        return redirect(url_for('dashboard.index'))

    final_condition = (request.form.get('final_condition') or '').strip().lower()
    review_notes = (request.form.get('review_notes') or '').strip() or None
    if final_condition not in ('excellent', 'good', 'fair', 'poor'):
        flash('Invalid final condition selected for return approval.', 'danger')
        return redirect(url_for('dashboard.index'))

    db = get_db()
    try:
        receipt_meta = _finalize_return_item(
            db=db,
            item_row=request_row,
            condition_returned=final_condition,
            notes=request_row.get('member_feedback'),
            source_return_request_id=return_request_id,
            source_review_notes=review_notes,
        )
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Failed to approve member return request %s', return_request_id)
        flash('Unable to approve return request due to an unexpected error.', 'danger')
        return redirect(url_for('dashboard.index'))

    # Notify member after return approval. If mail is not configured, this is queued as pending.
    recipient_email = (request_row.get('email') or request_row.get('google_email') or '').strip()
    if recipient_email:
        try:
            member_name = f"{request_row.get('first_name', '')} {request_row.get('last_name', '')}".strip() or request_row.get('member_code')
            notify_result = queue_and_send_notification(
                member_id=request_row['member_id'],
                borrow_id=request_row['borrow_id'],
                notification_type='return_confirmation',
                recipient_email=recipient_email,
                subject=f"Return Request Approved - {request_row.get('transaction_code')}",
                message=build_return_confirmation_message(
                    member_name=member_name,
                    transaction_code=request_row.get('transaction_code') or '-',
                    equipment_name=request_row.get('equipment_name') or 'Equipment item',
                    condition_returned=final_condition,
                    days_overdue=receipt_meta['days_overdue'],
                ),
            )
            if not notify_result.get('sent'):
                flash('Return approval processed, but email was not delivered (queued or failed). Check Notification Queue.', 'warning')
        except Exception:
            current_app.logger.exception('Failed to queue/send approval notification for return request %s', return_request_id)

    flash(
        f"Return request {request_row.get('return_request_code')} approved and processed for "
        f"{request_row.get('equipment_code')}.",
        'success'
    )
    emit_app_data_changed(
        reason='member_return_request_approved',
        member_id=request_row.get('member_id'),
        include_staff=True,
        include_members=True,
    )
    return redirect(url_for('borrow.return_receipt', borrow_item_id=receipt_meta['borrow_item_id']))


@borrow_bp.route('/borrow/return-requests/<int:return_request_id>/reject', methods=['POST'])
@login_required
def reject_member_return_request(return_request_id):
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    expire_stale_requests(expiry_minutes=current_app.config.get('REQUEST_EXPIRY_MINUTES', 30))

    request_row = MemberReturnRequest.get_request_detail(return_request_id)
    if not request_row:
        flash('Return request not found.', 'warning')
        return redirect(url_for('dashboard.index'))

    if request_row.get('status') != 'pending':
        flash('This return request is already reviewed.', 'warning')
        return redirect(url_for('dashboard.index'))

    review_notes = (request.form.get('review_notes') or '').strip() or None
    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE member_return_requests
                SET status = 'rejected',
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    review_notes = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE return_request_id = %s
                """,
                (current_user.id, review_notes, return_request_id),
            )

        _log_activity(
            db,
            action='RETURN_REQUEST_REJECTED',
            description=f"Rejected member return request {request_row.get('return_request_code')}.",
            member_id=request_row.get('member_id'),
        )
        db.commit()
    except Exception:
        db.rollback()
        current_app.logger.exception('Failed to reject member return request %s', return_request_id)
        flash('Unable to reject return request due to an unexpected error.', 'danger')
        return redirect(url_for('dashboard.index'))

    flash(f"Return request {request_row.get('return_request_code')} has been rejected.", 'info')
    emit_app_data_changed(
        reason='member_return_request_rejected',
        member_id=request_row.get('member_id'),
        include_staff=True,
        include_members=True,
    )

    recipient_email = (request_row.get('email') or request_row.get('google_email') or '').strip()
    if recipient_email:
        try:
            member_name = f"{request_row.get('first_name', '')} {request_row.get('last_name', '')}".strip() or request_row.get('member_code')
            notify_result = queue_and_send_notification(
                member_id=request_row.get('member_id'),
                borrow_id=request_row.get('borrow_id'),
                notification_type='return_request_rejected',
                recipient_email=recipient_email,
                subject=f"Return Request Rejected - {request_row.get('return_request_code')}",
                message=build_return_request_review_message(
                    member_name=member_name,
                    return_request_code=request_row.get('return_request_code') or '-',
                    status='rejected',
                    review_notes=review_notes,
                ),
            )
            if not notify_result.get('sent'):
                flash('Return rejection saved, but email was not delivered (queued or failed). Check Notification Queue.', 'warning')
        except Exception:
            current_app.logger.exception('Failed to queue/send rejection notification for return request %s', return_request_id)

    return redirect(url_for('dashboard.index'))


def _sync_overdue_borrowings(db):
    """Mark active transactions as overdue when expected return date has passed."""
    with db.cursor() as cursor:
        cursor.execute(
            """
            UPDATE borrow_records br
            SET br.status = 'overdue',
                br.updated_at = CURRENT_TIMESTAMP
            WHERE br.status = 'active'
                            AND (
                                        br.expected_return_date < CURDATE()
                                 OR (br.expected_return_date = CURDATE() AND CURTIME() > '17:00:00')
                            )
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
                            AND (
                                        br.expected_return_date > CURDATE()
                                 OR (br.expected_return_date = CURDATE() AND CURTIME() <= '17:00:00')
                            )
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


def _fetch_overdue_records(limit=200):
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT br.borrow_id, br.transaction_code, br.borrow_date, br.expected_return_date,
                   DATEDIFF(CURDATE(), br.expected_return_date) AS days_overdue,
                   GREATEST(TIMESTAMPDIFF(HOUR, DATE_ADD(br.expected_return_date, INTERVAL 17 HOUR), NOW()), 0) AS hours_overdue,
                   m.member_id, m.member_code, m.first_name, m.last_name,
                   COUNT(CASE WHEN bi.returned_at IS NULL THEN 1 END) AS unreturned_items
            FROM borrow_records br
            INNER JOIN members m ON m.member_id = br.member_id
            INNER JOIN borrow_items bi ON bi.borrow_id = br.borrow_id
            WHERE br.status IN ('active', 'overdue')
                            AND (
                                        br.expected_return_date < CURDATE()
                                 OR (br.expected_return_date = CURDATE() AND CURTIME() > '17:00:00')
                            )
              AND bi.returned_at IS NULL
            GROUP BY br.borrow_id, br.transaction_code, br.borrow_date, br.expected_return_date,
                     m.member_id, m.member_code, m.first_name, m.last_name
            ORDER BY days_overdue DESC, br.expected_return_date ASC
            LIMIT %s
            """,
            (limit,),
        )
        return cursor.fetchall()


@borrow_bp.route('/borrow/new', methods=['GET'])
@login_required
def new_borrow():
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    now = datetime.now()
    return render_template(
        'borrow/new.html',
        borrowed_during_working_hours=_is_within_working_hours(now),
        current_datetime=now.strftime('%Y-%m-%d %H:%M'),
    )


@borrow_bp.route('/return/new', methods=['GET'])
@login_required
def new_return():
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))
    return render_template('borrow/return.html')


@borrow_bp.route('/overdue', methods=['GET'])
@login_required
def overdue_management():
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    db = get_db()
    sync_summary = _sync_overdue_borrowings(db)
    db.commit()
    records = _fetch_overdue_records()

    return render_template('borrow/overdue.html', records=records, sync_summary=sync_summary)


@borrow_bp.route('/api/overdue/sync', methods=['POST'])
@login_required
def overdue_sync():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    db = get_db()
    try:
        sync_summary = _sync_overdue_borrowings(db)
        db.commit()
        emit_app_data_changed(reason='overdue_sync_completed', include_staff=True, include_members=False)
        return jsonify({'ok': True, 'sync': sync_summary})
    except Exception:
        db.rollback()
        return jsonify({'ok': False, 'message': 'Failed to sync overdue records.'}), 500


@borrow_bp.route('/api/borrow/member-check', methods=['GET'])
@login_required
def member_check():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = _get_member_from_query()
    eligibility = _build_member_eligibility(member_row)

    return jsonify(
        {
            'ok': True,
            'member': _serialize_member(member_row) if member_row else None,
            'eligibility': eligibility,
        }
    )


@borrow_bp.route('/api/borrow/equipment-search', methods=['GET'])
@login_required
def equipment_search():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    search = (request.args.get('query') or '').strip()
    rows = Equipment.get_all(status='available', search=search or None)

    return jsonify(
        {
            'ok': True,
            'results': [
                {
                    'equipment_id': row.get('equipment_id'),
                    'equipment_code': row.get('equipment_code'),
                    'equipment_name': row.get('equipment_name'),
                    'category': row.get('category'),
                    'condition_status': row.get('condition_status'),
                    'location': row.get('location'),
                    'requires_supervision': bool(row.get('requires_supervision')),
                    'restricted_areas': row.get('restricted_areas'),
                    'status': row.get('status'),
                }
                for row in rows
            ],
        }
    )


@borrow_bp.route('/api/borrow/precheck', methods=['GET'])
@login_required
def borrow_precheck():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = _get_member_from_query()
    eligibility = _build_member_eligibility(member_row)

    equipment_ids_csv = (request.args.get('equipment_ids') or '').strip()
    usage_area = (request.args.get('usage_area') or '').strip()
    selected_ids = []
    if equipment_ids_csv:
        for raw_id in equipment_ids_csv.split(','):
            raw_id = raw_id.strip()
            if raw_id.isdigit():
                selected_ids.append(int(raw_id))

    selected_items = _fetch_equipment_by_ids(selected_ids)
    unavailable_items = [item for item in selected_items if (item.get('status') or '').lower() != 'available']

    requires_supervision = any(bool(item.get('requires_supervision')) for item in selected_items)
    in_working_hours = _is_within_working_hours()

    warnings = []
    if not usage_area:
        warnings.append('Usage area is required for in-facility tracking.')
    if not in_working_hours:
        warnings.append('Borrowing is outside working hours (Mon-Fri, 8:00-16:30).')
    if unavailable_items:
        warnings.append('One or more selected equipment items are no longer available.')

    return jsonify(
        {
            'ok': True,
            'member': _serialize_member(member_row) if member_row else None,
            'eligibility': eligibility,
            'policy': {
                'borrowed_during_working_hours': in_working_hours,
                'requires_supervision': requires_supervision,
                'usage_area_provided': bool(usage_area),
            },
            'selected_count': len(selected_items),
            'unavailable_items': [item.get('equipment_code') for item in unavailable_items],
            'warnings': warnings,
            'ready_for_save': eligibility['eligible'] and len(selected_items) > 0 and not warnings,
        }
    )


@borrow_bp.route('/api/borrow/submit', methods=['POST'])
@login_required
def submit_borrow():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    member_code = (payload.get('member_code') or '').strip().upper()
    usage_area = (payload.get('usage_area') or '').strip()
    notes = (payload.get('notes') or '').strip() or None
    items = payload.get('items') or []

    if not member_code:
        return jsonify({'ok': False, 'message': 'Member code is required.'}), 400
    if not usage_area:
        return jsonify({'ok': False, 'message': 'Usage area is required.'}), 400

    if not _is_within_working_hours():
        return jsonify({'ok': False, 'message': 'Borrow transactions are only allowed from 8:00 AM to 4:30 PM.'}), 400

    # Admin/staff manual borrow follows same-day return policy.
    expected_return_date = datetime.now().date()

    if not items:
        return jsonify({'ok': False, 'message': 'At least one equipment item is required.'}), 400

    parsed_items = []
    for item in items:
        equipment_id = item.get('equipment_id')
        condition_borrowed = (item.get('condition_borrowed') or '').strip().lower()

        if not isinstance(equipment_id, int):
            return jsonify({'ok': False, 'message': 'Invalid equipment selection.'}), 400
        if condition_borrowed not in ('excellent', 'good', 'fair', 'poor'):
            return jsonify({'ok': False, 'message': 'Invalid borrowed condition value.'}), 400
        parsed_items.append({'equipment_id': equipment_id, 'condition_borrowed': condition_borrowed})

    member_row = Member.get_by_member_code(member_code)
    eligibility = _build_member_eligibility(member_row)
    if not member_row:
        return jsonify({'ok': False, 'message': 'Member not found.'}), 404
    if not eligibility['eligible']:
        return jsonify({'ok': False, 'message': eligibility['message']}), 400

    selected_ids = [item['equipment_id'] for item in parsed_items]
    selected_equipment = _fetch_equipment_by_ids(selected_ids)
    if len(selected_equipment) != len(set(selected_ids)):
        return jsonify({'ok': False, 'message': 'Some selected equipment no longer exists.'}), 400

    unavailable = [item for item in selected_equipment if (item.get('status') or '').lower() != 'available']
    if unavailable:
        codes = ', '.join(item.get('equipment_code') or str(item.get('equipment_id')) for item in unavailable)
        return jsonify({'ok': False, 'message': f'Unavailable equipment detected: {codes}'}), 400

    requires_supervision = any(bool(item.get('requires_supervision')) for item in selected_equipment)
    borrowed_during_working_hours = _is_within_working_hours()

    db = get_db()
    try:
        transaction_code = _generate_transaction_code(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO borrow_records (
                    transaction_code,
                    member_id,
                    expected_return_date,
                    status,
                    total_items,
                    usage_area,
                    requires_supervision,
                    borrowed_during_working_hours,
                    processed_by,
                    notes
                ) VALUES (%s, %s, %s, 'active', %s, %s, %s, %s, %s, %s)
                """,
                (
                    transaction_code,
                    member_row['member_id'],
                    expected_return_date,
                    len(parsed_items),
                    usage_area,
                    requires_supervision,
                    borrowed_during_working_hours,
                    current_user.id,
                    notes,
                ),
            )
            borrow_id = cursor.lastrowid

            for item in parsed_items:
                cursor.execute(
                    """
                    INSERT INTO borrow_items (borrow_id, equipment_id, condition_borrowed)
                    VALUES (%s, %s, %s)
                    """,
                    (borrow_id, item['equipment_id'], item['condition_borrowed']),
                )

            for equipment_id in selected_ids:
                cursor.execute(
                    "UPDATE equipment SET status = 'borrowed', updated_at = CURRENT_TIMESTAMP WHERE equipment_id = %s",
                    (equipment_id,),
                )

            cursor.execute(
                """
                UPDATE members
                SET current_borrow_count = current_borrow_count + %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE member_id = %s
                """,
                (len(parsed_items), member_row['member_id']),
            )

        _log_activity(
            db,
            action='BORROW_CREATED',
            description=f"Created borrow transaction {transaction_code} with {len(parsed_items)} item(s).",
            member_id=member_row['member_id'],
        )

        db.commit()
    except Exception:
        db.rollback()
        return jsonify({'ok': False, 'message': 'Failed to save borrow transaction.'}), 500

    # Day 5 afternoon: queue and attempt to send borrow confirmation email.
    recipient_email = (member_row.get('email') or member_row.get('google_email') or '').strip()
    if recipient_email:
        try:
            member_name = f"{member_row.get('first_name', '')} {member_row.get('last_name', '')}".strip() or member_code
            queue_and_send_notification(
                member_id=member_row['member_id'],
                borrow_id=borrow_id,
                notification_type='borrow_confirmation',
                recipient_email=recipient_email,
                subject=f'Borrow Confirmation - {transaction_code}',
                message=build_borrow_confirmation_message(
                    member_name=member_name,
                    transaction_code=transaction_code,
                    expected_return_date=str(expected_return_date),
                    usage_area=usage_area,
                    total_items=len(parsed_items),
                ),
            )
        except Exception:
            current_app.logger.exception('Failed to queue/send borrow confirmation for %s', transaction_code)

    emit_app_data_changed(
        reason='borrow_transaction_created',
        member_id=member_row['member_id'],
        include_staff=True,
        include_members=True,
    )

    return jsonify(
        {
            'ok': True,
            'transaction_code': transaction_code,
            'receipt_url': url_for('borrow.borrow_receipt', transaction_code=transaction_code),
        }
    )


@borrow_bp.route('/borrow/receipt/<transaction_code>', methods=['GET'])
@login_required
def borrow_receipt(transaction_code):
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    header, items = _fetch_receipt_data(transaction_code)
    if not header:
        return redirect(url_for('borrow.new_borrow'))

    return render_template('borrow/receipt.html', header=header, items=items)


@borrow_bp.route('/api/return/member-items', methods=['GET'])
@login_required
def return_member_items():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = _get_member_from_query()
    if not member_row:
        return jsonify({'ok': False, 'message': 'Member not found.'}), 404

    rows = _fetch_member_active_borrowed_items(member_row['member_id'])
    return jsonify(
        {
            'ok': True,
            'member': _serialize_member(member_row),
            'items': [
                {
                    'borrow_item_id': row.get('borrow_item_id'),
                    'borrow_id': row.get('borrow_id'),
                    'transaction_code': row.get('transaction_code'),
                    'expected_return_date': str(row.get('expected_return_date')),
                    'borrow_status': row.get('borrow_status'),
                    'condition_borrowed': row.get('condition_borrowed'),
                    'equipment_id': row.get('equipment_id'),
                    'equipment_code': row.get('equipment_code'),
                    'equipment_name': row.get('equipment_name'),
                    'category': row.get('category'),
                    'location': row.get('location'),
                }
                for row in rows
            ],
        }
    )


@borrow_bp.route('/api/return/submit', methods=['POST'])
@login_required
def submit_return():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    borrow_item_id = payload.get('borrow_item_id')
    condition_returned = (payload.get('condition_returned') or '').strip().lower()
    notes = (payload.get('notes') or '').strip() or None

    if not isinstance(borrow_item_id, int):
        return jsonify({'ok': False, 'message': 'Invalid return item selection.'}), 400
    if condition_returned not in ('excellent', 'good', 'fair', 'poor'):
        return jsonify({'ok': False, 'message': 'Invalid return condition value.'}), 400

    db = get_db()
    try:
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT bi.borrow_item_id, bi.borrow_id, bi.equipment_id, bi.condition_borrowed,
                       br.member_id, br.transaction_code, br.expected_return_date, br.status AS borrow_status,
                       m.first_name, m.last_name, m.email, m.google_email,
                       e.equipment_name
                FROM borrow_items bi
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                INNER JOIN members m ON m.member_id = br.member_id
                INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
                WHERE bi.borrow_item_id = %s AND bi.returned_at IS NULL
                LIMIT 1
                """,
                (borrow_item_id,),
            )
            item_row = cursor.fetchone()

            if not item_row:
                return jsonify({'ok': False, 'message': 'Borrow item not found or already returned.'}), 404

            receipt_meta = _finalize_return_item(
                db=db,
                item_row=item_row,
                condition_returned=condition_returned,
                notes=notes,
            )

        db.commit()
    except Exception:
        db.rollback()
        return jsonify({'ok': False, 'message': 'Failed to process return transaction.'}), 500

    # Day 5 afternoon: queue and attempt to send return confirmation email.
    recipient_email = (item_row.get('email') or item_row.get('google_email') or '').strip()
    if recipient_email:
        try:
            member_name = f"{item_row.get('first_name', '')} {item_row.get('last_name', '')}".strip() or item_row.get('member_id')
            queue_and_send_notification(
                member_id=item_row['member_id'],
                borrow_id=item_row['borrow_id'],
                notification_type='return_confirmation',
                recipient_email=recipient_email,
                subject=f"Return Confirmation - {item_row['transaction_code']}",
                message=build_return_confirmation_message(
                    member_name=member_name,
                    transaction_code=item_row['transaction_code'],
                    equipment_name=item_row.get('equipment_name') or 'Equipment item',
                    condition_returned=condition_returned,
                    days_overdue=receipt_meta['days_overdue'],
                ),
            )
        except Exception:
            current_app.logger.exception('Failed to queue/send return confirmation for item %s', borrow_item_id)

    emit_app_data_changed(
        reason='return_transaction_processed',
        member_id=item_row['member_id'],
        include_staff=True,
        include_members=True,
    )

    return jsonify(
        {
            'ok': True,
            'receipt_url': url_for('borrow.return_receipt', borrow_item_id=borrow_item_id),
            'days_overdue': receipt_meta['days_overdue'],
            'is_damaged': receipt_meta['is_damaged'],
            'condition_borrowed': receipt_meta['condition_borrowed'],
            'condition_returned': receipt_meta['condition_returned'],
        }
    )


@borrow_bp.route('/return/receipt/<int:borrow_item_id>', methods=['GET'])
@login_required
def return_receipt(borrow_item_id):
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            """
            SELECT bi.borrow_item_id, bi.condition_borrowed, bi.condition_returned, bi.returned_at,
                   br.transaction_code, br.expected_return_date,
                   m.member_code, m.first_name, m.middle_name, m.last_name,
                   e.equipment_code, e.equipment_name
            FROM borrow_items bi
            INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
            INNER JOIN members m ON m.member_id = br.member_id
            INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
            WHERE bi.borrow_item_id = %s
            LIMIT 1
            """,
            (borrow_item_id,),
        )
        row = cursor.fetchone()

    if not row:
        return redirect(url_for('borrow.new_return'))

    today = datetime.now().date()
    expected = row.get('expected_return_date')
    days_overdue = max(0, (today - expected).days) if expected else 0
    is_damaged = _is_condition_worse(row.get('condition_borrowed'), row.get('condition_returned'))

    return render_template(
        'borrow/return_receipt.html',
        record=row,
        days_overdue=days_overdue,
        is_damaged=is_damaged,
    )


@borrow_bp.route('/notifications', methods=['GET'])
@login_required
def notification_center():
    role = getattr(current_user, 'role', None)
    if role not in ('admin', 'staff', 'member'):
        return redirect(url_for('dashboard.index'))

    mail_configured = bool(
        current_app.config.get('MAIL_NOTIFICATIONS_ENABLED', True)
        and current_app.config.get('MAIL_SERVER')
        and current_app.config.get('MAIL_PORT')
        and current_app.config.get('MAIL_USERNAME')
        and current_app.config.get('MAIL_PASSWORD')
    )

    db = get_db()
    with db.cursor() as cursor:
        if role == 'member':
            cursor.execute(
                """
                SELECT
                    SUM(CASE WHEN n.status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN n.status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                    SUM(CASE WHEN n.status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    COUNT(*) AS total_count
                FROM notifications n
                WHERE n.member_id = %s
                """,
                (current_user.id,),
            )
            stats = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT n.notification_id, n.notification_type, n.recipient_email, n.subject,
                     n.status, n.retry_count, n.error_message, n.created_at, n.sent_at,
                       m.member_code, m.first_name, m.last_name,
                       br.transaction_code
                FROM notifications n
                LEFT JOIN members m ON m.member_id = n.member_id
                LEFT JOIN borrow_records br ON br.borrow_id = n.borrow_id
                WHERE n.member_id = %s
                ORDER BY n.created_at DESC
                LIMIT 100
                """,
                (current_user.id,),
            )
            notifications = cursor.fetchall()
        else:
            cursor.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    COUNT(*) AS total_count
                FROM notifications
                """
            )
            stats = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT n.notification_id, n.notification_type, n.recipient_email, n.subject,
                     n.status, n.retry_count, n.error_message, n.created_at, n.sent_at,
                       m.member_code, m.first_name, m.last_name,
                       br.transaction_code
                FROM notifications n
                LEFT JOIN members m ON m.member_id = n.member_id
                LEFT JOIN borrow_records br ON br.borrow_id = n.borrow_id
                ORDER BY n.created_at DESC
                LIMIT 100
                """
            )
            notifications = cursor.fetchall()

    return render_template(
        'borrow/notifications.html',
        stats=stats,
        notifications=notifications,
        mail_configured=mail_configured,
        can_manage_notifications=role in ('admin', 'staff'),
    )


@borrow_bp.route('/api/notifications/process-pending', methods=['POST'])
@login_required
def process_pending_notifications():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    try:
        result = process_pending_notifications_service(limit=50)
        emit_app_data_changed(reason='notification_queue_processed', include_staff=True, include_members=True)
        return jsonify({'ok': True, **result})
    except Exception:
        return jsonify({'ok': False, 'message': 'Failed to process pending notifications.'}), 500


@borrow_bp.route('/api/reminders/run', methods=['POST'])
@login_required
def run_reminders_now():
    if not _is_authorized_borrower():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    try:
        summary = run_reminder_cycle()
        emit_app_data_changed(reason='reminder_cycle_completed', include_staff=True, include_members=True)
        return jsonify({'ok': True, 'summary': summary})
    except Exception:
        current_app.logger.exception('Manual reminder cycle failed.')
        return jsonify({'ok': False, 'message': 'Failed to run reminder cycle.'}), 500


@borrow_bp.route('/api/notifications/<int:notification_id>/delete', methods=['POST'])
@login_required
def delete_notification(notification_id):
    role = getattr(current_user, 'role', None)
    if role not in ('member', 'admin', 'staff'):
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403
    
    db = get_db()
    with db.cursor() as cursor:
        if role == 'member':
            cursor.execute(
                "SELECT notification_id FROM notifications WHERE notification_id = %s AND member_id = %s",
                (notification_id, current_user.id)
            )
        else:
            cursor.execute(
                "SELECT notification_id FROM notifications WHERE notification_id = %s",
                (notification_id,)
            )

        if not cursor.fetchone():
            return jsonify({'ok': False, 'message': 'Notification not found'}), 404
        
        cursor.execute("DELETE FROM notifications WHERE notification_id = %s", (notification_id,))
        db.commit()
    
    return jsonify({'ok': True, 'message': 'Notification deleted'})


@borrow_bp.route('/api/notifications/delete-all', methods=['POST'])
@login_required
def delete_all_notifications():
    role = getattr(current_user, 'role', None)
    if role not in ('member', 'admin', 'staff'):
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403
    
    db = get_db()
    with db.cursor() as cursor:
        if role == 'member':
            cursor.execute("DELETE FROM notifications WHERE member_id = %s", (current_user.id,))
        else:
            cursor.execute("DELETE FROM notifications")
        db.commit()
    
    return jsonify({'ok': True, 'message': 'All notifications deleted'})
