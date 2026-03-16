from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.forms import MemberRegistrationForm
from app.models.member import Member
from app.models.staff import Staff
from app.utils.qr import generate_member_qr

members_bp = Blueprint('members', __name__)


def _is_authorized_registrar():
    """Only first admin or authorized staff can register members."""
    return current_user.role in ('admin', 'staff')


@members_bp.route('/members/register', methods=['GET', 'POST'])
@login_required
def register_member():
    if not _is_authorized_registrar():
        flash('You are not authorized to register members.', 'danger')
        return redirect(url_for('dashboard.index'))

    form = MemberRegistrationForm()
    created_member = None

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        allowed_domain = current_app.config.get('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph')

        if not email.endswith(f'@{allowed_domain}'):
            flash(f'Member Google email must end with @{allowed_domain}.', 'danger')
            return render_template('members/register.html', form=form)

        existing_member = Member.get_by_email_or_google_email(email)
        if existing_member:
            flash('A member with this Google email already exists.', 'warning')
            return render_template('members/register.html', form=form)

        existing_staff = Staff.get_by_email(email)
        if existing_staff:
            flash('This Google email already belongs to a staff account.', 'warning')
            return render_template('members/register.html', form=form)

        member_code = Member.get_next_member_code()
        qr_code_path = generate_member_qr(member_code)

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
            created_by=current_user.id,
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

        flash(f"Member {created_member['member_code']} registered successfully.", 'success')

    return render_template('members/register.html', form=form, created_member=created_member)
