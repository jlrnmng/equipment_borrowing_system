from urllib.parse import urlsplit
import secrets
from datetime import datetime, time, timedelta

from flask import Blueprint, current_app, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import oauth
from app.forms import LoginForm
from app.models.app_setting import AppSetting
from app.models.equipment import Equipment
from app.models.member import Member
from app.models.member_request import MemberBorrowRequest
from app.models.member_return_request import MemberReturnRequest
from app.models.staff import Staff
from app.realtime import emit_app_data_changed
from app.utils.db import get_db
from app.utils.request_expiry import expire_stale_requests
from app.utils.qr import extract_equipment_code
from app.utils.notifications import build_welcome_message, queue_and_send_notification
from app.utils.qr import generate_member_qr

auth_bp = Blueprint('auth', __name__)


def _get_google_allowed_domains():
    """Return normalized allowed domains for Google OAuth email checks."""
    raw_value = current_app.config.get('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph') or ''
    configured = [part.strip().lower() for part in str(raw_value).split(',') if part.strip()]

    # Keep legacy/default domain and explicitly allow cpsc.edu.ph addresses.
    defaults = ['my.cspc.edu.ph', 'cpsc.edu.ph']
    ordered = configured + defaults

    seen = set()
    domains = []
    for domain in ordered:
        if domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def _member_redirect_after_login(member_row):
    member_user = Member.to_member_user(member_row, photo_url=session.get('google_picture_url'))
    login_user(member_user)

    if not Member.is_profile_complete(member_row):
        flash('Please complete your profile details before accessing your dashboard.', 'warning')
        return redirect(url_for('auth.complete_profile'))

    flash(f'Welcome, {member_user.full_name}!', 'success')
    return redirect(url_for('auth.member_dashboard'))


def get_google_oauth_config():
    """Return Google OAuth settings from app config (never hardcode credentials)."""
    allowed_domains = _get_google_allowed_domains()
    return {
        'client_id': current_app.config.get('GOOGLE_CLIENT_ID'),
        'client_secret': current_app.config.get('GOOGLE_CLIENT_SECRET'),
        'redirect_uri': current_app.config.get('GOOGLE_REDIRECT_URI'),
        'allowed_domain': allowed_domains[0],
        'allowed_domains': allowed_domains,
    }


def is_google_oauth_enabled():
    cfg = get_google_oauth_config()
    return bool(cfg['client_id'] and cfg['client_secret'])


