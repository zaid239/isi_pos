"""
Tests for Reports Center:
1. Each report generates a valid PDF (Invoice-wise, Item-wise, Day-wise)
2. Totals in the PDF equal the SQL totals
3. Cashier receives 403 Forbidden
"""
import pytest
from decimal import Decimal
import pypdfium2 as pdfium

from app.services.sales_report_service import SalesReportService
from tests.test_part_b_sales_report import report_setup


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    pdf = pdfium.PdfDocument(pdf_bytes)
    text_parts = []
    for page in pdf:
        text_parts.append(page.get_textpage().get_text_range())
    return " ".join(text_parts)


@pytest.mark.parametrize("view_name", ["invoice", "item", "day"])
def test_each_report_generates_valid_pdf(client, report_setup, view_name):
    """Verifies that each report generates a valid A4 PDF with proper headers and footer."""
    client.post("/auth/login", data={"username": "rep_manager", "password": "Manager123!"})

    resp = client.get(f"/reports/sales/export/pdf?view={view_name}")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data.startswith(b"%PDF-")

    # Verify footer branding and pagination
    pdf_text = _extract_pdf_text(resp.data)
    assert "System by Noventrasystem" in pdf_text
    assert "Page 1 of" in pdf_text


def test_pdf_totals_equal_sql_totals(app, client, report_setup):
    """Verifies that totals printed in each PDF report exactly match the SQL aggregated totals."""
    client.post("/auth/login", data={"username": "rep_admin", "password": "Admin123!"})

    with app.app_context():
        # 1. Invoice-wise check
        inv_resp = client.get("/reports/sales/export/pdf?view=invoice")
        assert inv_resp.status_code == 200
        inv_pdf_text = _extract_pdf_text(inv_resp.data)

        criteria_inv = SalesReportService.build_filter_criteria({"view": "invoice"})
        base_q_inv = SalesReportService.get_base_invoice_query(criteria_inv)
        inv_sql = SalesReportService.get_invoice_wise_report(base_q_inv, criteria_inv)
        sql_inv_total = f"{inv_sql['totals_row']['payable']:,.2f}"
        assert sql_inv_total in inv_pdf_text

        # 2. Item-wise check
        item_resp = client.get("/reports/sales/export/pdf?view=item")
        assert item_resp.status_code == 200
        item_pdf_text = _extract_pdf_text(item_resp.data)

        criteria_item = SalesReportService.build_filter_criteria({"view": "item"})
        base_q_item = SalesReportService.get_base_invoice_query(criteria_item)
        item_sql = SalesReportService.get_item_wise_report(base_q_item, criteria_item)
        sql_item_net = f"{item_sql['totals_row']['net']:,.2f}"
        assert sql_item_net in item_pdf_text

        # 3. Day-wise check
        day_resp = client.get("/reports/sales/export/pdf?view=day")
        assert day_resp.status_code == 200
        day_pdf_text = _extract_pdf_text(day_resp.data)

        criteria_day = SalesReportService.build_filter_criteria({"view": "day"})
        base_q_day = SalesReportService.get_base_invoice_query(criteria_day)
        day_sql = SalesReportService.get_day_wise_report(base_q_day, criteria_day)
        sql_day_total = f"{day_sql['totals_row']['total']:,.2f}"
        assert sql_day_total in day_pdf_text


def test_cashier_gets_403_on_reports(client, report_setup):
    """Verifies that cashier is strictly blocked with 403 from Reports Center and PDF export."""
    client.get("/auth/logout")
    client.post("/auth/login", data={"username": "rep_cashier", "password": "Cashier123!"})

    # Web page access
    res_page = client.get("/reports/sales")
    assert res_page.status_code == 403

    # PDF generation access
    res_pdf = client.get("/reports/sales/export/pdf?view=invoice")
    assert res_pdf.status_code == 403

    # Excel export access
    res_excel = client.get("/reports/sales/export/excel?view=invoice")
    assert res_excel.status_code == 403
