from urllib.parse import urlsplit
import secrets

from flask import Blueprint, current_app, flash, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app import oauth
from app.forms import LoginForm, MemberRegistrationForm
from app.models.member import Member
from app.models.staff import Staff
from app.utils.qr import generate_member_qr

auth_bp = Blueprint('auth', __name__)


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


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
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


@auth_bp.route('/auth/google')
def google_login():
    if not is_google_oauth_enabled():
        flash('Google OAuth is not configured yet.', 'warning')
        return redirect(url_for('auth.login'))

    google = oauth.create_client('google')
    if google is None:
        flash('Google OAuth client initialization failed.', 'danger')
        return redirect(url_for('auth.login'))

    nonce = secrets.token_urlsafe(16)
    session['oauth_nonce'] = nonce
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

        flash('Member account verified via Google. Member portal will be available in next phase.', 'info')
        return redirect(url_for('auth.login'))

    return redirect(url_for('auth.request_access', email=email))


@auth_bp.route('/request-access')
def request_access():
    email = (request.args.get('email') or '').strip().lower()
    return render_template('auth/request_access.html', email=email)


@auth_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    form = MemberRegistrationForm()
    created_member = None

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        allowed_domain = current_app.config.get('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph')

        if not email.endswith(f'@{allowed_domain}'):
            flash(f'Member Google email must end with @{allowed_domain}.', 'danger')
            return render_template('auth/signup.html', form=form)

        existing_member = Member.get_by_email_or_google_email(email)
        if existing_member:
            flash('A member with this Google email already exists. Please sign in with Google.', 'warning')
            return redirect(url_for('auth.login'))

        existing_staff = Staff.get_by_email(email)
        if existing_staff:
            flash('This Google email is already registered as staff. Please use staff login.', 'warning')
            return redirect(url_for('auth.login'))

        member_code = Member.get_next_member_code()
        qr_code_path = generate_member_qr(member_code)
        created_by = current_user.id if current_user.is_authenticated else None

        created_member = Member.create_member(
            member_code=member_code,
            first_name=form.first_name.data.strip(),
            middle_name=(form.middle_name.data or '').strip() or None,
            last_name=form.last_name.data.strip(),
            email=email,
            phone=(form.phone.data or '').strip() or None,
            student_id=(form.student_id.data or '').strip() or None,
            startup=(form.startup.data or '').strip() or None,
            max_borrow_limit=form.max_borrow_limit.data,
            created_by=created_by,
            qr_code_path=qr_code_path,
        )

        created_member.update(
            {
                'full_name': f"{form.first_name.data.strip()} {form.last_name.data.strip()}",
                'email': email,
                'startup': (form.startup.data or '').strip(),
                'qr_code_path': qr_code_path,
            }
        )

        flash(
            'Signup successful. Your account is ready. You may now continue with Google sign-in.',
            'success',
        )

    return render_template('auth/signup.html', form=form, created_member=created_member)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out.', 'info')
    response = make_response(redirect(url_for('auth.login')))
    response.delete_cookie(current_app.config.get('SESSION_COOKIE_NAME', 'session'))
    response.delete_cookie(current_app.config.get('REMEMBER_COOKIE_NAME', 'remember_token'))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
