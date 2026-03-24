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
