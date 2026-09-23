"""
Integration and Arithmetic Tests for:
1. Tax-Exclusive Mode (Rule 1: tax added on top of item price; line-level individual GST rates).
2. Bill Discount (Rule 2: proportional distribution before tax, GST on discounted amount).
3. Cashier role discount cap enforcement (both % and Rs. loophole closure).
4. Totals reconciliation upon discount clearing.
5. Checkout with discount and AuditLog entry verification.
"""
import uuid
from decimal import Decimal
import pytest
from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit
from app.models.invoice import Invoice, InvoiceItem
from app.models.audit import AuditLog
from app.models.setting import Setting
from app.services.setting_service import SettingService
from app.services.calculation_service import CalculationService
from app.services.pos_service import POSService


@pytest.fixture(scope="module")
def tax_discount_setup(app):
    """Sets up test categories, units, test cashier/manager users, and test items in Oracle test schema."""
    with app.app_context():
        # Ensure tax_inclusive is false and cash_rounding_mode is off for exact math
        Setting.set_value("tax_inclusive", "false", setting_type="bool", group_name="financial")
        Setting.set_value("cash_rounding_mode", "off", setting_type="string", group_name="financial")
        Setting.set_value("cashier_max_discount_percent", "10.00", setting_type="decimal", group_name="pos")
        db.session.commit()
        SettingService.invalidate_cache()

        # 1. Roles
        cashier_role = Role.query.filter_by(role_code="cashier").first()
        if not cashier_role:
            cashier_role = Role(role_code="cashier", role_name="Cashier", is_system=1)
            db.session.add(cashier_role)
            db.session.commit()

        manager_role = Role.query.filter_by(role_code="manager").first()
        if not manager_role:
            manager_role = Role(role_code="manager", role_name="Manager", is_system=1)
            db.session.add(manager_role)
            db.session.commit()

        # 2. Users
        cashier = User.query.filter_by(username="test_disc_cashier").first()
        if not cashier:
            cashier = User(
                username="test_disc_cashier",
                full_name="Discount Test Cashier",
                role_id=cashier_role.id,
                is_active=1,
            )
            cashier.set_password("Cashier123!")
            db.session.add(cashier)

        manager = User.query.filter_by(username="test_disc_manager").first()
        if not manager:
            manager = User(
                username="test_disc_manager",
                full_name="Discount Test Manager",
                role_id=manager_role.id,
                is_active=1,
            )
            manager.set_password("Manager123!")
            db.session.add(manager)
        db.session.commit()

        # 3. Units & Category
        unit = Unit.query.filter_by(unit_code="pcs").first()
        if not unit:
            unit = Unit(unit_code="pcs", unit_name="Pieces", allow_decimal=0, is_active=1)
            db.session.add(unit)
            db.session.commit()

        cat = Category.query.filter_by(cat_code="TEST-CAT-DISC").first()
        if not cat:
            cat = Category(cat_code="TEST-CAT-DISC", cat_name="Test Discount Category", is_active=1)
            db.session.add(cat)
            db.session.commit()

        # 4. Isolated Test Items (in ISI_POS_TEST schema only)
        # Item 1: Test Latte at 550.00 with 16% GST
        latte = Item.query.filter_by(sku="TEST-LATTE-550").first()
        if not latte:
            latte = Item(
                sku="TEST-LATTE-550",
                barcode=f"BAR-{uuid.uuid4().hex[:8]}",
                item_name="Test Latte",
                category_id=cat.id,
                unit_id=unit.id,
                cost_price=Decimal("200.00"),
                sale_price=Decimal("550.00"),
                tax_percent=Decimal("16.00"),
                current_stock=Decimal("100.000"),
                reorder_level=Decimal("10.000"),
                is_active=1,
            )
            db.session.add(latte)

        # Item 2: Test Croissant at 380.00 with 18% GST (different GST rate)
        croissant = Item.query.filter_by(sku="TEST-CROISSANT-380").first()
        if not croissant:
            croissant = Item(
                sku="TEST-CROISSANT-380",
                barcode=f"BAR-{uuid.uuid4().hex[:8]}",
                item_name="Test Croissant",
                category_id=cat.id,
                unit_id=unit.id,
                cost_price=Decimal("120.00"),
                sale_price=Decimal("380.00"),
                tax_percent=Decimal("18.00"),
                current_stock=Decimal("100.000"),
                reorder_level=Decimal("10.000"),
                is_active=1,
            )
            db.session.add(croissant)

        db.session.commit()

        return {
            "cashier_id": cashier.id,
            "manager_id": manager.id,
            "latte_id": latte.id,
            "croissant_id": croissant.id,
        }


