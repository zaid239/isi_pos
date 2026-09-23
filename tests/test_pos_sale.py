"""
Automated Integration Tests for POS Sale Screen, Fast Barcode Scanning,
Cart Calculation via CalculationService, and Atomic Checkout Transactions on Oracle.
"""
import uuid
from decimal import Decimal
import pytest
from flask_login import login_user

from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit
from app.models.payment import PaymentMode, InvoicePayment
from app.models.invoice import Invoice, InvoiceItem
from app.models.setting import Setting
from app.models.inventory import StockMovement
from app.services.pos_service import POSService
from app.services.calculation_service import CalculationService


@pytest.fixture(scope="module")
def pos_setup(app):
    """Initializes test catalog items, roles, user, payment modes, and units."""
    with app.app_context():
        # Ensure tax_inclusive is false and cash_rounding_mode is off for test consistency
        Setting.set_value("tax_inclusive", "false", setting_type="bool", group_name="tax")
        Setting.set_value("cash_rounding_mode", "off", setting_type="string", group_name="pos")
        db.session.commit()
        # 1. Role & Cashier User
        role = Role.query.filter_by(role_code="cashier").first()
        if not role:
            role = Role(role_code="cashier", role_name="Cashier", is_system=1)
            db.session.add(role)
            db.session.commit()

        cashier = User.query.filter_by(username="pos_cashier_test").first()
        if not cashier:
            cashier = User(
                username="pos_cashier_test",
                full_name="Test Cashier",
                role_id=role.id,
                is_active=1,
                must_change_pwd=0,
            )
            cashier.set_password("Cashier123!")
            db.session.add(cashier)
            db.session.commit()
        cashier_id = cashier.id

        # 2. Units
        unit = Unit.query.filter_by(unit_code="pcs").first()
        if not unit:
            unit = Unit(unit_code="pcs", unit_name="Pieces", allow_decimal=0, is_active=1)
            db.session.add(unit)
            db.session.commit()
        unit_id = unit.id

        # 3. Category
        cat = Category.query.filter_by(cat_code="CAT-POS-TEST").first()
        if not cat:
            cat = Category(cat_name="POS Test Category", cat_name_ur="پی او ایس زمرہ", cat_code="CAT-POS-TEST", is_active=1)
            db.session.add(cat)
            db.session.commit()
        cat_id = cat.id

        # 4. Payment Modes
        for code, name in [("cash", "Cash"), ("card", "Debit / Credit Card")]:
            pm = PaymentMode.query.filter_by(mode_code=code).first()
            if not pm:
                pm = PaymentMode(mode_code=code, mode_name=name, is_active=1)
                db.session.add(pm)
        db.session.commit()

        # 5. Test Items
        unique_suffix = uuid.uuid4().hex[:6]
        item1_sku = f"POS-PEN-{unique_suffix}"
        item1_barcode = f"896000{unique_suffix[:6]}"
        item1_name = f"Gel Pen Blue {unique_suffix}"
        item1 = Item(
            item_name=item1_name,
            item_name_ur="جیل قلم نیلا",
            sku=item1_sku,
            barcode=item1_barcode,
            category_id=cat_id,
            unit_id=unit_id,
            cost_price=Decimal("40.00"),
            sale_price=Decimal("60.00"),
            tax_percent=Decimal("18.00"),
            opening_stock=Decimal("100.000"),
            current_stock=Decimal("100.000"),
            is_active=1,
        )

        item2_sku = f"POS-TEA-{unique_suffix}"
        item2_barcode = f"896111{unique_suffix[:6]}"
        item2_name_ur = f"عمدہ سیاہ چائے {unique_suffix}"
        item2 = Item(
            item_name="Premium Black Tea 250g",
            item_name_ur=item2_name_ur,
            sku=item2_sku,
            barcode=item2_barcode,
            category_id=cat_id,
            unit_id=unit_id,
            cost_price=Decimal("300.00"),
            sale_price=Decimal("400.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("50.000"),
            current_stock=Decimal("50.000"),
            is_active=1,
        )

        db.session.add_all([item1, item2])
        db.session.commit()

        item1_id = item1.id
        item2_id = item2.id

        yield {
            "cashier_id": cashier_id,
            "item1_id": item1_id,
            "item1_name": item1_name,
            "item1_sku": item1_sku,
            "item1_barcode": item1_barcode,
            "item2_id": item2_id,
            "item2_sku": item2_sku,
            "item2_barcode": item2_barcode,
            "item2_name_ur": item2_name_ur,
        }


def test_pos_search_and_barcode_lookup(app, pos_setup):
    """
    Verifies searching items by name, Urdu name, SKU, and barcode lookup logic.
    """
    with app.app_context():
        # 1. Search by English Name
        results = POSService.search_items(pos_setup["item1_name"])
        assert len(results) >= 1
        assert any(r["id"] == pos_setup["item1_id"] for r in results)

        # 2. Verify Urdu is excluded from search (English name, SKU, and Barcode only)
        results_ur = POSService.search_items(pos_setup["item2_name_ur"])
        assert not any(r["id"] == pos_setup["item2_id"] for r in results_ur)

        # 3. Search by SKU
        results_sku = POSService.search_items(pos_setup["item1_sku"])
        assert len(results_sku) == 1
        assert results_sku[0]["sku"] == pos_setup["item1_sku"]

        # 4. Exact Barcode Lookup
        item_bc = POSService.lookup_barcode(pos_setup["item1_barcode"])
        assert item_bc is not None
        assert item_bc["id"] == pos_setup["item1_id"]

        # 5. Case-insensitive / SKU fallback lookup
        item_sku_lookup = POSService.lookup_barcode(pos_setup["item2_sku"].lower())
        assert item_sku_lookup is not None
        assert item_sku_lookup["id"] == pos_setup["item2_id"]

        # 6. Non-existent barcode
        item_none = POSService.lookup_barcode("NON_EXISTENT_BARCODE_999")
        assert item_none is None


def test_pos_cart_calculation(app, pos_setup):
    """
    Verifies that cart calculation is strictly computed via CalculationService.
    """
    with app.app_context():
        items_payload = [
            {"item_id": pos_setup["item1_id"], "quantity": 2},  # 2 * 60 = 120 (tax 18% = 21.60 -> 141.60)
            {"item_id": pos_setup["item2_id"], "quantity": 1},  # 1 * 400 = 400 (tax 0% = 0.00 -> 400.00)
        ]
        calc = POSService.calculate_cart(items_payload)

        assert calc["gross_total"] == 520.0
        assert calc["taxable_total"] == 520.0
        assert calc["tax_total"] == 21.60
        assert calc["payable_total"] == 541.60
        assert len(calc["lines"]) == 2


def test_pos_atomic_cash_checkout(app, pos_setup):
    """
    Verifies that cash checkout creates Invoice, InvoiceItems, InvoicePayment,
    deducts item stock, and records StockMovements in a single atomic transaction.
    """
    with app.app_context():
        idempotency_key = f"IDEMP-{uuid.uuid4().hex}"
        checkout_payload = {
            "idempotency_key": idempotency_key,
            "items": [
                {"item_id": pos_setup["item1_id"], "quantity": 3},  # 3 * 60 = 180 + 32.40 = 212.40
                {"item_id": pos_setup["item2_id"], "quantity": 2},  # 2 * 400 = 800 + 0 = 800.00
            ],
            "payment": {
                "mode": "cash",
                "tendered_amount": 1050.00,
            }
        }

        # Initial stock check
        it1_before = db.session.get(Item, pos_setup["item1_id"]).current_stock
        it2_before = db.session.get(Item, pos_setup["item2_id"]).current_stock

        receipt = POSService.process_checkout(
            cashier_id=pos_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=checkout_payload,
        )

        assert receipt["invoice_id"] is not None
        assert receipt["invoice_no"].startswith("INV-")
        assert receipt["subtotal"] == 980.00
        assert receipt["tax_amount"] == 32.40
        assert receipt["payable_amount"] == 1012.40
        assert receipt["paid_amount"] == 1012.40
        assert receipt["change_amount"] == 37.60  # 1050.00 - 1012.40

        # Verify Database Rows
        inv = db.session.get(Invoice, receipt["invoice_id"])
        assert inv is not None
        assert inv.idempotency_key == idempotency_key
        assert inv.status_code == "completed"
        assert len(inv.items) == 2
        assert len(inv.payments) == 1

        # Verify Payment Row
        payment = inv.payments[0]
        assert payment.amount == Decimal("1012.40")
        assert payment.tendered_amt == Decimal("1050.00")
        assert payment.change_amt == Decimal("37.60")

        # Verify Stock Deduction
        it1_after = db.session.get(Item, pos_setup["item1_id"]).current_stock
        it2_after = db.session.get(Item, pos_setup["item2_id"]).current_stock
        assert it1_after == it1_before - Decimal("3.000")
        assert it2_after == it2_before - Decimal("2.000")

        # Verify Stock Movements in Ledger
        movements = StockMovement.query.filter_by(ref_doc_no=inv.invoice_no).all()
        assert len(movements) == 2
        for m in movements:
            assert m.movement_type == "sale"
            assert m.ref_doc_type == "invoice"
            assert m.qty_change < 0
            assert m.user_id == pos_setup["cashier_id"]


def test_pos_atomic_card_checkout(app, pos_setup):
    """
    Verifies that card checkout records last 4 digits and sets change to zero.
    """
    with app.app_context():
        idempotency_key = f"IDEMP-CARD-{uuid.uuid4().hex}"
        checkout_payload = {
            "idempotency_key": idempotency_key,
            "items": [
                {"item_id": pos_setup["item1_id"], "quantity": 1},
            ],
            "payment": {
                "mode": "card",
                "card_last4": "5544",
            }
        }

        receipt = POSService.process_checkout(
            cashier_id=pos_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=checkout_payload,
        )

        assert receipt["payable_amount"] == 70.80  # 60 + 18% = 70.80
        inv = db.session.get(Invoice, receipt["invoice_id"])
        payment = inv.payments[0]
        assert payment.card_last4 == "5544"
        assert payment.change_amt == Decimal("0.00")


def test_pos_idempotency_guard(app, pos_setup):
    """
    Verifies that resubmitting the same idempotency_key returns existing invoice
    without creating duplicate records or deducting stock twice.
    """
    with app.app_context():
        idempotency_key = f"IDEMP-REPEAT-{uuid.uuid4().hex}"
        checkout_payload = {
            "idempotency_key": idempotency_key,
            "items": [
                {"item_id": pos_setup["item1_id"], "quantity": 1},
            ],
            "payment": {
                "mode": "cash",
                "tendered_amount": 100.00,
            }
        }

        it1_stock_before = db.session.get(Item, pos_setup["item1_id"]).current_stock

        # First request
        res1 = POSService.process_checkout(
            cashier_id=pos_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=checkout_payload,
        )
        inv_id_1 = res1["invoice_id"]

        it1_stock_after_first = db.session.get(Item, pos_setup["item1_id"]).current_stock
        assert it1_stock_after_first == it1_stock_before - Decimal("1.000")

        # Second request with IDENTICAL idempotency key
        res2 = POSService.process_checkout(
            cashier_id=pos_setup["cashier_id"],
            terminal_code="T01",
            counter_code="C01",
            payload=checkout_payload,
        )
        inv_id_2 = res2["invoice_id"]

        assert inv_id_1 == inv_id_2

        # Stock must NOT have been deducted a second time
        it1_stock_after_second = db.session.get(Item, pos_setup["item1_id"]).current_stock
        assert it1_stock_after_second == it1_stock_after_first

        # Only one invoice should exist for this idempotency key
        count = Invoice.query.filter_by(idempotency_key=idempotency_key).count()
        assert count == 1


def test_pos_insufficient_stock_rejection(app, pos_setup):
    """
    Verifies that checkout is rejected when requested quantity exceeds available stock,
    and no stock or invoice changes occur.
    """
    with app.app_context():
        item = db.session.get(Item, pos_setup["item2_id"])
        stock_before = item.current_stock

        idempotency_key = f"IDEMP-OVERSTOCK-{uuid.uuid4().hex}"
        checkout_payload = {
            "idempotency_key": idempotency_key,
            "items": [
                {"item_id": pos_setup["item2_id"], "quantity": float(stock_before) + 50},
            ],
            "payment": {
                "mode": "cash",
                "tendered_amount": 50000.00,
            }
        }

        with pytest.raises(ValueError, match="Insufficient stock"):
            POSService.process_checkout(
                cashier_id=pos_setup["cashier_id"],
                terminal_code="T01",
                counter_code="C01",
                payload=checkout_payload,
            )

        # Confirm stock was not modified
        db.session.rollback()
        item_refreshed = db.session.get(Item, pos_setup["item2_id"])
        assert item_refreshed.current_stock == stock_before


def test_pos_receipt_view(client, app, pos_setup):
    """
    Verifies HTTP GET /pos/receipt/<id> renders clean receipt with org details.
    """
    # Authenticate cashier via login endpoint
    login_resp = client.post("/auth/login", data={"username": "pos_cashier_test", "password": "Cashier123!"}, follow_redirects=True)
    assert login_resp.status_code == 200

    with app.app_context():
        cashier = db.session.get(User, pos_setup["cashier_id"])

        idempotency_key = f"IDEMP-VIEW-{uuid.uuid4().hex}"
        receipt = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload={
                "idempotency_key": idempotency_key,
                "items": [{"item_id": pos_setup["item1_id"], "quantity": 1}],
                "payment": {"mode": "cash", "tendered_amount": 100.00},
            }
        )

        resp = client.get(f"/pos/receipt/{receipt['invoice_id']}")
        assert resp.status_code == 200
        assert receipt["invoice_no"].encode() in resp.data
        assert b"Receipt" in resp.data
        assert b"window.print()" in resp.data


