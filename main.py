import os

from app import create_app
from app.realtime import socketio

app = create_app()

if __name__ == '__main__':
    debug_mode = os.getenv('FLASK_DEBUG', '0').strip().lower() in ('1', 'true', 'yes', 'on')
    socketio.run(app, debug=debug_mode)
