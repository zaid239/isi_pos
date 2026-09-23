"""
Integration Test Suite against Live Oracle ISI_POS_TEST Schema.
Validates all 10 required database-backed behaviors:
1. conftest safety abort against production
2. Route-level role enforcement (@role_required 403)
3. Forced password change redirect for must_change_pwd=1
4. CSRF rejection for POST without token
5. End-to-end account lockout through login route
6. Database-level sync_uid uniqueness (IntegrityError)
7. Monotonic row_version increment via SQLAlchemy listener
8. Transactional outbox safety (zero outbox rows on rollback)
9. Soft delete timestamp persistence without row deletion
10. SKU reuse after soft delete (function-based unique index validation)
"""
import os
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from flask_login import login_user

from app.extensions import db
from app.models.user import User, Role, LoginHistory
from app.models.item import Category, Unit, Item
from app.models.payment import PaymentMode
from app.models.expense import ExpenseHead
from app.models.sync import SyncOutbox
from app.utils.ulid import generate_ulid
from app.utils.timezone import utc_now
from tests.conftest import assert_test_environment_safety


# ==============================================================================
# 1. Safety Guard Abort Verification
# ==============================================================================
def test_01_conftest_aborts_on_production_match(monkeypatch):
    """
    Test 1: conftest.py aborts when the resolved test DSN or schema matches production.
    Demonstrates SystemExit is raised with descriptive safety failure messages.
    """
    # Test case A: test user matches production user
    monkeypatch.setenv("TEST_DB_USER", "isi")
    with pytest.raises(SystemExit) as exc_info:
        assert_test_environment_safety()
    assert "FATAL SAFETY FAILURE: Test DB service and user match production credentials!" in str(exc_info.value)

    # Test case B: test schema matches production schema
    monkeypatch.setenv("TEST_DB_USER", "isi_pos_test")
    monkeypatch.setenv("TEST_DB_SCHEMA", "isi")
    with pytest.raises(SystemExit) as exc_info:
        assert_test_environment_safety()
    assert "FATAL SAFETY FAILURE: Test schema matches production schema!" in str(exc_info.value)


# ==============================================================================
# 2. Role Enforcement at Route Level
# ==============================================================================
def test_02_role_enforcement_at_route_level(app, client):
    """
    Test 2: A Cashier session receives 403 on a Manager-only route via @role_required.
    """
    with app.app_context():
        cashier_role = Role.query.filter_by(role_code="cashier").first()
        if not cashier_role:
            cashier_role = Role(role_code="cashier", role_name="Cashier")
            db.session.add(cashier_role)
            db.session.commit()

        username = f"cashier_{generate_ulid()[:8]}"
        user = User(
            username=username,
            full_name="Route Cashier Test",
            role_id=cashier_role.id,
            is_active=1,
            must_change_pwd=0,
        )
        user.set_password("CashierPass123!")
        db.session.add(user)
        db.session.commit()

        # Login as Cashier via test client
        login_res = client.post("/auth/login", data={"username": username, "password": "CashierPass123!"}, follow_redirects=True)
        assert login_res.status_code == 200

        # Attempt to access manager-only route
        mgr_res = client.get("/manager-only")
        assert mgr_res.status_code == 403


# ==============================================================================
# 3. Forced Password Change Enforcement
# ==============================================================================
def test_03_forced_password_change_redirects(app, client):
    """
    Test 3: A user with must_change_pwd=1 is redirected and cannot reach any other route.
    """
    with app.app_context():
        role = Role.query.first() or Role(role_code="cashier", role_name="Cashier")
        if not role.id:
            db.session.add(role)
            db.session.commit()

        username = f"forcepwd_{generate_ulid()[:8]}"
        user = User(
            username=username,
            full_name="Force Password User",
            role_id=role.id,
            is_active=1,
            must_change_pwd=1,
        )
        user.set_password("TempPassword123!")
        db.session.add(user)
        db.session.commit()

        # Login user
        login_res = client.post("/auth/login", data={"username": username, "password": "TempPassword123!"}, follow_redirects=False)
        assert login_res.status_code == 302
        assert "/auth/change-password" in login_res.headers["Location"]

        # Attempt to access dashboard directly while must_change_pwd=1
        dash_res = client.get("/dashboard", follow_redirects=False)
        assert dash_res.status_code == 302
        assert "/auth/change-password" in dash_res.headers["Location"]


