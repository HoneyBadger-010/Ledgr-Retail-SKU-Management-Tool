"""
auth.py — Authentication & Access Control (Brief Part 2B)

Roles: owner / manager / salesman
Multi-store scoping: every query includes store_id filter.
Session timeout: 12h owner/manager, 8h salesman.
"""
import os
import functools
from datetime import timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash, session, jsonify
from flask_login import LoginManager, login_user, logout_user, login_required, current_user

auth_bp = Blueprint('auth', __name__)

# Demo users (in production, these would be in PostgreSQL)
DEMO_USERS = {
    'owner@sunrise.com': {
        'id': 'u-owner-001',
        'email': 'owner@sunrise.com',
        'password': 'sunrise2024',
        'role': 'owner',
        'full_name': 'Rajesh Sharma',
        'stores': ['store-pune-001', 'store-nashik-001']
    },
    'manager@sunrise.com': {
        'id': 'u-manager-001',
        'email': 'manager@sunrise.com',
        'password': 'manager2024',
        'role': 'manager',
        'full_name': 'Priya Deshmukh',
        'stores': ['store-pune-001']
    },
    'salesman@sunrise.com': {
        'id': 'u-salesman-001',
        'email': 'salesman@sunrise.com',
        'password': 'sales2024',
        'role': 'salesman',
        'full_name': 'Amit Patil',
        'stores': ['store-nashik-001']
    }
}


class DemoUser:
    """Demo user object for Flask-Login integration."""
    def __init__(self, user_data):
        self.id = user_data['id']
        self.email = user_data['email']
        self.role = user_data['role']
        self.full_name = user_data['full_name']
        self.stores = user_data['stores']

    @property
    def is_authenticated(self):
        return True
    @property
    def is_active(self):
        return True
    @property
    def is_anonymous(self):
        return False
    def get_id(self):
        return self.id

    @property
    def initials(self):
        parts = self.full_name.split()
        return ''.join(p[0].upper() for p in parts[:2])


def init_auth(app):
    """Initialize Flask-Login with the app."""
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access the dashboard.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        for email, data in DEMO_USERS.items():
            if data['id'] == user_id:
                return DemoUser(data)
        return None

    # Set session timeout
    @app.before_request
    def set_session_timeout():
        if current_user.is_authenticated:
            if current_user.role == 'salesman':
                session.permanent = True
                app.permanent_session_lifetime = timedelta(hours=8)
            else:
                session.permanent = True
                app.permanent_session_lifetime = timedelta(hours=12)


def role_required(*roles):
    """Decorator to restrict access to specific roles."""
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if current_user.role not in roles:
                flash('You do not have permission to access this page.', 'danger')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '').strip()

        user_data = DEMO_USERS.get(email)
        if user_data and user_data['password'] == password:
            user = DemoUser(user_data)
            login_user(user, remember=True)
            next_page = request.args.get('next')
            if user.role == 'salesman':
                return redirect('/mobile/')
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid email or password.', 'danger')

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('auth.login'))
