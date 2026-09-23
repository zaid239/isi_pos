"""
Tests for Part A:
- Payment Modes management (Cash cannot be deactivated)
- Dynamic modes seeding (Cash, Card, EasyPaisa, JazzCash, Bank Transfer / Raast)
- Split payment execution
- Cash over-tender with change
- Non-cash over-payment refusal
- Rule 6 enforcement (sum of payments == payable total)
- Remarks saved to invoices.notes and printed/rendered escaped on receipt
"""
import uuid
from decimal import Decimal
import pytest

from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit
from app.models.payment import PaymentMode, InvoicePayment
from app.models.invoice import Invoice
from app.models.setting import Setting
from app.services.payment_service import PaymentService
from app.services.pos_service import POSService


@pytest.fixture(scope="module")
def part_a_setup(app):
    with app.app_context():
        PaymentService.ensure_seed_modes()
        Setting.set_value("tax_inclusive", "false", setting_type="bool", group_name="tax")
        Setting.set_value("cash_rounding_mode", "off", setting_type="string", group_name="pos")

        # Cashier user
        role = Role.query.filter_by(role_code="cashier").first()
        if not role:
            role = Role(role_code="cashier", role_name="Cashier", is_system=1)
            db.session.add(role)
            db.session.commit()

        cashier = User.query.filter_by(username="part_a_cashier").first()
        if not cashier:
            cashier = User(
                username="part_a_cashier",
                full_name="Part A Cashier",
                role_id=role.id,
                is_active=1,
                must_change_pwd=0,
            )
            cashier.set_password("Cashier123!")
            db.session.add(cashier)
            db.session.commit()

        # Super Admin
        admin_role = Role.query.filter_by(role_code="super_admin").first()
        if not admin_role:
            admin_role = Role(role_code="super_admin", role_name="Super Admin", is_system=1)
            db.session.add(admin_role)
            db.session.commit()

        admin = User.query.filter_by(username="part_a_admin").first()
        if not admin:
            admin = User(
                username="part_a_admin",
                full_name="Part A Admin",
                role_id=admin_role.id,
                is_active=1,
                must_change_pwd=0,
            )
            admin.set_password("Admin123!")
            db.session.add(admin)
            db.session.commit()

        # Unit
        unit = Unit.query.filter_by(unit_code="pcs").first()
        if not unit:
            unit = Unit(unit_code="pcs", unit_name="Pieces", allow_decimal=0, is_active=1)
            db.session.add(unit)
            db.session.commit()

        # Category
        cat = Category.query.filter_by(cat_code="CAT-SPLIT-TEST").first()
        if not cat:
            cat = Category(cat_name="Split Test Cat", cat_code="CAT-SPLIT-TEST", is_active=1)
            db.session.add(cat)
            db.session.commit()

        # Test Item
        suffix = uuid.uuid4().hex[:6]
        item = Item(
            item_name=f"Split Test Item {suffix}",
            sku=f"SKU-SPLIT-{suffix}",
            barcode=f"991100{suffix}",
            category_id=cat.id,
            unit_id=unit.id,
            cost_price=Decimal("50.00"),
            sale_price=Decimal("100.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("1000.000"),
            current_stock=Decimal("1000.000"),
            is_active=1,
        )
        db.session.add(item)
        db.session.commit()

        return {
            "cashier_id": cashier.id,
            "admin_id": admin.id,
            "item_id": item.id,
        }


def test_payment_modes_seed_and_cash_cannot_be_deactivated(app, part_a_setup):
    """Test seed modes exist and cash deactivation is strictly prevented."""
    with app.app_context():
        modes = PaymentService.get_active_modes()
        codes = [m.mode_code for m in modes]
        assert "cash" in codes
        assert "card" in codes
        assert "easypaisa" in codes
        assert "jazzcash" in codes
        assert "bank_transfer" in codes

        cash_mode = PaymentMode.query.filter_by(mode_code="cash").first()
        assert cash_mode is not None

        # Attempt to toggle cash mode active status must fail
        with pytest.raises(ValueError, match="Cash payment mode cannot be deactivated"):
            PaymentService.toggle_mode_active(cash_mode.id)


def test_single_mode_checkout(app, part_a_setup):
    """Test single mode checkout completes in one step and saves 1 invoice_payments row."""
    with app.app_context():
        payload = {
            "idempotency_key": f"IDEMP-SINGLE-{uuid.uuid4().hex}",
            "items": [{"item_id": part_a_setup["item_id"], "quantity": 1}],
            "payment": {"mode": "cash", "amount": 100.00, "tendered_amount": 100.00},
            "remarks": "Single mode test",
        }
        receipt = POSService.process_checkout(
            cashier_id=part_a_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=payload,
        )
        assert receipt["payable_amount"] == 100.00
        assert receipt["paid_amount"] == 100.00
        assert receipt["change_amount"] == 0.00
        assert len(receipt["payments"]) == 1
        assert receipt["payments"][0]["mode_code"] == "cash"
        assert receipt["remarks"] == "Single mode test"


def test_cash_and_easypaisa_split(app, part_a_setup):
    """Test split payment: Cash + EasyPaisa."""
    with app.app_context():
        payload = {
            "idempotency_key": f"IDEMP-SPLIT-{uuid.uuid4().hex}",
            "items": [{"item_id": part_a_setup["item_id"], "quantity": 2}],  # Total 200.00
            "payments": [
                {"mode": "easypaisa", "amount": 120.00, "reference": "EP-TID-98765"},
                {"mode": "cash", "amount": 80.00, "tendered_amount": 80.00},
            ],
            "remarks": "Split cash and easypaisa",
        }
        receipt = POSService.process_checkout(
            cashier_id=part_a_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=payload,
        )
        assert receipt["payable_amount"] == 200.00
        assert len(receipt["payments"]) == 2

        ep_line = next(p for p in receipt["payments"] if p["mode_code"] == "easypaisa")
        assert ep_line["amount"] == 120.00
        assert ep_line["reference"] == "EP-TID-98765"

        cash_line = next(p for p in receipt["payments"] if p["mode_code"] == "cash")
        assert cash_line["amount"] == 80.00
        assert cash_line["change_amt"] == 0.00


def test_cash_overtender_with_change(app, part_a_setup):
    """Test Cash line may be over-tendered and excess becomes change."""
    with app.app_context():
        payload = {
            "idempotency_key": f"IDEMP-CHANGE-{uuid.uuid4().hex}",
            "items": [{"item_id": part_a_setup["item_id"], "quantity": 1}],  # Total 100.00
            "payments": [
                {"mode": "cash", "amount": 100.00, "tendered_amount": 500.00},
            ],
        }
        receipt = POSService.process_checkout(
            cashier_id=part_a_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=payload,
        )
        assert receipt["payable_amount"] == 100.00
        assert receipt["change_amount"] == 400.00
        assert receipt["payments"][0]["tendered_amt"] == 500.00
        assert receipt["payments"][0]["change_amt"] == 400.00


def test_non_cash_overpayment_refused(app, part_a_setup):
    """Test non-cash lines cannot exceed remaining amount."""
    with app.app_context():
        payload = {
            "idempotency_key": f"IDEMP-OVERPAY-{uuid.uuid4().hex}",
            "items": [{"item_id": part_a_setup["item_id"], "quantity": 1}],  # Total 100.00
            "payments": [
                {"mode": "easypaisa", "amount": 150.00, "reference": "EP-999"},
            ],
        }
        with pytest.raises(ValueError, match="cannot exceed remaining due"):
            POSService.process_checkout(
                cashier_id=part_a_setup["cashier_id"],
                terminal_code="T01",
                counter_code="C01",
                payload=payload,
            )


def test_sum_not_matching_payable_refused(app, part_a_setup):
    """Test Rule 6: sum of applied amounts must equal payable total."""
    with app.app_context():
        # Short payment
        payload_short = {
            "idempotency_key": f"IDEMP-SHORT-{uuid.uuid4().hex}",
            "items": [{"item_id": part_a_setup["item_id"], "quantity": 1}],  # Total 100.00
            "payments": [
                {"mode": "cash", "amount": 80.00, "tendered_amount": 80.00},
            ],
        }
        with pytest.raises(ValueError, match="does not equal payable amount"):
            POSService.process_checkout(
                cashier_id=part_a_setup["cashier_id"],
                terminal_code="T01",
                counter_code="C01",
                payload=payload_short,
            )


def test_remarks_saved_and_printed(app, client, part_a_setup):
    """Test remarks saved to invoices.notes and printed/rendered escaped on receipt."""
    with app.app_context():
        # Remarks with potential XSS characters
        remarks_text = "<script>alert('xss')</script> & Special Note 123"
        payload = {
            "idempotency_key": f"IDEMP-REM-{uuid.uuid4().hex}",
            "items": [{"item_id": part_a_setup["item_id"], "quantity": 1}],
            "payment": {"mode": "cash", "amount": 100.00, "tendered_amount": 100.00},
            "remarks": remarks_text,
        }
        receipt = POSService.process_checkout(
            cashier_id=part_a_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=payload,
        )
        invoice = Invoice.query.filter_by(id=receipt["invoice_id"]).first()
        assert invoice.notes == remarks_text

        # Test receipt page rendering
        # Login cashier
        client.post("/auth/login", data={"username": "part_a_cashier", "password": "Cashier123!"})
        res = client.get(f"/pos/receipt/{invoice.id}")
        assert res.status_code == 200
        html = res.data.decode("utf-8")
        assert "Remarks:" in html
        # Script tags must be escaped, not rendered as raw HTML
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "Special Note 123" in html
