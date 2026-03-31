import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ('1', 'true', 'yes', 'on')


class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'change-this-in-production')
    MYSQL_HOST = os.getenv('DB_HOST', 'localhost')
    MYSQL_PORT = int(os.getenv('DB_PORT', 3306))
    MYSQL_DB = os.getenv('DB_NAME', 'equipment_borrowing')
    MYSQL_USER = os.getenv('DB_USER', 'root')
    MYSQL_PASSWORD = os.getenv('DB_PASSWORD', '')

    # Google OAuth (read from environment only)
    GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
    GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI')
    GOOGLE_ALLOWED_DOMAIN = os.getenv('GOOGLE_ALLOWED_DOMAIN', 'my.cspc.edu.ph')

    # Mail settings for notification pipeline
    MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
    MAIL_USE_TLS = _env_bool('MAIL_USE_TLS', True)
    MAIL_USE_SSL = _env_bool('MAIL_USE_SSL', False)
    MAIL_USERNAME = os.getenv('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', MAIL_USERNAME or 'no-reply@localhost')
    MAIL_NOTIFICATIONS_ENABLED = _env_bool('MAIL_NOTIFICATIONS_ENABLED', True)

    #reminder automation settings.
    REMINDER_AUTOMATION_ENABLED = _env_bool('REMINDER_AUTOMATION_ENABLED', True)
    REMINDER_JOB_INTERVAL_MINUTES = int(os.getenv('REMINDER_JOB_INTERVAL_MINUTES', 15))
    REMINDER_PROCESS_PENDING_ON_RUN = _env_bool('REMINDER_PROCESS_PENDING_ON_RUN', True)
    REQUEST_EXPIRY_MINUTES = int(os.getenv('REQUEST_EXPIRY_MINUTES', 30))

    WTF_CSRF_ENABLED = True
    # Session expires after 30 minutes of inactivity
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
