"""
scripts/create_admin.py
-----------------------
One-time utility to create the initial admin staff account.

Usage (from project root, with venv active):
    python scripts/create_admin.py
"""

import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from dotenv import load_dotenv
load_dotenv()

import pymysql
import pymysql.cursors
import bcrypt
import secrets
import string

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'equipment_borrowing'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
}


def generate_staff_code(cursor):
    """Return a unique STAFF001-style code."""
    cursor.execute("SELECT staff_code FROM staff ORDER BY staff_id DESC LIMIT 1")
    row = cursor.fetchone()
    if row:
        num = int(row['staff_code'].replace('STAFF', '')) + 1
    else:
        num = 1
    return f'STAFF{num:03d}'


def main():
    print("=== Create Admin Staff Account ===\n")

    full_name = input("Full name: ").strip()
    email = input("Email address: ").strip().lower()
    password = input("Password (min 8 chars): ").strip()

    if not full_name or not email or len(password) < 8:
        print("\nERROR: All fields required; password must be at least 8 characters.")
        sys.exit(1)

    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    try:
        conn = pymysql.connect(**DB_CONFIG)
        with conn.cursor() as cursor:
            # Check duplicate email
            cursor.execute("SELECT staff_id FROM staff WHERE email = %s", (email,))
            if cursor.fetchone():
                print(f"\nERROR: A staff account with email '{email}' already exists.")
                sys.exit(1)

            staff_code = generate_staff_code(cursor)
            cursor.execute(
                """INSERT INTO staff (staff_code, email, password_hash, full_name, role, status)
                   VALUES (%s, %s, %s, %s, 'admin', 'active')""",
                (staff_code, email, password_hash, full_name),
            )
        conn.commit()
        conn.close()

        print(f"\n✓ Admin account created successfully!")
        print(f"  Staff Code : {staff_code}")
        print(f"  Name       : {full_name}")
        print(f"  Email      : {email}")
        print(f"  Role       : admin")

    except pymysql.Error as e:
        print(f"\nDatabase error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
