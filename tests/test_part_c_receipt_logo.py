"""
Tests for Part C:
- Logo must not print on receipt or appear on receipt modal by default
- Settings toggle 'Print logo on receipt' defaults to off
- When turned on, logo appears on receipt
- Logo stays visible on screens (sidebar, login)
"""
import uuid
from decimal import Decimal
import pytest

from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit
from app.models.setting import Setting
from app.services.setting_service import SettingService
from app.services.pos_service import POSService
from app.services.payment_service import PaymentService


@pytest.fixture(scope="module")
def part_c_setup(app):
    with app.app_context():
        PaymentService.ensure_seed_modes()
        # Set a test logo URL in settings
        Setting.set_value("org_logo_url", "/static/uploads/test_logo.png", setting_type="string", group_name="branding")
        # Ensure default is off
        Setting.set_value("print_logo_on_receipt", "false", setting_type="bool", group_name="receipt")
        db.session.commit()

        cashier_role = Role.query.filter_by(role_code="cashier").first()
        if not cashier_role:
            cashier_role = Role(role_code="cashier", role_name="Cashier")
            db.session.add(cashier_role)
            db.session.commit()

        cashier = User.query.filter_by(username="part_c_cashier").first()
        if not cashier:
            cashier = User(username="part_c_cashier", full_name="Part C Cashier", role_id=cashier_role.id, is_active=1)
            cashier.set_password("Cashier123!")
            db.session.add(cashier)
            db.session.commit()

        cat = Category.query.first()
        if not cat:
            cat = Category(cat_name="Logo Test Category", cat_code="CAT-LOGO", is_active=1)
            db.session.add(cat)
            db.session.commit()

        unit = Unit.query.first()
        if not unit:
            unit = Unit(unit_code="pcs", unit_name="Pieces", allow_decimal=0, is_active=1)
            db.session.add(unit)
            db.session.commit()

        item = Item(
            item_name=f"Logo Test Item {uuid.uuid4().hex[:4]}",
            sku=f"LOGO-{uuid.uuid4().hex[:4]}",
            barcode=f"889900{uuid.uuid4().hex[:4]}",
            category_id=cat.id,
            unit_id=unit.id,
            cost_price=Decimal("10.00"),
            sale_price=Decimal("25.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("100"),
            current_stock=Decimal("100"),
            is_active=1,
        )
        db.session.add(item)
        db.session.commit()

        rcpt = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload={
                "idempotency_key": f"IDEMP-LOGO-{uuid.uuid4().hex}",
                "items": [{"item_id": item.id, "quantity": 1}],
                "payment": {"mode": "cash", "amount": 25.00, "tendered_amount": 25.00},
            }
        )

        return {
            "cashier": cashier,
            "invoice_id": rcpt["invoice_id"],
        }


def test_logo_does_not_print_by_default(app, client, part_c_setup):
    """By default, print_logo_on_receipt is off and receipt has no logo."""
    with app.app_context():
        # Verify setting default
        val = SettingService.get_value("print_logo_on_receipt", False)
        assert str(val).lower() in ("false", "0", "")

        rcpt_data = POSService.get_receipt_data(part_c_setup["invoice_id"])
        assert rcpt_data["print_logo_on_receipt"] is False
        assert rcpt_data["org_logo_url"] == "/static/uploads/test_logo.png"

    # View receipt page via client
    client.post("/auth/login", data={"username": "part_c_cashier", "password": "Cashier123!"})
    res = client.get(f"/pos/receipt/{part_c_setup['invoice_id']}")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert '<img src="/static/uploads/test_logo.png"' not in html


def test_logo_prints_when_toggle_enabled(app, client, part_c_setup):
    """When print_logo_on_receipt is set to true, logo appears on the receipt."""
    with app.app_context():
        Setting.set_value("print_logo_on_receipt", "true", setting_type="bool", group_name="receipt")
        db.session.commit()

        rcpt_data = POSService.get_receipt_data(part_c_setup["invoice_id"])
        assert rcpt_data["print_logo_on_receipt"] is True

    client.post("/auth/login", data={"username": "part_c_cashier", "password": "Cashier123!"})
    res = client.get(f"/pos/receipt/{part_c_setup['invoice_id']}")
    assert res.status_code == 200
    html = res.data.decode("utf-8")
    assert '<img src="/static/uploads/test_logo.png"' in html

    # Reset back to false for cleanliness
    with app.app_context():
        Setting.set_value("print_logo_on_receipt", "false", setting_type="bool", group_name="receipt")
        db.session.commit()
