import json

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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, 'manual', %s, %s)
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
