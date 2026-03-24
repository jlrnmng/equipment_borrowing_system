import re

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models.member import Member

members_bp = Blueprint('members', __name__)


def _is_authorized_scanner():
    return current_user.role in ('admin', 'staff')


def _extract_member_code(value):
    text = (value or '').strip().upper()
    if not text:
        return None

    # Accept raw member code and QR payload variants, e.g. MEMBER:MEM001
    match = re.search(r'(MEM\d{3,})', text)
    return match.group(1) if match else None


def _serialize_member(row):
    if not row:
        return None
    middle_name = (row.get('middle_name') or '').strip()
    full_name = f"{row.get('first_name', '').strip()} {middle_name} {row.get('last_name', '').strip()}".replace('  ', ' ').strip()
    return {
        'member_id': row.get('member_id'),
        'member_code': row.get('member_code'),
        'full_name': full_name,
        'email': row.get('email') or row.get('google_email'),
        'phone': row.get('phone'),
        'student_id': row.get('student_id'),
        'startup': row.get('startup'),
        'status': row.get('status'),
        'current_borrow_count': row.get('current_borrow_count') or 0,
        'max_borrow_limit': row.get('max_borrow_limit') or 0,
        'qr_code_path': row.get('qr_code_path'),
    }


@members_bp.route('/members/register', methods=['GET', 'POST'])
def register_member():
    return redirect(url_for('auth.signup'))


@members_bp.route('/members/scan', methods=['GET'])
@login_required
def scan_member():
    if not _is_authorized_scanner():
        flash('You are not authorized to scan member QR codes.', 'danger')
        return redirect(url_for('dashboard.index'))
    return render_template('members/scan.html')


@members_bp.route('/api/members/lookup', methods=['GET'])
@login_required
def lookup_member():
    if not _is_authorized_scanner():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_code = _extract_member_code(request.args.get('member_code'))
    raw_qr = request.args.get('qr_data')
    query = (request.args.get('query') or '').strip()
    email = (request.args.get('email') or '').strip().lower()

    if not member_code and raw_qr:
        member_code = _extract_member_code(raw_qr)

    row = None
    if member_code:
        row = Member.get_by_member_code(member_code)
    elif email:
        row = Member.get_by_email_or_google_email(email)
    elif query:
        query_code = _extract_member_code(query)
        if query_code:
            row = Member.get_by_member_code(query_code)
        elif '@' in query:
            row = Member.get_by_email_or_google_email(query.lower())

    if row:
        return jsonify({'ok': True, 'member': _serialize_member(row)})

    if query:
        results = Member.search_for_lookup(query=query, limit=10)
        return jsonify({'ok': True, 'member': None, 'results': [_serialize_member(r) for r in results]})

    return jsonify({'ok': True, 'member': None, 'results': []})
