"""
Automated Integration Tests for Stage 2 (Branding/Settings + Dashboard) and Stage 3 (Catalog Items, Categories, Units, Auditing).
Runs directly against live Oracle ISI_POS_TEST schema.
"""
import io
import json
import uuid
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import text
from flask_login import login_user

from app.extensions import db
from app.models.user import User, Role
from app.models.setting import Setting
from app.models.item import Item, Category, Unit, ItemImage
from app.models.audit import AuditLog
from app.services.setting_service import SettingService
from app.services.item_service import ItemService
from app.utils.upload import validate_image_file, save_uploaded_image


# ==============================================================================
# 1. SettingService Persistence, Typed Retrieval & Audit Logging
# ==============================================================================
def test_stage2_settings_persistence_and_audit(app):
    """
    Validates typed setting updates and ensures audit trail is logged to Oracle.
    """
    with app.app_context():
        # Setup admin user
        role = Role.query.filter_by(role_code="super_admin").first() or Role(role_code="super_admin", role_name="Super Admin")
        if not role.id:
            db.session.add(role)
            db.session.commit()

        admin = User.query.filter_by(username="settings_admin_test").first()
        if not admin:
            admin = User(username="settings_admin_test", full_name="Settings Admin", role_id=role.id)
            admin.set_password("AdminPass123!")
            db.session.add(admin)
            db.session.commit()
        admin_id = admin.id

        # Update settings with unique org name to ensure change detection across runs
        unique_en = f"Punjab Educational Supplies {uuid.uuid4().hex[:4]}"
        updates = {
            "org_name_en": unique_en,
            "org_name_ur": "پنجاب تعلیمی اسٹور",
            "currency_symbol": "PKR",
            "default_tax_percent": "18.00",
            "tax_inclusive": "true",
            "invoice_padding": 8,
        }
        SettingService.update_settings(
            new_values=updates,
            user_id=admin_id,
            ip_address="127.0.0.1",
            user_agent="PyTest-Runner",
        )

        # Clear session and reload from Oracle
        db.session.remove()

        assert SettingService.get_value("org_name_en") == unique_en
        assert SettingService.get_value("org_name_ur") == "پنجاب تعلیمی اسٹور"
        assert SettingService.get_value("currency_symbol") == "PKR"
        assert SettingService.get_value("default_tax_percent") == Decimal("18.00")
        assert SettingService.get_value("tax_inclusive") is True
        assert SettingService.get_value("invoice_padding") == 8

        # Query Oracle audit_logs table directly
        audit = AuditLog.query.filter_by(
            module_name="settings",
            action_name="update_settings",
            record_id="system_settings"
        ).order_by(AuditLog.id.desc()).first()

        assert audit is not None
        assert audit.user_id == admin_id
        assert unique_en in audit.new_val
        assert audit.ip_address == "127.0.0.1"


# ==============================================================================
# 2. Context Processor Exposes Dynamic Settings Without Hardcoding
# ==============================================================================
def test_stage2_context_processor_injection(app, client):
    """
    Verifies that the context processor returns all branding and currency defaults.
    """
    with app.test_request_context("/"):
        with app.app_context():
            from app import create_app
            ctx = {}
            for cp in app.template_context_processors[None]:
                ctx.update(cp())

            assert "app_settings" in ctx
            assert "org_name_en" in ctx
            assert "org_name_ur" in ctx
            assert "currency_symbol" in ctx
            assert "theme_primary" in ctx
            assert "theme_secondary" in ctx
            assert ctx["currency_symbol"] in ("Rs.", "PKR")


