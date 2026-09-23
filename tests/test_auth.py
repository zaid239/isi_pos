"""
Unit Tests for Authentication and Security Models.
"""
from datetime import datetime, timezone, timedelta
import pytest
from app.models.user import User, Role
from app.utils.ulid import generate_ulid
from app.utils.timezone import utc_now


def test_ulid_generation():
    """Verifies that generated ULID is 26 characters and distinct."""
    uid1 = generate_ulid()
    uid2 = generate_ulid()
    assert len(uid1) == 26
    assert len(uid2) == 26
    assert uid1 != uid2


def test_password_hashing_and_verification():
    """Verifies secure password hashing and verification."""
    u = User(username="testcashier", full_name="Test Cashier")
    u.set_password("Secret123!")
    assert u.password_hash != "Secret123!"
    assert u.check_password("Secret123!") is True
    assert u.check_password("WrongPassword") is False


def test_account_lockout_logic():
    """Verifies account lockout triggers after 5 failed attempts."""
    u = User(username="cashier1", full_name="Cashier 1", failed_attempts=0)
    assert u.is_locked() is False

    # Simulate 4 failed attempts
    u.failed_attempts = 4
    assert u.is_locked() is False

    # 5th attempt locks account
    u.failed_attempts = 5
    u.locked_until = utc_now() + timedelta(minutes=15)
    assert u.is_locked() is True

    # After expiry, lockout releases
    u.locked_until = utc_now() - timedelta(minutes=1)
    assert u.is_locked() is False
