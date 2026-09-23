"""
Role-based authorization decorators for enforcing server-side permissions.
"""
from functools import wraps
from flask import abort, flash, redirect, url_for, request
from flask_login import current_user


def role_required(*roles):
    """
    Decorator that restricts endpoint access to users with specified role codes or names.
    Super Admin has universal access.
    """
    def decorator(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))

            # Super Admin always bypasses role checks
            user_role = getattr(current_user, "role_code", "") or (
                current_user.role.role_code if getattr(current_user, "role", None) else ""
            )

            if user_role == "super_admin":
                return fn(*args, **kwargs)

            allowed_roles = [r.lower() for r in roles]
            if user_role.lower() not in allowed_roles:
                abort(403)

            return fn(*args, **kwargs)

        return decorated_view

    return decorator


def permission_required(permission: str):
    """Decorator checking specific functional permissions on user's role."""
    def decorator(fn):
        @wraps(fn)
        def decorated_view(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))

            if not current_user.has_permission(permission):
                abort(403)

            return fn(*args, **kwargs)

        return decorated_view

    return decorator
