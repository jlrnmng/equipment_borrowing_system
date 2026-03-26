import re

import pymysql
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.forms import MemberProfileForm
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


@members_bp.route('/members/<member_code>', methods=['GET', 'POST'])
@login_required
def member_profile(member_code):
    if not _is_authorized_scanner():
        flash('You are not authorized to view member profiles.', 'danger')
        return redirect(url_for('dashboard.index'))

    normalized_code = _extract_member_code(member_code)
    if not normalized_code:
        flash('Invalid member code format.', 'warning')
        return redirect(url_for('members.scan_member'))

    profile = Member.get_profile_by_member_code(normalized_code)
    if not profile:
        flash('Member not found.', 'danger')
        return redirect(url_for('members.scan_member'))

    form = MemberProfileForm()

    if form.validate_on_submit():
        try:
            Member.update_profile(
                member_id=profile['member_id'],
                first_name=form.first_name.data.strip(),
                middle_name=form.middle_name.data.strip() if form.middle_name.data else None,
                last_name=form.last_name.data.strip(),
                email=form.email.data.strip().lower(),
                phone=form.phone.data.strip() if form.phone.data else None,
                student_id=form.student_id.data.strip() if form.student_id.data else None,
                startup=form.startup.data.strip() if form.startup.data else None,
                status=form.status.data,
                max_borrow_limit=form.max_borrow_limit.data,
            )
            flash('Member profile updated successfully.', 'success')
            return redirect(url_for('members.member_profile', member_code=normalized_code))
        except pymysql.err.IntegrityError as exc:
            message = str(exc).lower()
            if 'email' in message:
                flash('Email is already in use by another account.', 'warning')
            else:
                flash('Unable to save member profile due to a data constraint.', 'danger')
        except Exception:
            current_app.logger.exception('Failed to update member profile %s', normalized_code)
            flash('An unexpected error occurred while saving this profile.', 'danger')

    if request.method == 'GET':
        form.first_name.data = profile.get('first_name')
        form.middle_name.data = profile.get('middle_name')
        form.last_name.data = profile.get('last_name')
        form.email.data = profile.get('email') or profile.get('google_email')
        form.phone.data = profile.get('phone')
        form.student_id.data = profile.get('student_id')
        form.startup.data = profile.get('startup')
        form.status.data = profile.get('status')
        form.max_borrow_limit.data = profile.get('max_borrow_limit')

    if request.method == 'POST' and form.errors:
        flash('Please correct the highlighted fields and try again.', 'warning')

    middle_name = (profile.get('middle_name') or '').strip()
    full_name = f"{(profile.get('first_name') or '').strip()} {middle_name} {(profile.get('last_name') or '').strip()}".replace('  ', ' ').strip()

    active_items = Member.get_current_borrowed_items(profile['member_id'])
    borrow_history = Member.get_borrowing_history(profile['member_id'], limit=25)
    violations = Member.get_violations(profile['member_id'], limit=25)
    calendar_status = Member.get_calendar_status(profile['member_id'])

    return render_template(
        'members/profile.html',
        member=profile,
        full_name=full_name,
        form=form,
        active_items=active_items,
        borrow_history=borrow_history,
        violations=violations,
        calendar_status=calendar_status,
    )
