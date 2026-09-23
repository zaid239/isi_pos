"""
Authentication and User Management Models.
Includes Role, User, and LoginHistory.
Complies with Oracle 19c reserved word constraints (table 'users', not 'user').
"""
from datetime import timedelta, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener
from app.utils.timezone import utc_now


@register_outbox_listener
class Role(SyncMixin, db.Model):
    __tablename__ = "roles"

    role_name = db.Column(db.String(50), nullable=False)
    role_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    is_system = db.Column(db.SmallInteger, nullable=False, default=1)  # 1 = system role, cannot be deleted

    users = db.relationship("User", back_populates="role", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("is_system IN (0, 1)", name="ck_roles_is_system"),
    )

    def __repr__(self):
        return f"<Role {self.role_code}>"


@register_outbox_listener
class User(SyncMixin, UserMixin, db.Model):
    __tablename__ = "users"

    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=True, index=True)
    phone_no = db.Column(db.String(20), nullable=True)

    role_id = db.Column(db.BigInteger, db.ForeignKey("roles.id"), nullable=False)
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)
    must_change_pwd = db.Column(db.SmallInteger, nullable=False, default=0)

    # Account Lockout and Security
    failed_attempts = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)

    role = db.relationship("Role", back_populates="users")
    login_history = db.relationship("LoginHistory", back_populates="user", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("is_active IN (0, 1)", name="ck_users_is_active"),
        db.CheckConstraint("must_change_pwd IN (0, 1)", name="ck_users_must_change_pwd"),
    )

    @property
    def role_code(self) -> str:
        return self.role.role_code if self.role else ""

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, password)

    def is_locked(self) -> bool:
        if (self.failed_attempts or 0) >= 5 and self.locked_until:
            now = utc_now()
            locked = self.locked_until
            if locked.tzinfo is None and now.tzinfo is not None:
                locked = locked.replace(tzinfo=now.tzinfo)
            elif locked.tzinfo is not None and now.tzinfo is None:
                now = now.replace(tzinfo=locked.tzinfo)
            return locked > now
        return False

    def record_failed_login(self, max_attempts: int = 5, lock_minutes: int = 15):
        self.failed_attempts = (self.failed_attempts or 0) + 1
        if self.failed_attempts >= max_attempts:
            from datetime import timedelta
            self.locked_until = utc_now() + timedelta(minutes=lock_minutes)
        db.session.commit()

    def reset_failed_logins(self):
        self.failed_attempts = 0
        self.locked_until = None
        self.last_login_at = utc_now()
        db.session.commit()

    def soft_delete(self, user_id=None):
        raise NotImplementedError(
            "User does not support soft deletion. Use is_active=0 for status deactivation "
            "to avoid permanently blocking unique username re-use."
        )

    def has_permission(self, permission: str) -> bool:
        if self.role_code == "super_admin":
            return True
        # Detailed permission mapping can be added here or in role models
        return False

    def __repr__(self):
        return f"<User {self.username}>"


@register_outbox_listener
class LoginHistory(SyncMixin, db.Model):
    __tablename__ = "login_history"

    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True)
    username_attempted = db.Column(db.String(50), nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    login_status = db.Column(db.String(20), nullable=False)  # success, failed, locked
    failure_reason = db.Column(db.String(100), nullable=True)
    attempted_at = db.Column(db.DateTime, nullable=False, default=utc_now)

    user = db.relationship("User", back_populates="login_history")

    def __repr__(self):
        return f"<LoginHistory {self.username_attempted} - {self.login_status}>"