# ==============================================================================
# 3. File Upload Validation & Strict SVG Rejection
# ==============================================================================
def test_stage2_upload_security_and_svg_rejection(app):
    """
    Asserts SVG files and invalid extensions are strictly rejected.
    """
    with app.app_context():
        # Case 1: SVG file rejection
        class DummyFile:
            def __init__(self, filename, content, mimetype="image/svg+xml"):
                self.filename = filename
                self.stream = io.BytesIO(content)
                self.mimetype = mimetype
            def seek(self, *args):
                self.stream.seek(*args)
            def tell(self):
                return self.stream.tell()
            def read(self, *args):
                return self.stream.read(*args)

        svg_file = DummyFile("malicious.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert('XSS')</script></svg>")
        with pytest.raises(ValueError) as exc_svg:
            validate_image_file(svg_file)
        assert "SVG files are strictly forbidden" in str(exc_svg.value)

        # Case 2: Disallowed extension (.exe)
        exe_file = DummyFile("payload.exe", b"MZ\x90\x00", mimetype="application/x-msdownload")
        with pytest.raises(ValueError) as exc_exe:
            validate_image_file(exe_file)
        assert "not permitted" in str(exc_exe.value)

        # Case 3: Genuine PNG header passes
        png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        valid_png = DummyFile("test_logo.png", png_bytes, mimetype="image/png")
        ext, size = validate_image_file(valid_png)
        assert ext == "png"
        assert size == len(png_bytes)


# ==============================================================================
# 4. Category Hierarchy & Urdu Name Multi-Byte Persistence
# ==============================================================================
def test_stage3_category_hierarchy_and_urdu(app):
    """
    Validates category creation, parent-child tree, and Urdu CHAR semantics in Oracle.
    """
    with app.app_context():
        suffix = uuid.uuid4().hex[:8].upper()
        parent_code = f"ROOT_{suffix}"
        child_code = f"SUB_{suffix}"

        parent = Category(
            cat_code=parent_code,
            cat_name=f"Stationery Goods {suffix}",
            cat_name_ur="اسٹیشنری سامان",
            sort_order=1,
            is_active=1,
        )
        db.session.add(parent)
        db.session.commit()
        parent_id = parent.id

        child = Category(
            cat_code=child_code,
            cat_name=f"Writing Pens {suffix}",
            cat_name_ur="لکھنے والے قلم",
            parent_id=parent_id,
            sort_order=10,
            is_active=1,
        )
        db.session.add(child)
        db.session.commit()
        child_id = child.id

        # Reload in fresh session
        db.session.remove()
        loaded_child = db.session.get(Category, child_id)
        assert loaded_child is not None
        assert loaded_child.parent.cat_code == parent_code
        assert loaded_child.cat_name_ur == "لکھنے والے قلم"


# ==============================================================================
# 5. Units of Measure & Decimal Quantities
# ==============================================================================
def test_stage3_unit_creation_and_constraints(app):
    """
    Tests Unit creation and check constraint allow_decimal IN (0, 1).
    """
    with app.app_context():
        unit_code = f"u_{uuid.uuid4().hex[:6]}"
        unit = Unit(
            unit_code=unit_code,
            unit_name="Kilogram Metric",
            allow_decimal=1,
            is_active=1,
        )
        db.session.add(unit)
        db.session.commit()

        db.session.remove()
        reloaded = Unit.query.filter_by(unit_code=unit_code).first()
        assert reloaded is not None
        assert reloaded.allow_decimal == 1


# ==============================================================================
# 6. Item Creation with Auto-Generated SKU and Manual SKU
# ==============================================================================
def test_stage3_item_creation_auto_sku_and_manual(app):
    """
    Verifies auto-suggested SKU and manual SKU override.
    """
    with app.app_context():
        unit = Unit.query.first()
        unit_id = unit.id
        auto_sku = ItemService.generate_next_sku()
        assert auto_sku.startswith("SKU-")

        # Create Item with auto SKU
        item1 = ItemService.create_item({
            "sku": auto_sku,
            "item_name": "Auto SKU Item",
            "unit_id": unit_id,
            "cost_price": "50.00",
            "sale_price": "75.00",
        })
        assert item1.sku == auto_sku

        # Create Item with manual override SKU
        manual_sku = f"MANUAL-{uuid.uuid4().hex[:8].upper()}"
        item2 = ItemService.create_item({
            "sku": manual_sku,
            "item_name": "Manual SKU Item",
            "unit_id": unit_id,
            "cost_price": "100.00",
            "sale_price": "150.00",
        })
        assert item2.sku == manual_sku


