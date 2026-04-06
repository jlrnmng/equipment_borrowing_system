from urllib.parse import urlsplit
import secrets
from datetime import datetime

from flask import Blueprint, current_app, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import oauth
from app.forms import LoginForm
from app.models.equipment import Equipment
from app.models.member import Member
from app.models.member_request import MemberBorrowRequest
from app.models.member_return_request import MemberReturnRequest
from app.models.staff import Staff
from app.utils.request_expiry import expire_stale_requests
from app.utils.qr import extract_equipment_code
from app.utils.notifications import build_welcome_message, queue_and_send_notification
from app.utils.qr import generate_member_qr

auth_bp = Blueprint('auth', __name__)


def _member_redirect_after_login(member_row):
    member_user = Member.to_member_user(member_row)
    login_user(member_user)

    if not Member.is_profile_complete(member_row):
        flash('Please complete your profile details before accessing your dashboard.', 'warning')
        return redirect(url_for('auth.complete_profile'))

    flash(f'Welcome, {member_user.full_name}!', 'success')
    return redirect(url_for('auth.member_dashboard'))


def get_google_oauth_config():
    """Return Google OAuth settings from app config (never hardcode credentials)."""
    return {
        'client_id': current_app.config.get('GOOGLE_CLIENT_ID'),
        'client_secret': current_app.config.get('GOOGLE_CLIENT_SECRET'),
        'redirect_uri': current_app.config.get('GOOGLE_REDIRECT_URI'),
        'allowed_domain': current_app.config.get('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph'),
    }


def is_google_oauth_enabled():
    cfg = get_google_oauth_config()
    return bool(cfg['client_id'] and cfg['client_secret'])


def is_allowed_domain(email):
    domain = get_google_oauth_config()['allowed_domain'].lower()
    return email.lower().endswith(f"@{domain}")


def _split_google_name(full_name):
    parts = [part for part in (full_name or '').split() if part]
    if not parts:
        return 'Google', 'User'
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], ' '.join(parts[1:])


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if getattr(current_user, 'role', None) == 'member':
            if not getattr(current_user, 'profile_complete', False):
                return redirect(url_for('auth.complete_profile'))
            return redirect(url_for('auth.member_dashboard'))
        return redirect(url_for('dashboard.index'))

    form = LoginForm()
    if form.validate_on_submit():
        row = Staff.get_by_email(form.email.data.strip().lower())

        if row and row['status'] == 'active' and Staff.check_password(
            row['password_hash'], form.password.data
        ):
            if not Staff.is_first_admin(row):
                flash('Manual login is reserved for the first admin. Please use Google Sign-In.', 'warning')
                return redirect(url_for('auth.login'))

            staff = Staff(
                row['staff_id'], row['staff_code'], row['email'],
                row['full_name'], row['role'], row['status'],
            )
            login_user(staff)
            Staff.touch_last_login(staff.id)

            flash(f'Welcome back, {staff.full_name}!', 'success')

            # Safe redirect – reject absolute URLs to prevent open redirect
            next_page = request.args.get('next')
            if next_page and urlsplit(next_page).netloc == '':
                return redirect(next_page)
            return redirect(url_for('dashboard.index'))

        flash('Invalid email or password.', 'danger')

    google_oauth_enabled = is_google_oauth_enabled()
    return render_template(
        'auth/login.html',
        form=form,
        google_oauth_enabled=google_oauth_enabled,
        allowed_domain=get_google_oauth_config()['allowed_domain'],
    )


@auth_bp.route('/auth/google', methods=['GET', 'POST'])
def google_login():
    if request.method != 'POST':
        return redirect(url_for('auth.login'))

    if not is_google_oauth_enabled():
        flash('Google OAuth is not configured yet.', 'warning')
        return redirect(url_for('auth.login'))

    google = oauth.create_client('google')
    if google is None:
        flash('Google OAuth client initialization failed.', 'danger')
        return redirect(url_for('auth.login'))

    nonce = secrets.token_urlsafe(16)
    session['oauth_nonce'] = nonce
    session['oauth_flow'] = 'login'
    cfg = get_google_oauth_config()
    redirect_uri = cfg['redirect_uri'] or url_for('auth.google_callback', _external=True)
    return google.authorize_redirect(
        redirect_uri,
        nonce=nonce,
        hd=cfg['allowed_domain'],
        prompt='select_account',
    )


