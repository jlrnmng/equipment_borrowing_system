"""
Reports and Analytics Route
Provides staff with comprehensive reports on borrowing activity, equipment usage, and violations.
Supports filtering, date ranges, and CSV export.
"""

from flask import Blueprint, render_template, request, jsonify, send_file
from flask_login import login_required, current_user
from io import StringIO
import csv
from datetime import datetime, timedelta
from app.utils.db import get_db
from app.models.staff import Staff

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')


def check_staff_access():
    """Ensure current user is staff"""
    if not current_user.is_authenticated:
        return False
    staff = Staff.get_by_id(current_user.id)
    return staff is not None


# ============================================================================
# REPORT QUERY FUNCTIONS
# ============================================================================

def get_active_borrowings(member_code=None, equipment_code=None, usage_area=None):
    """
    Get all active (not returned) borrowing records
    
    Args:
        member_code: Filter by specific member code
        equipment_code: Filter by specific equipment code
        usage_area: Filter by facility usage area
    
    Returns:
        List of active borrow records with member and equipment details
    """
    db = get_db()
    cursor = db.cursor()
    
    query = """
        SELECT 
            br.id,
            br.borrow_date,
            br.expected_return_date,
            br.status,
            br.usage_area,
            m.member_code,
            m.name as member_name,
            m.email,
            GROUP_CONCAT(e.code) as equipment_codes,
            GROUP_CONCAT(e.name) as equipment_names,
            COUNT(bi.id) as item_count
        FROM borrow_records br
        JOIN members m ON br.member_id = m.id
        JOIN borrow_items bi ON br.id = bi.borrow_record_id
        JOIN equipment e ON bi.equipment_id = e.id
        WHERE br.status = 'active'
    """
    
    params = []
    
    if member_code:
        query += " AND m.member_code LIKE %s"
        params.append(f"%{member_code}%")
    
    if equipment_code:
        query += " AND e.code LIKE %s"
        params.append(f"%{equipment_code}%")
    
    if usage_area:
        query += " AND br.usage_area = %s"
        params.append(usage_area)
    
    query += " GROUP BY br.id ORDER BY br.borrow_date DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    
    return results if results else []


def get_overdue_items(days_overdue=None, member_code=None):
    """
    Get all overdue borrowing records
    
    Args:
        days_overdue: Filter for items overdue by at least X days (None = all overdue)
        member_code: Filter by specific member code
    
    Returns:
        List of overdue records with calculated overdue duration
    """
    db = get_db()
    cursor = db.cursor()
    
    query = """
        SELECT 
            br.id,
            br.borrow_date,
            br.expected_return_date,
            DATEDIFF(NOW(), br.expected_return_date) as days_overdue,
            br.status,
            br.usage_area,
            m.member_code,
            m.name as member_name,
            m.email,
            GROUP_CONCAT(e.code) as equipment_codes,
            GROUP_CONCAT(e.name) as equipment_names,
            COUNT(bi.id) as item_count
        FROM borrow_records br
        JOIN members m ON br.member_id = m.id
        JOIN borrow_items bi ON br.id = bi.borrow_record_id
        JOIN equipment e ON bi.equipment_id = e.id
        WHERE br.status = 'active' AND br.expected_return_date < NOW()
    """
    
    params = []
    
    if days_overdue:
        query += " AND DATEDIFF(NOW(), br.expected_return_date) >= %s"
        params.append(days_overdue)
    
    if member_code:
        query += " AND m.member_code LIKE %s"
        params.append(f"%{member_code}%")
    
    query += " GROUP BY br.id ORDER BY br.expected_return_date ASC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    
    return results if results else []