def is_allowed_domain(email):
    email_value = (email or '').strip().lower()
    if '@' not in email_value:
        return False

    email_domain = email_value.split('@', 1)[1]
    for domain in _get_google_allowed_domains():
        if email_domain == domain or email_domain.endswith(f".{domain}"):
            return True
    return False


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
        allowed_domains=get_google_oauth_config()['allowed_domains'],
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
        userinfo = None
        try:
            userinfo = google.parse_id_token(token, nonce=nonce)
        except Exception:
            current_app.logger.exception('Google parse_id_token failed; will try userinfo fallback')

        if not userinfo:
            try:
                profile_response = google.get('userinfo')
                if profile_response and profile_response.ok:
                    userinfo = profile_response.json() or None
            except Exception:
                current_app.logger.exception('Google userinfo fallback failed')
    except Exception:
        flash('Google sign-in failed. Please try again.', 'danger')
        return redirect(url_for('auth.login'))

    if not userinfo:
        flash('Unable to retrieve Google profile information.', 'danger')
        return redirect(url_for('auth.login'))

    email = (userinfo.get('email') or '').strip().lower()
    google_sub = userinfo.get('sub')
    full_name = (userinfo.get('name') or 'User').strip()
    google_picture_url = (userinfo.get('picture') or '').strip() or None
    email_verified = bool(userinfo.get('email_verified'))
    oauth_flow = session.pop('oauth_flow', 'login')

    if not email or not google_sub:
        flash('Google account data is incomplete.', 'danger')
        return redirect(url_for('auth.login'))

    if not email_verified:
        flash('Your Google email must be verified.', 'warning')
        return redirect(url_for('auth.login'))

    if google_picture_url:
        session['google_picture_url'] = google_picture_url

    if not is_allowed_domain(email):
        domains_label = ', '.join(f"@{d}" for d in _get_google_allowed_domains())
        flash(f'Only {domains_label} accounts are allowed.', 'danger')
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
            Staff.update_google_identity(staff_row['staff_id'], email, google_sub, profile_picture_url=google_picture_url)

        staff = Staff(
            staff_row['staff_id'],
            staff_row['staff_code'],
            staff_row['email'],
            staff_row['full_name'] or full_name,
            staff_row['role'],
            staff_row['status'],
            photo_url=google_picture_url or staff_row.get('profile_picture_url'),
        )
        login_user(staff)
        Staff.touch_last_login(staff.id)
        flash(f'Welcome, {staff.full_name}!', 'success')
        return redirect(url_for('dashboard.index'))

    member_row = Member.get_by_email_or_google_email(email)
    if member_row:
        if member_row['status'] == 'pending':
            flash('Your registration is pending admin approval. Please check your notifications for updates.', 'info')
            return redirect(url_for('auth.login'))
        
        if member_row['status'] != 'active':
            flash('Your member account is not active. Contact staff.', 'danger')
            return redirect(url_for('auth.login'))

        existing_sub = member_row.get('google_sub')
        if existing_sub and existing_sub != google_sub:
            flash('Google account mismatch. Contact staff for account relink.', 'danger')
            return redirect(url_for('auth.login'))

        if not existing_sub:
            Member.link_google_identity(member_row['member_id'], email, google_sub, profile_picture_url=google_picture_url)

        member_row = Member.get_auth_by_member_id(member_row['member_id'])

        return _member_redirect_after_login(member_row)

    if oauth_flow == 'signup':
        first_name, last_name = _split_google_name(full_name)
        member_code = Member.get_next_member_code()
        qr_code_path = generate_member_qr(member_code)
        default_borrow_limit = AppSetting.get_int('default_borrow_limit', 3)
        if default_borrow_limit < 1:
            default_borrow_limit = 3
        created_member = Member.create_member(
            member_code=member_code,
            first_name=first_name,
            middle_name=None,
            last_name=last_name,
            email=email,
            phone=None,
            student_id=None,
            startup=None,
            max_borrow_limit=default_borrow_limit,
            created_by=None,
            qr_code_path=qr_code_path,
        )
        Member.link_google_identity(created_member['member_id'], email, google_sub, profile_picture_url=google_picture_url)

        # Queue registration pending approval notification
        try:
            queue_and_send_notification(
                member_id=created_member['member_id'],
                borrow_id=None,
                notification_type='registration_pending',
                recipient_email=email,
                subject='Registration Pending – QR Equipment Borrowing System',
                message=f"""Dear {first_name},

Thank you for registering with our QR Equipment Borrowing System. Your registration is currently pending admin approval.

Your account details:
- Member Code: {member_code}
- Email: {email}

You will receive a notification once your registration has been approved by our administrators.

Best regards,
Equipment Borrowing System Admin""",
            )
        except Exception:
            current_app.logger.exception('Failed to queue/send registration pending notification for member %s', member_code)

        flash('Registration successful! Your account is pending admin approval. Check your notifications for updates.', 'success')
        return redirect(url_for('auth.login'))

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
        allowed_domains=get_google_oauth_config()['allowed_domains'],
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

    if not member_row.get('qr_code_path'):
        try:
            qr_path = generate_member_qr(member_row['member_code'])
            db = get_db()
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE members SET qr_code_path = %s, updated_at = CURRENT_TIMESTAMP WHERE member_id = %s",
                    (qr_path, member_row['member_id']),
                )
            db.commit()
            member_row['qr_code_path'] = qr_path
        except Exception:
            current_app.logger.exception('Failed to auto-generate member QR for %s', member_row.get('member_code'))

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
            login_user(Member.to_member_user(updated_row, photo_url=session.get('google_picture_url')))
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

    history_items = []
    for req in requests:
        history_items.append(
            {
                'entry_type': 'borrow',
                'code': req.get('request_code'),
                'created_at': req.get('created_at'),
                'status': req.get('status'),
                'usage_area': req.get('usage_area'),
                'summary': req.get('equipment_names') or '-',
                'meta': f"{req.get('total_items') or 0} item(s)",
                'review_notes': req.get('review_notes'),
            }
        )

    for req in return_requests:
        history_items.append(
            {
                'entry_type': 'return',
                'code': req.get('return_request_code'),
                'created_at': req.get('created_at'),
                'status': req.get('status'),
                'usage_area': '-',
                'summary': req.get('equipment_name') or '-',
                'meta': f"Condition: {(req.get('requested_condition') or '-').upper()}",
                'review_notes': req.get('review_notes'),
            }
        )

    history_items.sort(
        key=lambda row: row.get('created_at') or datetime.min,
        reverse=True,
    )

    now_dt = datetime.now()
    grace_cutoff_time = time(17, 0)  # 4:30 PM + 30-minute grace period
    reminder_time = (datetime.combine(now_dt.date(), grace_cutoff_time) - timedelta(hours=1)).time()
    show_overdue_soon_popup = False

    for item in active_return_items:
        due_date = item.get('expected_return_date')
        if due_date == now_dt.date() and reminder_time <= now_dt.time() < grace_cutoff_time:
            show_overdue_soon_popup = True
            break
    
    # Calculate pending requests count
    pending_requests_count = sum(1 for req in requests if req.get('status') == 'pending')

    return render_template(
        'auth/member_dashboard.html',
        member=member_row,
        requests=requests,
        active_return_items=active_return_items,
        return_requests=return_requests,
        history_items=history_items,
        pending_requests_count=pending_requests_count,
        show_overdue_soon_popup=show_overdue_soon_popup,
    )