# ==============================================================================
# 4. CSRF Protection
# ==============================================================================
def test_04_csrf_rejects_post_without_token(app, csrf_client):
    """
    Test 4: A POST without a CSRF token is rejected with 400 Bad Request.
    """
    res = csrf_client.post("/auth/login", data={"username": "any", "password": "any"})
    assert res.status_code == 400


# ==============================================================================
# 5. Account Lockout End-to-End through Login Route
# ==============================================================================
def test_05_account_lockout_end_to_end(app, client):
    """
    Test 5: Account lockout end to end through the login route after 5 failed attempts.
    """
    with app.app_context():
        role = Role.query.first() or Role(role_code="cashier", role_name="Cashier")
        if not role.id:
            db.session.add(role)
            db.session.commit()

        username = f"lock_{generate_ulid()[:8]}"
        user = User(
            username=username,
            full_name="Lockout Target User",
            role_id=role.id,
            is_active=1,
            failed_attempts=0,
            must_change_pwd=0,
        )
        user.set_password("CorrectPassword123!")
        db.session.add(user)
        db.session.commit()

        # Submit 5 failed attempts via route
        for i in range(5):
            res = client.post("/auth/login", data={"username": username, "password": "WrongPassword!"}, follow_redirects=True)
            assert res.status_code == 200

        # Query Oracle to confirm database-persisted lockout state
        db.session.expire_all()
        refreshed_user = User.query.filter_by(username=username).first()
        assert refreshed_user.failed_attempts == 5
        assert refreshed_user.locked_until is not None
        assert refreshed_user.is_locked() is True

        # Attempt 6 with CORRECT password must still be rejected
        res6 = client.post("/auth/login", data={"username": username, "password": "CorrectPassword123!"}, follow_redirects=True)
        assert res6.status_code == 200
        assert "Account temporarily locked" in res6.get_data(as_text=True)

        # Confirm locked event is logged to login_history in Oracle
        history = LoginHistory.query.filter_by(username_attempted=username, login_status="locked").first()
        assert history is not None


# ==============================================================================
# 6. Database Enforcement of sync_uid Uniqueness
# ==============================================================================
def test_06_database_enforces_sync_uid_uniqueness(app):
    """
    Test 6: sync_uid uniqueness enforced by the Oracle database, not by Python.
    Asserts Oracle IntegrityError (ORA-00001) on duplicate sync_uid.
    """
    with app.app_context():
        dup_uid = generate_ulid()

        cat1 = Category(
            cat_name="Electronics Test",
            cat_code=f"CAT_{generate_ulid()[:8]}",
            sync_uid=dup_uid,
            terminal_code="T01",
        )
        db.session.add(cat1)
        db.session.commit()

        # Insert second category with identical sync_uid
        cat2 = Category(
            cat_name="Duplicate UID Cat",
            cat_code=f"CAT_{generate_ulid()[:8]}",
            sync_uid=dup_uid,
            terminal_code="T01",
        )
        db.session.add(cat2)
        with pytest.raises(IntegrityError):
            db.session.commit()

        db.session.rollback()


# ==============================================================================
# 7. Monotonic row_version Increment
# ==============================================================================
def test_07_row_version_increments_on_update(app):
    """
    Test 7: row_version increments monotonically on update via the event listener.
    """
    with app.app_context():
        unit = Unit(
            unit_name="Versioned Unit",
            unit_code=f"U_{generate_ulid()[-12:]}",
            allow_decimal=0,
            terminal_code="T01",
        )
        db.session.add(unit)
        db.session.commit()
        assert unit.row_version == 1

        # First update
        unit.unit_name = "Versioned Unit Updated Once"
        db.session.commit()
        db.session.refresh(unit)
        assert unit.row_version == 2

        # Second update
        unit.unit_name = "Versioned Unit Updated Twice"
        db.session.commit()
        db.session.refresh(unit)
        assert unit.row_version == 3


# ==============================================================================
# 8. Outbox Transactional Atomicity (Rollback leaves Zero rows)
# ==============================================================================
def test_08_outbox_transactionality_zero_on_rollback(app):
    """
    Test 8: Outbox listener writes inside the transaction;
    a rolled-back transaction leaves zero sync_outbox rows in Oracle.
    """
    with app.app_context():
        target_uid = generate_ulid()

        cat = Category(
            cat_name="Rollback Outbox Category",
            cat_code=f"RB_{generate_ulid()[:8]}",
            sync_uid=target_uid,
            terminal_code="T01",
        )
        db.session.add(cat)
        # Flush stages the category and triggers the outbox listener within transaction
        db.session.flush()

        # Explicitly roll back the business transaction
        db.session.rollback()

        # Query Oracle directly to assert zero sync_outbox rows were committed
        count = db.session.execute(
            text("SELECT count(*) FROM sync_outbox WHERE row_uid = :uid"),
            {"uid": target_uid},
        ).scalar()
        assert count == 0


