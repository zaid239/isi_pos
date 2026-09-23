"""
Tests for Part B: Sales Report
- Role restriction: Manager and Super Admin only, Cashier gets 403
- Filters: each filter alone, two combined
- Totals row equals sum of rows
- Split invoice counted correctly under both modes in payment breakdown and day-wise
- Today's total matches Dashboard today's sales exactly
- Excel and CSV exports
"""
import io
import uuid
from decimal import Decimal
from datetime import timedelta
import pytest
import openpyxl

from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit
from app.models.payment import PaymentMode, InvoicePayment
from app.models.invoice import Invoice
from app.models.setting import Setting
from app.services.payment_service import PaymentService
from app.services.pos_service import POSService
from app.services.dashboard_service import DashboardService
from app.services.sales_report_service import SalesReportService
from app.utils.timezone import utc_now, get_business_date


@pytest.fixture(scope="module")
def report_setup(app):
    with app.app_context():
        PaymentService.ensure_seed_modes()
        Setting.set_value("tax_inclusive", "false", setting_type="bool", group_name="tax")
        Setting.set_value("cash_rounding_mode", "off", setting_type="string", group_name="pos")

        # Roles
        cashier_role = Role.query.filter_by(role_code="cashier").first()
        if not cashier_role:
            cashier_role = Role(role_code="cashier", role_name="Cashier")
            db.session.add(cashier_role)
            db.session.commit()

        manager_role = Role.query.filter_by(role_code="manager").first()
        if not manager_role:
            manager_role = Role(role_code="manager", role_name="Manager")
            db.session.add(manager_role)
            db.session.commit()

        admin_role = Role.query.filter_by(role_code="super_admin").first()
        if not admin_role:
            admin_role = Role(role_code="super_admin", role_name="Super Admin")
            db.session.add(admin_role)
            db.session.commit()

        # Users
        cashier = User.query.filter_by(username="rep_cashier").first()
        if not cashier:
            cashier = User(username="rep_cashier", full_name="Report Cashier", role_id=cashier_role.id, is_active=1)
            cashier.set_password("Cashier123!")
            db.session.add(cashier)

        manager = User.query.filter_by(username="rep_manager").first()
        if not manager:
            manager = User(username="rep_manager", full_name="Report Manager", role_id=manager_role.id, is_active=1)
            manager.set_password("Manager123!")
            db.session.add(manager)

        admin = User.query.filter_by(username="rep_admin").first()
        if not admin:
            admin = User(username="rep_admin", full_name="Report Admin", role_id=admin_role.id, is_active=1)
            admin.set_password("Admin123!")
            db.session.add(admin)

        db.session.commit()

        # Categories
        cat_a = Category.query.filter_by(cat_code="CAT-REPA").first()
        if not cat_a:
            cat_a = Category(cat_name="Report Cat A", cat_code="CAT-REPA", is_active=1)
            db.session.add(cat_a)

        cat_b = Category.query.filter_by(cat_code="CAT-REPB").first()
        if not cat_b:
            cat_b = Category(cat_name="Report Cat B", cat_code="CAT-REPB", is_active=1)
            db.session.add(cat_b)

        unit = Unit.query.filter_by(unit_code="pcs").first()
        if not unit:
            unit = Unit(unit_code="pcs", unit_name="Pieces", allow_decimal=0, is_active=1)
            db.session.add(unit)

        db.session.commit()

        # Items
        sfx = uuid.uuid4().hex[:5]
        item_a = Item(
            item_name=f"Report Item Alpha {sfx}",
            sku=f"REP-A-{sfx}",
            barcode=f"110022{sfx}",
            category_id=cat_a.id,
            unit_id=unit.id,
            cost_price=Decimal("20.00"),
            sale_price=Decimal("50.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("500"),
            current_stock=Decimal("500"),
            is_active=1,
        )
        item_b = Item(
            item_name=f"Report Item Beta {sfx}",
            sku=f"REP-B-{sfx}",
            barcode=f"330044{sfx}",
            category_id=cat_b.id,
            unit_id=unit.id,
            cost_price=Decimal("40.00"),
            sale_price=Decimal("100.00"),
            tax_percent=Decimal("0.00"),
            opening_stock=Decimal("500"),
            current_stock=Decimal("500"),
            is_active=1,
        )
        db.session.add_all([item_a, item_b])
        db.session.commit()

        # Create 3 distinct checkout transactions:
        # 1. Cash sale with Item A (50.00)
        rcpt1 = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload={
                "idempotency_key": f"IDEMP-R1-{uuid.uuid4().hex}",
                "items": [{"item_id": item_a.id, "quantity": 1}],
                "payment": {"mode": "cash", "amount": 50.00, "tendered_amount": 50.00},
                "remarks": "Cash sale for report",
            }
        )

        # 2. Split sale with Item B (200.00): 80 Cash + 120 EasyPaisa
        rcpt2 = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload={
                "idempotency_key": f"IDEMP-R2-{uuid.uuid4().hex}",
                "items": [{"item_id": item_b.id, "quantity": 2}],
                "payments": [
                    {"mode": "cash", "amount": 80.00, "tendered_amount": 80.00},
                    {"mode": "easypaisa", "amount": 120.00, "reference": "EP-REP-123"},
                ],
                "remarks": "Split sale for report",
            }
        )

        # 3. Card sale with Item A & B (150.00): Card only
        rcpt3 = POSService.process_checkout(
            cashier_id=cashier.id,
            terminal_code="T01",
            counter_code="C01",
            payload={
                "idempotency_key": f"IDEMP-R3-{uuid.uuid4().hex}",
                "items": [
                    {"item_id": item_a.id, "quantity": 1},
                    {"item_id": item_b.id, "quantity": 1},
                ],
                "payment": {"mode": "card", "amount": 150.00, "card_last4": "1234"},
                "remarks": "Card sale for report",
            }
        )

        return {
            "cashier_id": cashier.id,
            "manager_id": manager.id,
            "admin_id": admin.id,
            "cat_a_id": cat_a.id,
            "cat_b_id": cat_b.id,
            "item_a_id": item_a.id,
            "item_b_id": item_b.id,
            "inv1_no": rcpt1["invoice_no"],
            "inv2_no": rcpt2["invoice_no"],
            "inv3_no": rcpt3["invoice_no"],
        }


