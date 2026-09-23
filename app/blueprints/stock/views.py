"""
Stock Management Views.
Exposes endpoints for:
- Stock Inventory List with valuation & Excel export
- Multi-line Stock In receiving (with weighted average cost calculation)
- Stock Out (wastage, damage, staff use, other)
- Physical Inventory Count & Adjustment (Manager / Super Admin)
- Chronologically reconciled Item Ledger
- JSON Item info API for responsive dynamic forms
All endpoints are strictly protected by @role_required("super_admin", "manager").
"""
import uuid
from decimal import Decimal
from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user

from app.extensions import db
from app.blueprints.stock import stock_bp
from app.utils.decorators import role_required
from app.models.item import Item, Category, Unit
from app.services.stock_service import StockService
from app.services.setting_service import SettingService
from app.utils.timezone import utc_now, to_pakistan_time


@stock_bp.route("/")
@login_required
@role_required("super_admin", "manager")
def index():
    """Stock inventory dashboard & list of tracked items."""
    search = request.args.get("q", "").strip()
    category_id = request.args.get("category_id", type=int)
    low_stock_only = request.args.get("low_stock") in ("1", "true", "True")
    sort_by = request.args.get("sort", "name_asc")
    page = request.args.get("page", 1, type=int)

    base_query = StockService.get_stock_list_query(
        search=search,
        category_id=category_id,
        low_stock_only=low_stock_only,
        sort_by=sort_by,
    )
    pagination = base_query.paginate(page=page, per_page=20, error_out=False)
    summary = StockService.get_stock_summary()
    categories = Category.query.filter_by(deleted_at=None, is_active=1).order_by(Category.cat_name).all()

    return render_template(
        "stock/index.html",
        items=pagination.items,
        pagination=pagination,
        categories=categories,
        summary=summary,
        current_search=search,
        current_category=category_id,
        current_low_stock=low_stock_only,
        current_sort=sort_by,
    )


