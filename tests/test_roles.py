"""
Unit Tests for Role Definitions and Authorization.
"""
import pytest
from app.models.user import Role, User


def test_role_attributes():
    """Verifies role code and system role flag."""
    admin_role = Role(role_code="super_admin", role_name="Super Admin", is_system=1)
    cashier_role = Role(role_code="cashier", role_name="Cashier", is_system=1)

    assert admin_role.role_code == "super_admin"
    assert admin_role.is_system == 1
    assert cashier_role.role_code == "cashier"


def test_user_role_association():
    """Verifies association between user and role."""
    role = Role(role_code="manager", role_name="Manager")
    user = User(username="manager1", full_name="Store Manager", role=role)

    assert user.role_code == "manager"
    assert user.has_permission("any_permission") is False  # Only super_admin has universal bypass