def test_01_cashier_receives_403(client, report_setup):
    """Cashier gets 403 on /reports/sales, while Manager and Admin can access."""
    # Login cashier
    client.post("/auth/login", data={"username": "rep_cashier", "password": "Cashier123!"})
    res = client.get("/reports/sales")
    assert res.status_code == 403

    # Logout cashier and login manager
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "rep_manager", "password": "Manager123!"})
    res_mgr = client.get("/reports/sales")
    assert res_mgr.status_code == 200

    # Logout manager and login super admin
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "rep_admin", "password": "Admin123!"})
    res_adm = client.get("/reports/sales")
    assert res_adm.status_code == 200



def test_02_each_filter_alone(app, report_setup):
    """Test filtering by each criteria alone."""
    with app.app_context():
        # A. Filter by invoice number alone
        data_inv = SalesReportService.get_full_report_data({
            "preset": "today",
            "invoice_no": report_setup["inv1_no"][-4:],
        })
        matching_nos = [r["invoice_no"] for r in data_inv["report"]["rows"]]
        assert report_setup["inv1_no"] in matching_nos

        # B. Filter by payment mode alone: EasyPaisa
        data_ep = SalesReportService.get_full_report_data({
            "preset": "today",
            "payment_mode": "easypaisa",
        })
        ep_invs = [r["invoice_no"] for r in data_ep["report"]["rows"]]
        assert report_setup["inv2_no"] in ep_invs
        assert report_setup["inv1_no"] not in ep_invs

        # C. Filter by category alone: Category A
        data_cat = SalesReportService.get_full_report_data({
            "preset": "today",
            "category_id": str(report_setup["cat_a_id"]),
        })
        cat_invs = [r["invoice_no"] for r in data_cat["report"]["rows"]]
        assert report_setup["inv1_no"] in cat_invs
        assert report_setup["inv3_no"] in cat_invs

        # D. Filter by item alone
        data_item = SalesReportService.get_full_report_data({
            "preset": "today",
            "item_query": "Alpha",
        })
        item_invs = [r["invoice_no"] for r in data_item["report"]["rows"]]
        assert report_setup["inv1_no"] in item_invs
        assert report_setup["inv3_no"] in item_invs