def test_01_latte_550_tax_exclusive_to_638(app, tax_discount_setup):
    """
    Business rule: Latte at Rs. 550 with 16% GST must total Rs. 638.00.
    550 + (550 * 0.16 = 88) = 638.00.
    """
    with app.app_context():
        items = [{"item_id": tax_discount_setup["latte_id"], "quantity": 1}]
        res = POSService.calculate_cart(items_payload=items)

        assert res["gross_total"] == 550.00
        assert res["discount_amount"] == 0.00
        assert res["taxable_total"] == 550.00
        assert res["tax_total"] == 88.00
        assert res["payable_total"] == 638.00
        assert len(res["lines"]) == 1
        assert res["lines"][0]["tax_percent"] == 16.00
        assert res["lines"][0]["tax_amount"] == 88.00
        assert res["lines"][0]["line_total"] == 638.00


def test_02_mixed_rate_order_computes_each_lines_own_rate(app, tax_discount_setup):
    """
    Mixed-rate order:
    - Latte: 550 @ 16% GST -> Tax: 88.00 -> Line Total: 638.00
    - Croissant: 380 @ 18% GST -> Tax: 68.40 -> Line Total: 448.40
    Subtotal: 930.00, Tax: 156.40, Total: 1086.40.
    """
    with app.app_context():
        items = [
            {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            {"item_id": tax_discount_setup["croissant_id"], "quantity": 1},
        ]
        res = POSService.calculate_cart(items_payload=items)

        assert res["gross_total"] == 930.00
        assert res["taxable_total"] == 930.00
        assert res["tax_total"] == 156.40
        assert res["payable_total"] == 1086.40

        latte_line = next(l for l in res["lines"] if l["item_id"] == tax_discount_setup["latte_id"])
        croissant_line = next(l for l in res["lines"] if l["item_id"] == tax_discount_setup["croissant_id"])

        assert latte_line["tax_percent"] == 16.00
        assert latte_line["tax_amount"] == 88.00
        assert latte_line["line_total"] == 638.00

        assert croissant_line["tax_percent"] == 18.00
        assert croissant_line["tax_amount"] == 68.40
        assert croissant_line["line_total"] == 448.40


def test_03_discount_as_percentage_recalculates_gst_on_discounted_amount(app, tax_discount_setup):
    """
    10% discount on mixed-rate cart (930.00):
    Total discount: 93.00.
    Proportional distribution before GST:
    - Latte (550 / 930 * 93.00 = 55.00): Taxable = 495.00 -> 16% GST = 79.20 -> Total = 574.20
    - Croissant (380 / 930 * 93.00 = 38.00): Taxable = 342.00 -> 18% GST = 61.56 -> Total = 403.56
    Total GST = 79.20 + 61.56 = 140.76.
    Payable Total = 574.20 + 403.56 = 977.76.
    """
    with app.app_context():
        items = [
            {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            {"item_id": tax_discount_setup["croissant_id"], "quantity": 1},
        ]
        res = POSService.calculate_cart(
            items_payload=items,
            discount_type="percent",
            discount_value=10.00,
        )

        assert res["gross_total"] == 930.00
        assert res["discount_amount"] == 93.00
        assert res["discount_rate"] == 10.00
        assert res["taxable_total"] == 837.00
        assert res["tax_total"] == 140.76
        assert res["payable_total"] == 977.76

        latte_line = next(l for l in res["lines"] if l["item_id"] == tax_discount_setup["latte_id"])
        croissant_line = next(l for l in res["lines"] if l["item_id"] == tax_discount_setup["croissant_id"])

        assert latte_line["tax_amount"] == 79.20
        assert latte_line["line_total"] == 574.20
        assert croissant_line["tax_amount"] == 61.56
        assert croissant_line["line_total"] == 403.56


def test_04_discount_as_rupees_recalculates_gst(app, tax_discount_setup):
    """
    Fixed Rs. 50.00 discount on mixed-rate cart (930.00).
    Rule 2: spread proportionally before GST, GST computed on discounted base.
    """
    with app.app_context():
        items = [
            {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            {"item_id": tax_discount_setup["croissant_id"], "quantity": 1},
        ]
        res = POSService.calculate_cart(
            items_payload=items,
            discount_type="amount",
            discount_value=50.00,
        )

        assert res["gross_total"] == 930.00
        assert res["discount_amount"] == 50.00
        assert res["taxable_total"] == 880.00
        # Tax on 880 discounted base is less than undiscounted 156.40
        assert res["tax_total"] < 156.40
        assert res["payable_total"] == res["taxable_total"] + res["tax_total"]


def test_05_cashier_over_limit_percentage_refused(app, tax_discount_setup):
    """
    Cashier has max 10% discount cap.
    Attempting 15% discount as a cashier is refused with manager approval message.
    """
    with app.app_context():
        cashier_user = db.session.get(User, tax_discount_setup["cashier_id"])
        items = [{"item_id": tax_discount_setup["latte_id"], "quantity": 1}]

        with pytest.raises(ValueError, match="Manager approval is needed"):
            POSService.calculate_cart(
                items_payload=items,
                discount_type="percent",
                discount_value=15.00,
                user=cashier_user,
            )


def test_06_cashier_rupees_loophole_over_limit_refused(app, tax_discount_setup):
    """
    Cashier limit is 10%.
    Cashier selects Rs. toggle and enters Rs. 232.50 on a Rs. 930 order (25% effective discount).
    Must be refused server-side with manager approval message.
    """
    with app.app_context():
        cashier_user = db.session.get(User, tax_discount_setup["cashier_id"])
        items = [
            {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            {"item_id": tax_discount_setup["croissant_id"], "quantity": 1},
        ]

        with pytest.raises(ValueError, match="Manager approval is needed"):
            POSService.calculate_cart(
                items_payload=items,
                discount_type="amount",
                discount_value=232.50,  # 25% of 930.00
                user=cashier_user,
            )


def test_07_manager_and_super_admin_have_no_cap(app, tax_discount_setup):
    """
    Manager user applying 25% discount is NOT capped.
    """
    with app.app_context():
        manager_user = db.session.get(User, tax_discount_setup["manager_id"])
        items = [
            {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            {"item_id": tax_discount_setup["croissant_id"], "quantity": 1},
        ]
        res = POSService.calculate_cart(
            items_payload=items,
            discount_type="percent",
            discount_value=25.00,
            user=manager_user,
        )
        assert res["discount_rate"] == 25.00
        assert res["discount_amount"] == 232.50


def test_08_totals_reconcile_after_clearing_discount(app, tax_discount_setup):
    """
    Applying a discount and then setting it to 0 / clearing it fully reverses it
    and reconciles back to the exact initial totals.
    """
    with app.app_context():
        items = [
            {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            {"item_id": tax_discount_setup["croissant_id"], "quantity": 1},
        ]
        initial = POSService.calculate_cart(items_payload=items, discount_value=0)
        discounted = POSService.calculate_cart(items_payload=items, discount_value=10)
        cleared = POSService.calculate_cart(items_payload=items, discount_value=0)

        assert discounted["payable_total"] < initial["payable_total"]
        assert cleared["gross_total"] == initial["gross_total"]
        assert cleared["discount_amount"] == 0.00
        assert cleared["taxable_total"] == initial["taxable_total"]
        assert cleared["tax_total"] == initial["tax_total"]
        assert cleared["payable_total"] == initial["payable_total"]


def test_09_discount_audit_log_written_on_checkout(app, tax_discount_setup):
    """
    Completing a checkout with a discount records an AuditLog entry
    containing user, amount, and invoice details.
    """
    with app.app_context():
        idempotency_key = f"test-disc-audit-{uuid.uuid4().hex}"
        payload = {
            "idempotency_key": idempotency_key,
            "items": [
                {"item_id": tax_discount_setup["latte_id"], "quantity": 1},
            ],
            "discount": {
                "type": "percent",
                "value": 5.00,
            },
            "payment": {
                "mode": "cash",
                "tendered_amount": 1000.00,
            },
        }

        receipt = POSService.process_checkout(
            cashier_id=tax_discount_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=payload,
        )

        assert receipt["discount_amount"] > 0
        invoice_no = receipt["invoice_no"]

        # Check AuditLog table for discount entry
        audit = AuditLog.query.filter_by(
            action_name="discount_applied",
            record_id=invoice_no,
        ).first()

        assert audit is not None
        assert audit.user_id == tax_discount_setup["cashier_id"]
        assert audit.module_name == "pos"
        assert invoice_no in audit.new_val
