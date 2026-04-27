import os

import pymysql
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.forms import MemberProfileForm
from app.models.app_setting import AppSetting
from app.models.member import Member
from app.realtime import emit_app_data_changed
from app.utils.db import get_db
from app.utils.qr import (
    build_member_qr_download_filename,
    build_member_qr_filename,
    extract_member_code,
    generate_member_qr,
)

members_bp = Blueprint('members', __name__)


def _is_authorized_scanner():
    return current_user.role in ('admin', 'staff')


def _extract_member_code(value):
    return extract_member_code(value)


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
    existing_members = Member.get_active_members(limit=25)
    default_borrow_limit = AppSetting.get_int('default_borrow_limit', 3)
    return render_template(
        'members/scan.html',
        existing_members=existing_members,
        default_borrow_limit=default_borrow_limit,
    )


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


@members_bp.route('/api/members/<member_code>/borrow-limit', methods=['POST'])
@login_required
def update_member_borrow_limit(member_code):
    if not _is_authorized_scanner():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    normalized_code = _extract_member_code(member_code)
    if not normalized_code:
        return jsonify({'ok': False, 'message': 'Invalid member code format.'}), 400

    profile = Member.get_profile_by_member_code(normalized_code)
    if not profile:
        return jsonify({'ok': False, 'message': 'Member not found.'}), 404

    payload = request.get_json(silent=True) or {}
    raw_limit = payload.get('max_borrow_limit')

    try:
        new_limit = int(raw_limit)
    except Exception:
        return jsonify({'ok': False, 'message': 'Borrow limit must be a whole number.'}), 400

    if new_limit < 1 or new_limit > 10:
        return jsonify({'ok': False, 'message': 'Borrow limit must be between 1 and 10.'}), 400

    current_borrow_count = int(profile.get('current_borrow_count') or 0)
    if new_limit < current_borrow_count:
        return jsonify(
            {
                'ok': False,
                'message': f'Borrow limit cannot be less than current borrowed count ({current_borrow_count}).',
            }
        ), 400

    try:
        Member.update_max_borrow_limit(profile['member_id'], new_limit)
    except Exception:
        current_app.logger.exception('Failed to update borrow limit for %s', normalized_code)
        return jsonify({'ok': False, 'message': 'Unable to update borrow limit right now.'}), 500

    emit_app_data_changed(
        reason='member_borrow_limit_updated',
        member_id=profile['member_id'],
        include_staff=True,
        include_members=True,
    )

    return jsonify(
        {
            'ok': True,
            'member_code': normalized_code,
            'current_borrow_count': current_borrow_count,
            'max_borrow_limit': new_limit,
        }
    )


@members_bp.route('/api/members/borrow-limit/global', methods=['POST'])
@login_required
def update_global_borrow_limit():
    if not _is_authorized_scanner():
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    payload = request.get_json(silent=True) or {}
    raw_limit = payload.get('default_borrow_limit')
    apply_to_all = bool(payload.get('apply_to_all'))
    target_member_code = _extract_member_code(payload.get('target_member_code'))

    try:
        default_limit = int(raw_limit)
    except Exception:
        return jsonify({'ok': False, 'message': 'Borrow limit must be a whole number.'}), 400

    if default_limit < 1 or default_limit > 10:
        return jsonify({'ok': False, 'message': 'Borrow limit must be between 1 and 10.'}), 400

    target_profile = None
    if not apply_to_all:
        if not target_member_code:
            return jsonify({'ok': False, 'message': 'Select a member to apply the limit to, or enable apply-to-all.'}), 400

        target_profile = Member.get_profile_by_member_code(target_member_code)
        if not target_profile:
            return jsonify({'ok': False, 'message': 'Selected member not found.'}), 404

        current_borrow_count = int(target_profile.get('current_borrow_count') or 0)
        if default_limit < current_borrow_count:
            return jsonify(
                {
                    'ok': False,
                    'message': f'Borrow limit cannot be less than current borrowed count ({current_borrow_count}) for {target_member_code}.',
                }
            ), 400

    try:
        AppSetting.set_number(
            setting_key='default_borrow_limit',
            value=default_limit,
            updated_by=getattr(current_user, 'id', None),
            description='Default max borrow limit per member account',
            category='system',
            is_public=True,
        )

        updated_members_count = 0
        if apply_to_all:
            updated_members_count = Member.update_max_borrow_limit_for_all(default_limit)
        elif target_profile:
            Member.update_max_borrow_limit(target_profile['member_id'], default_limit)
    except Exception:
        current_app.logger.exception('Failed to update global borrow limit setting')
        return jsonify({'ok': False, 'message': 'Unable to update global borrow limit right now.'}), 500

    emit_app_data_changed(
        reason='global_borrow_limit_updated',
        member_id=None,
        include_staff=True,
        include_members=apply_to_all,
    )

    return jsonify(
        {
            'ok': True,
            'default_borrow_limit': default_limit,
            'apply_to_all': apply_to_all,
            'target_member_code': target_member_code,
            'updated_members_count': updated_members_count,
        }
    )


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

    expected_qr_path = f"qr/members/{build_member_qr_filename(profile['member_code'])}"
    current_qr_path = profile.get('qr_code_path')

    if not current_qr_path or current_qr_path != expected_qr_path:
        try:
            qr_path = generate_member_qr(profile['member_code'])
            db = get_db()
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE members SET qr_code_path = %s, updated_at = CURRENT_TIMESTAMP WHERE member_id = %s",
                    (qr_path, profile['member_id']),
                )
            db.commit()
            profile['qr_code_path'] = qr_path
        except Exception:
            current_app.logger.exception('Failed to auto-generate missing member QR for %s', profile.get('member_code'))

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
    existing_members = Member.get_active_members(limit=25)

    return render_template(
        'members/profile.html',
        member=profile,
        full_name=full_name,
        form=form,
        active_items=active_items,
        borrow_history=borrow_history,
        violations=violations,
        existing_members=existing_members,
    )


