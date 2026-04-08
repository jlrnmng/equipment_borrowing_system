from datetime import datetime

from flask_login import current_user
from flask_socketio import SocketIO, join_room

socketio = SocketIO()


def init_realtime(app):
    socketio.init_app(
        app,
        cors_allowed_origins=app.config.get('SOCKETIO_CORS_ALLOWED_ORIGINS', '*'),
        message_queue=app.config.get('SOCKETIO_MESSAGE_QUEUE') or None,
    )


@socketio.on('connect')
def handle_connect():
    if not current_user.is_authenticated:
        return False

    role = getattr(current_user, 'role', None)
    user_id = getattr(current_user, 'id', None)

    join_room('authenticated')

    if role in ('admin', 'staff'):
        join_room('staff')

    if role == 'member' and user_id is not None:
        join_room(f'member:{user_id}')


def emit_app_data_changed(reason, member_id=None, include_staff=True, include_members=False):
    """Emit a lightweight signal that tells connected clients to refresh relevant pages."""
    payload = {
        'reason': reason,
        'member_id': member_id,
        'ts': datetime.utcnow().isoformat(),
    }

    if include_staff:
        socketio.emit('app_data_changed', payload, room='staff')

    if include_members:
        if member_id is not None:
            socketio.emit('app_data_changed', payload, room=f'member:{member_id}')
        else:
            socketio.emit('app_data_changed', payload, room='authenticated')
