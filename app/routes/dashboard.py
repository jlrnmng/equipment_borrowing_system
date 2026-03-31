from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.models.equipment import Equipment
from app.utils.db import get_db

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@dashboard_bp.route('/dashboard')
@login_required
def index():
    if getattr(current_user, 'role', None) == 'member':
        if not getattr(current_user, 'profile_complete', False):
            flash('Please complete your profile details before accessing your dashboard.', 'warning')
            return redirect(url_for('auth.complete_profile'))
        return redirect(url_for('auth.member_dashboard'))

    db = get_db()
    search_query = (request.args.get('q') or '').strip()
    stats = {
        'available_equipment': 0,
        'active_borrowings': 0,
        'overdue_items': 0,
        'todays_transactions': 0,
        'todays_borrows': 0,
        'todays_returns': 0,
    }
    search_results = {
        'members': [],
        'equipment': [],
        'transactions': [],
    }

    try:
        with db.cursor() as cursor:
            stats['available_equipment'] = Equipment.count_by_status('available')

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM borrow_records WHERE status = 'active'"
            )
            stats['active_borrowings'] = cursor.fetchone()['cnt']

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM borrow_records WHERE status = 'overdue'"
            )
            stats['overdue_items'] = cursor.fetchone()['cnt']

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM borrow_records
                WHERE DATE(borrow_date) = CURDATE()
                """
            )
            stats['todays_borrows'] = int((cursor.fetchone() or {}).get('cnt', 0))

            cursor.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM borrow_items
                WHERE DATE(returned_at) = CURDATE()
                """
            )
            stats['todays_returns'] = int((cursor.fetchone() or {}).get('cnt', 0))
            stats['todays_transactions'] = stats['todays_borrows'] + stats['todays_returns']

            # 5 most recent activity log entries
            cursor.execute(
                "SELECT al.action, al.description, al.created_at, "
                "       s.full_name AS staff_name "
                "FROM activity_log al "
                "LEFT JOIN staff s ON s.staff_id = al.staff_id "
                "ORDER BY al.created_at DESC LIMIT 5"
            )
            recent_activity = cursor.fetchall()

            if search_query:
                like = f"%{search_query}%"

                cursor.execute(
                    """
                    SELECT member_id, member_code, first_name, last_name, email, status
                    FROM members
                    WHERE member_code LIKE %s
                       OR CONCAT(first_name, ' ', last_name) LIKE %s
                       OR email LIKE %s
                    ORDER BY created_at DESC
                    LIMIT 8
                    """,
                    (like, like, like),
                )
                search_results['members'] = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT equipment_id, equipment_code, equipment_name, category, status
                    FROM equipment
                    WHERE equipment_code LIKE %s
                       OR equipment_name LIKE %s
                       OR category LIKE %s
                    ORDER BY updated_at DESC
                    LIMIT 8
                    """,
                    (like, like, like),
                )
                search_results['equipment'] = cursor.fetchall()

                cursor.execute(
                    """
                    SELECT br.borrow_id, br.transaction_code, br.status, br.expected_return_date,
                           m.member_code, m.first_name, m.last_name
                    FROM borrow_records br
                    INNER JOIN members m ON m.member_id = br.member_id
                    WHERE br.transaction_code LIKE %s
                       OR m.member_code LIKE %s
                       OR CONCAT(m.first_name, ' ', m.last_name) LIKE %s
                    ORDER BY br.borrow_date DESC
                    LIMIT 8
                    """,
                    (like, like, like),
                )
                search_results['transactions'] = cursor.fetchall()
    except Exception:
        recent_activity = []

    return render_template(
        'dashboard/index.html',
        stats=stats,
        recent_activity=recent_activity,
        search_query=search_query,
        search_results=search_results,
    )
