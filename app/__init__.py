import os
from datetime import datetime

from flask import Flask, request, session
from flask_login import LoginManager, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from authlib.integrations.flask_client import OAuth

from config.config import config
from app.utils.db import close_db, get_db
from app.utils.reminders import maybe_start_scheduler
from app.realtime import init_realtime

login_manager = LoginManager()
csrf = CSRFProtect()
oauth = OAuth()


def create_app(config_name=None):
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')

    app = Flask(__name__)
    app.config.from_object(config.get(config_name, config['default']))

    # Extensions
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    login_manager.login_message_category = 'warning'

    csrf.init_app(app)
    oauth.init_app(app)
    init_realtime(app)

    if app.config.get('GOOGLE_CLIENT_ID') and app.config.get('GOOGLE_CLIENT_SECRET'):
        oauth.register(
            name='google',
            client_id=app.config['GOOGLE_CLIENT_ID'],
            client_secret=app.config['GOOGLE_CLIENT_SECRET'],
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'},
        )

    # Tear-down DB connection after each request
    app.teardown_appcontext(close_db)

    # Prevent browser from caching auth and HTML pages to avoid stale back/forward history.
    @app.after_request
    def set_no_cache(response):
        endpoint = request.endpoint or ''
        is_auth_endpoint = endpoint.startswith('auth.')
        is_html_response = response.mimetype == 'text/html'

        if current_user.is_authenticated or is_auth_endpoint or is_html_response:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response

    # Session timeout enforcement
    @app.before_request
    def enforce_session_timeout():
        if current_user.is_authenticated:
            session.permanent = True
            last_active = session.get('_last_active')
            timeout = app.config['PERMANENT_SESSION_LIFETIME']
            if last_active and (datetime.utcnow() - datetime.fromisoformat(last_active)) > timeout:
                logout_user()
                session.clear()
                from flask import redirect, url_for, flash
                flash('Your session expired. Please log in again.', 'warning')
                return redirect(url_for('auth.login'))
            session['_last_active'] = datetime.utcnow().isoformat()

    @app.context_processor
    def inject_notification_counts():
        try:
            if not current_user.is_authenticated:
                return {'unread_count': 0}

            role = getattr(current_user, 'role', None)
            session_key = f'notifications_last_seen_{role or "user"}'
            last_seen_raw = session.get(session_key)
            last_seen_at = None
            if last_seen_raw:
                try:
                    last_seen_at = datetime.fromisoformat(last_seen_raw)
                except ValueError:
                    last_seen_at = None

            db = get_db()

            with db.cursor() as cursor:
                if role == 'member':
                    if last_seen_at:
                        cursor.execute(
                            """
                            SELECT COUNT(*) AS cnt
                            FROM notifications
                            WHERE member_id = %s
                              AND created_at > %s
                            """,
                            (current_user.id, last_seen_at),
                        )
                    else:
                        cursor.execute(
                            """
                            SELECT COUNT(*) AS cnt
                            FROM notifications
                            WHERE member_id = %s
                              AND created_at >= (NOW() - INTERVAL 1 DAY)
                            """,
                            (current_user.id,),
                        )
                else:
                    if last_seen_at:
                        cursor.execute(
                            """
                            SELECT COUNT(*) AS cnt
                            FROM notifications
                            WHERE created_at > %s
                            """,
                            (last_seen_at,),
                        )
                    else:
                        cursor.execute(
                            """
                            SELECT COUNT(*) AS cnt
                            FROM notifications
                            WHERE status = 'pending'
                            """
                        )

                row = cursor.fetchone() or {}

            return {'unread_count': int(row.get('cnt') or 0)}
        except Exception:
            app.logger.exception('Failed to inject notification counts; using fallback unread_count=0')
            return {'unread_count': 0}

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.members import members_bp
    from app.routes.staff_admin import staff_admin_bp
    from app.routes.equipment import equipment_bp
    from app.routes.borrow import borrow_bp
    from app.routes.reports import reports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(staff_admin_bp)
    app.register_blueprint(equipment_bp)
    app.register_blueprint(borrow_bp)
    app.register_blueprint(reports_bp)

    # Day 6 afternoon: background reminder automation scheduler.
    maybe_start_scheduler(app)

    return app


@login_manager.user_loader
def load_user(user_id):
    from app.models.member import Member
    from app.models.staff import Staff

    user_id = str(user_id)
    if user_id.startswith('member:'):
        member_id = int(user_id.split(':', 1)[1])
        return Member.to_member_user(Member.get_auth_by_member_id(member_id))

    return Staff.get_by_id(int(user_id))
