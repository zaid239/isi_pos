"""
Sales Report Service.
Authoritative reporting module for Noventra POS:
- Business-day cutoff integration via SettingService and DashboardService
- Multi-filter combinations (dates, invoice no, item, category, mode, cashier, status)
- 3 Reporting Views: Invoice-wise, Item-wise, Day-wise
- Dynamic Payment Breakdown summary cards (summed from invoice_payments)
- Server-side pagination (50 per page), sorting, and comprehensive totals row
- Multi-format exports: Excel .xlsx (openpyxl with styled header and breakdown sheet), CSV, Print
- Strictly Decimal and format_money() arithmetic
"""
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import io
import csv
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload, selectinload
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.lib.pagesizes import A4, landscape, portrait
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfgen import canvas

from app.extensions import db
from app.models.invoice import Invoice, InvoiceItem
from app.models.payment import InvoicePayment, PaymentMode
from app.models.item import Item, Category
from app.models.user import User, Role
from app.services.setting_service import SettingService
from app.services.payment_service import PaymentService
from app.services.dashboard_service import DashboardService
from app.utils.timezone import utc_now, get_business_date, to_pakistan_time, PK_TZ

ZERO_DECIMAL = Decimal("0.00")


class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas that accumulates pages and appends total page count:
    'Page X of Y' and 'System by Noventrasystem' footer on every page.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_footer(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.black)
        self.setStrokeColor(colors.black)
        self.setLineWidth(0.5)
        w, h = self._pagesize
        # Divider line
        self.line(28, 26, w - 28, 26)
        # Left: System by Noventrasystem
        self.drawString(28, 15, "System by Noventrasystem")
        # Right: Page X of Y
        self.drawRightString(w - 28, 15, f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


REPORT_REGISTRY = [
    {
        "id": "invoice",
        "title": "Invoice-wise Sales",
        "description": "Transaction-level details with cashier, payment modes, GST, and totals.",
        "orientation": "landscape",
        "supported_params": ["date_from", "date_to", "invoice_no", "cashier_id", "payment_mode", "status"],
    },
    {
        "id": "item",
        "title": "Item-wise Sales",
        "description": "Product sales quantities, gross amounts, discounts, GST, and net revenues.",
        "orientation": "portrait",
        "supported_params": ["date_from", "date_to", "item_query", "category_id"],
    },
    {
        "id": "day",
        "title": "Day-wise Sales",
        "description": "Daily aggregated sales totals with separate columns for each payment mode.",
        "orientation": "portrait",
        "supported_params": ["date_from", "date_to", "payment_mode"],
    },
]


class SalesReportService:
    @classmethod
    def get_report_registry(cls) -> List[Dict[str, Any]]:
        return REPORT_REGISTRY

    @classmethod
    def get_cutoff_str(cls) -> str:
        return str(SettingService.get_value("business_day_cutoff", "04:00") or "04:00")

    @classmethod
    def get_business_today(cls) -> date:
        cutoff = cls.get_cutoff_str()
        return get_business_date(utc_now(), cutoff_str=cutoff)

    @classmethod
    def resolve_date_presets(cls, preset: Optional[str] = None) -> Tuple[date, date, str]:
        """
        Resolves date preset strings (today, yesterday, this_week, this_month, last_month)
        into business date boundaries (from_date, to_date).
        """
        b_today = cls.get_business_today()
        p = (preset or "").strip().lower()

        if p == "yesterday":
            d = b_today - timedelta(days=1)
            return d, d, "yesterday"
        elif p == "this_week":
            # Monday as start of week
            start = b_today - timedelta(days=b_today.weekday())
            return start, b_today, "this_week"
        elif p == "this_month":
            start = b_today.replace(day=1)
            return start, b_today, "this_month"
        elif p == "last_month":
            first_this_month = b_today.replace(day=1)
            last_prev_month = first_this_month - timedelta(days=1)
            first_prev_month = last_prev_month.replace(day=1)
            return first_prev_month, last_prev_month, "last_month"
        else:
            # Default to today
            return b_today, b_today, "today"

    @classmethod
    def build_filter_criteria(cls, args: Dict[str, Any]) -> Dict[str, Any]:
        """Parses and sanitizes request parameters."""
        cutoff_str = cls.get_cutoff_str()
        preset = args.get("preset")

        raw_from = (args.get("date_from") or "").strip()
        raw_to = (args.get("date_to") or "").strip()

        if preset:
            from_date, to_date, active_preset = cls.resolve_date_presets(preset)
        elif raw_from or raw_to:
            active_preset = "custom"
            try:
                from_date = date.fromisoformat(raw_from) if raw_from else cls.get_business_today()
            except ValueError:
                from_date = cls.get_business_today()
            try:
                to_date = date.fromisoformat(raw_to) if raw_to else from_date
            except ValueError:
                to_date = from_date
            if from_date > to_date:
                from_date, to_date = to_date, from_date
        else:
            from_date, to_date, active_preset = cls.resolve_date_presets("today")

        start_utc, _ = DashboardService.get_business_day_utc_range(from_date, cutoff_str)
        _, end_utc = DashboardService.get_business_day_utc_range(to_date, cutoff_str)

        invoice_no = (args.get("invoice_no") or "").strip()
        item_query = (args.get("item_query") or "").strip()
        category_id = None
        if "category_id" in args and args["category_id"]:
            try:
                category_id = int(args["category_id"])
            except (ValueError, TypeError):
                category_id = None


        payment_mode = (args.get("payment_mode") or "").strip().lower()
        cashier_id = None
        if "cashier_id" in args:
            try:
                cashier_id = int(args["cashier_id"])
            except (ValueError, TypeError):
                cashier_id = None

        status = (args.get("status") or "completed").strip().lower()
        if status not in ("completed", "voided", "all"):
            status = "completed"

        view = (args.get("view") or "invoice").strip().lower()
        if view not in ("invoice", "item", "day"):
            view = "invoice"

        sort_by = (args.get("sort_by") or "").strip()
        sort_order = (args.get("sort_order") or "desc").strip().lower()
        if sort_order not in ("asc", "desc"):
            sort_order = "desc"

        page = 1
        if "page" in args:
            try:
                page = max(1, int(args["page"]))
            except (ValueError, TypeError):
                page = 1

        return {
            "date_from": from_date.isoformat(),
            "date_to": to_date.isoformat(),
            "from_date_obj": from_date,
            "to_date_obj": to_date,
            "active_preset": active_preset,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "invoice_no": invoice_no,
            "item_query": item_query,
            "category_id": category_id,
            "payment_mode": payment_mode,
            "cashier_id": cashier_id,
            "status": status,
            "view": view,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "page": page,
            "per_page": 50,
        }

    @classmethod
    def get_base_invoice_query(cls, criteria: Dict[str, Any]):
        """Builds filtered invoice query according to criteria."""
        query = Invoice.query.filter(
            Invoice.invoice_date >= criteria["start_utc"],
            Invoice.invoice_date < criteria["end_utc"],
        )

        if criteria["status"] != "all":
            query = query.filter(Invoice.status_code == criteria["status"])

        if criteria["invoice_no"]:
            query = query.filter(Invoice.invoice_no.ilike(f"%{criteria['invoice_no']}%"))

        if criteria["cashier_id"]:
            query = query.filter(Invoice.cashier_id == criteria["cashier_id"])

        if criteria["payment_mode"]:
            query = query.filter(
                Invoice.payments.any(
                    InvoicePayment.payment_mode.has(PaymentMode.mode_code == criteria["payment_mode"])
                )
            )

        if criteria["category_id"]:
            query = query.filter(
                Invoice.items.any(
                    InvoiceItem.item.has(Item.category_id == criteria["category_id"])
                )
            )

        if criteria["item_query"]:
            iq = criteria["item_query"]
            query = query.filter(
                Invoice.items.any(
                    or_(
                        InvoiceItem.snap_item_name.ilike(f"%{iq}%"),
                        InvoiceItem.snap_sku.ilike(f"%{iq}%"),
                    )
                )
            )

        return query

    @classmethod
    def get_payment_breakdown(cls, base_query) -> Tuple[List[Dict[str, Any]], Decimal]:
        """
        Computes payment breakdown cards summed from invoice_payments rows.
        Guarantees that active payment modes added in Settings appear automatically.
        """
        inv_subq = base_query.with_entities(Invoice.id).scalar_subquery()

        # Query all active modes
        active_modes = PaymentService.get_active_modes()

        # Aggregate payments matching filtered invoices
        payment_sums = dict(
            db.session.query(
                InvoicePayment.payment_mode_id,
                func.coalesce(func.sum(InvoicePayment.amount), 0)
            ).filter(
                InvoicePayment.invoice_id.in_(inv_subq)
            ).group_by(
                InvoicePayment.payment_mode_id
            ).all()
        )

        total_sales = sum((Decimal(str(amt)) for amt in payment_sums.values()), ZERO_DECIMAL)

        breakdown = []
        for mode in active_modes:
            amt = Decimal(str(payment_sums.get(mode.id, 0)))
            share = ((amt / total_sales) * Decimal("100.00")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP) if total_sales > ZERO_DECIMAL else ZERO_DECIMAL
            breakdown.append({
                "mode_id": mode.id,
                "mode_code": mode.mode_code,
                "mode_name": mode.mode_name,
                "total_amt": amt,
                "share_pct": float(share),
            })

        return breakdown, total_sales

    @classmethod
    def get_invoice_wise_report(cls, base_query, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Generates Invoice-wise report with server-side pagination and full totals row."""
        # 1. Totals row across the ENTIRE filtered result
        totals_agg = base_query.with_entities(
            func.count(Invoice.id).label("total_invoices"),
            func.coalesce(func.sum(Invoice.subtotal), 0).label("total_subtotal"),
            func.coalesce(func.sum(Invoice.discount_amount), 0).label("total_discount"),
            func.coalesce(func.sum(Invoice.tax_amount), 0).label("total_tax"),
            func.coalesce(func.sum(Invoice.round_off), 0).label("total_round_off"),
            func.coalesce(func.sum(Invoice.payable_amount), 0).label("total_payable"),
        ).first()

        total_invoices = totals_agg.total_invoices if totals_agg else 0
        total_subtotal = Decimal(str(totals_agg.total_subtotal or 0))
        total_discount = Decimal(str(totals_agg.total_discount or 0))
        total_tax = Decimal(str(totals_agg.total_tax or 0))
        total_round_off = Decimal(str(totals_agg.total_round_off or 0))
        total_payable = Decimal(str(totals_agg.total_payable or 0))

        # Total item count across all matching invoices
        inv_subq = base_query.with_entities(Invoice.id).scalar_subquery()
        total_items_count = db.session.query(
            func.coalesce(func.sum(InvoiceItem.quantity), 0)
        ).filter(InvoiceItem.invoice_id.in_(inv_subq)).scalar() or 0

        totals_row = {
            "invoices_count": total_invoices,
            "items_count": int(total_items_count),
            "subtotal": total_subtotal,
            "discount": total_discount,
            "tax": total_tax,
            "round_off": total_round_off,
            "payable": total_payable,
        }

        # 2. Sorting
        sort_by = criteria.get("sort_by") or "date"
        sort_order = criteria.get("sort_order") or "desc"

        sort_map = {
            "invoice_no": Invoice.invoice_no,
            "date": Invoice.invoice_date,
            "subtotal": Invoice.subtotal,
            "discount": Invoice.discount_amount,
            "tax": Invoice.tax_amount,
            "total": Invoice.payable_amount,
        }
        order_col = sort_map.get(sort_by, Invoice.invoice_date)
        order_clause = order_col.asc() if sort_order == "asc" else order_col.desc()

        # 3. Paginate
        page = criteria["page"]
        per_page = criteria["per_page"]

        paginated = base_query.options(
            joinedload(Invoice.cashier),
            selectinload(Invoice.items),
            selectinload(Invoice.payments).joinedload(InvoicePayment.payment_mode),
        ).order_by(order_clause).paginate(page=page, per_page=per_page, error_out=False)

        rows = []
        for inv in paginated.items:
            # Format Pakistan datetime
            pkt_time = to_pakistan_time(inv.invoice_date)
            formatted_dt = pkt_time.strftime("%d-%b-%Y %I:%M %p") if pkt_time else ""

            item_count = sum(float(it.quantity) for it in inv.items)
            
            # Payment mode badges
            modes_present = [p.payment_mode.mode_name for p in inv.payments if p.payment_mode]
            distinct_modes = list(dict.fromkeys(modes_present))
            
            if len(distinct_modes) > 1:
                payment_display = {
                    "is_split": True,
                    "modes_text": ", ".join(distinct_modes),
                    "summary": f"Split ({', '.join(distinct_modes)})"
                }
            elif len(distinct_modes) == 1:
                payment_display = {
                    "is_split": False,
                    "modes_text": distinct_modes[0],
                    "summary": distinct_modes[0]
                }
            else:
                payment_display = {
                    "is_split": False,
                    "modes_text": "Cash",
                    "summary": "Cash"
                }

            rows.append({
                "id": inv.id,
                "invoice_no": inv.invoice_no,
                "date_time": formatted_dt,
                "cashier": inv.cashier.full_name if inv.cashier else "Cashier",
                "item_count": item_count,
                "subtotal": Decimal(str(inv.subtotal)),
                "discount": Decimal(str(inv.discount_amount or 0)),
                "tax": Decimal(str(inv.tax_amount or 0)),
                "round_off": Decimal(str(inv.round_off or 0)),
                "total": Decimal(str(inv.payable_amount or 0)),
                "payment_display": payment_display,
                "remarks": inv.notes or "",
                "status_code": inv.status_code,
            })

        return {
            "rows": rows,
            "totals_row": totals_row,
            "pagination": paginated,
        }

    @classmethod
    def get_item_wise_report(cls, base_query, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Generates Item-wise report with server-side pagination and full totals row."""
        inv_subq = base_query.with_entities(Invoice.id).scalar_subquery()

        # Item-wise aggregate
        q = db.session.query(
            Item.id,
            Item.item_name,
            Item.sku,
            Category.cat_name,
            func.coalesce(func.sum(InvoiceItem.quantity), 0).label("qty_sold"),
            func.coalesce(func.sum(InvoiceItem.unit_price * InvoiceItem.quantity), 0).label("gross_amt"),
            func.coalesce(func.sum(InvoiceItem.line_disc_amt), 0).label("discount_amt"),
            func.coalesce(func.sum(InvoiceItem.tax_amount), 0).label("tax_amt"),
            func.coalesce(func.sum(InvoiceItem.line_total), 0).label("net_amt"),
            func.count(func.distinct(InvoiceItem.invoice_id)).label("invoices_count"),
        ).join(
            InvoiceItem, InvoiceItem.item_id == Item.id
        ).outerjoin(
            Category, Item.category_id == Category.id
        ).filter(
            InvoiceItem.invoice_id.in_(inv_subq)
        )

        if criteria["category_id"]:
            q = q.filter(Item.category_id == criteria["category_id"])

        if criteria["item_query"]:
            iq = criteria["item_query"]
            q = q.filter(or_(Item.item_name.ilike(f"%{iq}%"), Item.sku.ilike(f"%{iq}%")))

        q = q.group_by(Item.id, Item.item_name, Item.sku, Category.cat_name)

        # Sorting
        sort_by = criteria.get("sort_by") or "net"
        sort_order = criteria.get("sort_order") or "desc"

        sort_map = {
            "item": Item.item_name,
            "sku": Item.sku,
            "category": Category.cat_name,
            "qty": func.sum(InvoiceItem.quantity),
            "gross": func.sum(InvoiceItem.unit_price * InvoiceItem.quantity),
            "discount": func.sum(InvoiceItem.line_disc_amt),
            "tax": func.sum(InvoiceItem.tax_amount),
            "net": func.sum(InvoiceItem.line_total),
            "invoices": func.count(func.distinct(InvoiceItem.invoice_id)),
        }
        order_col = sort_map.get(sort_by, func.sum(InvoiceItem.line_total))
        order_clause = order_col.asc() if sort_order == "asc" else order_col.desc()
        q = q.order_by(order_clause)

        all_items = q.all()

        # Compute totals row across all items
        total_items_distinct = len(all_items)
        total_qty = sum((Decimal(str(r.qty_sold)) for r in all_items), ZERO_DECIMAL)
        total_gross = sum((Decimal(str(r.gross_amt)) for r in all_items), ZERO_DECIMAL)
        total_disc = sum((Decimal(str(r.discount_amt)) for r in all_items), ZERO_DECIMAL)
        total_tax = sum((Decimal(str(r.tax_amt)) for r in all_items), ZERO_DECIMAL)
        total_net = sum((Decimal(str(r.net_amt)) for r in all_items), ZERO_DECIMAL)

        totals_row = {
            "items_count": total_items_distinct,
            "qty_sold": total_qty,
            "gross": total_gross,
            "discount": total_disc,
            "tax": total_tax,
            "net": total_net,
        }

        # Manual pagination of grouped items
        page = criteria["page"]
        per_page = criteria["per_page"]
        total_count = len(all_items)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paged_items = all_items[start_idx:end_idx]

        rows = []
        for r in paged_items:
            rows.append({
                "item_id": r.id,
                "item_name": r.item_name,
                "sku": r.sku,
                "category_name": r.cat_name or "-",
                "qty_sold": Decimal(str(r.qty_sold)),
                "gross": Decimal(str(r.gross_amt)),
                "discount": Decimal(str(r.discount_amt)),
                "tax": Decimal(str(r.tax_amt)),
                "net": Decimal(str(r.net_amt)),
                "invoices_count": r.invoices_count,
            })

        total_pages = max(1, (total_count + per_page - 1) // per_page)
        pagination = {
            "page": page,
            "per_page": per_page,
            "total": total_count,
            "pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_num": page - 1,
            "next_num": page + 1,
        }

        return {
            "rows": rows,
            "totals_row": totals_row,
            "pagination": pagination,
        }

    @classmethod
    def get_day_wise_report(cls, base_query, criteria: Dict[str, Any]) -> Dict[str, Any]:
        """Generates Day-wise report with columns per active payment mode."""
        active_modes = PaymentService.get_active_modes()
        cutoff_str = cls.get_cutoff_str()

        # Query matching invoices eager loading payments
        invoices = base_query.options(
            selectinload(Invoice.payments).joinedload(InvoicePayment.payment_mode)
        ).all()

        # Group by business date
        days_map: Dict[date, Dict[str, Any]] = {}

        # Pre-seed every day in the range [from_date, to_date]
        curr = criteria["from_date_obj"]
        while curr <= criteria["to_date_obj"]:
            days_map[curr] = {
                "date": curr,
                "invoices_count": 0,
                "subtotal": ZERO_DECIMAL,
                "discount": ZERO_DECIMAL,
                "tax": ZERO_DECIMAL,
                "total": ZERO_DECIMAL,
                "mode_totals": {m.mode_code: ZERO_DECIMAL for m in active_modes},
            }
            curr += timedelta(days=1)

        for inv in invoices:
            b_date = get_business_date(inv.invoice_date, cutoff_str=cutoff_str)
            if b_date not in days_map:
                days_map[b_date] = {
                    "date": b_date,
                    "invoices_count": 0,
                    "subtotal": ZERO_DECIMAL,
                    "discount": ZERO_DECIMAL,
                    "tax": ZERO_DECIMAL,
                    "total": ZERO_DECIMAL,
                    "mode_totals": {m.mode_code: ZERO_DECIMAL for m in active_modes},
                }

            entry = days_map[b_date]
            entry["invoices_count"] += 1
            entry["subtotal"] += Decimal(str(inv.subtotal or 0))
            entry["discount"] += Decimal(str(inv.discount_amount or 0))
            entry["tax"] += Decimal(str(inv.tax_amount or 0))
            entry["total"] += Decimal(str(inv.payable_amount or 0))

            for p in inv.payments:
                if p.payment_mode and p.payment_mode.mode_code in entry["mode_totals"]:
                    entry["mode_totals"][p.payment_mode.mode_code] += Decimal(str(p.amount or 0))

        # Sort days descending or ascending
        sort_order = criteria.get("sort_order") or "desc"
        sorted_dates = sorted(days_map.keys(), reverse=(sort_order == "desc"))

        rows = [days_map[d] for d in sorted_dates]

        # Compute totals row
        total_days = len(rows)
        tot_invoices = sum(r["invoices_count"] for r in rows)
        tot_subtotal = sum(r["subtotal"] for r in rows)
        tot_disc = sum(r["discount"] for r in rows)
        tot_tax = sum(r["tax"] for r in rows)
        tot_total = sum(r["total"] for r in rows)
        mode_column_totals = {
            m.mode_code: sum(r["mode_totals"].get(m.mode_code, ZERO_DECIMAL) for r in rows)
            for m in active_modes
        }

        totals_row = {
            "days_count": total_days,
            "invoices_count": tot_invoices,
            "subtotal": tot_subtotal,
            "discount": tot_disc,
            "tax": tot_tax,
            "total": tot_total,
            "mode_column_totals": mode_column_totals,
        }

        page = criteria["page"]
        per_page = criteria["per_page"]
        total_count = len(rows)
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paged_rows = rows[start_idx:end_idx]

        total_pages = max(1, (total_count + per_page - 1) // per_page)
        pagination = {
            "page": page,
            "per_page": per_page,
            "total": total_count,
            "pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "prev_num": page - 1,
            "next_num": page + 1,
        }

        return {
            "rows": paged_rows,
            "active_modes": active_modes,
            "totals_row": totals_row,
            "pagination": pagination,
        }

    @classmethod
    def get_full_report_data(cls, args: Dict[str, Any]) -> Dict[str, Any]:
        """Main dispatcher returning complete view context."""
        criteria = cls.build_filter_criteria(args)
        base_query = cls.get_base_invoice_query(criteria)

        # 1. Payment breakdown summary cards
        payment_breakdown, total_sales = cls.get_payment_breakdown(base_query)

        # 2. View specific data
        view = criteria["view"]
        if view == "item":
            report_result = cls.get_item_wise_report(base_query, criteria)
        elif view == "day":
            report_result = cls.get_day_wise_report(base_query, criteria)
        else:
            report_result = cls.get_invoice_wise_report(base_query, criteria)

        # Dropdown options
        categories = Category.query.filter_by(is_active=1).order_by(Category.cat_name.asc()).all()
        cashier_role = Role.query.filter_by(role_code="cashier").first()
        cashiers = User.query.filter_by(role_id=cashier_role.id, is_active=1).order_by(User.full_name.asc()).all() if cashier_role else []
        active_modes = PaymentService.get_active_modes()

        return {
            "criteria": criteria,
            "payment_breakdown": payment_breakdown,
            "total_sales": total_sales,
            "categories": categories,
            "cashiers": cashiers,
            "active_modes": active_modes,
            "report": report_result,
        }

    # =========================================================================
    # EXPORTS: PDF (ReportLab), EXCEL (.xlsx), CSV
    # =========================================================================

    @classmethod
    def generate_pdf(cls, args: Dict[str, Any], user_name: str = "Admin") -> io.BytesIO:
        """
        Generates clean, print-ready A4 PDF report using ReportLab:
        - Landscape for Invoice-wise, Portrait for Item-wise and Day-wise
        - Header: Org name, report title, parameters used, generated datetime & user
        - Table with column headings repeated on every page (repeatRows=1), totals row at end
        - Day-wise includes one column per payment mode
        - Footer: 'Page X of Y' and 'System by Noventrasystem'
        - Pure black text, simple sans-serif (Helvetica)
        """
        criteria = cls.build_filter_criteria(args)
        base_query = cls.get_base_invoice_query(criteria)
        view = criteria["view"]
        org_name = SettingService.get_value("org_name_en", "My Store") or "Noventra POS"

        buf = io.BytesIO()

        if view == "invoice":
            pagesize = landscape(A4)
            margin = 24
            report_title = "Invoice-wise Sales Report"
        elif view == "item":
            pagesize = portrait(A4)
            margin = 18
            report_title = "Item-wise Sales Report"
        else:
            pagesize = portrait(A4)
            margin = 18
            report_title = "Day-wise Sales Report"

        doc = SimpleDocTemplate(
            buf,
            pagesize=pagesize,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=22,
            bottomMargin=34,
        )

        styles = getSampleStyleSheet()
        org_style = ParagraphStyle(
            "ReportOrg",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=colors.black,
        )
        title_style = ParagraphStyle(
            "ReportHeadTitle",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            textColor=colors.black,
        )
        meta_style = ParagraphStyle(
            "ReportHeadMeta",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10.5,
            textColor=colors.black,
        )
        th_style = ParagraphStyle(
            "ReportTH",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            alignment=0,
        )
        th_right = ParagraphStyle(
            "ReportTHRight",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            alignment=2,
        )
        td_style = ParagraphStyle(
            "ReportTD",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            alignment=0,
        )
        td_right = ParagraphStyle(
            "ReportTDRight",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            alignment=2,
        )
        tot_style = ParagraphStyle(
            "ReportTot",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            alignment=0,
        )
        tot_right = ParagraphStyle(
            "ReportTotRight",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8.5,
            leading=10.5,
            textColor=colors.black,
            alignment=2,
        )

        grid_header_bg = colors.HexColor("#E7E6E6")
        grid_totals_bg = colors.HexColor("#E7E6E6")

        # Build Parameter description string
        if view == "invoice":
            cashier_name = "All"
            if criteria["cashier_id"]:
                c_user = User.query.get(criteria["cashier_id"])
                if c_user:
                    cashier_name = c_user.full_name
            mode_name = "All"
            if criteria["payment_mode"]:
                pm = PaymentMode.query.filter_by(mode_code=criteria["payment_mode"]).first()
                if pm:
                    mode_name = pm.mode_name
            params_str = (
                f"Date: {criteria['date_from']} to {criteria['date_to']} | "
                f"Invoice #: {criteria['invoice_no'] or 'All'} | "
                f"Cashier: {cashier_name} | "
                f"Payment Mode: {mode_name} | "
                f"Status: {criteria['status'].capitalize()}"
            )
        elif view == "item":
            cat_name = "All"
            if criteria["category_id"]:
                cat = Category.query.get(criteria["category_id"])
                if cat:
                    cat_name = cat.cat_name
            params_str = (
                f"Date: {criteria['date_from']} to {criteria['date_to']} | "
                f"Item/SKU: {criteria['item_query'] or 'All'} | "
                f"Category: {cat_name}"
            )
        else:
            mode_name = "All"
            if criteria["payment_mode"]:
                pm = PaymentMode.query.filter_by(mode_code=criteria["payment_mode"]).first()
                if pm:
                    mode_name = pm.mode_name
            params_str = (
                f"Date: {criteria['date_from']} to {criteria['date_to']} | "
                f"Payment Mode: {mode_name}"
            )

        now_pkt = to_pakistan_time(utc_now()).strftime("%d-%b-%Y %I:%M %p")
        gen_meta = f"Generated: {now_pkt} | User: {user_name}"

        story = [
            Paragraph(f"<b>{org_name}</b>", org_style),
            Spacer(1, 2),
            Paragraph(f"<b>{report_title}</b>", title_style),
            Spacer(1, 3),
            Paragraph(f"Parameters: {params_str}", meta_style),
            Paragraph(gen_meta, meta_style),
            Spacer(1, 8),
        ]

        if view == "invoice":
            inv_res = cls.get_invoice_wise_report(base_query, {**criteria, "page": 1, "per_page": 1000000})
            rows = inv_res["rows"]
            tot = inv_res["totals_row"]

            headers = [
                Paragraph("Invoice #", th_style),
                Paragraph("Date & Time", th_style),
                Paragraph("Cashier", th_style),
                Paragraph("Items", th_right),
                Paragraph("Subtotal", th_right),
                Paragraph("Discount", th_right),
                Paragraph("GST", th_right),
                Paragraph("Round Off", th_right),
                Paragraph("Total", th_right),
                Paragraph("Payment Mode", th_style),
                Paragraph("Status", th_style),
            ]
            table_data = [headers]

            if rows:
                for r in rows:
                    table_data.append([
                        Paragraph(r["invoice_no"], td_style),
                        Paragraph(r["date_time"], td_style),
                        Paragraph(r["cashier"], td_style),
                        Paragraph(str(int(r["item_count"])), td_right),
                        Paragraph(f"{r['subtotal']:,.2f}", td_right),
                        Paragraph(f"{r['discount']:,.2f}", td_right),
                        Paragraph(f"{r['tax']:,.2f}", td_right),
                        Paragraph(f"{r['round_off']:,.2f}", td_right),
                        Paragraph(f"{r['total']:,.2f}", td_right),
                        Paragraph(r["payment_display"]["summary"], td_style),
                        Paragraph(r["status_code"].capitalize(), td_style),
                    ])
            else:
                table_data.append([
                    Paragraph("No invoices found for the selected criteria", td_style),
                    "", "", "", "", "", "", "", "", "", ""
                ])

            totals_line = [
                Paragraph("<b>TOTAL</b>", tot_style),
                "",
                "",
                Paragraph(f"<b>{int(tot['items_count'])}</b>", tot_right),
                Paragraph(f"<b>{tot['subtotal']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['discount']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['tax']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['round_off']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['payable']:,.2f}</b>", tot_right),
                "",
                "",
            ]
            table_data.append(totals_line)

            col_widths = [86, 110, 80, 36, 70, 56, 56, 52, 74, 105, 62]
            t = Table(table_data, colWidths=col_widths, repeatRows=1)
            t_style = [
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), grid_header_bg),
                ("BACKGROUND", (0, -1), (-1, -1), grid_totals_bg),
                ("SPAN", (0, -1), (2, -1)),
            ]
            if not rows:
                t_style.append(("SPAN", (0, 1), (-1, 1)))
            t.setStyle(TableStyle(t_style))
            story.append(t)

        elif view == "item":
            item_res = cls.get_item_wise_report(base_query, {**criteria, "page": 1, "per_page": 1000000})
            rows = item_res["rows"]
            tot = item_res["totals_row"]

            headers = [
                Paragraph("Item Name", th_style),
                Paragraph("SKU", th_style),
                Paragraph("Category", th_style),
                Paragraph("Qty Sold", th_right),
                Paragraph("Gross", th_right),
                Paragraph("Discount", th_right),
                Paragraph("GST", th_right),
                Paragraph("Net", th_right),
                Paragraph("Invoices", th_right),
            ]
            table_data = [headers]

            if rows:
                for r in rows:
                    table_data.append([
                        Paragraph(r["item_name"], td_style),
                        Paragraph(r["sku"] or "-", td_style),
                        Paragraph(r["category_name"] or "-", td_style),
                        Paragraph(f"{r['qty_sold']:,.2f}", td_right),
                        Paragraph(f"{r['gross']:,.2f}", td_right),
                        Paragraph(f"{r['discount']:,.2f}", td_right),
                        Paragraph(f"{r['tax']:,.2f}", td_right),
                        Paragraph(f"{r['net']:,.2f}", td_right),
                        Paragraph(str(r["invoices_count"]), td_right),
                    ])
            else:
                table_data.append([
                    Paragraph("No items found for the selected criteria", td_style),
                    "", "", "", "", "", "", "", ""
                ])

            totals_line = [
                Paragraph("<b>TOTAL</b>", tot_style),
                "",
                "",
                Paragraph(f"<b>{tot['qty_sold']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['gross']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['discount']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['tax']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['net']:,.2f}</b>", tot_right),
                "",
            ]
            table_data.append(totals_line)

            col_widths = [126, 66, 66, 46, 58, 50, 50, 60, 36]
            t = Table(table_data, colWidths=col_widths, repeatRows=1)
            t_style = [
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), grid_header_bg),
                ("BACKGROUND", (0, -1), (-1, -1), grid_totals_bg),
                ("SPAN", (0, -1), (2, -1)),
            ]
            if not rows:
                t_style.append(("SPAN", (0, 1), (-1, 1)))
            t.setStyle(TableStyle(t_style))
            story.append(t)

        else:  # day
            day_res = cls.get_day_wise_report(base_query, {**criteria, "page": 1, "per_page": 1000000})
            rows = day_res["rows"]
            tot = day_res["totals_row"]
            active_modes = day_res["active_modes"]

            headers = [
                Paragraph("Date", th_style),
                Paragraph("Invoices", th_right),
                Paragraph("Subtotal", th_right),
                Paragraph("Discount", th_right),
                Paragraph("GST", th_right),
                Paragraph("Total", th_right),
            ]
            for m in active_modes:
                headers.append(Paragraph(m.mode_name, th_right))
            table_data = [headers]

            if rows:
                for r in rows:
                    date_str = r["date"].strftime("%d-%b-%Y") if hasattr(r["date"], "strftime") else str(r["date"])
                    row = [
                        Paragraph(date_str, td_style),
                        Paragraph(str(r["invoices_count"]), td_right),
                        Paragraph(f"{r['subtotal']:,.2f}", td_right),
                        Paragraph(f"{r['discount']:,.2f}", td_right),
                        Paragraph(f"{r['tax']:,.2f}", td_right),
                        Paragraph(f"{r['total']:,.2f}", td_right),
                    ]
                    for m in active_modes:
                        row.append(Paragraph(f"{r['mode_totals'].get(m.mode_code, ZERO_DECIMAL):,.2f}", td_right))
                    table_data.append(row)
            else:
                table_data.append([
                    Paragraph("No sales found for the selected date range", td_style),
                    *["" for _ in range(len(headers) - 1)]
                ])

            totals_line = [
                Paragraph("<b>TOTAL</b>", tot_style),
                Paragraph(f"<b>{tot['invoices_count']}</b>", tot_right),
                Paragraph(f"<b>{tot['subtotal']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['discount']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['tax']:,.2f}</b>", tot_right),
                Paragraph(f"<b>{tot['total']:,.2f}</b>", tot_right),
            ]
            for m in active_modes:
                totals_line.append(Paragraph(f"<b>{tot['mode_column_totals'].get(m.mode_code, ZERO_DECIMAL):,.2f}</b>", tot_right))
            table_data.append(totals_line)

            fixed_w = [70, 45, 65, 52, 52, 68]
            rem_w = max(50, 558 - sum(fixed_w))
            mode_w = rem_w / len(active_modes) if active_modes else 52
            col_widths = fixed_w + [mode_w] * len(active_modes)

            t = Table(table_data, colWidths=col_widths, repeatRows=1)
            t_style = [
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), grid_header_bg),
                ("BACKGROUND", (0, -1), (-1, -1), grid_totals_bg),
            ]
            if not rows:
                t_style.append(("SPAN", (0, 1), (-1, 1)))
            t.setStyle(TableStyle(t_style))
            story.append(t)

        doc.build(story, canvasmaker=NumberedCanvas)
        buf.seek(0)
        return buf

    @classmethod
    def export_excel(cls, args: Dict[str, Any]) -> io.BytesIO:
        """
        Generates styled Excel workbook (.xlsx) of full filtered result:
        - Sheet 1: Sales Data (Invoice-wise / Item-wise / Day-wise) with number formatting and totals row
        - Sheet 2: Payment Breakdown with mode totals and shares
        """
        criteria = cls.build_filter_criteria(args)
        base_query = cls.get_base_invoice_query(criteria)
        payment_breakdown, total_sales = cls.get_payment_breakdown(base_query)
        view = criteria["view"]

        wb = openpyxl.Workbook()
        ws_sales = wb.active
        ws_sales.title = f"Sales ({view.capitalize()}-wise)"

        # Style definitions: Excel grid style
        title_font = Font(name="Calibri", size=14, bold=True, color="1E293B")
        header_fill = PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid")
        header_font = Font(name="Calibri", size=10, bold=True, color="000000")
        total_fill = PatternFill(start_color="E7E6E6", end_color="E7E6E6", fill_type="solid")
        total_font = Font(name="Calibri", size=10, bold=True, color="000000")
        thin_border = Border(
            left=Side(style="thin", color="000000"),
            right=Side(style="thin", color="000000"),
            top=Side(style="thin", color="000000"),
            bottom=Side(style="thin", color="000000"),
        )

        # Title & Filters Info
        ws_sales.append(["Noventra POS - Sales Report"])
        ws_sales.cell(row=1, column=1).font = title_font

        filter_desc = f"Date Range: {criteria['date_from']} to {criteria['date_to']} | Status: {criteria['status'].capitalize()}"
        if criteria["invoice_no"]:
            filter_desc += f" | Invoice: {criteria['invoice_no']}"
        if criteria["item_query"]:
            filter_desc += f" | Item: {criteria['item_query']}"
        if criteria["payment_mode"]:
            filter_desc += f" | Mode: {criteria['payment_mode'].capitalize()}"
        ws_sales.append([filter_desc])
        ws_sales.append([])  # Blank row

        start_row = 4

        if view == "invoice":
            headers = ["Invoice #", "Date & Time", "Cashier", "Items", "Subtotal", "Discount", "GST", "Round Off", "Total", "Payment Mode", "Remarks"]
            ws_sales.append(headers)

            # Query all invoices matching base_query
            invoices = base_query.options(
                joinedload(Invoice.cashier),
                selectinload(Invoice.items),
                selectinload(Invoice.payments).joinedload(InvoicePayment.payment_mode),
            ).order_by(Invoice.invoice_date.desc()).all()

            for inv in invoices:
                pkt_time = to_pakistan_time(inv.invoice_date)
                dt_str = pkt_time.strftime("%d-%b-%Y %I:%M %p") if pkt_time else ""
                item_count = sum(float(it.quantity) for it in inv.items)
                modes = list(dict.fromkeys(p.payment_mode.mode_name for p in inv.payments if p.payment_mode))
                mode_str = f"Split ({', '.join(modes)})" if len(modes) > 1 else (modes[0] if modes else "Cash")

                ws_sales.append([
                    inv.invoice_no,
                    dt_str,
                    inv.cashier.full_name if inv.cashier else "Cashier",
                    item_count,
                    float(inv.subtotal),
                    float(inv.discount_amount or 0),
                    float(inv.tax_amount or 0),
                    float(inv.round_off or 0),
                    float(inv.payable_amount or 0),
                    mode_str,
                    inv.notes or "",
                ])

            last_data_row = ws_sales.max_row
            total_row_idx = last_data_row + 1

            # Totals row formula
            ws_sales.append([
                "Total",
                f"{len(invoices)} Invoices",
                "",
                f"=SUM(D{start_row+1}:D{last_data_row})",
                f"=SUM(E{start_row+1}:E{last_data_row})",
                f"=SUM(F{start_row+1}:F{last_data_row})",
                f"=SUM(G{start_row+1}:G{last_data_row})",
                f"=SUM(H{start_row+1}:H{last_data_row})",
                f"=SUM(I{start_row+1}:I{last_data_row})",
                "",
                ""
            ])

            # Apply formatting
            for row in range(start_row + 1, total_row_idx + 1):
                ws_sales.cell(row=row, column=4).number_format = "#,##0"
                for col in (5, 6, 7, 8, 9):
                    ws_sales.cell(row=row, column=col).number_format = "#,##0.00"

        elif view == "item":
            headers = ["Item Name", "SKU", "Category", "Qty Sold", "Gross", "Discount", "GST", "Net", "Invoices"]
            ws_sales.append(headers)

            item_res = cls.get_item_wise_report(base_query, {**criteria, "page": 1, "per_page": 100000})
            for it in item_res["rows"]:
                ws_sales.append([
                    it["item_name"],
                    it["sku"],
                    it["category_name"],
                    float(it["qty_sold"]),
                    float(it["gross"]),
                    float(it["discount"]),
                    float(it["tax"]),
                    float(it["net"]),
                    it["invoices_count"],
                ])

            last_data_row = ws_sales.max_row
            total_row_idx = last_data_row + 1
            ws_sales.append([
                "Total",
                f"{len(item_res['rows'])} Items",
                "",
                f"=SUM(D{start_row+1}:D{last_data_row})",
                f"=SUM(E{start_row+1}:E{last_data_row})",
                f"=SUM(F{start_row+1}:F{last_data_row})",
                f"=SUM(G{start_row+1}:G{last_data_row})",
                f"=SUM(H{start_row+1}:H{last_data_row})",
                "",
            ])
            for row in range(start_row + 1, total_row_idx + 1):
                ws_sales.cell(row=row, column=4).number_format = "#,##0"
                for col in (5, 6, 7, 8):
                    ws_sales.cell(row=row, column=col).number_format = "#,##0.00"

        else:  # day
            active_modes = PaymentService.get_active_modes()
            headers = ["Date", "Invoices", "Subtotal", "Discount", "GST", "Total"] + [m.mode_name for m in active_modes]
            ws_sales.append(headers)

            day_res = cls.get_day_wise_report(base_query, {**criteria, "page": 1, "per_page": 100000})
            for d in day_res["rows"]:
                row_vals = [
                    d["date"].strftime("%d-%b-%Y"),
                    d["invoices_count"],
                    float(d["subtotal"]),
                    float(d["discount"]),
                    float(d["tax"]),
                    float(d["total"]),
                ]
                for m in active_modes:
                    row_vals.append(float(d["mode_totals"].get(m.mode_code, 0)))
                ws_sales.append(row_vals)

            last_data_row = ws_sales.max_row
            total_row_idx = last_data_row + 1
            tot_row = ["Total", f"=SUM(B{start_row+1}:B{last_data_row})"]
            for col_idx in range(3, len(headers) + 1):
                col_letter = get_column_letter(col_idx)
                tot_row.append(f"=SUM({col_letter}{start_row+1}:{col_letter}{last_data_row})")
            ws_sales.append(tot_row)

            for row in range(start_row + 1, total_row_idx + 1):
                ws_sales.cell(row=row, column=2).number_format = "#,##0"
                for col in range(3, len(headers) + 1):
                    ws_sales.cell(row=row, column=col).number_format = "#,##0.00"

        # Apply thin border to every cell in table (header, data, total)
        for r in range(start_row, total_row_idx + 1):
            for c in range(1, len(headers) + 1):
                ws_sales.cell(row=r, column=c).border = thin_border

        # Apply header and totals formatting on Sheet 1
        for col in range(1, len(headers) + 1):
            h_cell = ws_sales.cell(row=start_row, column=col)
            h_cell.fill = header_fill
            h_cell.font = header_font
            h_cell.alignment = Alignment(vertical="center")

            t_cell = ws_sales.cell(row=total_row_idx, column=col)
            t_cell.fill = total_fill
            t_cell.font = total_font
            t_cell.alignment = Alignment(vertical="center")

        # Freeze header row on Sheet 1
        ws_sales.freeze_panes = f"A{start_row + 1}"

        # Sheet 2: Payment Breakdown
        ws_pay = wb.create_sheet(title="Payment Breakdown")
        ws_pay.append(["Payment Breakdown"])
        ws_pay.cell(row=1, column=1).font = title_font
        ws_pay.append([filter_desc])
        ws_pay.append([])

        pay_headers = ["Payment Mode", "Total Sales", "Share (%)"]
        ws_pay.append(pay_headers)
        p_start_row = 4

        for item in payment_breakdown:
            ws_pay.append([
                item["mode_name"],
                float(item["total_amt"]),
                item["share_pct"] / 100.0,
            ])

        p_last_row = ws_pay.max_row
        p_tot_row = p_last_row + 1
        ws_pay.append([
            "Total",
            f"=SUM(B{p_start_row+1}:B{p_last_row})",
            f"=SUM(C{p_start_row+1}:C{p_last_row})",
        ])

        # Apply thin border to every cell in Payment Breakdown table
        for r in range(p_start_row, p_tot_row + 1):
            for c in range(1, len(pay_headers) + 1):
                ws_pay.cell(row=r, column=c).border = thin_border

        for col in (1, 2, 3):
            h_cell = ws_pay.cell(row=p_start_row, column=col)
            h_cell.fill = header_fill
            h_cell.font = header_font
            h_cell.alignment = Alignment(vertical="center")

            t_cell = ws_pay.cell(row=p_tot_row, column=col)
            t_cell.fill = total_fill
            t_cell.font = total_font
            t_cell.alignment = Alignment(vertical="center")

        for row in range(p_start_row + 1, p_tot_row + 1):
            ws_pay.cell(row=row, column=2).number_format = "#,##0.00"
            ws_pay.cell(row=row, column=3).number_format = "0.0%"

        # Freeze header row on Sheet 2
        ws_pay.freeze_panes = f"A{p_start_row + 1}"

        # Auto-fit column widths for both sheets
        for ws in (ws_sales, ws_pay):
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    val_str = str(cell.value or "")
                    if val_str.startswith("="):
                        val_str = "123,456.78"
                    max_len = max(max_len, len(val_str))
                ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    @classmethod
    def export_csv(cls, args: Dict[str, Any]) -> io.StringIO:
        """Generates CSV of full filtered result."""
        criteria = cls.build_filter_criteria(args)
        base_query = cls.get_base_invoice_query(criteria)
        view = criteria["view"]

        output = io.StringIO()
        writer = csv.writer(output)

        if view == "invoice":
            writer.writerow(["Invoice #", "Date & Time", "Cashier", "Items", "Subtotal", "Discount", "GST", "Round Off", "Total", "Payment Mode", "Remarks"])
            invoices = base_query.options(
                joinedload(Invoice.cashier),
                selectinload(Invoice.items),
                selectinload(Invoice.payments).joinedload(InvoicePayment.payment_mode),
            ).order_by(Invoice.invoice_date.desc()).all()

            for inv in invoices:
                pkt_time = to_pakistan_time(inv.invoice_date)
                dt_str = pkt_time.strftime("%d-%b-%Y %I:%M %p") if pkt_time else ""
                item_count = sum(float(it.quantity) for it in inv.items)
                modes = list(dict.fromkeys(p.payment_mode.mode_name for p in inv.payments if p.payment_mode))
                mode_str = f"Split ({', '.join(modes)})" if len(modes) > 1 else (modes[0] if modes else "Cash")
                writer.writerow([
                    inv.invoice_no,
                    dt_str,
                    inv.cashier.full_name if inv.cashier else "Cashier",
                    item_count,
                    f"{inv.subtotal:.2f}",
                    f"{(inv.discount_amount or 0):.2f}",
                    f"{(inv.tax_amount or 0):.2f}",
                    f"{(inv.round_off or 0):.2f}",
                    f"{(inv.payable_amount or 0):.2f}",
                    mode_str,
                    inv.notes or "",
                ])

        elif view == "item":
            writer.writerow(["Item Name", "SKU", "Category", "Qty Sold", "Gross", "Discount", "GST", "Net", "Invoices"])
            item_res = cls.get_item_wise_report(base_query, {**criteria, "page": 1, "per_page": 100000})
            for it in item_res["rows"]:
                writer.writerow([
                    it["item_name"],
                    it["sku"],
                    it["category_name"],
                    f"{it['qty_sold']:.2f}",
                    f"{it['gross']:.2f}",
                    f"{it['discount']:.2f}",
                    f"{it['tax']:.2f}",
                    f"{it['net']:.2f}",
                    it["invoices_count"],
                ])

        else:  # day
            active_modes = PaymentService.get_active_modes()
            writer.writerow(["Date", "Invoices", "Subtotal", "Discount", "GST", "Total"] + [m.mode_name for m in active_modes])
            day_res = cls.get_day_wise_report(base_query, {**criteria, "page": 1, "per_page": 100000})
            for d in day_res["rows"]:
                row = [
                    d["date"].strftime("%d-%b-%Y"),
                    d["invoices_count"],
                    f"{d['subtotal']:.2f}",
                    f"{d['discount']:.2f}",
                    f"{d['tax']:.2f}",
                    f"{d['total']:.2f}",
                ]
                for m in active_modes:
                    row.append(f"{d['mode_totals'].get(m.mode_code, 0):.2f}")
                writer.writerow(row)

        output.seek(0)
        return output