def get_member_borrowing_history(member_code=None, start_date=None, end_date=None):
    """
    Get complete borrowing history for member(s)
    
    Args:
        member_code: Filter by specific member code
        start_date: Filter from date (YYYY-MM-DD)
        end_date: Filter to date (YYYY-MM-DD)
    
    Returns:
        List of all borrow records for member(s) with status and dates
    """
    db = get_db()
    cursor = db.cursor()
    
    query = """
        SELECT 
            br.id,
            br.borrow_date,
            br.expected_return_date,
            br.return_date,
            br.status,
            br.usage_area,
            m.member_code,
            m.name as member_name,
            m.email,
            GROUP_CONCAT(e.code) as equipment_codes,
            GROUP_CONCAT(e.name) as equipment_names,
            COUNT(bi.id) as item_count
        FROM borrow_records br
        JOIN members m ON br.member_id = m.id
        JOIN borrow_items bi ON br.id = bi.borrow_record_id
        JOIN equipment e ON bi.equipment_id = e.id
        WHERE 1=1
    """
    
    params = []
    
    if member_code:
        query += " AND m.member_code LIKE %s"
        params.append(f"%{member_code}%")
    else:
        # If no member filter, limit to recent history
        if not start_date:
            start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    
    if start_date:
        query += " AND br.borrow_date >= %s"
        params.append(start_date)
    
    if end_date:
        query += " AND br.borrow_date <= %s"
        params.append(end_date)
    
    query += " GROUP BY br.id ORDER BY br.borrow_date DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    
    return results if results else []


def get_equipment_usage_report(equipment_code=None, start_date=None, end_date=None):
    """
    Get equipment usage statistics
    
    Args:
        equipment_code: Filter by specific equipment code
        start_date: Filter from date (YYYY-MM-DD)
        end_date: Filter to date (YYYY-MM-DD)
    
    Returns:
        List of equipment with usage statistics
    """
    db = get_db()
    cursor = db.cursor()
    
    # Default to last 90 days
    if not start_date:
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
    
    query = """
        SELECT 
            e.id,
            e.code,
            e.name,
            e.category,
            e.status,
            COUNT(DISTINCT bi.id) as total_times_borrowed,
            COUNT(DISTINCT br.member_id) as unique_members,
            SUM(CASE WHEN br.status = 'active' THEN 1 ELSE 0 END) as currently_borrowed,
            MAX(br.borrow_date) as last_borrowed_date,
            AVG(DATEDIFF(br.return_date, br.borrow_date)) as avg_borrow_duration_days
        FROM equipment e
        LEFT JOIN borrow_items bi ON e.id = bi.equipment_id
        LEFT JOIN borrow_records br ON bi.borrow_record_id = br.id 
            AND br.borrow_date >= %s 
            AND br.borrow_date <= %s
        WHERE 1=1
    """
    
    params = [start_date, end_date]
    
    if equipment_code:
        query += " AND e.code LIKE %s"
        params.append(f"%{equipment_code}%")
    
    query += " GROUP BY e.id ORDER BY total_times_borrowed DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    
    return results if results else []


def get_violation_log(violation_type=None, member_code=None, start_date=None, end_date=None):
    """
    Get all recorded violations
    
    Args:
        violation_type: Filter by violation type (overdue, damage, unauthorized_external_use)
        member_code: Filter by member code
        start_date: Filter from date (YYYY-MM-DD)
        end_date: Filter to date (YYYY-MM-DD)
    
    Returns:
        List of violations with member and equipment details
    """
    db = get_db()
    cursor = db.cursor()
    
    query = """
        SELECT
            v.violation_id AS id,
            v.violation_type,
            v.description,
            v.violation_date AS recorded_date,
            m.member_code,
            TRIM(CONCAT(
                COALESCE(m.first_name, ''),
                ' ',
                COALESCE(m.middle_name, ''),
                ' ',
                COALESCE(m.last_name, '')
            )) AS member_name,
            m.email,
            e.equipment_code,
            e.equipment_name,
            br.borrow_date,
            br.actual_return_date AS return_date
        FROM violations v
        JOIN members m ON v.member_id = m.member_id
        LEFT JOIN equipment e ON v.equipment_id = e.equipment_id
        LEFT JOIN borrow_records br ON v.borrow_id = br.borrow_id
        WHERE 1=1
    """
    
    params = []
    
    if violation_type:
        query += " AND v.violation_type = %s"
        params.append(violation_type)
    
    if member_code:
        query += " AND m.member_code LIKE %s"
        params.append(f"%{member_code}%")
    
    if start_date:
        query += " AND v.violation_date >= %s"
        params.append(start_date)
    else:
        # Default to last 90 days if not specified
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
        query += " AND v.violation_date >= %s"
        params.append(start_date)
    
    if end_date:
        query += " AND v.violation_date <= %s"
        params.append(end_date)
    
    query += " ORDER BY v.violation_date DESC"
    
    cursor.execute(query, params)
    results = cursor.fetchall()
    cursor.close()
    
    return results if results else []


