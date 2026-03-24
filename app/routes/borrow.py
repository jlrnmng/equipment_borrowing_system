from datetime import datetime, timedelta

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models.equipment import Equipment
from app.models.member import Member
from app.utils.db import get_db

borrow_bp = Blueprint('borrow', __name__)


def _is_authorized_borrower():
    return current_user.role in ('admin', 'staff')


def _is_within_working_hours(now=None):
    now = now or datetime.now()
    # Monday=0 to Friday=4, 08:00-17:00
    if now.weekday() > 4:
        return False
    return 8 <= now.hour < 17


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
              AND (status = 'overdue' OR expected_return_date < CURDATE())
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


@borrow_bp.route('/borrow/new', methods=['GET'])
@login_required
def new_borrow():
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))

    now = datetime.now()
    return render_template(
        'borrow/new.html',
        default_return_date=(now + timedelta(days=7)).strftime('%Y-%m-%d'),
        borrowed_during_working_hours=_is_within_working_hours(now),
        current_datetime=now.strftime('%Y-%m-%d %H:%M'),
    )


@borrow_bp.route('/return/new', methods=['GET'])
@login_required
def new_return():
    if not _is_authorized_borrower():
        return redirect(url_for('dashboard.index'))
    return render_template('borrow/return.html')


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
    rows = rows[:20]

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
        warnings.append('Borrowing is outside working hours (Mon-Fri, 8:00-17:00).')
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
    expected_return_date_raw = (payload.get('expected_return_date') or '').strip()
    notes = (payload.get('notes') or '').strip() or None
    items = payload.get('items') or []

    if not member_code:
        return jsonify({'ok': False, 'message': 'Member code is required.'}), 400
    if not usage_area:
        return jsonify({'ok': False, 'message': 'Usage area is required.'}), 400

    expected_return_date = _parse_expected_return_date(expected_return_date_raw)
    if not expected_return_date:
        return jsonify({'ok': False, 'message': 'Expected return date is invalid.'}), 400

    if expected_return_date < datetime.now().date():
        return jsonify({'ok': False, 'message': 'Expected return date cannot be in the past.'}), 400

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
                       br.member_id, br.transaction_code, br.expected_return_date, br.status AS borrow_status
                FROM borrow_items bi
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                WHERE bi.borrow_item_id = %s AND bi.returned_at IS NULL
                LIMIT 1
                """,
                (borrow_item_id,),
            )
            item_row = cursor.fetchone()

            if not item_row:
                return jsonify({'ok': False, 'message': 'Borrow item not found or already returned.'}), 404

            expected_return_date = item_row.get('expected_return_date')
            today = datetime.now().date()
            days_overdue = max(0, (today - expected_return_date).days) if expected_return_date else 0
            is_overdue = days_overdue > 0
            is_damaged = _is_condition_worse(item_row.get('condition_borrowed'), condition_returned)

            cursor.execute(
                """
                UPDATE borrow_items
                SET condition_returned = %s,
                    returned_at = NOW(),
                    notes = %s
                WHERE borrow_item_id = %s
                """,
                (condition_returned, notes, borrow_item_id),
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

            # Update transaction header status based on remaining unreturned items and due date.
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

            _log_activity(
                db,
                action='RETURN_PROCESSED',
                description=(
                    f"Processed return for item {item_row['borrow_item_id']} in transaction "
                    f"{item_row['transaction_code']}."
                ),
                member_id=item_row['member_id'],
            )

            receipt_meta = {
                'borrow_item_id': item_row['borrow_item_id'],
                'days_overdue': days_overdue,
                'is_damaged': is_damaged,
                'condition_borrowed': item_row.get('condition_borrowed'),
                'condition_returned': condition_returned,
            }

        db.commit()
    except Exception:
        db.rollback()
        return jsonify({'ok': False, 'message': 'Failed to process return transaction.'}), 500

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