def test_03_two_filters_combined(app, report_setup):
    """Test combining two filters simultaneously (Category + Payment Mode)."""
    with app.app_context():
        # Invoices containing Cat B items AND paid with Card
        data = SalesReportService.get_full_report_data({
            "preset": "today",
            "category_id": str(report_setup["cat_b_id"]),
            "payment_mode": "card",
        })
        inv_nos = [r["invoice_no"] for r in data["report"]["rows"]]
        assert report_setup["inv3_no"] in inv_nos
        assert report_setup["inv1_no"] not in inv_nos
        assert report_setup["inv2_no"] not in inv_nos


def test_04_totals_row_equals_sum_of_rows(app, report_setup):
    """Verify that the totals row matches the sum of the filtered invoice rows."""
    with app.app_context():
        data = SalesReportService.get_full_report_data({
            "preset": "today",
            "view": "invoice",
            "cashier_id": report_setup["cashier_id"],
        })
        rows = data["report"]["rows"]
        totals = data["report"]["totals_row"]

        sum_subtotal = sum((r["subtotal"] for r in rows), Decimal("0.00"))
        sum_discount = sum((r["discount"] for r in rows), Decimal("0.00"))
        sum_tax = sum((r["tax"] for r in rows), Decimal("0.00"))
        sum_payable = sum((r["total"] for r in rows), Decimal("0.00"))

        assert totals["subtotal"] == sum_subtotal
        assert totals["discount"] == sum_discount
        assert totals["tax"] == sum_tax
        assert totals["payable"] == sum_payable


def test_05_split_invoice_counted_correctly_under_both_modes(app, report_setup):
    """
    Test split invoice:
    Invoice 2 had 80 Cash and 120 EasyPaisa.
    In payment breakdown, Cash has its 80 portion, EasyPaisa has its 120 portion.
    """
    with app.app_context():
        data = SalesReportService.get_full_report_data({
            "preset": "today",
            "invoice_no": report_setup["inv2_no"],
        })
        breakdown = {pm["mode_code"]: pm["total_amt"] for pm in data["payment_breakdown"]}
        assert breakdown["cash"] == Decimal("80.00")
        assert breakdown["easypaisa"] == Decimal("120.00")

        # In Day-wise view: Cash column has 80 and EasyPaisa column has 120
        day_data = SalesReportService.get_full_report_data({
            "preset": "today",
            "view": "day",
            "invoice_no": report_setup["inv2_no"],
        })
        day_row = day_data["report"]["rows"][0]
        assert day_row["mode_totals"]["cash"] == Decimal("80.00")
        assert day_row["mode_totals"]["easypaisa"] == Decimal("120.00")


def test_06_today_matches_dashboard(app, report_setup):
    """Verify that Sales Report Today's total matches Dashboard Today's Sales exactly."""
    with app.app_context():
        dash_data = DashboardService.get_dashboard_data(period="today")
        dash_today_sales = Decimal(str(dash_data["metrics"]["today_sales"]))

        report_data = SalesReportService.get_full_report_data({"preset": "today"})
        report_today_sales = report_data["report"]["totals_row"]["payable"]

        assert dash_today_sales == report_today_sales



def test_07_excel_and_csv_export(client, report_setup):
    """Test Excel and CSV exports generate valid content with sheets and headers."""
    client.post("/auth/login", data={"username": "rep_manager", "password": "Manager123!"})

    # Excel export
    res_excel = client.get("/reports/sales/export/excel?preset=today&view=invoice")
    assert res_excel.status_code == 200
    assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in res_excel.content_type

    wb = openpyxl.load_workbook(io.BytesIO(res_excel.data))
    sheet_names = wb.sheetnames
    assert "Sales (Invoice-wise)" in sheet_names
    assert "Payment Breakdown" in sheet_names

    # CSV export
    res_csv = client.get("/reports/sales/export/csv?preset=today&view=invoice")
    assert res_csv.status_code == 200
    assert "text/csv" in res_csv.content_type
    csv_text = res_csv.data.decode("utf-8")
    assert "Invoice #" in csv_text
    assert report_setup["inv1_no"] in csv_text