# ============================================================================
# ROUTES
# ============================================================================

@reports_bp.route('/', methods=['GET'])
@login_required
def reports_index():
    """Main reports page with links to all reports"""
    if not check_staff_access():
        return render_template('error.html', message='Access denied'), 403
    
    return render_template('reports/index.html')


@reports_bp.route('/active-borrowings', methods=['GET', 'POST'])
@login_required
def active_borrowings_report():
    """Active borrowings report with filters"""
    if not check_staff_access():
        return render_template('error.html', message='Access denied'), 403
    
    member_code = request.args.get('member_code', '').strip()
    equipment_code = request.args.get('equipment_code', '').strip()
    usage_area = request.args.get('usage_area', '').strip()
    export_csv = request.args.get('export', '').lower() == 'true'
    
    # Get unique usage areas for filter dropdown
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT DISTINCT usage_area FROM borrow_records WHERE usage_area IS NOT NULL ORDER BY usage_area")
    usage_areas = cursor.fetchall()
    cursor.close()
    
    # Get report data
    report_data = get_active_borrowings(member_code, equipment_code, usage_area or None)
    
    if export_csv:
        return generate_csv_report(
            'active_borrowings',
            ['ID', 'Member Code', 'Member Name', 'Email', 'Equipment', 'Items', 'Area', 'Borrow Date', 'Expected Return'],
            report_data,
            ['id', 'member_code', 'member_name', 'email', 'equipment_codes', 'item_count', 'usage_area', 'borrow_date', 'expected_return_date']
        )
    
    return render_template(
        'reports/active_borrowings.html',
        report_data=report_data,
        usage_areas=usage_areas,
        filters={
            'member_code': member_code,
            'equipment_code': equipment_code,
            'usage_area': usage_area
        }
    )


@reports_bp.route('/overdue-items', methods=['GET', 'POST'])
@login_required
def overdue_items_report():
    """Overdue items report with filters"""
    if not check_staff_access():
        return render_template('error.html', message='Access denied'), 403
    
    days_overdue = request.args.get('days_overdue', '', type=int) or None
    member_code = request.args.get('member_code', '').strip()
    export_csv = request.args.get('export', '').lower() == 'true'
    
    # Get report data
    report_data = get_overdue_items(days_overdue, member_code)
    
    if export_csv:
        return generate_csv_report(
            'overdue_items',
            ['ID', 'Member Code', 'Member Name', 'Email', 'Equipment', 'Items', 'Borrow Date', 'Due Date', 'Days Overdue'],
            report_data,
            ['id', 'member_code', 'member_name', 'email', 'equipment_codes', 'item_count', 'borrow_date', 'expected_return_date', 'days_overdue']
        )
    
    return render_template(
        'reports/overdue_items.html',
        report_data=report_data,
        filters={
            'days_overdue': days_overdue or '',
            'member_code': member_code
        }
    )


