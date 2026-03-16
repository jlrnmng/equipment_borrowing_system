from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.forms import StaffRegistrationForm
from app.models.member import Member
from app.models.staff import Staff

staff_admin_bp = Blueprint('staff_admin', __name__)


@staff_admin_bp.route('/staff/register', methods=['GET', 'POST'])
@login_required
def register_staff():
    # Admin-only in UI to prevent privilege escalation.
    if current_user.role != 'admin':
        flash('Only administrators can register staff accounts.', 'danger')
        return redirect(url_for('dashboard.index'))

    form = StaffRegistrationForm()
    created_staff = None

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        full_name = form.full_name.data.strip()
        role = form.role.data
        allowed_domain = current_app.config.get('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph')

        if not email.endswith(f'@{allowed_domain}'):
            flash(f'Staff Google email must end with @{allowed_domain}.', 'danger')
            return render_template('staff/register.html', form=form)

        existing_staff = Staff.get_by_email(email)
        if existing_staff:
            flash('A staff account with this Google email already exists.', 'warning')
            return render_template('staff/register.html', form=form)

        existing_member = Member.get_by_email_or_google_email(email)
        if existing_member:
            flash('This email already belongs to a member account.', 'warning')
            return render_template('staff/register.html', form=form)

        created_staff = Staff.create_google_only_staff(
            full_name=full_name,
            email=email,
            role=role,
        )
        created_staff.update(
            {
                'full_name': full_name,
                'email': email,
                'role': role,
            }
        )
        flash(f"Staff account {created_staff['staff_code']} registered successfully.", 'success')

    return render_template('staff/register.html', form=form, created_staff=created_staff)
