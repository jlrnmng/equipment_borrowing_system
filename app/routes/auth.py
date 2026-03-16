from urllib.parse import urlsplit

from flask import Blueprint, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.forms import LoginForm
from app.models.staff import Staff
from app.utils.db import get_db

auth_bp = Blueprint('auth', __name__)


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
            staff = Staff(
                row['staff_id'], row['staff_code'], row['email'],
                row['full_name'], row['role'], row['status'],
            )
            login_user(staff)

            # Record last login timestamp
            db = get_db()
            with db.cursor() as cursor:
                cursor.execute(
                    "UPDATE staff SET last_login = NOW() WHERE staff_id = %s",
                    (staff.id,),
                )
            db.commit()

            flash(f'Welcome back, {staff.full_name}!', 'success')

            # Safe redirect – reject absolute URLs to prevent open redirect
            next_page = request.args.get('next')
            if next_page and urlsplit(next_page).netloc == '':
                return redirect(next_page)
            return redirect(url_for('dashboard.index'))

        flash('Invalid email or password.', 'danger')

    return render_template('auth/login.html', form=form)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    response = make_response(redirect(url_for('auth.login')))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response