@reports_bp.route('/member-history', methods=['GET', 'POST'])
@login_required
def member_history_report():
    """Member borrowing history report with date range filters"""
    if not check_staff_access():
        return render_template('error.html', message='Access denied'), 403
    
    member_code = request.args.get('member_code', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    export_csv = request.args.get('export', '').lower() == 'true'
    
    # Get report data
    report_data = get_member_borrowing_history(member_code or None, start_date or None, end_date or None)
    
    if export_csv:
        return generate_csv_report(
            'member_history',
            ['ID', 'Member Code', 'Member Name', 'Email', 'Equipment', 'Items', 'Status', 'Borrow Date', 'Expected Return', 'Return Date'],
            report_data,
            ['id', 'member_code', 'member_name', 'email', 'equipment_codes', 'item_count', 'status', 'borrow_date', 'expected_return_date', 'return_date']
        )
    
    return render_template(
        'reports/member_history.html',
        report_data=report_data,
        filters={
            'member_code': member_code,
            'start_date': start_date,
            'end_date': end_date
        }
    )


@reports_bp.route('/equipment-usage', methods=['GET', 'POST'])
@login_required
def equipment_usage_report():
    """Equipment usage statistics report"""
    if not check_staff_access():
        return render_template('error.html', message='Access denied'), 403
    
    equipment_code = request.args.get('equipment_code', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    export_csv = request.args.get('export', '').lower() == 'true'
    
    # Get report data
    report_data = get_equipment_usage_report(equipment_code or None, start_date or None, end_date or None)
    
    if export_csv:
        return generate_csv_report(
            'equipment_usage',
            ['Equipment Code', 'Equipment Name', 'Category', 'Status', 'Times Borrowed', 'Unique Members', 'Currently Borrowed', 'Last Borrowed', 'Avg Duration (days)'],
            report_data,
            ['code', 'name', 'category', 'status', 'total_times_borrowed', 'unique_members', 'currently_borrowed', 'last_borrowed_date', 'avg_borrow_duration_days']
        )
    
    return render_template(
        'reports/equipment_usage.html',
        report_data=report_data,
        filters={
            'equipment_code': equipment_code,
            'start_date': start_date,
            'end_date': end_date
        }
    )


@reports_bp.route('/violations', methods=['GET', 'POST'])
@login_required
def violations_report():
    """Violation log report with filters"""
    if not check_staff_access():
        return render_template('error.html', message='Access denied'), 403
    
    violation_type = request.args.get('violation_type', '').strip()
    member_code = request.args.get('member_code', '').strip()
    start_date = request.args.get('start_date', '').strip()
    end_date = request.args.get('end_date', '').strip()
    export_csv = request.args.get('export', '').lower() == 'true'
    
    # Get report data
    report_data = get_violation_log(violation_type or None, member_code or None, start_date or None, end_date or None)
    
    if export_csv:
        return generate_csv_report(
            'violations',
            ['ID', 'Type', 'Member Code', 'Member Name', 'Equipment Code', 'Equipment Name', 'Description', 'Recorded Date'],
            report_data,
            ['id', 'violation_type', 'member_code', 'member_name', 'equipment_code', 'equipment_name', 'description', 'recorded_date']
        )
    
    return render_template(
        'reports/violations.html',
        report_data=report_data,
        filters={
            'violation_type': violation_type,
            'member_code': member_code,
            'start_date': start_date,
            'end_date': end_date
        }
    )


# ============================================================================
# CSV EXPORT HELPER
# ============================================================================

def generate_csv_report(filename_base, headers, data, field_keys):
    """
    Generate CSV file from report data
    
    Args:
        filename_base: Base name for CSV file (without extension)
        headers: List of column headers
        data: List of dictionaries with report data
        field_keys: List of dictionary keys to extract (in order)
    
    Returns:
        CSV file as attachment
    """
    output = StringIO()
    writer = csv.writer(output)
    
    # Write headers
    writer.writerow(headers)
    
    # Write data rows
    for row in data:
        csv_row = []
        for key in field_keys:
            value = row.get(key, '')
            # Format dates nicely
            if isinstance(value, datetime):
                value = value.strftime('%Y-%m-%d %H:%M')
            csv_row.append(value if value is not None else '')
        writer.writerow(csv_row)
    
    # Create file-like object
    output.seek(0)
    file_bytes = StringIO(output.getvalue())
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{filename_base}_{timestamp}.csv"
    
    return send_file(
        file_bytes,
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )
