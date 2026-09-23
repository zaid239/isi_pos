"""
Dashboard Service.
Provides authoritative aggregation and analytics for the operational dashboard:
1. Today's sales, completed order count, and Average Order Value (AOV), respecting business-day cutoff.
2. Low stock alert counts.
3. 7-day daily sales trend for Chart.js.
4. Top 5 selling items today with quantities sold.
5. Last 10 completed invoices with line item counts, payment modes, and totals.
Strictly queries real database tables (invoices, invoice_items, invoice_payments, items).
"""
from datetime import datetime, timezone, date, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.extensions import db
from app.models.invoice import Invoice, InvoiceItem
from app.models.payment import InvoicePayment
from app.models.item import Item
from app.services.setting_service import SettingService
from app.utils.timezone import utc_now, get_business_date, to_pakistan_time, format_pakistan_datetime, PK_TZ

ZERO_DECIMAL = Decimal("0.00")


class DashboardService:
    @staticmethod
    def parse_cutoff(cutoff_str: Optional[str] = None) -> Tuple[int, int]:
        """Parses HH:MM cutoff string into (hour, minute)."""
        if not cutoff_str:
            cutoff_str = str(SettingService.get_value("business_day_cutoff", "04:00") or "04:00")
        parts = cutoff_str.strip().split(":")
        hour = int(parts[0]) if parts[0].isdigit() else 4
        minute = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return hour, minute

    @staticmethod
    def get_business_day_utc_range(b_date: date, cutoff_str: Optional[str] = None) -> Tuple[datetime, datetime]:
        """
        Computes the UTC datetime range [start_utc, end_utc) for a specific business date,
        taking into account the Asia/Karachi cutoff time (e.g. 04:00 AM PKT).
        Returns naive datetimes in UTC for cross-database (Oracle/SQLite) query safety.
        """
        hour, minute = DashboardService.parse_cutoff(cutoff_str)
        # Business day begins at cutoff time in Pakistan
        start_pkt = datetime(b_date.year, b_date.month, b_date.day, hour, minute, tzinfo=PK_TZ)
        end_pkt = start_pkt + timedelta(days=1)

        start_utc = start_pkt.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = end_pkt.astimezone(timezone.utc).replace(tzinfo=None)
        return start_utc, end_utc

    @classmethod
    def get_period_analytics(cls, period: str = "7d", start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculates chart trend data and top sellers for a requested period (today, 7d, 30d, 90d, or custom).
        Re-queries real database records respecting business-day cutoff.
        """
        cutoff_str = str(SettingService.get_value("business_day_cutoff", "04:00") or "04:00")
        now_utc = utc_now()
        current_b_date = get_business_date(now_utc, cutoff_str=cutoff_str)

        period = (period or "7d").lower()
        if period == "today":
            start_b_date = current_b_date
            end_b_date = current_b_date
            period_title = "Today"
        elif period == "30d":
            start_b_date = current_b_date - timedelta(days=29)
            end_b_date = current_b_date
            period_title = "Last 30 days"
        elif period == "90d":
            start_b_date = current_b_date - timedelta(days=89)
            end_b_date = current_b_date
            period_title = "Last 90 days"
        elif period == "custom" and start_date and end_date:
            try:
                start_b_date = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
                end_b_date = datetime.strptime(end_date.strip(), "%Y-%m-%d").date()
                if start_b_date > end_b_date:
                    start_b_date, end_b_date = end_b_date, start_b_date
                period_title = f"{start_b_date.strftime('%b %d')} – {end_b_date.strftime('%b %d, %Y')}"
            except Exception:
                period = "7d"
                start_b_date = current_b_date - timedelta(days=6)
                end_b_date = current_b_date
                period_title = "Last 7 days"
        else:
            period = "7d"
            start_b_date = current_b_date - timedelta(days=6)
            end_b_date = current_b_date
            period_title = "Last 7 days"

        # Compute UTC range
        range_start_utc, _ = cls.get_business_day_utc_range(start_b_date, cutoff_str)
        _, range_end_utc = cls.get_business_day_utc_range(end_b_date, cutoff_str)

        # Query completed invoices with eager-loaded items for the selected range
        invoices = Invoice.query.options(selectinload(Invoice.items)).filter(
            Invoice.status_code == "completed",
            Invoice.invoice_date >= range_start_utc,
            Invoice.invoice_date < range_end_utc
        ).order_by(Invoice.invoice_date.asc()).all()

        total_sales = ZERO_DECIMAL
        chart_labels: List[str] = []
        chart_values: List[float] = []
        chart_order_counts: List[int] = []

        num_days = (end_b_date - start_b_date).days + 1

        if period == "today" or num_days == 1:
            # Hourly breakdown for single-day period
            sales_by_hour: Dict[int, Decimal] = {}
            orders_by_hour: Dict[int, int] = {}
            for inv in invoices:
                inv_pk = to_pakistan_time(inv.invoice_date)
                hr = inv_pk.hour if inv_pk else 0
                sales_by_hour[hr] = sales_by_hour.get(hr, ZERO_DECIMAL) + Decimal(str(inv.payable_amount or 0))
                orders_by_hour[hr] = orders_by_hour.get(hr, 0) + 1
                total_sales += Decimal(str(inv.payable_amount or 0))

            min_hr = min([8] + list(sales_by_hour.keys()))
            max_hr = max([21] + list(sales_by_hour.keys()))
            for hr in range(min_hr, max_hr + 1):
                dt_hr = time(hr, 0)
                lbl = dt_hr.strftime("%I %p").lstrip("0")
                hr_sales = sales_by_hour.get(hr, ZERO_DECIMAL)
                hr_orders = orders_by_hour.get(hr, 0)
                chart_labels.append(lbl)
                chart_values.append(float(hr_sales))
                chart_order_counts.append(hr_orders)
        else:
            sales_by_bdate: Dict[date, Decimal] = {}
            orders_by_bdate: Dict[date, int] = {}
            for inv in invoices:
                inv_bdate = get_business_date(inv.invoice_date, cutoff_str=cutoff_str)
                sales_by_bdate[inv_bdate] = sales_by_bdate.get(inv_bdate, ZERO_DECIMAL) + Decimal(str(inv.payable_amount or 0))
                orders_by_bdate[inv_bdate] = orders_by_bdate.get(inv_bdate, 0) + 1
                total_sales += Decimal(str(inv.payable_amount or 0))

            curr_date = start_b_date
            while curr_date <= end_b_date:
                if curr_date == current_b_date:
                    lbl = "Today"
                elif num_days <= 7:
                    lbl = curr_date.strftime("%a, %b %d")
                else:
                    lbl = curr_date.strftime("%b %d")

                day_sales = sales_by_bdate.get(curr_date, ZERO_DECIMAL)
                day_orders = orders_by_bdate.get(curr_date, 0)
                chart_labels.append(lbl)
                chart_values.append(float(day_sales))
                chart_order_counts.append(day_orders)
                curr_date += timedelta(days=1)

        # Top Sellers for this period
        top_sellers: List[Dict[str, Any]] = []
        if invoices:
            item_totals: Dict[Tuple[Any, str, str], Dict[str, Decimal]] = {}
            for inv in invoices:
                for itm in (inv.items or []):
                    key = (itm.item_id, itm.snap_item_name or "", itm.snap_sku or "")
                    if key not in item_totals:
                        item_totals[key] = {"qty": Decimal(0), "revenue": Decimal(0)}
                    item_totals[key]["qty"] += Decimal(str(itm.quantity or 0))
                    item_totals[key]["revenue"] += Decimal(str(itm.line_total or 0))

            sorted_items = sorted(item_totals.items(), key=lambda x: x[1]["qty"], reverse=True)[:5]
            for (i_id, i_name, i_sku), stats in sorted_items:
                top_sellers.append({
                    "item_id": i_id,
                    "name": i_name,
                    "sku": i_sku,
                    "quantity": float(stats["qty"]),
                    "revenue": float(stats["revenue"]),
                })

        has_chart_data = (total_sales > ZERO_DECIMAL or sum(chart_order_counts) > 0)
        currency_sym = SettingService.get_value("currency_symbol", "Rs.")

        return {
            "period": period,
            "period_title": period_title,
            "start_date": start_b_date.strftime("%Y-%m-%d"),
            "end_date": end_b_date.strftime("%Y-%m-%d"),
            "total_sales": float(total_sales),
            "total_sales_formatted": f"{currency_sym} {float(total_sales):,.2f}",
            "labels": chart_labels,
            "sales_values": chart_values,
            "order_counts": chart_order_counts,
            "has_data": has_chart_data,
            "top_sellers": top_sellers,
            "currency_symbol": currency_sym,
        }

    @classmethod
    def get_dashboard_data(cls, period: str = "7d", start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Gathers all real-time dashboard data in optimized queries:
        - Metric cards stay on TODAY.
        - Chart trend and top sellers follow the requested period.
        - Recent orders displays the latest 10 completed invoices.
        """
        cutoff_str = str(SettingService.get_value("business_day_cutoff", "04:00") or "04:00")
        now_utc = utc_now()
        current_b_date = get_business_date(now_utc, cutoff_str=cutoff_str)

        # 1. Today's Metrics (Always strictly calculated for current business day)
        today_start_utc, today_end_utc = cls.get_business_day_utc_range(current_b_date, cutoff_str)
        today_invoices = Invoice.query.filter(
            Invoice.status_code == "completed",
            Invoice.invoice_date >= today_start_utc,
            Invoice.invoice_date < today_end_utc
        ).all()

        today_sales = sum((Decimal(str(inv.payable_amount or 0)) for inv in today_invoices), ZERO_DECIMAL)
        today_orders_count = len(today_invoices)
        if today_orders_count > 0:
            avg_order_val = (today_sales / Decimal(today_orders_count)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            avg_order_val = ZERO_DECIMAL

        # 2. Low Stock Count
        try:
            low_stock_count = Item.query.filter(
                Item.deleted_at.is_(None),
                Item.is_active == 1,
                Item.current_stock <= Item.reorder_level
            ).count()
        except Exception:
            low_stock_count = 0

        # 3. Period Analytics (Chart + Top Sellers)
        period_data = cls.get_period_analytics(period=period, start_date=start_date, end_date=end_date)

        # 4. Recent Orders (Last 10 completed invoices across all dates)
        recent_invoices = Invoice.query.options(
            selectinload(Invoice.payments),
            selectinload(Invoice.items)
        ).filter(
            Invoice.status_code == "completed"
        ).order_by(Invoice.invoice_date.desc()).limit(10).all()

        recent_orders: List[Dict[str, Any]] = []
        for inv in recent_invoices:
            inv_pk_dt = to_pakistan_time(inv.invoice_date)
            if inv_pk_dt:
                inv_bdate = get_business_date(inv.invoice_date, cutoff_str=cutoff_str)
                if inv_bdate == current_b_date:
                    formatted_time = inv_pk_dt.strftime("%I:%M %p")
                else:
                    formatted_time = inv_pk_dt.strftime("%b %d, %I:%M %p")
            else:
                formatted_time = ""

            total_items_count = int(sum(item.quantity for item in inv.items)) if inv.items else 0

            if inv.payments:
                modes = []
                for p in inv.payments:
                    if p.payment_mode and p.payment_mode.mode_name:
                        modes.append(p.payment_mode.mode_name)
                    else:
                        modes.append("Cash")
                payment_method = ", ".join(dict.fromkeys(modes))
            else:
                payment_method = "Cash"

            recent_orders.append({
                "id": inv.id,
                "invoice_no": inv.invoice_no,
                "formatted_time": formatted_time,
                "item_count": total_items_count,
                "payment_method": payment_method,
                "payable_amount": float(inv.payable_amount or 0),
            })

        return {
            "current_business_date": current_b_date.strftime("%b %d, %Y"),
            "metrics": {
                "today_sales": float(today_sales),
                "today_orders_count": today_orders_count,
                "average_order_value": float(avg_order_val),
                "low_stock_count": low_stock_count,
            },
            "chart_data": {
                "period": period_data["period"],
                "period_title": period_data["period_title"],
                "labels": period_data["labels"],
                "sales_values": period_data["sales_values"],
                "order_counts": period_data["order_counts"],
                "total_sales": period_data["total_sales"],
                "total_sales_formatted": period_data["total_sales_formatted"],
                "has_data": period_data["has_data"],
            },
            "top_sellers": period_data["top_sellers"],
            "recent_orders": recent_orders,
        }
