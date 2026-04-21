import bcrypt
from flask_login import UserMixin
from app.utils.db import get_db


class Staff(UserMixin):
    """Represents an authenticated staff member."""

    def __init__(self, staff_id, staff_code, email, full_name, role, status, photo_url=None):
        self.id = staff_id          # Flask-Login requires the attribute named 'id'
        self.staff_code = staff_code
        self.email = email
        self.full_name = full_name
        self.role = role
        self._status = status
        self.photo_url = photo_url

    # ------------------------------------------------------------------
    # Flask-Login required override – inactive staff cannot log in
    # ------------------------------------------------------------------
    @property
    def is_active(self):
        return self._status == 'active'

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @staticmethod
    def get_by_id(staff_id):
        """Load a Staff object by primary key (used by Flask-Login user_loader)."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT staff_id, staff_code, email, full_name, role, status, profile_picture_url "
                "FROM staff WHERE staff_id = %s",
                (staff_id,),
            )
            row = cursor.fetchone()
        if row:
            return Staff(
                row['staff_id'], row['staff_code'], row['email'],
                row['full_name'], row['role'], row['status'],
                photo_url=row.get('profile_picture_url'),
            )
        return None

    @staticmethod
    def get_by_email(email):
        """Return the raw DB row for a staff member (includes password_hash)."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT staff_id, staff_code, email, password_hash, "
                "full_name, role, status, google_email, google_sub, profile_picture_url "
                "FROM staff WHERE email = %s OR google_email = %s LIMIT 1",
                (email, email),
            )
            return cursor.fetchone()

    @staticmethod
    def is_first_admin(row):
        """Return True when the row represents the earliest admin account."""
        if not row:
            return False
        if row.get('role') != 'admin':
            return False

        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM staff WHERE role = 'admin' AND staff_id < %s",
                (row.get('staff_id'),),
            )
            result = cursor.fetchone()
        return (result or {}).get('cnt', 0) == 0

    @staticmethod
    def update_google_identity(staff_id, google_email, google_sub, profile_picture_url=None):
        """Link a staff account to Google identity on first OAuth login."""
        db = get_db()
        with db.cursor() as cursor:
            if profile_picture_url:
                cursor.execute(
                    "UPDATE staff SET google_email = %s, google_sub = %s, profile_picture_url = %s WHERE staff_id = %s",
                    (google_email, google_sub, profile_picture_url, staff_id),
                )
            else:
                cursor.execute(
                    "UPDATE staff SET google_email = %s, google_sub = %s WHERE staff_id = %s",
                    (google_email, google_sub, staff_id),
                )
        db.commit()

    @staticmethod
    def touch_last_login(staff_id):
        """Update staff last_login timestamp."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE staff SET last_login = NOW() WHERE staff_id = %s",
                (staff_id,),
            )
        db.commit()

    @staticmethod
    def get_next_staff_code():
        """Return next STAFF### code."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute("SELECT staff_code FROM staff ORDER BY staff_id DESC LIMIT 1")
            row = cursor.fetchone()
        if row and row.get('staff_code', '').startswith('STAFF'):
            next_num = int(row['staff_code'][5:]) + 1
        else:
            next_num = 1
        return f"STAFF{next_num:03d}"

    @staticmethod
    def create_google_only_staff(full_name, email, role):
        """Create staff account pre-registered for Google OAuth-only sign in."""
        db = get_db()
        staff_code = Staff.get_next_staff_code()
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO staff (
                    staff_code, email, password_hash, full_name, role, status, google_email
                ) VALUES (%s, %s, NULL, %s, %s, 'active', %s)
                """,
                (staff_code, email, full_name, role, email),
            )
            staff_id = cursor.lastrowid
        db.commit()
        return {
            'staff_id': staff_id,
            'staff_code': staff_code,
        }

    # ------------------------------------------------------------------
    # Password helpers
    # ------------------------------------------------------------------
    @staticmethod
    def hash_password(plain_text):
        """Return a bcrypt hash for *plain_text*."""
        return bcrypt.hashpw(plain_text.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    @staticmethod
    def check_password(password_hash, plain_text):
        """Return True if *plain_text* matches the stored *password_hash*."""
        if not password_hash:
            return False
        return bcrypt.checkpw(plain_text.encode('utf-8'), password_hash.encode('utf-8'))
