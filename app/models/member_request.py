from app.utils.db import get_db


class MemberBorrowRequest:
    """Self-service member borrow request persistence helpers."""

    @staticmethod
    def _ensure_tables(db):
        with db.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS member_borrow_requests (
                    request_id INT PRIMARY KEY AUTO_INCREMENT,
                    request_code VARCHAR(30) UNIQUE NOT NULL,
                    member_id INT NOT NULL,
                    expected_return_date DATE NOT NULL,
                    usage_area VARCHAR(120) NOT NULL,
                    notes TEXT NULL,
                    status ENUM('pending', 'approved', 'rejected', 'cancelled', 'expired') DEFAULT 'pending',
                    reviewed_by INT NULL,
                    reviewed_at TIMESTAMP NULL,
                    review_notes TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_member_id (member_id),
                    INDEX idx_status (status),
                    INDEX idx_created_at (created_at),
                    FOREIGN KEY (member_id) REFERENCES members(member_id) ON DELETE CASCADE,
                    FOREIGN KEY (reviewed_by) REFERENCES staff(staff_id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS member_borrow_request_items (
                    request_item_id INT PRIMARY KEY AUTO_INCREMENT,
                    request_id INT NOT NULL,
                    equipment_id INT NOT NULL,
                    condition_requested ENUM('excellent', 'good', 'fair', 'poor') DEFAULT 'good',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_request_equipment (request_id, equipment_id),
                    INDEX idx_request_id (request_id),
                    INDEX idx_equipment_id (equipment_id),
                    FOREIGN KEY (request_id) REFERENCES member_borrow_requests(request_id) ON DELETE CASCADE,
                    FOREIGN KEY (equipment_id) REFERENCES equipment(equipment_id) ON DELETE CASCADE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )

    @staticmethod
    def _generate_request_code(db):
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT request_code
                FROM member_borrow_requests
                ORDER BY request_id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()

        if row and str(row.get('request_code', '')).startswith('REQ'):
            number = int(str(row['request_code'])[3:]) + 1
        else:
            number = 1
        return f"REQ{number:05d}"

    @staticmethod
    def create_request(member_id, expected_return_date, usage_area, notes, items):
        db = get_db()
        MemberBorrowRequest._ensure_tables(db)

        request_code = MemberBorrowRequest._generate_request_code(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO member_borrow_requests (
                    request_code, member_id, expected_return_date, usage_area, notes, status
                ) VALUES (%s, %s, %s, %s, %s, 'pending')
                """,
                (request_code, member_id, expected_return_date, usage_area, notes),
            )
            request_id = cursor.lastrowid

            for item in items:
                cursor.execute(
                    """
                    INSERT INTO member_borrow_request_items (
                        request_id, equipment_id, condition_requested
                    ) VALUES (%s, %s, %s)
                    """,
                    (request_id, item['equipment_id'], item.get('condition_requested', 'good')),
                )

        db.commit()
        return {'request_id': request_id, 'request_code': request_code}

    @staticmethod
    def get_member_requests(member_id, limit=30):
        db = get_db()
        MemberBorrowRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.request_id, r.request_code, r.expected_return_date,
                       r.usage_area, r.notes, r.status, r.review_notes,
                       r.created_at, r.updated_at,
                       GROUP_CONCAT(e.equipment_code ORDER BY e.equipment_code SEPARATOR ', ') AS equipment_codes,
                       GROUP_CONCAT(e.equipment_name ORDER BY e.equipment_name SEPARATOR ', ') AS equipment_names,
                      GROUP_CONCAT(COALESCE(NULLIF(e.serial_number, ''), e.inventory_number) ORDER BY e.equipment_name SEPARATOR ', ') AS equipment_serials,
                       COUNT(ri.request_item_id) AS total_items
                FROM member_borrow_requests r
                LEFT JOIN member_borrow_request_items ri ON ri.request_id = r.request_id
                LEFT JOIN equipment e ON e.equipment_id = ri.equipment_id
                WHERE r.member_id = %s
                GROUP BY r.request_id, r.request_code, r.expected_return_date,
                         r.usage_area, r.notes, r.status, r.review_notes,
                         r.created_at, r.updated_at
                ORDER BY r.created_at DESC
                LIMIT %s
                """,
                (member_id, int(limit)),
            )
            return cursor.fetchall()

    @staticmethod
    def get_pending_requests(limit=20):
        db = get_db()
        MemberBorrowRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.request_id, r.request_code, r.expected_return_date,
                       r.usage_area, r.notes, r.status, r.created_at,
                       m.member_id, m.member_code, m.first_name, m.last_name,
                       COUNT(ri.request_item_id) AS total_items,
                      GROUP_CONCAT(e.equipment_name ORDER BY e.equipment_name SEPARATOR ', ') AS equipment_names,
                      GROUP_CONCAT(COALESCE(NULLIF(e.serial_number, ''), e.inventory_number) ORDER BY e.equipment_name SEPARATOR ', ') AS equipment_serials,
                      GROUP_CONCAT(e.equipment_code ORDER BY e.equipment_name SEPARATOR ', ') AS equipment_codes
                FROM member_borrow_requests r
                INNER JOIN members m ON m.member_id = r.member_id
                LEFT JOIN member_borrow_request_items ri ON ri.request_id = r.request_id
                LEFT JOIN equipment e ON e.equipment_id = ri.equipment_id
                WHERE r.status = 'pending'
                GROUP BY r.request_id, r.request_code, r.expected_return_date,
                         r.usage_area, r.notes, r.status, r.created_at,
                         m.member_id, m.member_code, m.first_name, m.last_name
                ORDER BY r.created_at ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            return cursor.fetchall()

    @staticmethod
    def get_request_detail(request_id):
        db = get_db()
        MemberBorrowRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.request_id, r.request_code, r.member_id, r.expected_return_date,
                       r.usage_area, r.notes, r.status, r.created_at,
                       m.member_code, m.first_name, m.middle_name, m.last_name,
                       m.email, m.google_email, m.startup, m.status AS member_status,
                       m.current_borrow_count, m.max_borrow_limit
                FROM member_borrow_requests r
                INNER JOIN members m ON m.member_id = r.member_id
                WHERE r.request_id = %s
                LIMIT 1
                """,
                (request_id,),
            )
            request_row = cursor.fetchone()

            if not request_row:
                return None, []

            cursor.execute(
                """
                SELECT ri.request_item_id, ri.equipment_id, ri.condition_requested,
                       e.equipment_code, e.equipment_name, e.category, e.status,
                       e.requires_supervision, e.restricted_areas
                FROM member_borrow_request_items ri
                INNER JOIN equipment e ON e.equipment_id = ri.equipment_id
                WHERE ri.request_id = %s
                ORDER BY ri.request_item_id ASC
                """,
                (request_id,),
            )
            items = cursor.fetchall()

        return request_row, items