@members_bp.route('/members/<member_code>/delete', methods=['POST'])
@login_required
def delete_member(member_code):
    if not _is_authorized_scanner():
        flash('You are not authorized to manage members.', 'danger')
        return redirect(url_for('dashboard.index'))

    normalized_code = _extract_member_code(member_code)
    if not normalized_code:
        flash('Invalid member code format.', 'warning')
        return redirect(url_for('members.scan_member'))

    profile = Member.get_profile_by_member_code(normalized_code)
    if not profile:
        flash('Member not found.', 'danger')
        return redirect(url_for('members.scan_member'))

    if Member.soft_delete_member(profile['member_id']):
        flash(f'Member {normalized_code} has been deactivated.', 'success')
    else:
        flash('Unable to deactivate member right now.', 'danger')

    return redirect(url_for('members.member_profile', member_code=normalized_code))


@members_bp.route('/members/<member_code>/qr/download', methods=['GET'])
@login_required
def download_member_qr(member_code):
    normalized_code = _extract_member_code(member_code)
    if not normalized_code:
        flash('Invalid member code format.', 'warning')
        return redirect(url_for('members.scan_member'))

    is_owner_member = getattr(current_user, 'role', None) == 'member' and getattr(current_user, 'member_code', None) == normalized_code
    if not _is_authorized_scanner() and not is_owner_member:
        flash('You are not authorized to download this member QR code.', 'danger')
        if getattr(current_user, 'role', None) == 'member':
            return redirect(url_for('auth.complete_profile'))
        return redirect(url_for('dashboard.index'))

    profile = Member.get_profile_by_member_code(normalized_code)
    if not profile:
        flash('Member not found.', 'danger')
        return redirect(url_for('members.scan_member'))

    qr_path = profile.get('qr_code_path')
    if not qr_path:
        qr_path = generate_member_qr(normalized_code)
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE members SET qr_code_path = %s, updated_at = CURRENT_TIMESTAMP WHERE member_id = %s",
                (qr_path, profile['member_id']),
            )
        db.commit()

    abs_qr_path = os.path.join(current_app.static_folder, qr_path)
    if not os.path.exists(abs_qr_path):
        qr_path = generate_member_qr(normalized_code)
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE members SET qr_code_path = %s, updated_at = CURRENT_TIMESTAMP WHERE member_id = %s",
                (qr_path, profile['member_id']),
            )
        db.commit()
        abs_qr_path = os.path.join(current_app.static_folder, qr_path)

    download_name = build_member_qr_download_filename(
        last_name=profile.get('last_name'),
        student_id=profile.get('student_id'),
        member_code=profile.get('member_code'),
    )

    return send_file(abs_qr_path, as_attachment=True, download_name=download_name, mimetype='image/png')


@members_bp.route('/members/<member_code>/qr/regenerate', methods=['POST'])
@login_required
def regenerate_member_qr(member_code):
    if not _is_authorized_scanner():
        flash('You are not authorized to manage member QR codes.', 'danger')
        return redirect(url_for('dashboard.index'))

    normalized_code = _extract_member_code(member_code)
    if not normalized_code:
        flash('Invalid member code format.', 'warning')
        return redirect(url_for('members.scan_member'))

    profile = Member.get_profile_by_member_code(normalized_code)
    if not profile:
        flash('Member not found.', 'danger')
        return redirect(url_for('members.scan_member'))

    try:
        qr_path = generate_member_qr(normalized_code)
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE members SET qr_code_path = %s, updated_at = CURRENT_TIMESTAMP WHERE member_id = %s",
                (qr_path, profile['member_id']),
            )
        db.commit()
    except Exception:
        current_app.logger.exception('Failed regenerating member QR for %s', normalized_code)
        flash('Unable to regenerate member QR right now.', 'danger')
        return redirect(url_for('members.member_profile', member_code=normalized_code))

    flash('Member QR code regenerated successfully.', 'success')
    return redirect(url_for('members.member_profile', member_code=normalized_code))