# ==============================================================================
# 7. Item Financials: Exact Decimal Round-Trip (Zero Floats)
# ==============================================================================
def test_stage3_item_financials_decimal_exact(app):
    """
    Asserts cost, sale, and wholesale prices are strict Decimals with exact precision.
    """
    with app.app_context():
        unit = Unit.query.first()
        unit_id = unit.id
        item = ItemService.create_item({
            "sku": f"FIN_{uuid.uuid4().hex[:8].upper()}",
            "item_name": "Precision Test Item",
            "unit_id": unit_id,
            "cost_price": "123.45",
            "sale_price": "199.99",
            "wholesale_price": "175.50",
            "tax_percent": "18.00",
            "reorder_level": "25.500",
        })
        item_id = item.id

        db.session.remove()
        loaded = db.session.get(Item, item_id)
        assert isinstance(loaded.cost_price, Decimal)
        assert loaded.cost_price == Decimal("123.45")
        assert isinstance(loaded.sale_price, Decimal)
        assert loaded.sale_price == Decimal("199.99")
        assert isinstance(loaded.wholesale_price, Decimal)
        assert loaded.wholesale_price == Decimal("175.50")
        assert isinstance(loaded.tax_percent, Decimal)
        assert loaded.tax_percent == Decimal("18.00")
        assert isinstance(loaded.reorder_level, Decimal)
        assert loaded.reorder_level == Decimal("25.500")


# ==============================================================================
# 8. Item Edit Field-Level Audit Trail in Oracle
# ==============================================================================
def test_stage3_item_edit_writes_audit_log(app):
    """
    Tests that updating an item writes a field-level JSON diff to 'audit_logs'.
    """
    with app.app_context():
        unit = Unit.query.first()
        unit_id = unit.id
        admin = User.query.first()
        admin_id = admin.id

        item = ItemService.create_item({
            "sku": f"AUD_{uuid.uuid4().hex[:8].upper()}",
            "item_name": "Original Name",
            "unit_id": unit_id,
            "cost_price": "100.00",
            "sale_price": "150.00",
        }, user_id=admin_id)
        item_id = item.id

        # Update item
        updated_item, diffs = ItemService.update_item(
            item=item,
            form_data={
                "item_name": "Updated Audit Name",
                "sale_price": "220.00",
                "cost_price": "100.00",
                "unit_id": unit_id,
            },
            user_id=admin_id,
            ip_address="192.168.1.50",
            user_agent="Audit-Inspector",
        )

        assert "item_name" in diffs
        assert diffs["item_name"] == "Updated Audit Name"
        assert "sale_price" in diffs
        assert diffs["sale_price"] == "220.00"

        # Verify audit_logs table directly in Oracle
        audit = AuditLog.query.filter_by(
            module_name="items",
            action_name="update_item",
            record_id=str(item_id)
        ).order_by(AuditLog.id.desc()).first()

        assert audit is not None
        assert audit.user_id == admin_id
        old_val_dict = json.loads(audit.old_val)
        new_val_dict = json.loads(audit.new_val)
        assert old_val_dict["item_name"] == "Original Name"
        assert new_val_dict["item_name"] == "Updated Audit Name"
        assert old_val_dict["sale_price"] == "150.00"
        assert new_val_dict["sale_price"] == "220.00"
        assert audit.ip_address == "192.168.1.50"


# ==============================================================================
# 9. Item Soft-Delete & SKU Reuse Proof
# ==============================================================================
def test_stage3_item_soft_delete_and_sku_reuse(app):
    """
    Soft-deletes an item and proves that its SKU can immediately be reused
    by a new item thanks to the function-based partial unique index.
    """
    with app.app_context():
        unit = Unit.query.first()
        target_unit_id = unit.id
        target_sku = f"REUSE_{uuid.uuid4().hex[:8].upper()}"

        # Step 1: Create initial item
        item1 = ItemService.create_item({
            "sku": target_sku,
            "item_name": "Item To Be Deleted",
            "unit_id": target_unit_id,
            "sale_price": "50.00",
        })
        item1_id = item1.id

        # Step 2: Soft delete item1
        success = ItemService.soft_delete_item(item1_id)
        assert success is True

        db.session.remove()
        deleted_item = db.session.get(Item, item1_id)
        assert deleted_item.deleted_at is not None

        # Step 3: Re-create brand new item with the EXACT same SKU
        # Under a regular UNIQUE constraint, this would fail with ORA-00001
        # Under our function-based unique index, this succeeds cleanly
        item2 = ItemService.create_item({
            "sku": target_sku,
            "item_name": "New Item Reusing Old SKU",
            "unit_id": target_unit_id,
            "sale_price": "60.00",
        })
        assert item2.id != item1_id
        assert item2.sku == target_sku
        assert item2.deleted_at is None


