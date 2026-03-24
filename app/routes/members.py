from flask import Blueprint, redirect, url_for

members_bp = Blueprint('members', __name__)


@members_bp.route('/members/register', methods=['GET', 'POST'])
def register_member():
    return redirect(url_for('auth.signup'))