@stock_bp.route("/export")
@login_required
@role_required("super_admin", "manager")
def export():
    """Streams an Excel workbook containing current stock valuation and status."""
    search = request.args.get("q", "").strip()
    category_id = request.args.get("category_id", type=int)
    low_stock_only = request.args.get("low_stock") in ("1", "true", "True")

    stream = StockService.export_stock_excel(
        search=search,
        category_id=category_id,
        low_stock_only=low_stock_only,
    )
    timestamp = to_pakistan_time(utc_now()).strftime("%Y%m%d_%H%M%S")
    filename = f"Stock_Inventory_{timestamp}.xlsx"

    return send_file(
        stream,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@stock_bp.route("/in", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def stock_in():
    """Multi-line receiving of new inventory."""
    if request.method == "POST":
        idempotency_key = request.form.get("idempotency_key", "").strip() or None
        supplier_name = request.form.get("supplier_name", "").strip() or None
        reference_no = request.form.get("reference_no", "").strip() or None
        movement_date = request.form.get("movement_date", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        # Parse dynamic item lines
        item_ids = request.form.getlist("item_id[]")
        quantities = request.form.getlist("quantity[]")
        cost_prices = request.form.getlist("cost_price[]")
        line_notes = request.form.getlist("line_notes[]")

        lines = []
        for i in range(len(item_ids)):
            raw_id = (item_ids[i] or "").strip()
            raw_qty = (quantities[i] or "").strip() if i < len(quantities) else ""
            raw_cost = (cost_prices[i] or "").strip() if i < len(cost_prices) else ""
            raw_note = (line_notes[i] or "").strip() if i < len(line_notes) else ""

            if raw_id and raw_qty:
                lines.append({
                    "item_id": int(raw_id),
                    "quantity": raw_qty,
                    "cost_price": raw_cost if raw_cost else None,
                    "notes": raw_note or None,
                })

        if not lines:
            flash("Please add at least one valid item line to record stock in.", "warning")
            return redirect(url_for("stock.stock_in"))

        try:
            movements = StockService.record_stock_in(
                user_id=current_user.id,
                movement_date=movement_date,
                supplier_name=supplier_name,
                reference_no=reference_no,
                lines=lines,
                notes=notes,
                idempotency_key=idempotency_key,
            )
            flash(f"Stock In recorded successfully for {len(movements)} item line(s).", "success")
            return redirect(url_for("stock.index"))
        except ValueError as val_err:
            flash(str(val_err), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Failed to record Stock In: {exc}", "danger")

    # GET request
    tracked_items = Item.query.filter_by(is_stock_tracked=1, is_active=1, deleted_at=None).order_by(Item.item_name).all()
    today_str = to_pakistan_time(utc_now()).strftime("%Y-%m-%d")
    new_idempotency_key = str(uuid.uuid4())

    return render_template(
        "stock/stock_in.html",
        items=tracked_items,
        today_str=today_str,
        idempotency_key=new_idempotency_key,
    )


@stock_bp.route("/out", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def stock_out():
    """Stock reduction for wastage, damage, staff use, or other reasons."""
    if request.method == "POST":
        idempotency_key = request.form.get("idempotency_key", "").strip() or None
        item_id = request.form.get("item_id", type=int)
        quantity = request.form.get("quantity", "").strip()
        reason = request.form.get("reason", "").strip()
        notes = request.form.get("notes", "").strip() or None

        if not item_id or not quantity or not reason:
            flash("Item, quantity, and reason are required.", "warning")
            return redirect(url_for("stock.stock_out"))

        try:
            mv = StockService.record_stock_out(
                user_id=current_user.id,
                item_id=item_id,
                quantity=quantity,
                reason=reason,
                notes=notes,
                idempotency_key=idempotency_key,
            )
            flash(f"Stock Out recorded successfully. Deducted {abs(float(mv.qty_change))} unit(s).", "success")
            return redirect(url_for("stock.index"))
        except ValueError as val_err:
            flash(str(val_err), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Failed to record Stock Out: {exc}", "danger")

    # GET request
    preselected_id = request.args.get("item_id", type=int)
    tracked_items = Item.query.filter_by(is_stock_tracked=1, is_active=1, deleted_at=None).order_by(Item.item_name).all()
    new_idempotency_key = str(uuid.uuid4())

    return render_template(
        "stock/stock_out.html",
        items=tracked_items,
        preselected_id=preselected_id,
        idempotency_key=new_idempotency_key,
    )


@stock_bp.route("/adjustment", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def adjustment():
    """Physical count inventory adjustment."""
    if request.method == "POST":
        idempotency_key = request.form.get("idempotency_key", "").strip() or None
        item_id = request.form.get("item_id", type=int)
        counted_qty = request.form.get("counted_qty", "").strip()
        reason = request.form.get("reason", "").strip()
        notes = request.form.get("notes", "").strip() or None

        if not item_id or counted_qty == "" or not reason:
            flash("Item, physical counted quantity, and reason are required.", "warning")
            return redirect(url_for("stock.adjustment"))

        try:
            adj, mv = StockService.record_adjustment(
                user_id=current_user.id,
                item_id=item_id,
                counted_qty=counted_qty,
                reason=reason,
                notes=notes,
                idempotency_key=idempotency_key,
            )
            delta_str = f"{float(mv.qty_change):+}"
            flash(f"Inventory Adjustment #{adj.adj_no} applied successfully (Difference: {delta_str}).", "success")
            return redirect(url_for("stock.index"))
        except ValueError as val_err:
            flash(str(val_err), "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Failed to record adjustment: {exc}", "danger")

    # GET request
    preselected_id = request.args.get("item_id", type=int)
    tracked_items = Item.query.filter_by(is_stock_tracked=1, is_active=1, deleted_at=None).order_by(Item.item_name).all()
    new_idempotency_key = str(uuid.uuid4())

    return render_template(
        "stock/adjustment.html",
        items=tracked_items,
        preselected_id=preselected_id,
        idempotency_key=new_idempotency_key,
    )


@stock_bp.route("/ledger/<int:item_id>")
@login_required
@role_required("super_admin", "manager")
def ledger(item_id: int):
    """Chronologically reconciled Item Ledger with opening balance row."""
    date_from = request.args.get("date_from", "").strip() or None
    date_to = request.args.get("date_to", "").strip() or None

    try:
        ledger_data = StockService.get_item_ledger(
            item_id=item_id,
            date_from=date_from,
            date_to=date_to,
        )
    except ValueError as err:
        flash(str(err), "danger")
        return redirect(url_for("stock.index"))

    return render_template(
        "stock/ledger.html",
        **ledger_data
    )


@stock_bp.route("/api/item/<int:item_id>")
@login_required
@role_required("super_admin", "manager")
def api_item_info(item_id: int):
    """Returns real-time stock and unit metadata for an item."""
    item = Item.query.options(joinedload(Item.unit)).filter_by(id=item_id, deleted_at=None).first()
    if not item:
        return jsonify({"error": "Item not found"}), 404

    return jsonify({
        "id": item.id,
        "sku": item.sku,
        "barcode": item.barcode or "",
        "item_name": item.item_name,
        "current_stock": float(item.current_stock),
        "cost_price": float(item.cost_price),
        "sale_price": float(item.sale_price),
        "reorder_level": float(item.reorder_level),
        "unit_name": item.unit.unit_name if item.unit else "pcs",
        "unit_code": item.unit.unit_code if item.unit else "pcs",
        "allow_decimal": bool(item.unit.allow_decimal) if item.unit else False,
        "is_stock_tracked": bool(item.is_stock_tracked),
    })