@auth_bp.route('/auth/google/signup', methods=['GET', 'POST'])
def google_signup():
    if request.method != 'POST':
        return redirect(url_for('auth.signup'))

    if not is_google_oauth_enabled():
        flash('Google OAuth is not configured yet.', 'warning')
        return redirect(url_for('auth.signup'))

    google = oauth.create_client('google')
    if google is None:
        flash('Google OAuth client initialization failed.', 'danger')
        return redirect(url_for('auth.signup'))

    nonce = secrets.token_urlsafe(16)
    session['oauth_nonce'] = nonce
    session['oauth_flow'] = 'signup'
    cfg = get_google_oauth_config()
    redirect_uri = cfg['redirect_uri'] or url_for('auth.google_callback', _external=True)
    return google.authorize_redirect(
        redirect_uri,
        nonce=nonce,
        hd=cfg['allowed_domain'],
        prompt='select_account',
    )


@auth_bp.route('/auth/google/callback')
def google_callback():
    if not is_google_oauth_enabled():
        flash('Google OAuth is not configured yet.', 'warning')
        return redirect(url_for('auth.login'))

    google = oauth.create_client('google')
    if google is None:
        flash('Google OAuth client initialization failed.', 'danger')
        return redirect(url_for('auth.login'))

    try:
        token = google.authorize_access_token()
        nonce = session.pop('oauth_nonce', None)
        userinfo = google.parse_id_token(token, nonce=nonce)
    except Exception:
        flash('Google sign-in failed. Please try again.', 'danger')
        return redirect(url_for('auth.login'))

    if not userinfo:
        flash('Unable to retrieve Google profile information.', 'danger')
        return redirect(url_for('auth.login'))

    email = (userinfo.get('email') or '').strip().lower()
    google_sub = userinfo.get('sub')
    full_name = (userinfo.get('name') or 'User').strip()
    email_verified = bool(userinfo.get('email_verified'))
    oauth_flow = session.pop('oauth_flow', 'login')

    if not email or not google_sub:
        flash('Google account data is incomplete.', 'danger')
        return redirect(url_for('auth.login'))

    if not email_verified:
        flash('Your Google email must be verified.', 'warning')
        return redirect(url_for('auth.login'))

    if not is_allowed_domain(email):
        flash('Only @my.cspc.edu.ph accounts are allowed.', 'danger')
        return redirect(url_for('auth.login'))

    staff_row = Staff.get_by_email(email)
    if staff_row:
        if staff_row['status'] != 'active':
            flash('Your staff account is inactive. Contact the administrator.', 'danger')
            return redirect(url_for('auth.login'))

        existing_sub = staff_row.get('google_sub')
        if existing_sub and existing_sub != google_sub:
            flash('Google account mismatch. Contact the administrator.', 'danger')
            return redirect(url_for('auth.login'))

        if not existing_sub:
            Staff.update_google_identity(staff_row['staff_id'], email, google_sub)

        staff = Staff(
            staff_row['staff_id'],
            staff_row['staff_code'],
            staff_row['email'],
            staff_row['full_name'] or full_name,
            staff_row['role'],
            staff_row['status'],
        )
        login_user(staff)
        Staff.touch_last_login(staff.id)
        flash(f'Welcome, {staff.full_name}!', 'success')
        return redirect(url_for('dashboard.index'))

    member_row = Member.get_by_email_or_google_email(email)
    if member_row:
        if member_row['status'] != 'active':
            flash('Your member account is not active. Contact staff.', 'danger')
            return redirect(url_for('auth.login'))

        existing_sub = member_row.get('google_sub')
        if existing_sub and existing_sub != google_sub:
            flash('Google account mismatch. Contact staff for account relink.', 'danger')
            return redirect(url_for('auth.login'))

        if not existing_sub:
            Member.link_google_identity(member_row['member_id'], email, google_sub)

        member_row = Member.get_auth_by_member_id(member_row['member_id'])

        return _member_redirect_after_login(member_row)

    if oauth_flow == 'signup':
        first_name, last_name = _split_google_name(full_name)
        member_code = Member.get_next_member_code()
        qr_code_path = generate_member_qr(member_code)
        created_member = Member.create_member(
            member_code=member_code,
            first_name=first_name,
            middle_name=None,
            last_name=last_name,
            email=email,
            phone=None,
            student_id=None,
            startup=None,
            max_borrow_limit=3,
            created_by=None,
            qr_code_path=qr_code_path,
        )
        Member.link_google_identity(created_member['member_id'], email, google_sub)

        # Queue welcome notification and attempt immediate delivery when mail config is available.
        try:
            queue_and_send_notification(
                member_id=created_member['member_id'],
                borrow_id=None,
                notification_type='welcome',
                recipient_email=email,
                subject='Welcome to QR Equipment Borrowing System',
                message=build_welcome_message(
                    member_name=f"{first_name} {last_name}".strip(),
                    member_code=member_code,
                ),
            )
        except Exception:
            current_app.logger.exception('Failed to queue/send welcome notification for member %s', member_code)

        flash('Google signup successful. Your member account has been created.', 'success')
        return _member_redirect_after_login(Member.get_auth_by_member_id(created_member['member_id']))

    return redirect(url_for('auth.request_access', email=email))