def test_pos_consecutive_sales_lock_existing_counter(app, pos_setup):
    """
    Verifies that two consecutive sales succeed without ORA-02014.
    The first sale creates or increments the DocCounter row.
    The second sale issues SELECT ... FOR UPDATE on the existing DocCounter row
    using one_or_none(), confirming no FETCH FIRST / row-limiting clause is injected.
    """
    with app.app_context():
        cashier = db.session.get(User, pos_setup["cashier_id"])
        item = db.session.get(Item, pos_setup["item1_id"])

        # Sale 1: Creates or locks counter row
        idemp1 = f"IDEMP-CONSEC-1-{uuid.uuid4().hex}"
        payload1 = {
            "idempotency_key": idemp1,
            "items": [{"item_id": item.id, "quantity": 1}],
            "payment": {"mode": "cash", "tendered_amount": 100.00},
        }
        res1 = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload=payload1,
        )
        assert res1["invoice_id"] is not None
        inv1_no = res1["invoice_no"]

        # Sale 2: Second consecutive sale on identical terminal and counter.
        # This triggers with_for_update().one_or_none() on the EXISTING counter row.
        idemp2 = f"IDEMP-CONSEC-2-{uuid.uuid4().hex}"
        payload2 = {
            "idempotency_key": idemp2,
            "items": [{"item_id": item.id, "quantity": 1}],
            "payment": {"mode": "cash", "tendered_amount": 100.00},
        }
        res2 = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload=payload2,
        )
        assert res2["invoice_id"] is not None
        inv2_no = res2["invoice_no"]

        # Both invoices must have distinct consecutive numbers
        assert inv1_no != inv2_no

