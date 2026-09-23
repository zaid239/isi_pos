"""
Flask Extensions Initialization.
All extensions are created here and initialized within create_app().
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# SQLAlchemy ORM instance
db = SQLAlchemy()

# Alembic database migration instance
migrate = Migrate()

# Flask-Login authentication manager
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"

# CSRF Protection across all forms
csrf = CSRFProtect()

# Rate limiting for auth and sensitive actions
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["1200 per day", "300 per hour"],
    storage_uri="memory://",
)