@auth_bp.route('/profile', methods=['GET'])
@login_required
def view_profile():
    role = getattr(current_user, 'role', None)

    if role == 'member':
        member_row = Member.get_auth_by_member_id(current_user.id)
        if not member_row:
            flash('Member account not found. Please sign in again.', 'danger')
            return redirect(url_for('auth.logout'))
        return render_template('auth/view_profile.html', profile=member_row, role='member')

    staff_row = Staff.get_by_id(current_user.id)
    if not staff_row:
        flash('Staff account not found. Please sign in again.', 'danger')
        return redirect(url_for('auth.logout'))
    return render_template('auth/view_profile.html', profile=staff_row, role=role)


@auth_bp.route('/profile/edit', methods=['GET', 'POST'])
@login_required
def edit_profile():
    role = getattr(current_user, 'role', None)

    if role == 'member':
        member_row = Member.get_auth_by_member_id(current_user.id)
        if not member_row:
            flash('Member account not found. Please sign in again.', 'danger')
            return redirect(url_for('auth.logout'))

        if request.method == 'POST':
            first_name = (request.form.get('first_name') or '').strip()
            middle_name = (request.form.get('middle_name') or '').strip()
            last_name = (request.form.get('last_name') or '').strip()
            phone = (request.form.get('phone') or '').strip()
            student_id = (request.form.get('student_id') or '').strip()
            startup = (request.form.get('startup') or '').strip()
            college_department = (request.form.get('college_department') or '').strip()
            program = (request.form.get('program') or '').strip()
            year_level = (request.form.get('year_level') or '').strip()

            if not first_name or not last_name:
                flash('First name and last name are required.', 'warning')
            elif not phone or not student_id or not startup or not college_department or not program or not year_level:
                flash('Phone, ID number, startup/agency, college department, program, and year are required.', 'warning')
            else:
                db = get_db()
                with db.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE members
                        SET first_name = %s,
                            middle_name = %s,
                            last_name = %s,
                            phone = %s,
                            student_id = %s,
                            startup = %s,
                            college_department = %s,
                            program = %s,
                            year_level = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE member_id = %s
                        """,
                        (
                            first_name,
                            middle_name or None,
                            last_name,
                            phone,
                            student_id,
                            startup,
                            college_department,
                            program,
                            year_level,
                            member_row['member_id'],
                        ),
                    )
                db.commit()

                updated_member = Member.get_auth_by_member_id(member_row['member_id'])
                if updated_member:
                    login_user(Member.to_member_user(updated_member, photo_url=session.get('google_picture_url')))
                flash('Profile updated successfully.', 'success')
                return redirect(url_for('auth.view_profile'))

        refreshed_member = Member.get_auth_by_member_id(member_row['member_id']) or member_row
        return render_template('auth/edit_profile.html', profile=refreshed_member, role='member')

    staff_row = Staff.get_by_id(current_user.id)
    if not staff_row:
        flash('Staff account not found. Please sign in again.', 'danger')
        return redirect(url_for('auth.logout'))

    if request.method == 'POST':
        full_name = (request.form.get('full_name') or '').strip()
        if not full_name:
            flash('Full name is required.', 'warning')
        else:
            db = get_db()
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE staff
                    SET full_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE staff_id = %s
                    """,
                    (full_name, staff_row['staff_id']),
                )
            db.commit()

            updated_staff = Staff.get_by_id(staff_row['staff_id'])
            if updated_staff:
                login_user(updated_staff)
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('auth.view_profile'))

    refreshed_staff = Staff.get_by_id(staff_row['staff_id']) or staff_row
    return render_template('auth/edit_profile.html', profile=refreshed_staff, role=role)


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
                    'equipment_image_path': row.get('equipment_image_path'),
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
                'equipment_image_path': equipment.get('equipment_image_path'),
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
    usage_area = (payload.get('usage_area') or '').strip()
    notes = (payload.get('notes') or '').strip() or None
    paper_request = payload.get('paper_request') or None
    items = payload.get('items') or []

    # Member self-service borrow requests are same-day only.
    expected_return_date = datetime.now().date()
    now_time = datetime.now().time()
    if not (time(8, 0) <= now_time <= time(16, 30)):
        return jsonify({'ok': False, 'message': 'Borrow requests are only allowed from 8:00 AM to 4:30 PM.'}), 400
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

    if len(parsed_items) > 3:
        return jsonify({'ok': False, 'message': 'You can only request up to 3 equipment items per borrow request.'}), 400

    current_borrow_count = int(member_row.get('current_borrow_count') or 0)
    max_borrow_limit = int(member_row.get('max_borrow_limit') or 0)
    next_borrow_count = current_borrow_count + len(parsed_items)
    if max_borrow_limit <= 0 or next_borrow_count > max_borrow_limit:
        return jsonify(
            {
                'ok': False,
                'message': f'Borrowed items limit exceeded. Your limit is {max_borrow_limit} item(s).',
            }
        ), 400

    available_rows = Equipment.get_all(status='available')
    available_ids = {row.get('equipment_id') for row in available_rows}
    unavailable = [str(item_id) for item_id in selected_ids if item_id not in available_ids]
    if unavailable:
        return jsonify({'ok': False, 'message': 'Some selected items are no longer available.'}), 400

    selected_lookup = {row.get('equipment_id'): row for row in available_rows if row.get('equipment_id') in selected_ids}
    needs_paper_details = False
    for equipment_id in selected_ids:
        row = selected_lookup.get(equipment_id) or {}
        category = (row.get('category') or '').lower()
        name = (row.get('equipment_name') or '').lower()
        if 'printer' in category or 'scanner' in category or 'printer' in name or 'scanner' in name:
            needs_paper_details = True
            break

    if needs_paper_details:
        if not isinstance(paper_request, dict):
            return jsonify({'ok': False, 'message': 'Paper details are required for printer/scanner requests.'}), 400

        source = (paper_request.get('source') or 'own').strip().lower()

        if source not in ('bondpaper', 'own'):
            return jsonify({'ok': False, 'message': 'Select a valid paper source.'}), 400

        if source == 'bondpaper':
            paper_type = (paper_request.get('type') or '').strip()
            quantity = paper_request.get('quantity')

            if paper_type not in ('Long', 'Short', 'A4', 'Special'):
                return jsonify({'ok': False, 'message': 'Select a valid paper type (Long, Short, A4, or Special).'}), 400

            try:
                quantity_int = int(quantity)
            except Exception:
                return jsonify({'ok': False, 'message': 'Paper quantity must be a whole number.'}), 400

            if quantity_int < 1:
                return jsonify({'ok': False, 'message': 'Paper quantity must be at least 1 sheet.'}), 400

            paper_details_line = f"[Paper Request] Use facility paper; Type: {paper_type}; Quantity: {quantity_int} sheet(s)"
        else:
            paper_details_line = '[Paper Request] Will provide own paper'

        notes = f"{notes}\n{paper_details_line}" if notes else paper_details_line

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

    emit_app_data_changed(
        reason='member_borrow_request_submitted',
        member_id=member_row['member_id'],
        include_staff=True,
        include_members=True,
    )

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

    emit_app_data_changed(
        reason='member_return_request_submitted',
        member_id=member_row['member_id'],
        include_staff=True,
        include_members=True,
    )

    return jsonify({'ok': True, 'return_request_code': result.get('return_request_code')})


