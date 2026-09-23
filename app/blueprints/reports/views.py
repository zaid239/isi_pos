"""
Sales Reports Blueprint Views.
Access strictly restricted to Super Admin and Manager roles.
Cashier receives 403 Forbidden.
"""
from flask import render_template, request, send_file, Response, make_response
from flask_login import login_required, current_user
from app.blueprints.reports import reports_bp
from app.services.sales_report_service import SalesReportService
from app.utils.decorators import role_required


@reports_bp.route("")
@reports_bp.route("/")
@reports_bp.route("/sales")
@login_required
@role_required("super_admin", "manager")
def sales():
    """Renders the Reports Center desktop interface."""
    data = SalesReportService.get_full_report_data(request.args)
    registry = SalesReportService.get_report_registry()
    today_str = SalesReportService.get_business_today().isoformat()
    return render_template(
        "reports/sales.html",
        registry=registry,
        today_str=today_str,
        **data
    )


@reports_bp.route("/sales/export/pdf")
@reports_bp.route("/pdf")
@login_required
@role_required("super_admin", "manager")
def export_pdf():
    """Generates ReportLab PDF and streams it to open in a new tab."""
    user_name = current_user.full_name if (current_user and current_user.full_name) else "Admin"
    buf = SalesReportService.generate_pdf(request.args, user_name=user_name)
    view = request.args.get("view", "invoice")
    date_from = request.args.get("date_from", "today")
    filename = f"sales_report_{view}_{date_from}.pdf"
    return send_file(
        buf,
        as_attachment=False,
        download_name=filename,
        mimetype="application/pdf",
    )


@reports_bp.route("/sales/export/excel")
@login_required
@role_required("super_admin", "manager")
def export_excel():
    """Streams formatted Excel (.xlsx) file of full filtered report."""
    buf = SalesReportService.export_excel(request.args)
    filename = f"sales_report_{request.args.get('view', 'invoice')}_{request.args.get('date_from', 'today')}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@reports_bp.route("/sales/export/csv")
@login_required
@role_required("super_admin", "manager")
def export_csv():
    """Streams CSV file of full filtered report."""
    buf = SalesReportService.export_csv(request.args)
    filename = f"sales_report_{request.args.get('view', 'invoice')}_{request.args.get('date_from', 'today')}.csv"
    response = make_response(buf.getvalue())
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    return response
