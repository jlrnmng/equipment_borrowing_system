import os
from datetime import datetime

from flask import Flask, request, session
from flask_login import LoginManager, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from authlib.integrations.flask_client import OAuth

from config.config import config
from app.utils.db import close_db

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

    # Blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.members import members_bp
    from app.routes.staff_admin import staff_admin_bp
    from app.routes.equipment import equipment_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(members_bp)
    app.register_blueprint(staff_admin_bp)
    app.register_blueprint(equipment_bp)

    return app


@login_manager.user_loader
def load_user(user_id):
    from app.models.staff import Staff
    return Staff.get_by_id(int(user_id))
