import json
from flask_login import UserMixin

from app.utils.db import get_db


class Member:
    """Member data helpers for registration and OAuth linking."""

    @staticmethod
    def get_by_email_or_google_email(email):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT member_id, member_code, email, google_email, google_sub, "
                "first_name, last_name, status "
                "FROM members WHERE email = %s OR google_email = %s LIMIT 1",
                (email, email),
            )
            return cursor.fetchone()

    @staticmethod
    def get_by_member_code(member_code):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT member_id, member_code, first_name, middle_name, last_name,
                       email, google_email, phone, student_id, startup,
                       status, current_borrow_count, max_borrow_limit, qr_code_path
                FROM members
                WHERE member_code = %s
                LIMIT 1
                """,
                (member_code,),
            )
            return cursor.fetchone()

    @staticmethod
    def get_profile_by_member_code(member_code):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.member_id, m.member_code, m.first_name, m.middle_name, m.last_name,
                       m.email, m.google_email, m.google_sub, m.phone, m.student_id, m.startup,
                       m.status, m.current_borrow_count, m.max_borrow_limit, m.qr_code_path,
                       m.google_calendar_enabled, m.created_at, m.updated_at,
                       s.full_name AS created_by_name
                FROM members m
                LEFT JOIN staff s ON s.staff_id = m.created_by
                WHERE m.member_code = %s
                LIMIT 1
                """,
                (member_code,),
            )
            return cursor.fetchone()

    @staticmethod
    def get_current_borrowed_items(member_id):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT bi.borrow_item_id, bi.condition_borrowed, bi.borrowed_at,
                       br.borrow_id, br.transaction_code, br.borrow_date, br.expected_return_date,
                       br.status AS borrow_status, br.usage_area,
                       e.equipment_id, e.equipment_code, e.equipment_name, e.category
                FROM borrow_items bi
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
                WHERE br.member_id = %s
                  AND bi.returned_at IS NULL
                  AND br.status IN ('active', 'overdue')
                ORDER BY br.expected_return_date ASC, bi.borrow_item_id ASC
                """,
                (member_id,),
            )
            return cursor.fetchall()

    @staticmethod
    def get_borrowing_history(member_id, limit=30):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT br.borrow_id, br.transaction_code, br.borrow_date, br.expected_return_date,
                       br.actual_return_date, br.status, br.total_items, br.usage_area,
                       GROUP_CONCAT(e.equipment_code ORDER BY e.equipment_code SEPARATOR ', ') AS equipment_codes,
                       GROUP_CONCAT(e.equipment_name ORDER BY e.equipment_name SEPARATOR ', ') AS equipment_names
                FROM borrow_records br
                LEFT JOIN borrow_items bi ON bi.borrow_id = br.borrow_id
                LEFT JOIN equipment e ON e.equipment_id = bi.equipment_id
                WHERE br.member_id = %s
                GROUP BY br.borrow_id, br.transaction_code, br.borrow_date, br.expected_return_date,
                         br.actual_return_date, br.status, br.total_items, br.usage_area
                ORDER BY br.borrow_date DESC
                LIMIT %s
                """,
                (member_id, int(limit)),
            )
            return cursor.fetchall()

    @staticmethod
    def get_violations(member_id, limit=30):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT v.violation_id, v.violation_type, v.violation_date, v.days_overdue,
                       v.description, v.penalty_amount, v.status,
                       br.transaction_code,
                       e.equipment_code, e.equipment_name
                FROM violations v
                LEFT JOIN borrow_records br ON br.borrow_id = v.borrow_id
                LEFT JOIN equipment e ON e.equipment_id = v.equipment_id
                WHERE v.member_id = %s
                ORDER BY v.violation_date DESC
                LIMIT %s
                """,
                (member_id, int(limit)),
            )
            return cursor.fetchall()

    @staticmethod
    def get_calendar_status(member_id):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT google_calendar_enabled
                FROM members
                WHERE member_id = %s
                LIMIT 1
                """,
                (member_id,),
            )
            member_row = cursor.fetchone() or {}

            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_events,
                    SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active_events,
                    SUM(CASE WHEN synced_to_google = 1 THEN 1 ELSE 0 END) AS synced_events,
                    MAX(last_sync_date) AS last_sync_date
                FROM google_calendar_events
                WHERE member_id = %s
                """,
                (member_id,),
            )
            stats = cursor.fetchone() or {}

        return {
            'google_calendar_enabled': bool(member_row.get('google_calendar_enabled')),
            'total_events': int(stats.get('total_events') or 0),
            'active_events': int(stats.get('active_events') or 0),
            'synced_events': int(stats.get('synced_events') or 0),
            'last_sync_date': stats.get('last_sync_date'),
        }

    @staticmethod
    def update_profile(
        member_id,
        first_name,
        middle_name,
        last_name,
        email,
        phone,
        student_id,
        startup,
        status,
        max_borrow_limit,
    ):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE members
                SET first_name = %s,
                    middle_name = %s,
                    last_name = %s,
                    email = %s,
                    google_email = %s,
                    phone = %s,
                    student_id = %s,
                    startup = %s,
                    status = %s,
                    max_borrow_limit = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE member_id = %s
                """,
                (
                    first_name,
                    middle_name,
                    last_name,
                    email,
                    email,
                    phone,
                    student_id,
                    startup,
                    status,
                    max_borrow_limit,
                    member_id,
                ),
            )
        db.commit()

    @staticmethod
    def search_for_lookup(query, limit=10):
        db = get_db()
        with db.cursor() as cursor:
            search_term = f"%{query}%"
            cursor.execute(
                """
                SELECT member_id, member_code, first_name, middle_name, last_name,
                       email, google_email, phone, student_id, startup,
                       status, current_borrow_count, max_borrow_limit, qr_code_path
                FROM members
                WHERE member_code LIKE %s
                   OR email LIKE %s
                   OR google_email LIKE %s
                   OR first_name LIKE %s
                   OR last_name LIKE %s
                   OR student_id LIKE %s
                   OR startup LIKE %s
                ORDER BY
                    CASE WHEN status = 'active' THEN 0 ELSE 1 END,
                    last_name ASC,
                    first_name ASC
                LIMIT %s
                """,
                (
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                    limit,
                ),
            )
            return cursor.fetchall()

    @staticmethod
    def get_next_member_code():
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute("SELECT member_code FROM members ORDER BY member_id DESC LIMIT 1")
            row = cursor.fetchone()
        if row and row.get('member_code', '').startswith('MEM'):
            next_num = int(row['member_code'][3:]) + 1
        else:
            next_num = 1
        return f"MEM{next_num:03d}"

    @staticmethod
    def create_member(
        member_code,
        first_name,
        middle_name,
        last_name,
        email,
        phone,
        student_id,
        startup,
        max_borrow_limit,
        created_by,
        qr_code_path,
    ):
        db = get_db()
        notification_preferences = json.dumps({
            'email': True,
            'sms': False,
            'calendar': False,
        })

        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO members (
                    member_code,
                    first_name,
                    middle_name,
                    last_name,
                    email,
                    phone,
                    student_id,
                    startup,
                    status,
                    max_borrow_limit,
                    qr_code_path,
                    google_email,
                    registration_method,
                    notification_preferences,
                    created_by
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, 'manual', %s, %s)
                """,
                (
                    member_code,
                    first_name,
                    middle_name,
                    last_name,
                    email,
                    phone,
                    student_id,
                    startup,
                    max_borrow_limit,
                    qr_code_path,
                    email,
                    notification_preferences,
                    created_by,
                ),
            )
            member_id = cursor.lastrowid
        db.commit()

        return {
            'member_id': member_id,
            'member_code': member_code,
        }

    @staticmethod
    def link_google_identity(member_id, google_email, google_sub):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE members
                SET google_email = %s,
                    google_sub = %s,
                    registration_method = 'google_oauth'
                WHERE member_id = %s
                """,
                (google_email, google_sub, member_id),
            )
        db.commit()

    @staticmethod
    def get_auth_by_member_id(member_id):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT member_id, member_code, email, google_email,
                       first_name, middle_name, last_name,
                       phone, student_id, startup, college_department,
                      program, year_level, status,
                      current_borrow_count, max_borrow_limit, qr_code_path
                FROM members
                WHERE member_id = %s
                LIMIT 1
                """,
                (member_id,),
            )
            return cursor.fetchone()

    @staticmethod
    def is_profile_complete(member_row):
        if not member_row:
            return False

        required_fields = (
            member_row.get('first_name'),
            member_row.get('last_name'),
            member_row.get('phone'),
            member_row.get('student_id'),
            member_row.get('startup'),
            member_row.get('college_department'),
            member_row.get('program'),
            member_row.get('year_level'),
        )
        return all((value or '').strip() for value in required_fields)

    @staticmethod
    def to_member_user(member_row, photo_url=None):
        if not member_row:
            return None

        first_name = (member_row.get('first_name') or '').strip()
        middle_name = (member_row.get('middle_name') or '').strip()
        last_name = (member_row.get('last_name') or '').strip()
        full_name = f"{first_name} {middle_name} {last_name}".replace('  ', ' ').strip() or member_row.get('member_code')
        email = (member_row.get('email') or member_row.get('google_email') or '').strip().lower()

        return MemberUser(
            member_id=member_row['member_id'],
            member_code=member_row['member_code'],
            email=email,
            full_name=full_name,
            status=member_row.get('status') or 'inactive',
            profile_complete=Member.is_profile_complete(member_row),
            photo_url=photo_url,
        )

    @staticmethod
    def complete_profile(member_id, phone, student_id, startup, college_department, program, year_level):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE members
                SET phone = %s,
                    student_id = %s,
                    startup = %s,
                    college_department = %s,
                    program = %s,
                    year_level = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE member_id = %s
                """,
                (phone, student_id, startup, college_department, program, year_level, member_id),
            )
        db.commit()

    @staticmethod
    def get_pending_members():
        """Retrieve all pending member registrations awaiting admin approval."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT member_id, member_code, first_name, middle_name, last_name,
                       email, google_email, phone, student_id, startup,
                       status, created_at, updated_at
                FROM members
                WHERE status = 'pending'
                ORDER BY created_at DESC
                """
            )
            return cursor.fetchall()

    @staticmethod
    def get_active_members(limit=25):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT member_id, member_code, first_name, middle_name, last_name,
                       email, google_email, status, current_borrow_count, max_borrow_limit
                FROM members
                WHERE status = 'active'
                ORDER BY last_name ASC, first_name ASC, member_id DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            return cursor.fetchall()

    @staticmethod
    def soft_delete_member(member_id):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE members
                SET status = 'inactive',
                    updated_at = CURRENT_TIMESTAMP
                WHERE member_id = %s
                """,
                (member_id,),
            )
        db.commit()
        return cursor.rowcount > 0

    @staticmethod
    def approve_member(member_id):
        """Approve a pending member registration, changing status to active."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE members
                SET status = 'active',
                    updated_at = CURRENT_TIMESTAMP
                WHERE member_id = %s AND status = 'pending'
                """,
                (member_id,),
            )
        db.commit()
        return cursor.rowcount > 0


class MemberUser(UserMixin):
    """Represents an authenticated member account via Google OAuth."""

    def __init__(self, member_id, member_code, email, full_name, status, profile_complete, photo_url=None):
        self.id = member_id
        self.member_code = member_code
        self.email = email
        self.full_name = full_name
        self.role = 'member'
        self._status = status
        self.profile_complete = profile_complete
        self.photo_url = photo_url

    @property
    def is_active(self):
        return self._status == 'active'

    def get_id(self):
        return f"member:{self.id}"
