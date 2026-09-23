"""
POS Blueprint Views: Sale screen, fast barcode scanning, search APIs,
and atomic sale transaction checkout.
"""
from flask import render_template, request, jsonify, current_app, abort
from flask_login import login_required, current_user
from app.blueprints.pos import pos_bp
from app.services.pos_service import POSService
from app.models.item import Category
from app.utils.decorators import role_required


@pos_bp.route("/")
@login_required
@role_required("super_admin", "manager", "cashier")
def index():
    """Renders the main point of sale counter terminal."""
    from decimal import Decimal
    from app.services.setting_service import SettingService
    categories = Category.query.filter_by(is_active=1).order_by(Category.sort_order.asc(), Category.cat_name.asc()).all()
    # Pre-fetch first 30 items for instant display without waiting for first keystroke
    initial_items = POSService.search_items(query="", limit=30)
    user_role = current_user.role.role_code if (hasattr(current_user, "role") and current_user.role) else "cashier"
    cashier_max_disc = SettingService.get_value("cashier_max_discount_percent", Decimal("10.00"))
    from app.services.payment_service import PaymentService
    active_modes = [
        {
            "id": m.id,
            "mode_code": m.mode_code,
            "mode_name": m.mode_name,
            "requires_ref": bool(m.requires_ref),
            "sort_order": m.sort_order,
        }
        for m in PaymentService.get_active_modes()
    ]
    return render_template(
        "pos/index.html",
        categories=categories,
        initial_items=initial_items,
        user_role=user_role,
        cashier_max_discount_percent=float(cashier_max_disc),
        payment_modes=active_modes,
    )


@pos_bp.route("/api/payment-modes")
@login_required
@role_required("super_admin", "manager", "cashier")
def api_payment_modes():
    """Returns active payment modes."""
    from app.services.payment_service import PaymentService
    modes = PaymentService.get_active_modes()
    return jsonify({
        "modes": [
            {
                "id": m.id,
                "mode_code": m.mode_code,
                "mode_name": m.mode_name,
                "requires_ref": bool(m.requires_ref),
                "sort_order": m.sort_order,
            }
            for m in modes
        ]
    })



@pos_bp.route("/api/items")
@login_required
@role_required("super_admin", "manager", "cashier")
def api_items():
    """AJAX search endpoint for items by name, Urdu name, SKU, or barcode."""
    query = request.args.get("q", "").strip()
    category_id = request.args.get("category_id", type=int)
    items = POSService.search_items(query=query, category_id=category_id, limit=40)
    return jsonify({"items": items})


@pos_bp.route("/api/barcode")
@login_required
@role_required("super_admin", "manager", "cashier")
def api_barcode():
    """Fast barcode scanner lookup. Returns exact or case-insensitive match on barcode or SKU."""
    code = request.args.get("code", "").strip()
    if not code:
        return jsonify({"error": "Barcode is required"}), 400

    item = POSService.lookup_barcode(code)
    if not item:
        return jsonify({"error": f"Item with barcode or SKU '{code}' not found."}), 404

    return jsonify({"item": item})


@pos_bp.route("/api/calculate", methods=["POST"])
@login_required
@role_required("super_admin", "manager", "cashier")
def api_calculate():
    """Authoritative cart calculation via CalculationService."""
    payload = request.get_json(silent=True) or {}
    items = payload.get("items", [])
    discount_type = payload.get("discount_type", "percent")
    discount_value = payload.get("discount_value", 0)
    try:
        calc_res = POSService.calculate_cart(
            items_payload=items,
            discount_type=discount_type,
            discount_value=discount_value,
            user=current_user,
        )
        return jsonify(calc_res)
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@pos_bp.route("/api/checkout", methods=["POST"])
@login_required
@role_required("super_admin", "manager", "cashier")
def api_checkout():
    """
    Processes final checkout atomically:
    Validates idempotency, locks items, deducts stock, records stock movement,
    saves invoice & payment, and returns on-screen receipt data.
    """
    payload = request.get_json(silent=True) or {}
    terminal_code = current_app.config.get("TERMINAL_CODE")
    counter_code = current_app.config.get("COUNTER_CODE")

    try:
        receipt_data = POSService.process_checkout(
            cashier_id=current_user.id,
            terminal_code=terminal_code,
            counter_code=counter_code,
            payload=payload,
        )
        return jsonify(receipt_data)
    except ValueError as val_err:
        return jsonify({"error": str(val_err)}), 400
    except Exception as exc:
        current_app.logger.exception(f"Checkout transaction failed: {exc}")
        return jsonify({"error": f"Transaction failed: {str(exc)}"}), 500


@pos_bp.route("/receipt/<int:invoice_id>")
@login_required
@role_required("super_admin", "manager", "cashier")
def receipt(invoice_id):
    """Renders a clean printable receipt page for browser printing via window.print()."""
    try:
        receipt_data = POSService.get_receipt_data(invoice_id)
        return render_template("pos/receipt.html", receipt=receipt_data)
    except ValueError:
        abort(404)
