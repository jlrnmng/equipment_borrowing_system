import hashlib

from app.utils.db import get_db


class Equipment:
    """Equipment data helpers for management and borrowing."""

    @staticmethod
    def _normalized_status_expression(column='status'):
        """SQL expression that normalizes legacy status values for consistent filtering/counting."""
        return (
            "CASE "
            f"WHEN LOWER(TRIM(COALESCE({column}, ''))) IN ('', 'active') THEN 'available' "
            f"ELSE LOWER(TRIM({column})) "
            "END"
        )

    @staticmethod
    def _normalize_status_value(status):
        """Normalize status values returned from DB for template consistency."""
        value = (status or '').strip().lower()
        if value in ('', 'active'):
            return 'available'
        return value

    @staticmethod
    def _normalize_status_rows(rows):
        """Mutate fetched rows in-place to normalize status value."""
        for row in rows:
            if 'status' in row:
                row['status'] = Equipment._normalize_status_value(row.get('status'))
        return rows

    @staticmethod
    def generate_equipment_code(inventory_number):
        """Create a compact deterministic code from the inventory number."""
        digest = hashlib.sha1(inventory_number.encode('utf-8')).hexdigest()[:10].upper()
        return f"EQ-{digest}"

    @staticmethod
    def get_next_inventory_number():
        """Generate the next equipment inventory number (EQP001, EQP002, etc.)."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT inventory_number
                FROM equipment
                WHERE inventory_number REGEXP '^EQP[0-9]+$'
                ORDER BY CAST(SUBSTRING(inventory_number, 4) AS UNSIGNED) DESC
                LIMIT 1
                """
            )
            row = cursor.fetchone()
        
        if row and row.get('inventory_number', '').startswith('EQP'):
            next_num = int(row['inventory_number'][3:]) + 1
        else:
            next_num = 1
        return f"EQP{next_num:03d}"

    @staticmethod
    def create_equipment(
        equipment_name,
        category,
        inventory_number,
        brand=None,
        serial_number=None,
        property_stock_number=None,
        status='available',
        condition_status='good',
        location=None,
        requires_supervision=False,
        restricted_areas=None,
        notes=None,
        added_by=None,
        equipment_image_path=None,
    ):
        """Create a new equipment entry."""
        db = get_db()
        equipment_code = Equipment.generate_equipment_code(inventory_number)
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO equipment 
                (equipment_code, equipment_name, category, inventory_number, brand,
                 serial_number, property_stock_number, qr_code_path, equipment_image_path, status, condition_status,
                 location, requires_supervision, restricted_areas, notes, added_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    equipment_code,
                    equipment_name,
                    category,
                    inventory_number,
                    brand,
                    serial_number,
                    property_stock_number,
                    equipment_image_path,
                    Equipment._normalize_status_value(status),
                    condition_status,
                    location,
                    requires_supervision,
                    restricted_areas,
                    notes,
                    added_by,
                ),
            )
            db.commit()
            equipment_id = cursor.lastrowid
        
        return Equipment.get_by_id(equipment_id)

    @staticmethod
    def update_equipment(
        equipment_id,
        equipment_name,
        category,
        inventory_number,
        brand=None,
        serial_number=None,
        property_stock_number=None,
        status='available',
        condition_status='good',
        location=None,
        requires_supervision=False,
        restricted_areas=None,
        notes=None,
        equipment_image_path=None,
    ):
        """Update editable equipment fields."""
        db = get_db()
        equipment_code = Equipment.generate_equipment_code(inventory_number)
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE equipment
                SET equipment_code = %s,
                    equipment_name = %s,
                    category = %s,
                    inventory_number = %s,
                    brand = %s,
                    serial_number = %s,
                    property_stock_number = %s,
                    status = %s,
                    condition_status = %s,
                    location = %s,
                    requires_supervision = %s,
                    restricted_areas = %s,
                    notes = %s,
                    equipment_image_path = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE equipment_id = %s
                """,
                (
                    equipment_code,
                    equipment_name,
                    category,
                    inventory_number,
                    brand,
                    serial_number,
                    property_stock_number,
                    Equipment._normalize_status_value(status),
                    condition_status,
                    location,
                    requires_supervision,
                    restricted_areas,
                    notes,
                    equipment_image_path,
                    equipment_id,
                ),
            )
            db.commit()
        return Equipment.get_by_id(equipment_id)

    @staticmethod
    def get_by_id(equipment_id):
        """Get equipment by ID."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM equipment WHERE equipment_id = %s",
                (equipment_id,),
            )
            row = cursor.fetchone()
        if row and 'status' in row:
            row['status'] = Equipment._normalize_status_value(row.get('status'))
        return row

    @staticmethod
    def get_by_inventory_number(inventory_number):
        """Get equipment by inventory number."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM equipment WHERE inventory_number = %s",
                (inventory_number,),
            )
            row = cursor.fetchone()
        if row and 'status' in row:
            row['status'] = Equipment._normalize_status_value(row.get('status'))
        return row

    @staticmethod
    def find_duplicate_fields(inventory_number=None, serial_number=None, property_stock_number=None):
        """Return duplicate matches for unique-like fields using exact value checks."""
        checks = []
        if inventory_number:
            checks.append(('inventory_number', inventory_number))
        if serial_number:
            checks.append(('serial_number', serial_number))
        if property_stock_number:
            checks.append(('property_stock_number', property_stock_number))

        if not checks:
            return []

        db = get_db()
        duplicates = []
        with db.cursor() as cursor:
            for field_name, field_value in checks:
                cursor.execute(
                    f"SELECT equipment_id FROM equipment WHERE {field_name} = %s LIMIT 1",
                    (field_value,),
                )
                row = cursor.fetchone()
                if row:
                    duplicates.append(
                        {
                            'field': field_name,
                            'value': field_value,
                            'equipment_id': row.get('equipment_id'),
                        }
                    )
        return duplicates

    @staticmethod
    def get_by_code_or_inventory(identifier):
        """Get one equipment row by equipment_code or inventory_number."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM equipment
                WHERE equipment_code = %s OR inventory_number = %s
                LIMIT 1
                """,
                (identifier, identifier),
            )
            row = cursor.fetchone()
        if row and 'status' in row:
            row['status'] = Equipment._normalize_status_value(row.get('status'))
        return row

    @staticmethod
    def update_qr_path(equipment_id, qr_path):
        """Persist generated equipment QR image path."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                UPDATE equipment
                SET qr_code_path = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE equipment_id = %s
                """,
                (qr_path, equipment_id),
            )
        db.commit()

    @staticmethod
    def get_all(status=None, location=None, search=None):
        """Get all equipment with optional filters."""
        db = get_db()
        with db.cursor() as cursor:
            query = "SELECT * FROM equipment WHERE 1=1"
            params = []

            if status:
                normalized_status = Equipment._normalize_status_value(status)
                status_expr = Equipment._normalized_status_expression('status')
                query += f" AND {status_expr} = %s"
                params.append(normalized_status)

            if location:
                query += " AND location = %s"
                params.append(location)

            if search:
                query += (
                    " AND (equipment_code LIKE %s OR equipment_name LIKE %s OR category LIKE %s "
                    "OR inventory_number LIKE %s OR property_stock_number LIKE %s OR serial_number LIKE %s)"
                )
                search_term = f"%{search}%"
                params.extend([
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                    search_term,
                ])

            query += " ORDER BY equipment_name ASC, equipment_id DESC"
            cursor.execute(query, params)
            rows = cursor.fetchall()
        return Equipment._normalize_status_rows(rows)

    @staticmethod
    def get_by_status(status):
        """Get all equipment with a specific status."""
        db = get_db()
        with db.cursor() as cursor:
            normalized_status = Equipment._normalize_status_value(status)
            status_expr = Equipment._normalized_status_expression('status')
            cursor.execute(
                f"SELECT * FROM equipment WHERE {status_expr} = %s ORDER BY equipment_name ASC, equipment_id DESC",
                (normalized_status,),
            )
            rows = cursor.fetchall()
        return Equipment._normalize_status_rows(rows)

    @staticmethod
    def update_status(equipment_id, status):
        """Update equipment status."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE equipment SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE equipment_id = %s",
                (status, equipment_id),
            )
            db.commit()
        return Equipment.get_by_id(equipment_id)

    @staticmethod
    def update_condition(equipment_id, condition_status):
        """Update equipment condition status."""
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                "UPDATE equipment SET condition_status = %s, updated_at = CURRENT_TIMESTAMP WHERE equipment_id = %s",
                (condition_status, equipment_id),
            )
            db.commit()
        return Equipment.get_by_id(equipment_id)

    @staticmethod
    def count_by_status(status):
        """Count equipment with a specific status."""
        db = get_db()
        with db.cursor() as cursor:
            normalized_status = Equipment._normalize_status_value(status)
            status_expr = Equipment._normalized_status_expression('status')
            cursor.execute(
                f"SELECT COUNT(*) as count FROM equipment WHERE {status_expr} = %s",
                (normalized_status,),
            )
            row = cursor.fetchone()
        return row['count'] if row else 0

    @staticmethod
    def get_statistics():
        """Get equipment statistics."""
        db = get_db()
        with db.cursor() as cursor:
            status_expr = Equipment._normalized_status_expression('status')
            cursor.execute(
                f"""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN {status_expr} = 'available' THEN 1 ELSE 0 END) as available,
                    SUM(CASE WHEN {status_expr} = 'borrowed' THEN 1 ELSE 0 END) as borrowed,
                    SUM(CASE WHEN {status_expr} = 'maintenance' THEN 1 ELSE 0 END) as maintenance,
                    SUM(CASE WHEN {status_expr} = 'retired' THEN 1 ELSE 0 END) as retired
                FROM equipment
                """
            )
            return cursor.fetchone()
