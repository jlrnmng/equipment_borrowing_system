from app.utils.db import get_db


class AppSetting:
    """Helpers for reading and writing app_settings values."""

    @staticmethod
    def get(setting_key, default=None):
        db = get_db()
        try:
            with db.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT setting_value
                    FROM app_settings
                    WHERE setting_key = %s
                    LIMIT 1
                    """,
                    (setting_key,),
                )
                row = cursor.fetchone()
        except Exception:
            return default

        if not row:
            return default

        value = row.get('setting_value')
        if value is None:
            return default
        return value

    @staticmethod
    def get_int(setting_key, default=0):
        value = AppSetting.get(setting_key, default)
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def set_number(setting_key, value, updated_by=None, description=None, category='system', is_public=True):
        db = get_db()
        with db.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO app_settings (
                    setting_key,
                    setting_value,
                    setting_type,
                    category,
                    description,
                    is_public,
                    updated_by
                ) VALUES (%s, %s, 'number', %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    setting_value = VALUES(setting_value),
                    setting_type = 'number',
                    category = VALUES(category),
                    description = COALESCE(VALUES(description), description),
                    is_public = VALUES(is_public),
                    updated_by = VALUES(updated_by),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    setting_key,
                    str(int(value)),
                    category,
                    description,
                    1 if is_public else 0,
                    updated_by,
                ),
            )
        db.commit()
