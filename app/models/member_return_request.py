from app.utils.db import get_db


class MemberReturnRequest:
    """Member-initiated return request persistence helpers."""

    @staticmethod
    def _ensure_tables(db):
        with db.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS member_return_requests (
                    return_request_id INT PRIMARY KEY AUTO_INCREMENT,
                    return_request_code VARCHAR(30) UNIQUE NOT NULL,
                    borrow_item_id INT NOT NULL,
                    member_id INT NOT NULL,
                    requested_condition ENUM('excellent', 'good', 'fair', 'poor') NOT NULL,
                    member_feedback TEXT NULL,
                    final_condition ENUM('excellent', 'good', 'fair', 'poor') NULL,
                    status ENUM('pending', 'approved', 'rejected', 'cancelled', 'expired') DEFAULT 'pending',
                    reviewed_by INT NULL,
                    reviewed_at TIMESTAMP NULL,
                    review_notes TEXT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_return_request_borrow_item (borrow_item_id),
                    INDEX idx_member_id (member_id),
                    INDEX idx_status (status),
                    INDEX idx_created_at (created_at),
                    FOREIGN KEY (borrow_item_id) REFERENCES borrow_items(borrow_item_id) ON DELETE CASCADE,
                    FOREIGN KEY (member_id) REFERENCES members(member_id) ON DELETE CASCADE,
                    FOREIGN KEY (reviewed_by) REFERENCES staff(staff_id) ON DELETE SET NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """
            )

    @staticmethod
    def _generate_request_code(db):
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT return_request_code
                FROM member_return_requests
                ORDER BY return_request_id DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()

        if row and str(row.get('return_request_code', '')).startswith('RET'):
            number = int(str(row['return_request_code'])[3:]) + 1
        else:
            number = 1
        return f"RET{number:05d}"

    @staticmethod
    def get_member_active_items(member_id):
        db = get_db()
        MemberReturnRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT bi.borrow_item_id, bi.borrow_id, bi.condition_borrowed,
                       br.transaction_code, br.expected_return_date, br.status AS borrow_status,
                       e.equipment_id, e.equipment_code, e.equipment_name, e.category,
                       rr.return_request_id, rr.return_request_code, rr.status AS return_request_status,
                       rr.requested_condition, rr.member_feedback, rr.created_at AS request_created_at,
                       rr.review_notes
                FROM borrow_items bi
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
                LEFT JOIN member_return_requests rr ON rr.borrow_item_id = bi.borrow_item_id
                WHERE br.member_id = %s
                  AND br.status IN ('active', 'overdue')
                  AND bi.returned_at IS NULL
                ORDER BY br.borrow_date ASC, bi.borrow_item_id ASC
                """,
                (member_id,),
            )
            return cursor.fetchall()

    @staticmethod
    def get_member_return_requests(member_id, limit=30):
        db = get_db()
        MemberReturnRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT rr.return_request_id, rr.return_request_code,
                       rr.requested_condition, rr.member_feedback,
                       rr.final_condition, rr.status, rr.review_notes,
                       rr.created_at, rr.updated_at,
                       br.transaction_code, br.expected_return_date,
                       e.equipment_code, e.equipment_name, e.category
                FROM member_return_requests rr
                INNER JOIN borrow_items bi ON bi.borrow_item_id = rr.borrow_item_id
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
                WHERE rr.member_id = %s
                ORDER BY rr.created_at DESC
                LIMIT %s
                """,
                (member_id, int(limit)),
            )
            return cursor.fetchall()

    @staticmethod
    def get_pending_requests(limit=20):
        db = get_db()
        MemberReturnRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT rr.return_request_id, rr.return_request_code,
                       rr.requested_condition, rr.member_feedback,
                       rr.status, rr.created_at,
                       m.member_id, m.member_code, m.first_name, m.last_name,
                       br.transaction_code, br.expected_return_date,
                       e.equipment_code, e.equipment_name, e.category,
                       bi.borrow_item_id, bi.condition_borrowed
                FROM member_return_requests rr
                INNER JOIN members m ON m.member_id = rr.member_id
                INNER JOIN borrow_items bi ON bi.borrow_item_id = rr.borrow_item_id
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
                WHERE rr.status = 'pending'
                  AND bi.returned_at IS NULL
                ORDER BY rr.created_at ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            return cursor.fetchall()

    @staticmethod
    def get_request_detail(return_request_id):
        db = get_db()
        MemberReturnRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT rr.return_request_id, rr.return_request_code,
                       rr.borrow_item_id, rr.member_id,
                       rr.requested_condition, rr.member_feedback,
                       rr.final_condition, rr.status, rr.review_notes,
                       rr.created_at, rr.updated_at,
                       m.member_code, m.first_name, m.middle_name, m.last_name,
                       m.email, m.google_email, m.status AS member_status,
                       bi.condition_borrowed, bi.returned_at,
                       br.borrow_id, br.transaction_code, br.expected_return_date, br.status AS borrow_status,
                       e.equipment_id, e.equipment_code, e.equipment_name, e.category
                FROM member_return_requests rr
                INNER JOIN members m ON m.member_id = rr.member_id
                INNER JOIN borrow_items bi ON bi.borrow_item_id = rr.borrow_item_id
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                INNER JOIN equipment e ON e.equipment_id = bi.equipment_id
                WHERE rr.return_request_id = %s
                LIMIT 1
                """,
                (return_request_id,),
            )
            return cursor.fetchone()

    @staticmethod
    def create_or_resubmit_request(member_id, borrow_item_id, requested_condition, member_feedback):
        db = get_db()
        MemberReturnRequest._ensure_tables(db)

        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT rr.return_request_id, rr.return_request_code, rr.status,
                       br.member_id, br.status AS borrow_status,
                       bi.returned_at
                FROM borrow_items bi
                INNER JOIN borrow_records br ON br.borrow_id = bi.borrow_id
                LEFT JOIN member_return_requests rr ON rr.borrow_item_id = bi.borrow_item_id
                WHERE bi.borrow_item_id = %s
                LIMIT 1
                """,
                (borrow_item_id,),
            )
            row = cursor.fetchone()

            if not row or int(row.get('member_id') or 0) != int(member_id):
                return {'ok': False, 'message': 'Borrow item not found for this member.'}

            if row.get('returned_at') is not None:
                return {'ok': False, 'message': 'This item is already returned.'}

            if (row.get('borrow_status') or '').lower() not in ('active', 'overdue'):
                return {'ok': False, 'message': 'Borrow transaction is not eligible for return request.'}

            existing_id = row.get('return_request_id')
            existing_status = (row.get('status') or '').lower()

            if existing_id and existing_status in ('pending', 'approved'):
                return {'ok': False, 'message': 'A return request already exists for this item.'}

            if existing_id and existing_status in ('rejected', 'cancelled', 'expired'):
                cursor.execute(
                    """
                    UPDATE member_return_requests
                    SET requested_condition = %s,
                        member_feedback = %s,
                        final_condition = NULL,
                        status = 'pending',
                        reviewed_by = NULL,
                        reviewed_at = NULL,
                        review_notes = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE return_request_id = %s
                    """,
                    (requested_condition, member_feedback, existing_id),
                )
                db.commit()
                return {
                    'ok': True,
                    'return_request_id': existing_id,
                    'return_request_code': row.get('return_request_code'),
                }

            return_request_code = MemberReturnRequest._generate_request_code(db)
            cursor.execute(
                """
                INSERT INTO member_return_requests (
                    return_request_code,
                    borrow_item_id,
                    member_id,
                    requested_condition,
                    member_feedback,
                    status
                ) VALUES (%s, %s, %s, %s, %s, 'pending')
                """,
                (return_request_code, borrow_item_id, member_id, requested_condition, member_feedback),
            )
            new_id = cursor.lastrowid

        db.commit()
        return {'ok': True, 'return_request_id': new_id, 'return_request_code': return_request_code}