@auth_bp.route('/api/member/return-request-all', methods=['POST'])
@login_required
def submit_member_return_request_all():
    return jsonify({'ok': False, 'message': 'Return-all is currently disabled. Please submit returns one item at a time.'}), 403

    if getattr(current_user, 'role', None) != 'member':
        return jsonify({'ok': False, 'message': 'Forbidden'}), 403

    member_row = Member.get_auth_by_member_id(current_user.id)
    if not Member.is_profile_complete(member_row):
        return jsonify({'ok': False, 'message': 'Complete your profile first.'}), 400

    payload = request.get_json(silent=True) or {}
    requested_condition = (payload.get('requested_condition') or 'good').strip().lower()
    member_feedback = (payload.get('member_feedback') or '').strip() or None

    if requested_condition not in ('excellent', 'good', 'fair', 'poor'):
        return jsonify({'ok': False, 'message': 'Invalid requested return condition.'}), 400

    active_items = MemberReturnRequest.get_member_active_items(member_row['member_id'])
    if not active_items:
        return jsonify({'ok': False, 'message': 'No active borrowed items found.'}), 400

    created_codes = []
    skipped = 0

    for item in active_items:
        status = (item.get('return_request_status') or '').strip().lower()
        if status in ('pending', 'approved'):
            skipped += 1
            continue

        result = MemberReturnRequest.create_or_resubmit_request(
            member_id=member_row['member_id'],
            borrow_item_id=item.get('borrow_item_id'),
            requested_condition=requested_condition,
            member_feedback=member_feedback,
        )
        if result.get('ok'):
            created_codes.append(result.get('return_request_code'))
        else:
            skipped += 1

    if not created_codes:
        return jsonify(
            {
                'ok': False,
                'message': 'All active items already have return requests or are not eligible.',
                'created_count': 0,
                'skipped_count': skipped,
            }
        ), 400

    emit_app_data_changed(
        reason='member_return_request_all_submitted',
        member_id=member_row['member_id'],
        include_staff=True,
        include_members=True,
    )

    return jsonify(
        {
            'ok': True,
            'message': f"Submitted {len(created_codes)} return request(s).",
            'created_count': len(created_codes),
            'skipped_count': skipped,
            'return_request_codes': created_codes,
        }
    )


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