# ==============================================================================
# 9. Soft Delete Sets Timestamp Without Deleting Row
# ==============================================================================
def test_09_soft_delete_sets_timestamp_preserves_row(app):
    """
    Test 9: Soft delete sets deleted_at and does not remove the row from Oracle.
    """
    with app.app_context():
        unit = Unit.query.first() or Unit(unit_name="SoftDel Unit", unit_code=f"U_{generate_ulid()[:6]}")
        if not unit.id:
            db.session.add(unit)
            db.session.commit()

        item = Item(
            sku=f"SKU_{generate_ulid()[:8]}",
            item_name="Soft Delete Test Item",
            unit_id=unit.id,
            cost_price=100,
            sale_price=150,
            terminal_code="T01",
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        # Perform soft delete
        item.soft_delete(user_id=1)
        db.session.commit()

        # Query Oracle directly via SQL to verify row is still present
        row = db.session.execute(
            text("SELECT id, deleted_at FROM items WHERE id = :item_id"),
            {"item_id": item_id},
        ).fetchone()

        assert row is not None
        assert row[0] == item_id
        assert row[1] is not None  # deleted_at is set in Oracle


# ==============================================================================
# 10. SKU Reuse After Soft Delete (Function-based Index Validation)
# ==============================================================================
def test_10_sku_reuse_after_soft_delete(app):
    """
    Test 10: SKU reuse after soft delete.
    Soft-delete an item, then create a new live item with the identical SKU.
    Must succeed due to function-based index (CASE WHEN deleted_at IS NULL THEN sku END).
    A subsequent attempt to create another live item with that SKU must fail.
    """
    with app.app_context():
        unit = Unit.query.first() or Unit(unit_name="Reuse Unit", unit_code=f"U_{generate_ulid()[:6]}")
        if not unit.id:
            db.session.add(unit)
            db.session.commit()

        reusable_sku = f"SKU_REUSE_{generate_ulid()[:8]}"

        # 1. Create first item with this SKU
        item1 = Item(
            sku=reusable_sku,
            item_name="Original Active Item",
            unit_id=unit.id,
            cost_price=50,
            sale_price=75,
            terminal_code="T01",
        )
        db.session.add(item1)
        db.session.commit()

        # 2. Soft-delete item1
        item1.soft_delete(user_id=1)
        db.session.commit()

        # 3. Create second item with identical SKU while item1 is soft-deleted
        item2 = Item(
            sku=reusable_sku,
            item_name="New Live Item with Reused SKU",
            unit_id=unit.id,
            cost_price=60,
            sale_price=90,
            terminal_code="T01",
            deleted_at=None,
        )
        db.session.add(item2)
        # This MUST succeed in Oracle because item1 has deleted_at NOT NULL
        db.session.commit()

        # Verify both items exist in Oracle
        rows = db.session.execute(
            text("SELECT id, sku, deleted_at FROM items WHERE sku = :sku ORDER BY id"),
            {"sku": reusable_sku},
        ).fetchall()
        assert len(rows) == 2
        assert rows[0][2] is not None  # first item soft-deleted
        assert rows[1][2] is None      # second item live and active

        # 4. Attempt to create a THIRD live item with the same SKU (must fail)
        item3 = Item(
            sku=reusable_sku,
            item_name="Conflicting Third Live Item",
            unit_id=unit.id,
            cost_price=70,
            sale_price=100,
            terminal_code="T01",
            deleted_at=None,
        )
        db.session.add(item3)
        with pytest.raises(IntegrityError):
            db.session.commit()

        db.session.rollback()


# ==============================================================================
# 11. Urdu Multi-byte CHAR Semantics Round-trip & Limit Validation
# ==============================================================================
def test_11_urdu_char_semantics_roundtrip_and_limit(app):
    """
    Test 11: End-to-end proof of AL32UTF8 and CHAR semantics:
    1. Insert item whose item_name_ur is a long Urdu string (>= 60 characters).
    2. Commit, read back in a fresh session, assert byte-for-byte identical.
    3. Assert Oracle SQL LENGTH(item_name_ur) returns exact character count inserted.
    4. Insert item that fills the column to its stated limit (250 characters) and assert accepted.
    """
    with app.app_context():
        unit = Unit.query.first() or Unit(unit_name="Urdu Unit", unit_code=f"U_{generate_ulid()[:6]}")
        if not unit.id:
            db.session.add(unit)
            db.session.commit()
        target_unit_id = unit.id

        # Part 1: Long Urdu string (89 characters, ~160 multi-byte bytes)
        urdu_long = "یہ ایک انتہائی معیاری اور خالص دیسی گھی کا بڑا ڈبہ ہے جو ادارہ جاتی سٹور کے لیے مخصوص ہے"
        assert len(urdu_long) >= 60

        sku_1 = f"SKU_URDU_{generate_ulid()}"
        item1 = Item(
            sku=sku_1,
            item_name="Desi Ghee Tin 5kg",
            item_name_ur=urdu_long,
            unit_id=target_unit_id,
            cost_price=Decimal("4500.00"),
            sale_price=Decimal("5200.00"),
        )
        db.session.add(item1)
        db.session.commit()
        item1_id = item1.id

        # Read back in a fresh database session
        db.session.remove()
        reloaded = db.session.get(Item, item1_id)
        assert reloaded is not None
        # Assert byte-for-byte equality
        assert reloaded.item_name_ur == urdu_long
        assert reloaded.item_name_ur.encode("utf-8") == urdu_long.encode("utf-8")

        # Assert Oracle SQL LENGTH() returns the exact character count
        sql_char_len = db.session.execute(
            text("SELECT LENGTH(item_name_ur) FROM items WHERE id = :id"),
            {"id": item1_id},
        ).scalar()
        assert sql_char_len == len(urdu_long)

        # Part 2: Exact stated character limit (VARCHAR2(250 CHAR))
        # Build an exact 250-character Urdu string (which exceeds 450 bytes in UTF-8)
        urdu_base = "ادارہ جاتی پوائنٹ آف سیل اور انوینٹری مینجمنٹ سسٹم برائے سٹور "
        urdu_250 = (urdu_base * 5)[:250]
        assert len(urdu_250) == 250
        assert len(urdu_250.encode("utf-8")) > 250  # Proves multi-byte (> 400 bytes)

        sku_2 = f"SKU_URDU250_{generate_ulid()}"
        item2 = Item(
            sku=sku_2,
            item_name="Urdu Max Limit Item",
            item_name_ur=urdu_250,
            unit_id=target_unit_id,
            cost_price=Decimal("100.00"),
            sale_price=Decimal("150.00"),
        )
        db.session.add(item2)
        # In byte semantics, this would fail with ORA-12899 because byte length > 250
        # In CHAR semantics (VARCHAR2(250 CHAR)), this succeeds cleanly
        db.session.commit()
        item2_id = item2.id

        db.session.remove()
        reloaded_250 = db.session.get(Item, item2_id)
        assert reloaded_250 is not None
        assert len(reloaded_250.item_name_ur) == 250
        assert reloaded_250.item_name_ur == urdu_250

        sql_char_len_250 = db.session.execute(
            text("SELECT LENGTH(item_name_ur) FROM items WHERE id = :id"),
            {"id": item2_id},
        ).scalar()
        assert sql_char_len_250 == 250


# ==============================================================================
# 12. Decimal Round-trip Exact Precision (NUMBER(12,2) with fetch_decimals)
# ==============================================================================
def test_12_decimal_roundtrip_exact_precision(app):
    """
    Test 12: Store 12345.67 in a NUMBER(12,2) column, read it back in a new session,
    assert it is a decimal.Decimal and not a float, and assert exact equality.
    Proves oracledb.defaults.fetch_decimals = True is active and functioning.
    """
    with app.app_context():
        unit = Unit.query.first() or Unit(unit_name="Decimal Unit", unit_code=f"U_{generate_ulid()[:6]}")
        if not unit.id:
            db.session.add(unit)
            db.session.commit()

        expected_val = Decimal("12345.67")
        sku = f"SKU_DEC_{generate_ulid()[:8]}"

        item = Item(
            sku=sku,
            item_name="Decimal Precision Test Item",
            unit_id=unit.id,
            cost_price=expected_val,
            sale_price=Decimal("20000.00"),
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id

        # Fresh session
        db.session.remove()
        retrieved = db.session.get(Item, item_id)
        assert retrieved is not None

        # Assert type is Decimal, not float
        assert isinstance(retrieved.cost_price, Decimal)
        assert not isinstance(retrieved.cost_price, float)
        # Assert exact equality
        assert retrieved.cost_price == expected_val


# ==============================================================================
# 13. Empty String and Whitespace Rejection
# ==============================================================================
def test_13_empty_and_whitespace_strings_rejected(app):
    """
    Test 13: Attempt to save a required text field as "" and as "   ",
    and assert both are rejected by validation — not silently stored as NULL.
    """
    with app.app_context():
        unit = Unit.query.first() or Unit(unit_name="Validation Unit", unit_code=f"U_{generate_ulid()[:6]}")
        if not unit.id:
            db.session.add(unit)
            db.session.commit()

        # Case 1: Empty string "" rejected by validation
        with pytest.raises(ValueError) as exc_info_empty:
            Item(
                sku=f"SKU_VAL_{generate_ulid()[:6]}",
                item_name="",
                unit_id=unit.id,
            )
        assert "cannot be empty or whitespace" in str(exc_info_empty.value)

        # Case 2: Whitespace-only string "   " rejected by validation
        with pytest.raises(ValueError) as exc_info_ws:
            Item(
                sku=f"SKU_VAL_{generate_ulid()[:6]}",
                item_name="     ",
                unit_id=unit.id,
            )
        assert "cannot be empty or whitespace" in str(exc_info_ws.value)


# ==============================================================================
# 14. Outbox Positive Case (Committed Insert Creates Outbox Record)
# ==============================================================================
def test_14_outbox_positive_case_committed_record(app):
    """
    Test 14: Outbox positive case: A committed insert leaves exactly one outbox
    row with the correct table_name, row_uid, operation, and row_version.
    """
    with app.app_context():
        target_uid = generate_ulid()
        cat = Category(
            cat_name="Outbox Positive Category",
            cat_code=f"OP_{generate_ulid()[:8]}",
            sync_uid=target_uid,
        )
        db.session.add(cat)
        db.session.commit()

        # Query Oracle directly for the sync_outbox record
        outbox_rows = SyncOutbox.query.filter_by(row_uid=target_uid).all()
        assert len(outbox_rows) == 1

        outbox = outbox_rows[0]
        assert outbox.table_name == "categories"
        assert outbox.row_uid == target_uid
        assert outbox.operation == "insert"
        assert outbox.row_version == 1
        assert outbox.status_code == "pending"
        assert outbox.payload is not None
        assert "Outbox Positive Category" in outbox.payload


# ==============================================================================
# 15. Non-Soft-Deletable Master Entities Raise NotImplementedError
# ==============================================================================
def test_15_non_soft_deletable_entities_raise_not_implemented(app):
    """
    Test 15: Master/lookup models with plain UNIQUE constraints (User, Category,
    Unit, PaymentMode, ExpenseHead) override soft_delete() to raise NotImplementedError.
    They must use status deactivation (is_active=0) rather than soft delete to
    prevent permanently blocking natural unique keys.
    """
    with app.app_context():
        # User
        user = User(username=f"u_{generate_ulid()[:8]}", full_name="Test", role_id=1)
        with pytest.raises(NotImplementedError) as exc_user:
            user.soft_delete()
        assert "User does not support soft deletion" in str(exc_user.value)

        # Category
        cat = Category(cat_name="Test Cat", cat_code=f"C_{generate_ulid()[:8]}")
        with pytest.raises(NotImplementedError) as exc_cat:
            cat.soft_delete()
        assert "Category does not support soft deletion" in str(exc_cat.value)

        # Unit
        unit = Unit(unit_name="Test Unit", unit_code=f"U_{generate_ulid()[:8]}")
        with pytest.raises(NotImplementedError) as exc_unit:
            unit.soft_delete()
        assert "Unit does not support soft deletion" in str(exc_unit.value)

        # PaymentMode
        mode = PaymentMode(mode_name="Test Mode", mode_code=f"M_{generate_ulid()[:8]}")
        with pytest.raises(NotImplementedError) as exc_mode:
            mode.soft_delete()
        assert "PaymentMode does not support soft deletion" in str(exc_mode.value)

        # ExpenseHead
        head = ExpenseHead(head_name="Test Head", head_code=f"H_{generate_ulid()[:8]}")
        with pytest.raises(NotImplementedError) as exc_head:
            head.soft_delete()
        assert "ExpenseHead does not support soft deletion" in str(exc_head.value)

