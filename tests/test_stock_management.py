"""
Integration Tests for Stock Management:
- Untracked items (is_stock_tracked == 0) skip stock check & movements during checkout.
- Tracked items (is_stock_tracked == 1) enforce stock check & deduction.
- Stock In multi-line receiving with weighted average costing.
- Stock Out refusing negative stock.
- Physical count adjustment recording delta and updating stock.
- Item ledger reconciliation (running balance matches current_stock).
- Double-submit protection via idempotency key.
- Role-based security (Cashier 403 on stock endpoints).
"""
import uuid
from decimal import Decimal
import pytest

from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit
from app.models.payment import PaymentMode
from app.models.inventory import StockMovement, StockAdjustment
from app.services.pos_service import POSService
from app.services.stock_service import StockService


@pytest.fixture(scope="module")
def stock_fixture(app):
    """Sets up roles, users, categories, units, and items for stock tests."""
    with app.app_context():
        # Roles
        cashier_role = Role.query.filter_by(role_code="cashier").first()
        if not cashier_role:
            cashier_role = Role(role_code="cashier", role_name="Cashier", is_system=1)
            db.session.add(cashier_role)

        manager_role = Role.query.filter_by(role_code="manager").first()
        if not manager_role:
            manager_role = Role(role_code="manager", role_name="Manager", is_system=1)
            db.session.add(manager_role)
        db.session.commit()

        # Users
        u_suffix = uuid.uuid4().hex[:6]
        cashier = User(
            username=f"cashier_stock_{u_suffix}",
            full_name="Stock Test Cashier",
            role_id=cashier_role.id,
            is_active=1,
            must_change_pwd=0,
        )
        cashier.set_password("Cashier123!")

        manager = User(
            username=f"manager_stock_{u_suffix}",
            full_name="Stock Test Manager",
            role_id=manager_role.id,
            is_active=1,
            must_change_pwd=0,
        )
        manager.set_password("Manager123!")

        db.session.add_all([cashier, manager])
        db.session.commit()

        # Unit
        unit = Unit.query.filter_by(unit_code="pcs").first()
        if not unit:
            unit = Unit(unit_code="pcs", unit_name="Pieces", allow_decimal=0, is_active=1)
            db.session.add(unit)
            db.session.commit()

        # Category
        cat = Category.query.filter_by(cat_code="CAT-STOCK-TEST").first()
        if not cat:
            cat = Category(cat_name="Stock Test Cat", cat_code="CAT-STOCK-TEST", is_active=1)
            db.session.add(cat)
            db.session.commit()

        # Payment Mode
        pm = PaymentMode.query.filter_by(mode_code="cash").first()
        if not pm:
            pm = PaymentMode(mode_code="cash", mode_name="Cash", is_active=1)
            db.session.add(pm)
            db.session.commit()

        # Tracked item with 0 stock
        tracked_item = Item(
            sku=f"TRK-ZERO-{u_suffix}",
            item_name=f"Tracked Zero Stock Item {u_suffix}",
            category_id=cat.id,
            unit_id=unit.id,
            cost_price=Decimal("50.00"),
            sale_price=Decimal("100.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("0.000"),
            current_stock=Decimal("0.000"),
            reorder_level=Decimal("5.000"),
            is_stock_tracked=1,
            is_active=1,
        )

        # Untracked item with 0 stock (e.g. coffee / made-to-order drink)
        untracked_item = Item(
            sku=f"UNTRK-ZERO-{u_suffix}",
            item_name=f"Untracked Drink Item {u_suffix}",
            category_id=cat.id,
            unit_id=unit.id,
            cost_price=Decimal("30.00"),
            sale_price=Decimal("80.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("0.000"),
            current_stock=Decimal("0.000"),
            reorder_level=Decimal("0.000"),
            is_stock_tracked=0,
            is_active=1,
        )

        # General stock test item
        general_item = Item(
            sku=f"STK-GEN-{u_suffix}",
            item_name=f"General Stock Item {u_suffix}",
            category_id=cat.id,
            unit_id=unit.id,
            cost_price=Decimal("100.00"),
            sale_price=Decimal("150.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("10.000"),
            current_stock=Decimal("10.000"),
            reorder_level=Decimal("5.000"),
            is_stock_tracked=1,
            is_active=1,
        )

        db.session.add_all([tracked_item, untracked_item, general_item])
        db.session.commit()

        yield {
            "cashier": cashier,
            "manager": manager,
            "tracked_item": tracked_item,
            "untracked_item": untracked_item,
            "general_item": general_item,
            "cat_id": cat.id,
            "unit_id": unit.id,
        }


def test_tracked_item_zero_stock_refused(app, stock_fixture):
    """Proves that a tracked item (is_stock_tracked == 1) with zero stock is refused at checkout."""
    with app.app_context():
        tracked_item = stock_fixture["tracked_item"]
        cashier = stock_fixture["cashier"]

        payload = {
            "idempotency_key": f"test-fail-tracked-{uuid.uuid4()}",
            "items": [{"item_id": tracked_item.id, "quantity": 1}],
            "payment": {"mode": "cash", "amount": 100.00, "tendered_amount": 100.00},
        }

        with pytest.raises(ValueError) as exc_info:
            POSService.process_checkout(
                cashier_id=cashier.id,
                terminal_code="T01",
                counter_code="C01",
                payload=payload,
            )

        assert "Insufficient stock" in str(exc_info.value)
        # Verify item stock remained 0
        db_item = db.session.get(Item, tracked_item.id)
        assert db_item.current_stock == Decimal("0.000")


def test_untracked_item_zero_stock_sells_without_movement(app, stock_fixture):
    """Proves that an untracked item (is_stock_tracked == 0) sells with zero stock and creates no stock movement."""
    with app.app_context():
        untracked_item = stock_fixture["untracked_item"]
        cashier = stock_fixture["cashier"]

        payload = {
            "idempotency_key": f"test-pass-untracked-{uuid.uuid4()}",
            "items": [{"item_id": untracked_item.id, "quantity": 2}],
            "payment": {"mode": "cash", "amount": 160.00, "tendered_amount": 160.00},
        }

        receipt = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload=payload,
        )

        assert receipt["invoice_no"] is not None
        # Verify item current_stock is still 0 (not deducted)
        db_item = db.session.get(Item, untracked_item.id)
        assert db_item.current_stock == Decimal("0.000")

        # Verify no StockMovement row was written for this untracked item
        movements = StockMovement.query.filter_by(item_id=untracked_item.id).all()
        assert len(movements) == 0


def test_stock_in_weighted_average_cost(app, stock_fixture):
    """
    Tests Stock In receiving with weighted average costing:
    old_qty = 10, old_cost = 100.00
    in_qty = 10, in_cost = 200.00
    new_cost = (10 * 100 + 10 * 200) / (10 + 10) = 3000 / 20 = 150.00
    """
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        manager = stock_fixture["manager"]

        assert general_item.current_stock == Decimal("10.000")
        assert general_item.cost_price == Decimal("100.00")

        idemp = f"test-in-{uuid.uuid4()}"
        lines = [
            {
                "item_id": general_item.id,
                "quantity": Decimal("10.000"),
                "cost_price": Decimal("200.00"),
                "notes": "Batch A",
            }
        ]

        movements = StockService.record_stock_in(
            user_id=manager.id,
            movement_date="2026-09-21",
            supplier_name="Fresh Mills Ltd",
            reference_no="PO-1001",
            lines=lines,
            notes="Morning delivery",
            idempotency_key=idemp,
        )

        assert len(movements) == 1
        mv = movements[0]
        assert mv.qty_change == Decimal("10.000")
        assert mv.qty_after == Decimal("20.000")
        assert mv.movement_type == "stock_in"

        # Check item updated state
        db.session.refresh(general_item)
        assert general_item.current_stock == Decimal("20.000")
        # Formula check: (10*100 + 10*200) / 20 = 150.00
        assert general_item.cost_price == Decimal("150.00")


def test_stock_in_without_cost_preserves_existing_cost(app, stock_fixture):
    """Tests Stock In receiving when cost is left blank preserves existing cost_price."""
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        manager = stock_fixture["manager"]
        cost_before = general_item.cost_price

        idemp = str(uuid.uuid4())
        lines = [
            {
                "item_id": general_item.id,
                "quantity": Decimal("5.000"),
                "cost_price": None,  # Cost not entered
            }
        ]

        StockService.record_stock_in(
            user_id=manager.id,
            lines=lines,
            idempotency_key=idemp,
        )

        db.session.refresh(general_item)
        assert general_item.cost_price == cost_before


def test_stock_out_negative_stock_refused(app, stock_fixture):
    """Tests that Stock Out cannot take stock below zero."""
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        manager = stock_fixture["manager"]

        current = general_item.current_stock
        with pytest.raises(ValueError) as exc_info:
            StockService.record_stock_out(
                user_id=manager.id,
                item_id=general_item.id,
                quantity=current + Decimal("1.000"),
                reason="wastage",
            )

        assert "Cannot take stock below zero" in str(exc_info.value)


def test_stock_out_valid_deduction(app, stock_fixture):
    """Tests that valid Stock Out decrements stock and writes StockMovement."""
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        manager = stock_fixture["manager"]

        qty_before = general_item.current_stock
        mv = StockService.record_stock_out(
            user_id=manager.id,
            item_id=general_item.id,
            quantity=Decimal("3.000"),
            reason="damage",
            notes="Broken package during transit",
        )

        assert mv.qty_change == Decimal("-3.000")
        assert mv.movement_type == "stock_out"
        assert mv.ref_doc_type == "damage"

        db.session.refresh(general_item)
        assert general_item.current_stock == qty_before - Decimal("3.000")


def test_stock_adjustment_physical_count(app, stock_fixture):
    """Tests physical count adjustment calculating delta and setting stock."""
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        manager = stock_fixture["manager"]

        old_stock = general_item.current_stock
        # Physical count reports 30.000
        new_count = Decimal("30.000")
        adj, mv = StockService.record_adjustment(
            user_id=manager.id,
            item_id=general_item.id,
            counted_qty=new_count,
            reason="Monthly physical inventory count audit",
            notes="Warehouse bay 4 recount",
        )

        assert adj.adj_no is not None
        assert adj.adj_qty == new_count - old_stock
        assert mv.qty_after == new_count
        assert mv.movement_type == "adjustment"

        db.session.refresh(general_item)
        assert general_item.current_stock == new_count


def test_item_ledger_reconciliation(app, stock_fixture):
    """Tests that Item Ledger running balance reconciles exactly to current_stock."""
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        ledger_data = StockService.get_item_ledger(item_id=general_item.id)

        # Baseline opening row exists
        assert len(ledger_data["rows"]) >= 1
        assert ledger_data["rows"][0]["is_opening"] is True

        # Closing balance equals item current_stock
        assert ledger_data["closing_balance"] == general_item.current_stock


def test_idempotency_protection(app, stock_fixture):
    """Tests that sending duplicate idempotency key does not duplicate stock additions."""
    with app.app_context():
        general_item = db.session.get(Item, stock_fixture["general_item"].id)
        manager = stock_fixture["manager"]

        stock_before = general_item.current_stock
        idemp = str(uuid.uuid4())
        lines = [{"item_id": general_item.id, "quantity": Decimal("5.000")}]

        # First call
        res1 = StockService.record_stock_in(user_id=manager.id, lines=lines, idempotency_key=idemp)
        db.session.refresh(general_item)
        assert general_item.current_stock == stock_before + Decimal("5.000")

        # Second call with SAME idempotency key
        res2 = StockService.record_stock_in(user_id=manager.id, lines=lines, idempotency_key=idemp)
        db.session.refresh(general_item)
        # Current stock must NOT increase again!
        assert general_item.current_stock == stock_before + Decimal("5.000")


def test_cashier_role_blocked_on_stock_screens(client, stock_fixture):
    """Tests that Cashier role receives 403 Forbidden on stock routes, while Manager has access."""
    cashier = stock_fixture["cashier"]
    manager = stock_fixture["manager"]

    # 1. Login as cashier
    client.post("/auth/login", data={"username": cashier.username, "password": "Cashier123!"})

    resp = client.get("/stock/")
    assert resp.status_code == 403

    resp = client.get("/stock/in")
    assert resp.status_code == 403

    resp = client.get("/stock/out")
    assert resp.status_code == 403

    resp = client.get("/stock/adjustment")
    assert resp.status_code == 403

    # 2. Login as manager and verify access
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": manager.username, "password": "Manager123!"})
    resp_mgr = client.get("/stock/")
    assert resp_mgr.status_code == 200
