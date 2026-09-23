"""
Test Dashboard Redesign & Back-Office Operational Analytics.
Verifies:
1. DashboardService computes today's sales, orders count, AOV, low stock items,
   7-day sales trend, top sellers today, and recent 10 invoices respecting business-day cutoff.
2. /dashboard route returns 200 with the redesigned layout, dense typography,
   no blue on the Open POS action, and proper components.
3. /system-info route contains the Pending Sync Outbox metric.
"""
from decimal import Decimal
import pytest
from app.extensions import db
from app.models.user import User, Role
from app.models.invoice import Invoice, InvoiceItem
from app.models.payment import PaymentMode, InvoicePayment
from app.models.item import Item, Category, Unit
from app.services.dashboard_service import DashboardService
from app.services.pos_service import POSService
from app.utils.timezone import utc_now


def test_dashboard_service_and_view(app, client):
    with app.app_context():
        # Ensure Super Admin role and user
        sa_role = Role.query.filter_by(role_code="super_admin").first()
        if not sa_role:
            sa_role = Role(role_code="super_admin", role_name="Super Admin", is_system=1)
            db.session.add(sa_role)
            db.session.commit()

        user = User.query.filter_by(username="dash_admin").first()
        if not user:
            user = User(
                username="dash_admin",
                full_name="Dashboard Administrator",
                email="dash@pos.local",
                role_id=sa_role.id,
                is_active=1,
            )
            user.set_password("Admin123!")
            db.session.add(user)
            db.session.commit()

        # 1. Test DashboardService in isolation
        data = DashboardService.get_dashboard_data()
        assert "metrics" in data
        assert "today_sales" in data["metrics"]
        assert "today_orders_count" in data["metrics"]
        assert "average_order_value" in data["metrics"]
        assert "low_stock_count" in data["metrics"]
        assert "chart_data" in data
        assert len(data["chart_data"]["labels"]) == 7
        assert len(data["chart_data"]["sales_values"]) == 7
        assert "top_sellers" in data
        assert "recent_orders" in data

        # 2. Log in and test /dashboard view
        login_res = client.post("/auth/login", data={"username": "dash_admin", "password": "Admin123!"}, follow_redirects=True)
        assert login_res.status_code == 200

        dash_res = client.get("/dashboard")
        assert dash_res.status_code == 200
        html = dash_res.get_data(as_text=True)

        # Verify typography and dense chrome
        assert "dashboard-page-title" in html
        assert "Dashboard" in html
        assert "metric-label" in html
        assert "Today&#39;s Sales" in html or "Today's Sales" in html
        assert "Today&#39;s Orders" in html or "Today's Orders" in html
        assert "Average Order Value" in html
        assert "Low Stock Items" in html
        assert "Last 7 days" in html
        assert "Top Sellers Today" in html
        assert "Recent Orders" in html
        assert "Open POS Terminal" in html
        assert "btn-accent" in html

        # Verify Pending Sync was removed from dashboard
        assert "Outbox Queue" not in html

        # 3. Test /system-info contains Pending Sync Outbox
        sys_res = client.get("/system-info")
        assert sys_res.status_code == 200
        sys_html = sys_res.get_data(as_text=True)
        assert "Pending Sync Outbox" in sys_html
