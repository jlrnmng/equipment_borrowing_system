import bcrypt
from flask_login import UserMixin
from app.utils.db import get_db


class Staff(UserMixin):
    """Represents an authenticated staff member."""

    def __init__(self, staff_id, staff_code, email, full_name, role, status):
        self.id = staff_id          # Flask-Login requires the attribute named 'id'
        self.staff_code = staff_code
        self.email = email
        self.full_name = full_name
        self.role = role
        self._status = status

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
                "SELECT staff_id, staff_code, email, full_name, role, status "
                "FROM staff WHERE staff_id = %s",
                (staff_id,),
            )
            row = cursor.fetchone()
        if row:
            return Staff(
                row['staff_id'], row['staff_code'], row['email'],
                row['full_name'], row['role'], row['status'],
            )
        return None

    @staticmethod
    def get_by_email(email):
        """Return the raw DB row for a staff member (includes password_hash)."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT staff_id, staff_code, email, password_hash, "
                "full_name, role, status "
                "FROM staff WHERE email = %s",
                (email,),
            )
            return cursor.fetchone()

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
