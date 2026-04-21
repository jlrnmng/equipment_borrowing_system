from flask import Blueprint, current_app, flash, redirect, render_template, url_for, jsonify, request
from flask_login import current_user, login_required

from app.forms import StaffRegistrationForm
from app.models.member import Member
from app.models.staff import Staff
from app.utils.notifications import queue_and_send_notification

staff_admin_bp = Blueprint('staff_admin', __name__)


def _get_staff_allowed_domains():
    """Return normalized allowed domains for staff registration emails."""
    raw_value = current_app.config.get('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph') or ''
    configured = [part.strip().lower() for part in str(raw_value).split(',') if part.strip()]

    defaults = ['my.cspc.edu.ph', 'cspc.edu.ph']
    ordered = configured + defaults

    seen = set()
    domains = []
    for domain in ordered:
        if domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def _is_allowed_staff_email_domain(email):
    email_value = (email or '').strip().lower()
    if '@' not in email_value:
        return False

    email_domain = email_value.split('@', 1)[1]
    for domain in _get_staff_allowed_domains():
        if email_domain == domain or email_domain.endswith(f'.{domain}'):
            return True
    return False


@staff_admin_bp.route('/staff/register', methods=['GET', 'POST'])
@login_required
def register_staff():
    # Both admin and staff can add/register new staff members
    if current_user.role not in ('admin', 'staff'):
        flash('Only admin or staff accounts can add new staff.', 'danger')
        return redirect(url_for('dashboard.index'))

    form = StaffRegistrationForm()
    form.role.data = 'staff'
    created_staff = None

    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        full_name = form.full_name.data.strip()
        role = 'staff'
        allowed_domains = _get_staff_allowed_domains()

        if not _is_allowed_staff_email_domain(email):
            domains_label = ', '.join(f'@{domain}' for domain in allowed_domains)
            flash(f'Staff Google email must use one of these domains: {domains_label}.', 'danger')
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


@staff_admin_bp.route('/members/pending-approvals', methods=['GET'])
@login_required
def pending_member_approvals():
    """List all pending member registrations awaiting admin approval."""
    if current_user.role not in ('admin', 'staff'):
        flash('You are not authorized to manage member approvals.', 'danger')
        return redirect(url_for('dashboard.index'))

    pending_members = Member.get_pending_members()
    
    return render_template(
        'staff/pending_member_approvals.html',
        pending_members=pending_members,
    )


@staff_admin_bp.route('/members/<int:member_id>/approve', methods=['POST'])
@login_required
def approve_member(member_id):
    """Approve a pending member registration."""
    if current_user.role not in ('admin', 'staff'):
        return jsonify({'ok': False, 'message': 'Unauthorized'}), 403

    member = Member.get_profile_by_member_code(None)
    
    # First, get the member details before approving
    db_conn = current_app.config.get('get_db')
    from app.utils.db import get_db
    db = get_db()
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT member_id, member_code, first_name, last_name, email, status FROM members WHERE member_id = %s",
            (member_id,),
        )
        member_row = cursor.fetchone()

    if not member_row:
        return jsonify({'ok': False, 'message': 'Member not found'}), 404

    if member_row['status'] != 'pending':
        return jsonify({'ok': False, 'message': 'Member is not pending approval'}), 400

    # Approve the member
    success = Member.approve_member(member_id)
    
    if success:
        # Send approval email notification
        try:
            member_name = f"{member_row.get('first_name', '')} {member_row.get('last_name', '')}".strip()
            queue_and_send_notification(
                member_id=member_id,
                borrow_id=None,
                notification_type='registration_approved',
                recipient_email=member_row['email'],
                subject='Registration Approved – QR Equipment Borrowing System',
                message=f"""Dear {member_name},

Great news! Your registration with our QR Equipment Borrowing System has been approved.

Your account details:
- Member Code: {member_row['member_code']}
- Email: {member_row['email']}

You can now log in using your Google account. Visit us at the login page to get started.

Best regards,
Equipment Borrowing System Admin""",
            )
        except Exception:
            current_app.logger.exception('Failed to send approval notification for member %s', member_id)

        flash(f'Member {member_row["member_code"]} has been approved successfully.', 'success')
        return jsonify({'ok': True, 'message': 'Member approved successfully'})
    
    return jsonify({'ok': False, 'message': 'Failed to approve member'}), 500