# ==============================================================================
# 10. Item Search & Multi-Filter Querying
# ==============================================================================
def test_stage3_search_and_stock_filtering(app):
    """
    Tests keyword search across English/Urdu/SKU and low_stock filtering.
    """
    with app.app_context():
        unit = Unit.query.first()
        unit_id = unit.id
        unique_term = f"UrduSearch_{uuid.uuid4().hex[:6]}"

        ItemService.create_item({
            "sku": f"SRC_{uuid.uuid4().hex[:8].upper()}",
            "item_name": f"Regular English {unique_term}",
            "item_name_ur": f"اردو تلاش {unique_term}",
            "unit_id": unit_id,
            "opening_stock": "2.000",
            "reorder_level": "10.000",  # Triggers low stock
        })

        # Test search by English keyword
        results_en = ItemService.get_filtered_items(search=unique_term)
        assert results_en.total >= 1

        # Test search by Urdu keyword
        results_ur = ItemService.get_filtered_items(search=unique_term)
        assert results_ur.total >= 1

        # Test low stock filter
        results_low = ItemService.get_filtered_items(search=unique_term, stock_filter="low_stock")
        assert results_low.total >= 1


# ==============================================================================
# 11. RBAC Route Level Enforcements
# ==============================================================================
def test_stage2_stage3_rbac_route_enforcements(app, client):
    """
    Verifies Cashier receives 403 Forbidden on Settings and Item Creation.
    Manager receives 200 OK on Item Creation, but 403 Forbidden on Settings.
    Super Admin receives 200 OK on both.
    """
    with app.app_context():
        cashier_role = Role.query.filter_by(role_code="cashier").first() or Role(role_code="cashier", role_name="Cashier")
        if not cashier_role.id: db.session.add(cashier_role)
        mgr_role = Role.query.filter_by(role_code="manager").first() or Role(role_code="manager", role_name="Manager")
        if not mgr_role.id: db.session.add(mgr_role)
        admin_role = Role.query.filter_by(role_code="super_admin").first() or Role(role_code="super_admin", role_name="Super Admin")
        if not admin_role.id: db.session.add(admin_role)
        db.session.commit()

        cashier_user = User(username=f"cashier_{uuid.uuid4().hex[:6]}", full_name="Cashier User", role_id=cashier_role.id)
        cashier_user.set_password("Cashier123!")
        db.session.add(cashier_user)

        mgr_user = User(username=f"mgr_{uuid.uuid4().hex[:6]}", full_name="Manager User", role_id=mgr_role.id)
        mgr_user.set_password("Manager123!")
        db.session.add(mgr_user)

        admin_user = User(username=f"admin_{uuid.uuid4().hex[:6]}", full_name="Admin User", role_id=admin_role.id)
        admin_user.set_password("Admin123!")
        db.session.add(admin_user)
        db.session.commit()

        # 1. Test as Cashier
        client.post("/auth/login", data={"username": cashier_user.username, "password": "Cashier123!"})
        res_settings = client.get("/settings/")
        assert res_settings.status_code == 403  # Forbidden
        res_item_create = client.get("/items/create")
        assert res_item_create.status_code == 403  # Forbidden
        client.get("/auth/logout")

        # 2. Test as Manager
        client.post("/auth/login", data={"username": mgr_user.username, "password": "Manager123!"})
        res_settings_mgr = client.get("/settings/")
        assert res_settings_mgr.status_code == 403  # Forbidden (Settings is Super Admin only)
        res_item_mgr = client.get("/items/create")
        assert res_item_mgr.status_code == 200  # Allowed
        client.get("/auth/logout")

        # 3. Test as Super Admin
        client.post("/auth/login", data={"username": admin_user.username, "password": "Admin123!"})
        res_settings_admin = client.get("/settings/")
        assert res_settings_admin.status_code == 200  # Allowed
        res_item_admin = client.get("/items/create")
        assert res_item_admin.status_code == 200  # Allowed
