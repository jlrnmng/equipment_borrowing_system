from flask import Blueprint, render_template
from flask_login import login_required

from app.models.equipment import Equipment
from app.utils.db import get_db

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@dashboard_bp.route('/dashboard')
@login_required
def index():
    db = get_db()
    stats = {
        'active_members': 0,
        'available_equipment': 0,
        'active_borrowings': 0,
        'overdue_items': 0,
    }
    try:
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM members WHERE status = 'active'"
            )
            stats['active_members'] = cursor.fetchone()['cnt']

            stats['available_equipment'] = Equipment.count_by_status('available')

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM borrow_records WHERE status = 'active'"
            )
            stats['active_borrowings'] = cursor.fetchone()['cnt']

            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM borrow_records WHERE status = 'overdue'"
            )
            stats['overdue_items'] = cursor.fetchone()['cnt']

            # 5 most recent activity log entries
            cursor.execute(
                "SELECT al.action, al.description, al.created_at, "
                "       s.full_name AS staff_name "
                "FROM activity_log al "
                "LEFT JOIN staff s ON s.staff_id = al.staff_id "
                "ORDER BY al.created_at DESC LIMIT 5"
            )
            recent_activity = cursor.fetchall()
    except Exception:
        recent_activity = []

    return render_template(
        'dashboard/index.html',
        stats=stats,
        recent_activity=recent_activity,
    )
