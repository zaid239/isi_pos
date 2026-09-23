"""
Stock Management Service.
Handles:
1. Multi-line Stock In receiving with weighted average cost calculation:
   new_cost = (old_qty * old_cost + in_qty * in_cost) / (old_qty + in_qty)
2. Stock Out (wastage, damage, staff use, other) strictly disallowing negative stock.
3. Physical Stock Adjustment (Manager / Super Admin) computing and recording delta.
4. Filtered Stock List with valuation and low-stock highlighting.
5. Excel export with professional formatting via openpyxl.
6. Chronologically reconciled Item Ledger with baseline opening balance row.
7. Idempotency protection to prevent double-submissions.
"""
import io
from datetime import datetime, date
from decimal import Decimal
from typing import Dict, Any, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload, joinedload

from app.extensions import db
from app.models.item import Item, Category, Unit
from app.models.inventory import StockMovement, StockAdjustment
from app.models.doc_counter import DocCounter
from app.models.base import get_current_terminal_code
from app.services.calculation_service import quantize_money, quantize_qty, to_decimal, ZERO
from app.utils.timezone import utc_now, get_fiscal_year, to_pakistan_time


class StockService:
    @staticmethod
    def _is_idempotent_duplicate(key: Optional[str], op_type: str) -> bool:
        """Checks if this idempotency key was already recorded."""
        if not key or not str(key).strip():
            return False
        clean_key = str(key).strip()
        marker = f"IDEMP-{op_type.upper()}-{clean_key}"[:50]
        # Check ref_doc_no or notes containing idempotency marker
        existing = StockMovement.query.filter(
            or_(
                StockMovement.ref_doc_no == marker,
                StockMovement.ref_doc_no == clean_key[:50],
                StockMovement.notes.like(f"%[idemp:{clean_key}]%")
            )
        ).first()
        return existing is not None

    @classmethod
    def record_stock_in(
        cls,
        user_id: int,
        movement_date: Optional[Any] = None,
        supplier_name: Optional[str] = None,
        reference_no: Optional[str] = None,
        lines: Optional[List[Dict[str, Any]]] = None,
        notes: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> List[StockMovement]:
        """
        Receives stock across multiple items in a single atomic transaction.
        Applies weighted average cost if cost is entered for a line:
        new_cost = (old_qty * old_cost + in_qty * in_cost) / (old_qty + in_qty)
        """
        if not lines:
            raise ValueError("At least one item line is required for Stock In.")

        clean_idemp = str(idempotency_key).strip() if idempotency_key else None
        if clean_idemp and cls._is_idempotent_duplicate(clean_idemp, "IN"):
            # Already processed; return existing movements
            marker = f"IDEMP-IN-{clean_idemp}"
            return StockMovement.query.filter(
                or_(
                    StockMovement.ref_doc_no == marker,
                    StockMovement.ref_doc_no == clean_idemp,
                    StockMovement.notes.like(f"%[idemp:{clean_idemp}]%")
                )
            ).all()

        # Parse movement date
        mov_dt = utc_now()
        if movement_date:
            if isinstance(movement_date, (datetime, date)):
                mov_dt = movement_date if isinstance(movement_date, datetime) else datetime.combine(movement_date, datetime.min.time())
            elif isinstance(movement_date, str) and movement_date.strip():
                try:
                    mov_dt = datetime.strptime(movement_date.strip(), "%Y-%m-%d")
                except ValueError:
                    pass

        # Aggregate requested quantities and costs by item ID
        validated_lines = []
        for row in lines:
            iid = int(row.get("item_id") or 0)
            if not iid:
                raise ValueError("Invalid item selected in receiving line.")
            qty = quantize_qty(to_decimal(row.get("quantity")))
            if qty <= ZERO:
                raise ValueError("Stock In quantity must be greater than zero.")

            raw_cost = row.get("cost_price")
            unit_cost = None
            if raw_cost is not None and str(raw_cost).strip() != "":
                unit_cost = quantize_money(to_decimal(raw_cost))
                if unit_cost < ZERO:
                    raise ValueError("Cost price cannot be negative.")

            validated_lines.append({
                "item_id": iid,
                "quantity": qty,
                "cost_price": unit_cost,
                "notes": str(row.get("notes") or "").strip() or None,
            })

        # Lock items in ascending order to prevent deadlocks
        sorted_ids = sorted({line["item_id"] for line in validated_lines})
        locked_items = Item.query.filter(Item.id.in_(sorted_ids)).order_by(Item.id.asc()).with_for_update().all()
        item_map = {item.id: item for item in locked_items}

        if len(item_map) != len(sorted_ids):
            raise ValueError("One or more selected items were not found in the catalog.")

        created_movements = []
        terminal_code = get_current_terminal_code()
        raw_doc_ref = (reference_no.strip() if reference_no and reference_no.strip() else None) or (f"IDEMP-IN-{clean_idemp}" if clean_idemp else None)
        doc_ref = raw_doc_ref[:50] if raw_doc_ref else None

        for line in validated_lines:
            item = item_map[line["item_id"]]
            in_qty = line["quantity"]
            in_cost = line["cost_price"]

            old_qty = quantize_qty(to_decimal(item.current_stock))
            old_cost = quantize_money(to_decimal(item.cost_price))

            # Weighted Average Cost Calculation
            # new_cost = (old_qty * old_cost + in_qty * in_cost) / (old_qty + in_qty)
            # Applied ONLY when a cost is entered for that line.
            if in_cost is not None:
                if old_qty > ZERO:
                    weighted_total = (old_qty * old_cost) + (in_qty * in_cost)
                    total_qty = old_qty + in_qty
                    new_cost = quantize_money(weighted_total / total_qty)
                else:
                    new_cost = in_cost
                item.cost_price = new_cost

            qty_before = old_qty
            qty_after = quantize_qty(qty_before + in_qty)
            item.current_stock = qty_after

            # Assemble readable notes
            parts = []
            if supplier_name and supplier_name.strip():
                parts.append(f"Supplier: {supplier_name.strip()}")
            if reference_no and reference_no.strip():
                parts.append(f"Ref: {reference_no.strip()}")
            if notes and notes.strip():
                parts.append(notes.strip())
            if line["notes"]:
                parts.append(line["notes"])
            if clean_idemp:
                parts.append(f"[idemp:{clean_idemp}]")

            line_notes = " | ".join(parts) if parts else "Stock In"

            movement = StockMovement(
                item_id=item.id,
                terminal_code=terminal_code,
                qty_before=qty_before,
                qty_change=in_qty,
                qty_after=qty_after,
                movement_type="stock_in",
                ref_doc_type="purchase" if supplier_name else "stock_in",
                ref_doc_no=doc_ref,
                unit_cost=in_cost if in_cost is not None else item.cost_price,
                user_id=user_id,
                notes=line_notes[:4000],
                created_at=mov_dt,
            )
            db.session.add(movement)
            created_movements.append(movement)

        db.session.commit()
        return created_movements

    @classmethod
    def record_stock_out(
        cls,
        user_id: int,
        item_id: int,
        quantity: Any,
        reason: str,
        notes: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> StockMovement:
        """
        Deducts stock for wastage, damage, staff use, or other reasons.
        Guarantees stock cannot be taken below zero.
        """
        clean_idemp = str(idempotency_key).strip() if idempotency_key else None
        if clean_idemp and cls._is_idempotent_duplicate(clean_idemp, "OUT"):
            marker = f"IDEMP-OUT-{clean_idemp}"
            existing = StockMovement.query.filter(
                or_(
                    StockMovement.ref_doc_no == marker,
                    StockMovement.ref_doc_no == clean_idemp,
                    StockMovement.notes.like(f"%[idemp:{clean_idemp}]%")
                )
            ).first()
            if existing:
                return existing

        valid_reasons = {"wastage", "damage", "staff_use", "other"}
        clean_reason = (reason or "").strip().lower()
        if clean_reason not in valid_reasons:
            raise ValueError(f"Invalid stock out reason. Must be one of: {', '.join(sorted(valid_reasons))}.")

        out_qty = quantize_qty(to_decimal(quantity))
        if out_qty <= ZERO:
            raise ValueError("Stock Out quantity must be greater than zero.")

        item = Item.query.filter_by(id=item_id).with_for_update().one_or_none()
        if not item or item.deleted_at is not None:
            raise ValueError("Item not found or deleted.")

        current_qty = quantize_qty(to_decimal(item.current_stock))
        if out_qty > current_qty:
            raise ValueError(
                f"Cannot take stock below zero. Current stock on hand: {float(current_qty)}, "
                f"Requested deduction: {float(out_qty)}."
            )

        qty_before = current_qty
        qty_after = quantize_qty(qty_before - out_qty)
        item.current_stock = qty_after

        doc_ref = f"IDEMP-OUT-{clean_idemp}"[:50] if clean_idemp else None
        note_parts = [f"Reason: {clean_reason.replace('_', ' ').capitalize()}"]
        if notes and notes.strip():
            note_parts.append(notes.strip())
        if clean_idemp:
            note_parts.append(f"[idemp:{clean_idemp}]")
        full_note = " | ".join(note_parts)

        movement = StockMovement(
            item_id=item.id,
            terminal_code=get_current_terminal_code(),
            qty_before=qty_before,
            qty_change=-out_qty,
            qty_after=qty_after,
            movement_type="stock_out",
            ref_doc_type=clean_reason,
            ref_doc_no=doc_ref,
            unit_cost=item.cost_price,
            user_id=user_id,
            notes=full_note[:4000],
            created_at=utc_now(),
        )
        db.session.add(movement)
        db.session.commit()
        return movement

    @classmethod
    def record_adjustment(
        cls,
        user_id: int,
        item_id: int,
        counted_qty: Any,
        reason: str,
        notes: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[StockAdjustment, StockMovement]:
        """
        Records a physical inventory count adjustment for Manager / Super Admin.
        Calculates diff = counted - current_stock and applies movement.
        """
        clean_idemp = str(idempotency_key).strip() if idempotency_key else None
        if clean_idemp and cls._is_idempotent_duplicate(clean_idemp, "ADJ"):
            marker = f"IDEMP-ADJ-{clean_idemp}"
            existing_mv = StockMovement.query.filter(
                or_(
                    StockMovement.ref_doc_no == marker,
                    StockMovement.ref_doc_no == clean_idemp,
                    StockMovement.notes.like(f"%[idemp:{clean_idemp}]%")
                )
            ).first()
            if existing_mv:
                existing_adj = StockAdjustment.query.filter_by(adj_no=existing_mv.ref_doc_no).first()
                return existing_adj, existing_mv

        clean_reason = (reason or "").strip()
        if not clean_reason:
            raise ValueError("A specific reason is required for stock adjustments.")

        new_count = quantize_qty(to_decimal(counted_qty))
        if new_count < ZERO:
            raise ValueError("Physical count quantity cannot be negative.")

        item = Item.query.filter_by(id=item_id).with_for_update().one_or_none()
        if not item or item.deleted_at is not None:
            raise ValueError("Item not found or deleted.")

        qty_before = quantize_qty(to_decimal(item.current_stock))
        diff = quantize_qty(new_count - qty_before)

        # Generate official adjustment number
        fy = get_fiscal_year()
        adj_no = DocCounter.allocate_next_number(
            doc_type="adjustment",
            fiscal_year=fy,
        )

        # Update item stock to physical count
        item.current_stock = new_count

        # Create StockAdjustment audit record
        adj_record = StockAdjustment(
            adj_no=adj_no,
            item_id=item.id,
            adj_qty=diff,
            reason=clean_reason,
            user_id=user_id,
            approved_by=user_id,
            approved_at=utc_now(),
            status_code="approved",
        )
        db.session.add(adj_record)

        # Create StockMovement
        note_parts = [f"Count: {float(new_count)} (diff: {float(diff):+})", f"Reason: {clean_reason}"]
        if notes and notes.strip():
            note_parts.append(notes.strip())
        if clean_idemp:
            note_parts.append(f"[idemp:{clean_idemp}]")
        full_note = " | ".join(note_parts)

        movement = StockMovement(
            item_id=item.id,
            terminal_code=get_current_terminal_code(),
            qty_before=qty_before,
            qty_change=diff,
            qty_after=new_count,
            movement_type="adjustment",
            ref_doc_type="adjustment",
            ref_doc_no=adj_no,
            unit_cost=item.cost_price,
            user_id=user_id,
            notes=full_note[:4000],
            created_at=utc_now(),
        )
        db.session.add(movement)
        db.session.commit()
        return adj_record, movement

    @classmethod
    def get_stock_list_query(
        cls,
        search: Optional[str] = None,
        category_id: Optional[int] = None,
        low_stock_only: bool = False,
        sort_by: str = "name_asc",
    ):
        """Returns base query for tracked items in stock management."""
        query = Item.query.options(
            joinedload(Item.unit),
            joinedload(Item.category),
        ).filter(
            Item.is_stock_tracked == 1,
            Item.is_active == 1,
            Item.deleted_at.is_(None),
        )

        if category_id:
            query = query.filter(Item.category_id == category_id)

        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    func.upper(Item.item_name).like(term.upper()),
                    func.upper(Item.sku).like(term.upper()),
                    Item.barcode.like(term),
                )
            )

        if low_stock_only:
            query = query.filter(Item.current_stock <= Item.reorder_level)

        if sort_by == "stock_asc":
            query = query.order_by(Item.current_stock.asc())
        elif sort_by == "stock_desc":
            query = query.order_by(Item.current_stock.desc())
        elif sort_by == "value_desc":
            query = query.order_by((Item.current_stock * Item.cost_price).desc())
        elif sort_by == "name_desc":
            query = query.order_by(Item.item_name.desc())
        else:
            query = query.order_by(Item.item_name.asc())

        return query

    @classmethod
    def get_stock_summary(cls) -> Dict[str, Any]:
        """Calculates global inventory valuation and alert metrics for tracked items."""
        items = Item.query.filter(
            Item.is_stock_tracked == 1,
            Item.is_active == 1,
            Item.deleted_at.is_(None)
        ).all()

        total_valuation = ZERO
        low_stock_count = 0
        out_of_stock_count = 0

        for item in items:
            c_qty = quantize_qty(to_decimal(item.current_stock))
            c_cost = quantize_money(to_decimal(item.cost_price))
            if c_qty > ZERO:
                total_valuation += quantize_money(c_qty * c_cost)

            if c_qty <= ZERO:
                out_of_stock_count += 1
            elif c_qty <= to_decimal(item.reorder_level):
                low_stock_count += 1

        return {
            "total_items": len(items),
            "total_valuation": quantize_money(total_valuation),
            "low_stock_count": low_stock_count,
            "out_of_stock_count": out_of_stock_count,
        }

    @classmethod
    def export_stock_excel(
        cls,
        search: Optional[str] = None,
        category_id: Optional[int] = None,
        low_stock_only: bool = False,
    ) -> io.BytesIO:
        """Exports the tracked stock list into a styled Excel workbook."""
        query = cls.get_stock_list_query(
            search=search,
            category_id=category_id,
            low_stock_only=low_stock_only,
            sort_by="name_asc"
        )
        items = query.all()

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Stock Inventory"

        # Styles
        title_font = Font(name="Calibri", size=14, bold=True, color="1E293B")
        header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
        header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        total_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        total_font = Font(name="Calibri", size=11, bold=True, color="000000")
        low_stock_fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")
        out_stock_fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color="E2E8F0"),
            right=Side(style="thin", color="E2E8F0"),
            top=Side(style="thin", color="E2E8F0"),
            bottom=Side(style="thin", color="E2E8F0"),
        )
        double_bottom = Border(
            top=Side(style="thin", color="CBD5E1"),
            bottom=Side(style="double", color="000000"),
        )

        ws.append(["Noventra POS - Current Stock Valuation Report"])
        ws.cell(row=1, column=1).font = title_font
        ws.append([f"Generated at: {to_pakistan_time(utc_now()).strftime('%Y-%m-%d %I:%M %p PKT')} | Filter: {'Low Stock Only' if low_stock_only else 'All Tracked Items'}"])
        ws.append([])

        headers = [
            "SKU", "Barcode", "Item Name", "Category", "Unit",
            "Cost Price", "Current Stock", "Reorder Level", "Stock Value (Cost)", "Status"
        ]
        ws.append(headers)
        header_row_idx = 4
        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=header_row_idx, column=col_idx)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center" if col_idx in (1, 2, 5, 10) else ("right" if col_idx in (6, 7, 8, 9) else "left"))

        total_value = ZERO
        current_row = header_row_idx + 1

        for item in items:
            c_qty = quantize_qty(to_decimal(item.current_stock))
            c_cost = quantize_money(to_decimal(item.cost_price))
            val = quantize_money(c_qty * c_cost) if c_qty > ZERO else ZERO
            total_value += val

            status_txt = "In Stock"
            if c_qty <= ZERO:
                status_txt = "Out of Stock"
            elif c_qty <= to_decimal(item.reorder_level):
                status_txt = "Low Stock"

            row_data = [
                item.sku,
                item.barcode or "",
                item.item_name,
                item.category.cat_name if item.category else "-",
                item.unit.unit_code if item.unit else "pcs",
                float(c_cost),
                float(c_qty),
                float(to_decimal(item.reorder_level)),
                float(val),
                status_txt,
            ]
            ws.append(row_data)

            # Cell formatting & borders
            for c_idx in range(1, len(headers) + 1):
                cell = ws.cell(row=current_row, column=c_idx)
                cell.border = thin_border
                if c_idx in (6, 9):
                    cell.number_format = "#,##0.00"
                    cell.alignment = Alignment(horizontal="right")
                elif c_idx in (7, 8):
                    cell.number_format = "#,##0.000"
                    cell.alignment = Alignment(horizontal="right")
                elif c_idx in (1, 2, 5, 10):
                    cell.alignment = Alignment(horizontal="center")

                if status_txt == "Out of Stock":
                    cell.fill = out_stock_fill
                elif status_txt == "Low Stock":
                    cell.fill = low_stock_fill

            current_row += 1

        # Summary / Total Row
        summary_row = ["TOTALS", "", f"{len(items)} Items", "", "", "", "", "", float(total_value), ""]
        ws.append(summary_row)
        for c_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=current_row, column=c_idx)
            cell.font = total_font
            cell.fill = total_fill
            cell.border = double_bottom
            if c_idx == 9:
                cell.number_format = "#,##0.00"
                cell.alignment = Alignment(horizontal="right")
            elif c_idx == 1:
                cell.alignment = Alignment(horizontal="left")

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return output

    @classmethod
    def get_item_ledger(
        cls,
        item_id: int,
        date_from: Optional[Any] = None,
        date_to: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Compiles chronological ledger history for an item.
        Calculates synthesized opening balance:
        baseline_opening = current_stock - sum(all_movement_qty_changes)
        Guarantees that running balance reconciles exactly with item.current_stock.
        """
        item = Item.query.options(
            joinedload(Item.unit),
            joinedload(Item.category),
        ).filter_by(id=item_id).first()

        if not item:
            raise ValueError(f"Item ID {item_id} not found.")

        # Query all movements for item in ascending chronological order
        all_movements = StockMovement.query.options(
            joinedload(StockMovement.user)
        ).filter(
            StockMovement.item_id == item_id
        ).order_by(StockMovement.created_at.asc(), StockMovement.id.asc()).all()

        current_stock = quantize_qty(to_decimal(item.current_stock))
        total_deltas = sum(to_decimal(m.qty_change) for m in all_movements)
        baseline_opening = quantize_qty(current_stock - total_deltas)

        # Parse date filters
        dt_from = None
        dt_to = None
        if date_from:
            if isinstance(date_from, (datetime, date)):
                dt_from = date_from if isinstance(date_from, datetime) else datetime.combine(date_from, datetime.min.time())
            elif isinstance(date_from, str) and date_from.strip():
                try:
                    dt_from = datetime.strptime(date_from.strip(), "%Y-%m-%d")
                except ValueError:
                    pass

        if date_to:
            if isinstance(date_to, (datetime, date)):
                dt_to = date_to if isinstance(date_to, datetime) else datetime.combine(date_to, datetime.max.time())
            elif isinstance(date_to, str) and date_to.strip():
                try:
                    dt_to = datetime.strptime(date_to.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                except ValueError:
                    pass

        # Split movements into prior (before date_from) and period
        prior_movements = []
        period_movements = []

        for m in all_movements:
            m_dt = m.created_at or utc_now()
            if dt_from and m_dt < dt_from:
                prior_movements.append(m)
            elif dt_to and m_dt > dt_to:
                continue
            else:
                period_movements.append(m)

        opening_balance = quantize_qty(baseline_opening + sum(to_decimal(m.qty_change) for m in prior_movements))

        # Build ledger rows
        ledger_rows = []
        running_bal = opening_balance

        # Baseline opening row
        ledger_rows.append({
            "is_opening": True,
            "datetime": dt_from if dt_from else (item.created_at or utc_now()),
            "movement_type": "Opening Balance",
            "badge_class": "bg-secondary",
            "ref_doc": "-",
            "user_name": "System Baseline",
            "qty_in": ZERO,
            "qty_out": ZERO,
            "balance": running_bal,
            "unit_cost": item.cost_price,
            "notes": "Reconciled initial inventory balance",
        })

        total_in = ZERO
        total_out = ZERO

        for m in period_movements:
            delta = quantize_qty(to_decimal(m.qty_change))
            running_bal = quantize_qty(running_bal + delta)

            m_in = delta if delta > ZERO else ZERO
            m_out = abs(delta) if delta < ZERO else ZERO
            total_in += m_in
            total_out += m_out

            m_type = (m.movement_type or "").lower()
            badge_class = "bg-primary"
            type_label = m_type.replace("_", " ").title()

            if m_type in ("sale", "pos"):
                badge_class = "bg-info text-dark"
                type_label = "POS Sale"
            elif m_type in ("stock_in", "purchase"):
                badge_class = "bg-success"
                type_label = "Stock In"
            elif m_type in ("stock_out", "wastage", "damage", "staff_use"):
                badge_class = "bg-danger"
                type_label = f"Stock Out ({m.ref_doc_type or 'out'})"
            elif m_type == "adjustment":
                badge_class = "bg-warning text-dark"
                type_label = "Adjustment"

            u_name = m.user.full_name if m.user else "System"

            ledger_rows.append({
                "is_opening": False,
                "id": m.id,
                "datetime": m.created_at,
                "movement_type": type_label,
                "badge_class": badge_class,
                "ref_doc": m.ref_doc_no or "-",
                "user_name": u_name,
                "qty_in": m_in,
                "qty_out": m_out,
                "balance": running_bal,
                "unit_cost": m.unit_cost or item.cost_price,
                "notes": m.notes or "",
            })

        closing_balance = running_bal

        return {
            "item": item,
            "rows": ledger_rows,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance,
            "current_stock": current_stock,
            "total_in": quantize_qty(total_in),
            "total_out": quantize_qty(total_out),
            "date_from": dt_from.strftime("%Y-%m-%d") if dt_from else "",
            "date_to": dt_to.strftime("%Y-%m-%d") if dt_to else "",
        }
