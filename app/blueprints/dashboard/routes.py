"""
Dashboard Blueprint Routes.
Serves the main application dashboard and shell.
"""
from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user
from app.utils.decorators import role_required
from app.models.item import Item, Category
from app.models.sync import SyncOutbox
from app.services.setting_service import SettingService
from app.services.dashboard_service import DashboardService

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/api/chart-data")
@dashboard_bp.route("/dashboard/api/chart-data")
@login_required
def chart_data_api():
    """
    Returns real-time aggregated chart and top sellers data for the selected period.
    """
    period = request.args.get("period", "7d")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    try:
        data = DashboardService.get_period_analytics(period=period, start_date=start_date, end_date=end_date)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
def index():
    period = request.args.get("period", "7d")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    try:
        data = DashboardService.get_dashboard_data(period=period, start_date=start_date, end_date=end_date)
    except Exception as e:
        # Graceful fallback if database connection has temporary issue
        data = {
            "current_business_date": "",
            "metrics": {
                "today_sales": 0.0,
                "today_orders_count": 0,
                "average_order_value": 0.0,
                "low_stock_count": 0,
            },
            "chart_data": {
                "labels": [],
                "sales_values": [],
                "order_counts": [],
                "total_sales": 0.0,
                "has_data": False,
            },
            "top_sellers": [],
            "recent_orders": [],
        }

    from app.services.backup_manager import get_backup_status
    backup_status = get_backup_status()

    return render_template(
        "dashboard/index.html",
        user=current_user,
        metrics=data["metrics"],
        chart_data=data["chart_data"],
        top_sellers=data["top_sellers"],
        recent_orders=data["recent_orders"],
        current_business_date=data.get("current_business_date", ""),
        backup_status=backup_status,
    )


@dashboard_bp.route("/system-info")
@login_required
@role_required("super_admin")
def system_info():
    try:
        pending_outbox = SyncOutbox.query.filter_by(status_code="pending").count()
    except Exception:
        pending_outbox = 0
    return render_template("dashboard/system_info.html", user=current_user, pending_outbox=pending_outbox)