@auth_bp.route('/request-access')
def request_access():
    email = (request.args.get('email') or '').strip().lower()
    return render_template('auth/request_access.html', email=email)


@auth_bp.route('/signup', methods=['GET'])
def signup():
    return render_template(
        'auth/signup.html',
        google_oauth_enabled=is_google_oauth_enabled(),
        allowed_domain=get_google_oauth_config()['allowed_domain'],
    )


@auth_bp.route('/member/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    if getattr(current_user, 'role', None) != 'member':
        return redirect(url_for('dashboard.index'))

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not member_row:
        flash('Member account not found. Please sign in again.', 'danger')
        return redirect(url_for('auth.logout'))

    if request.method == 'POST':
        phone = (request.form.get('phone') or '').strip()
        student_id = (request.form.get('student_id') or '').strip()
        startup = (request.form.get('startup') or '').strip()
        college_department = (request.form.get('college_department') or '').strip()
        program = (request.form.get('program') or '').strip()
        year_level = (request.form.get('year_level') or '').strip()

        if not phone or not student_id or not startup or not college_department or not program or not year_level:
            flash('Phone, ID number, startup/agency, college department, program, and year are required.', 'warning')
        else:
            Member.complete_profile(
                member_id=member_row['member_id'],
                phone=phone,
                student_id=student_id,
                startup=startup,
                college_department=college_department,
                program=program,
                year_level=year_level,
            )
            updated_row = Member.get_auth_by_member_id(member_row['member_id'])
            login_user(Member.to_member_user(updated_row))
            flash('Profile completed successfully.', 'success')
            return redirect(url_for('auth.member_dashboard'))

    return render_template('auth/member_complete_profile.html', member=member_row)


@auth_bp.route('/member/dashboard', methods=['GET'])
@login_required
def member_dashboard():
    if getattr(current_user, 'role', None) != 'member':
        return redirect(url_for('dashboard.index'))

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not Member.is_profile_complete(member_row):
        flash('Please complete your profile details first.', 'warning')
        return redirect(url_for('auth.complete_profile'))

    expire_stale_requests(expiry_minutes=current_app.config.get('REQUEST_EXPIRY_MINUTES', 30))

    requests = MemberBorrowRequest.get_member_requests(member_row['member_id'], limit=25)
    active_return_items = MemberReturnRequest.get_member_active_items(member_row['member_id'])
    return_requests = MemberReturnRequest.get_member_return_requests(member_row['member_id'], limit=25)
    
    # Calculate pending requests count
    pending_requests_count = sum(1 for req in requests if req.get('status') == 'pending')
    
    # Calculate default return date (7 days from today)
    from datetime import datetime, timedelta
    default_return_date = (datetime.today() + timedelta(days=7)).strftime('%Y-%m-%d')
    
    return render_template(
        'auth/member_dashboard.html',
        member=member_row,
        requests=requests,
        active_return_items=active_return_items,
        return_requests=return_requests,
        pending_requests_count=pending_requests_count,
        default_return_date=default_return_date,
    )


@auth_bp.route('/api/member/equipment-search', methods=['GET'])
@login_required
def member_equipment_search():
    if getattr(current_user, 'role', None) != 'member':
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not Member.is_profile_complete(member_row):
        return jsonify({'ok': False, 'message': 'Complete your profile first.'}), 400

    query = (request.args.get('query') or '').strip()
    rows = Equipment.get_all(status='available', search=query or None)
    rows = rows[:80]

    return jsonify(
        {
            'ok': True,
            'results': [
                {
                    'equipment_id': row.get('equipment_id'),
                    'equipment_code': row.get('equipment_code'),
                    'serial_number': row.get('serial_number'),
                    'inventory_number': row.get('inventory_number'),
                    'equipment_name': row.get('equipment_name'),
                    'category': row.get('category'),
                    'condition_status': row.get('condition_status'),
                    'location': row.get('location'),
                    'status': row.get('status'),
                    'qr_code_path': row.get('qr_code_path'),
                }
                for row in rows
            ],
        }
    )


@auth_bp.route('/api/member/equipment-from-qr', methods=['GET'])
@login_required
def member_equipment_from_qr():
    if getattr(current_user, 'role', None) != 'member':
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not Member.is_profile_complete(member_row):
        return jsonify({'ok': False, 'message': 'Complete your profile first.'}), 400

    qr_data = (request.args.get('qr_data') or '').strip()
    equipment_code = extract_equipment_code(qr_data)
    if not equipment_code:
        return jsonify({'ok': False, 'message': 'Invalid equipment QR payload.'}), 400

    equipment = Equipment.get_by_code_or_inventory(equipment_code)
    if not equipment:
        return jsonify({'ok': False, 'message': 'Equipment not found for scanned QR.'}), 404

    if (equipment.get('status') or '').lower() != 'available':
        return jsonify({'ok': False, 'message': 'Scanned equipment is not available.'}), 400

    return jsonify(
        {
            'ok': True,
            'item': {
                'equipment_id': equipment.get('equipment_id'),
                'equipment_code': equipment.get('equipment_code'),
                'serial_number': equipment.get('serial_number'),
                'inventory_number': equipment.get('inventory_number'),
                'equipment_name': equipment.get('equipment_name'),
                'category': equipment.get('category'),
                'condition_status': equipment.get('condition_status'),
                'location': equipment.get('location'),
                'status': equipment.get('status'),
            },
        }
    )


@auth_bp.route('/api/member/borrow-request', methods=['POST'])
@login_required
def submit_member_borrow_request():
    if getattr(current_user, 'role', None) != 'member':
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not Member.is_profile_complete(member_row):
        return jsonify({'ok': False, 'message': 'Complete your profile first.'}), 400

    payload = request.get_json(silent=True) or {}
    expected_return_date_raw = (payload.get('expected_return_date') or '').strip()
    usage_area = (payload.get('usage_area') or '').strip()
    notes = (payload.get('notes') or '').strip() or None
    items = payload.get('items') or []

    try:
        expected_return_date = datetime.strptime(expected_return_date_raw, '%Y-%m-%d').date()
    except Exception:
        return jsonify({'ok': False, 'message': 'Expected return date is invalid.'}), 400

    if expected_return_date < datetime.now().date():
        return jsonify({'ok': False, 'message': 'Expected return date cannot be in the past.'}), 400
    if not usage_area:
        return jsonify({'ok': False, 'message': 'Usage area is required.'}), 400
    if not isinstance(items, list) or not items:
        return jsonify({'ok': False, 'message': 'Select at least one equipment item.'}), 400

    parsed_items = []
    selected_ids = []
    for item in items:
        equipment_id = item.get('equipment_id')
        condition_requested = (item.get('condition_requested') or 'good').strip().lower()

        if not isinstance(equipment_id, int):
            return jsonify({'ok': False, 'message': 'Invalid equipment selection.'}), 400
        if condition_requested not in ('excellent', 'good', 'fair', 'poor'):
            return jsonify({'ok': False, 'message': 'Invalid condition value.'}), 400
        if equipment_id in selected_ids:
            continue

        selected_ids.append(equipment_id)
        parsed_items.append({'equipment_id': equipment_id, 'condition_requested': condition_requested})

    available_rows = Equipment.get_all(status='available')
    available_ids = {row.get('equipment_id') for row in available_rows}
    unavailable = [str(item_id) for item_id in selected_ids if item_id not in available_ids]
    if unavailable:
        return jsonify({'ok': False, 'message': 'Some selected items are no longer available.'}), 400

    try:
        created = MemberBorrowRequest.create_request(
            member_id=member_row['member_id'],
            expected_return_date=expected_return_date,
            usage_area=usage_area,
            notes=notes,
            items=parsed_items,
        )
    except Exception:
        current_app.logger.exception('Failed to create member borrow request for member %s', member_row['member_code'])
        return jsonify({'ok': False, 'message': 'Unable to submit request right now.'}), 500

    return jsonify({'ok': True, 'request_code': created['request_code']})


@auth_bp.route('/api/member/return-request', methods=['POST'])
@login_required
def submit_member_return_request():
    if getattr(current_user, 'role', None) != 'member':
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not Member.is_profile_complete(member_row):
        return jsonify({'ok': False, 'message': 'Complete your profile first.'}), 400

    payload = request.get_json(silent=True) or {}
    borrow_item_id = payload.get('borrow_item_id')
    requested_condition = (payload.get('requested_condition') or '').strip().lower()
    member_feedback = (payload.get('member_feedback') or '').strip() or None

    if not isinstance(borrow_item_id, int):
        return jsonify({'ok': False, 'message': 'Invalid borrowed item selection.'}), 400
    if requested_condition not in ('excellent', 'good', 'fair', 'poor'):
        return jsonify({'ok': False, 'message': 'Invalid requested return condition.'}), 400

    result = MemberReturnRequest.create_or_resubmit_request(
        member_id=member_row['member_id'],
        borrow_item_id=borrow_item_id,
        requested_condition=requested_condition,
        member_feedback=member_feedback,
    )
    if not result.get('ok'):
        return jsonify({'ok': False, 'message': result.get('message') or 'Unable to submit return request.'}), 400

    return jsonify({'ok': True, 'return_request_code': result.get('return_request_code')})


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out.', 'info')
    response = make_response(redirect(url_for('auth.login', logged_out=1)))
    response.delete_cookie(current_app.config.get('SESSION_COOKIE_NAME', 'session'))
    response.delete_cookie(current_app.config.get('REMEMBER_COOKIE_NAME', 'remember_token'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
