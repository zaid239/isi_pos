"""
POS Service: Handles POS item search, barcode lookup, cart calculation,
and atomic sale transaction processing.
Strictly adheres to:
- CalculationService for all monetary and tax calculations.
- Atomic single database transaction for Invoice, InvoiceItem, InvoicePayment,
  Item stock decrement, and StockMovement ledger records.
- Idempotency key guard to prevent duplicate billing.
- Row-level locking (SELECT ... FOR UPDATE) ordered by ID to eliminate deadlocks.
"""
from decimal import Decimal
from typing import Dict, Any, List, Optional
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload, joinedload
from app.extensions import db
from app.models.item import Item, Category, Unit
from app.models.invoice import Invoice, InvoiceItem
from app.models.payment import PaymentMode, InvoicePayment
from app.models.inventory import StockMovement
from app.models.doc_counter import DocCounter
from app.models.user import User
from app.models.audit import AuditLog
from app.services.calculation_service import CalculationService, quantize_money, quantize_qty, to_decimal, ZERO
from app.services.setting_service import SettingService
from app.utils.timezone import utc_now, get_fiscal_year, to_pakistan_time
from app.utils.upload import resolve_image_url, resolve_fallback_url


class POSService:
    @staticmethod
    def serialize_item(item: Item) -> Dict[str, Any]:
        """Serializes an Item model for POS catalog and search interfaces."""
        first_img = item.images[0] if (hasattr(item, "images") and item.images) else None
        stored_path = first_img.file_path if first_img else None
        img_url = resolve_image_url(stored_path) if stored_path else None
        fallback_url = resolve_fallback_url(stored_path) if stored_path else None

        return {
            "id": item.id,
            "sku": item.sku,
            "barcode": item.barcode or "",
            "item_name": item.item_name,
            "item_name_ur": item.item_name_ur or "",
            "category_id": item.category_id,
            "category_name": item.category.cat_name if item.category else "",
            "category_name_ur": item.category.cat_name_ur if item.category and item.category.cat_name_ur else "",
            "unit_id": item.unit_id,
            "unit_name": item.unit.unit_name if item.unit else "pcs",
            "unit_code": item.unit.unit_code if item.unit else "pcs",
            "allow_decimal": bool(item.unit.allow_decimal) if item.unit else False,
            "sale_price": float(item.sale_price),
            "tax_percent": float(item.tax_percent or 0),
            "current_stock": float(item.current_stock),
            "image_url": img_url,
            "image_fallback_url": fallback_url,
        }

    @staticmethod
    def search_items(query: str = "", category_id: Optional[int] = None, limit: int = 40) -> List[Dict[str, Any]]:
        """
        Searches active items by name, SKU, or barcode.
        Optionally filters by category.
        """
        base_query = Item.query.options(
            selectinload(Item.images),
            joinedload(Item.unit),
            joinedload(Item.category),
        ).filter(
            Item.is_active == 1,
            Item.deleted_at.is_(None)
        )

        if category_id:
            base_query = base_query.filter(Item.category_id == category_id)

        clean_q = (query or "").strip()
        if clean_q:
            like_pat = f"%{clean_q}%"
            base_query = base_query.filter(
                or_(
                    Item.barcode == clean_q,
                    Item.barcode.ilike(like_pat),
                    Item.sku.ilike(like_pat),
                    Item.item_name.ilike(like_pat),
                )
            )

        items = base_query.order_by(Item.item_name.asc()).limit(limit).all()
        return [POSService.serialize_item(item) for item in items]

    @staticmethod
    def lookup_barcode(code: str) -> Optional[Dict[str, Any]]:
        """
        Fast lookup for barcode scanner input.
        Matches barcode exact, case-insensitive, or SKU exact.
        """
        clean_code = (code or "").strip()
        if not clean_code:
            return None

        # 1. Exact barcode match
        item = Item.query.filter(
            Item.is_active == 1,
            Item.deleted_at.is_(None),
            Item.barcode == clean_code
        ).first()

        # 2. Case-insensitive barcode match
        if not item:
            item = Item.query.filter(
                Item.is_active == 1,
                Item.deleted_at.is_(None),
                func.upper(Item.barcode) == clean_code.upper()
            ).first()

        # 3. Fallback to SKU exact match
        if not item:
            item = Item.query.filter(
                Item.is_active == 1,
                Item.deleted_at.is_(None),
                func.upper(Item.sku) == clean_code.upper()
            ).first()

        if item:
            return POSService.serialize_item(item)
        return None

    @staticmethod
    def calculate_cart(
        items_payload: List[Dict[str, Any]],
        discount_type: str = "percent",
        discount_value: Any = 0,
        user: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Authoritative cart calculation via CalculationService.
        Loads item tax and prices from DB to guarantee financial integrity.
        Applies bill discount proportionally before tax (Rule 2) and enforces cashier limits.
        """
        discount_type = (discount_type or "percent").lower().strip()
        disc_val = to_decimal(discount_value)
        if disc_val < Decimal("0.00"):
            raise ValueError("Discount cannot be negative.")

        if not items_payload:
            return {
                "lines": [],
                "gross_total": 0.0,
                "discount_amount": 0.0,
                "discount_rate": 0.0,
                "discount_type": discount_type,
                "discount_value": float(disc_val),
                "taxable_total": 0.0,
                "tax_total": 0.0,
                "round_off": 0.0,
                "payable_total": 0.0,
            }

        item_ids = [int(line["item_id"]) for line in items_payload if line.get("item_id")]
        db_items = {item.id: item for item in Item.query.filter(Item.id.in_(item_ids)).all()}

        raw_lines = []
        for line in items_payload:
            iid = int(line.get("item_id", 0))
            db_item = db_items.get(iid)
            if not db_item:
                continue

            qty = quantize_qty(to_decimal(line.get("quantity", 1)))
            raw_lines.append({
                "item_id": db_item.id,
                "quantity": qty,
                "unit_price": db_item.sale_price,
                "line_discount": 0,
                "tax_percent": db_item.tax_percent or 0,
            })

        gross_subtotal = sum(quantize_money(l["quantity"] * to_decimal(l["unit_price"])) for l in raw_lines)

        if discount_type in ("percent", "%"):
            effective_percent = quantize_money(disc_val)
            bill_discount_amount = quantize_money((gross_subtotal * effective_percent) / Decimal("100.00"))
        else:
            bill_discount_amount = quantize_money(disc_val)
            effective_percent = quantize_money((bill_discount_amount / gross_subtotal) * Decimal("100.00")) if gross_subtotal > Decimal("0.00") else Decimal("0.00")

        if bill_discount_amount > gross_subtotal:
            raise ValueError("Discount cannot exceed subtotal.")

        # Enforce cashier role limit (Manager and Super Admin have no cap)
        if user and hasattr(user, "role") and user.role and user.role.role_code == "cashier":
            cashier_limit = SettingService.get_value("cashier_max_discount_percent", Decimal("10.00"))
            if not isinstance(cashier_limit, Decimal):
                cashier_limit = Decimal(str(cashier_limit))
            if effective_percent > cashier_limit:
                raise ValueError("Cashier discount limit exceeded. Manager approval is needed.")

        tax_inc_val = SettingService.get_value("tax_inclusive", False)
        is_tax_inclusive = tax_inc_val is True or str(tax_inc_val).lower() in ("true", "1")
        cash_rounding_mode = str(SettingService.get_value("cash_rounding_mode", "off"))

        calc_res = CalculationService.calculate_bill(
            raw_lines=raw_lines,
            bill_discount=bill_discount_amount,
            is_tax_inclusive=is_tax_inclusive,
            cash_rounding_mode=cash_rounding_mode,
        )

        serialized_lines = []
        for l in calc_res.lines:
            item_obj = db_items.get(l.item_id)
            serialized_lines.append({
                "item_id": l.item_id,
                "item_name": item_obj.item_name if item_obj else "",
                "item_name_ur": item_obj.item_name_ur if item_obj else "",
                "sku": item_obj.sku if item_obj else "",
                "quantity": float(l.quantity),
                "unit_price": float(l.unit_price),
                "gross_amount": float(l.gross_amount),
                "tax_percent": float(l.tax_percent),
                "tax_amount": float(l.tax_amount),
                "line_total": float(l.line_total),
            })

        return {
            "lines": serialized_lines,
            "gross_total": float(calc_res.gross_total),
            "discount_amount": float(bill_discount_amount),
            "discount_rate": float(effective_percent),
            "discount_type": discount_type,
            "discount_value": float(disc_val),
            "taxable_total": float(calc_res.taxable_total),
            "tax_total": float(calc_res.tax_total),
            "round_off": float(calc_res.round_off),
            "payable_total": float(calc_res.payable_total),
        }

    @staticmethod
    def process_checkout(
        cashier_id: int,
        terminal_code: str,
        counter_code: str,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Executes atomic checkout transaction:
        1. Checks idempotency_key.
        2. Acquires row locks on items in ascending order.
        3. Verifies stock availability and active status.
        4. Computes final bill via CalculationService.
        5. Generates gapless invoice_no via DocCounter.
        6. Inserts Invoice, InvoiceItem, InvoicePayment records.
        7. Decrements Item.current_stock and records StockMovement ledger rows.
        8. Commits all in one single transaction.
        """
        idempotency_key = (payload.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise ValueError("Idempotency key is required for checkout.")

        # 1. Idempotency Check: if invoice exists, return it immediately without modifying state
        existing_invoice = Invoice.query.filter_by(idempotency_key=idempotency_key).first()
        if existing_invoice:
            return POSService.get_receipt_data(existing_invoice.id)

        items_data = payload.get("items", [])
        if not items_data:
            raise ValueError("Cart cannot be empty.")

        # Aggregate requested quantities per item ID
        qty_by_item: Dict[int, Decimal] = {}
        for line in items_data:
            iid = int(line.get("item_id", 0))
            if not iid:
                raise ValueError("Invalid item ID in cart.")
            q = to_decimal(line.get("quantity", 0))
            if q <= ZERO:
                raise ValueError("Item quantity must be strictly greater than zero.")
            qty_by_item[iid] = qty_by_item.get(iid, ZERO) + q

        sorted_item_ids = sorted(qty_by_item.keys())

        # 2. Lock items in ascending order to prevent deadlocks (SELECT ... FOR UPDATE)
        locked_items = Item.query.filter(Item.id.in_(sorted_item_ids)).order_by(Item.id.asc()).with_for_update().all()
        db_items = {item.id: item for item in locked_items}

        if len(db_items) != len(sorted_item_ids):
            raise ValueError("One or more items in the cart do not exist in the database.")

        # 3. Verify active status and stock availability
        for iid in sorted_item_ids:
            item = db_items[iid]
            if not item.is_active or item.deleted_at is not None:
                raise ValueError(f"Item '{item.item_name}' is no longer available.")
            req_qty = qty_by_item[iid]
            # Untracked items (is_stock_tracked == 0, e.g. drinks) skip stock check.
            # Tracked items (is_stock_tracked == 1, e.g. bakery/retail) enforce stock availability.
            if getattr(item, "is_stock_tracked", 1) == 1:
                if item.current_stock < req_qty:
                    raise ValueError(
                        f"Insufficient stock for '{item.item_name}'. "
                        f"Available: {float(item.current_stock)}, Requested: {float(req_qty)}"
                    )

        gross_subtotal = sum(quantize_money(to_decimal(qty_by_item[iid]) * to_decimal(db_items[iid].sale_price)) for iid in sorted_item_ids)

        discount_info = payload.get("discount") or {}
        disc_type = (discount_info.get("type") or "percent").lower().strip()
        disc_val = to_decimal(discount_info.get("value") or 0)
        if disc_val < Decimal("0.00"):
            raise ValueError("Discount cannot be negative.")

        if disc_type in ("percent", "%"):
            effective_percent = quantize_money(disc_val)
            bill_discount_amount = quantize_money((gross_subtotal * effective_percent) / Decimal("100.00"))
        else:
            bill_discount_amount = quantize_money(disc_val)
            effective_percent = quantize_money((bill_discount_amount / gross_subtotal) * Decimal("100.00")) if gross_subtotal > Decimal("0.00") else Decimal("0.00")

        if bill_discount_amount > gross_subtotal:
            raise ValueError("Discount cannot exceed subtotal.")

        cashier_user = db.session.get(User, cashier_id)
        if cashier_user and cashier_user.role and cashier_user.role.role_code == "cashier":
            cashier_limit = SettingService.get_value("cashier_max_discount_percent", Decimal("10.00"))
            if not isinstance(cashier_limit, Decimal):
                cashier_limit = Decimal(str(cashier_limit))
            if effective_percent > cashier_limit:
                raise ValueError("Cashier discount limit exceeded. Manager approval is needed.")

        # 4. Prepare lines and execute financial arithmetic strictly through CalculationService
        tax_inc_val = SettingService.get_value("tax_inclusive", False)
        is_tax_inclusive = tax_inc_val is True or str(tax_inc_val).lower() in ("true", "1")
        cash_rounding_mode = str(SettingService.get_value("cash_rounding_mode", "off"))

        raw_lines = []
        for iid in sorted_item_ids:
            item = db_items[iid]
            raw_lines.append({
                "item_id": item.id,
                "quantity": qty_by_item[iid],
                "unit_price": item.sale_price,
                "line_discount": 0,
                "tax_percent": item.tax_percent or 0,
            })

        calc_result = CalculationService.calculate_bill(
            raw_lines=raw_lines,
            bill_discount=bill_discount_amount,
            is_tax_inclusive=is_tax_inclusive,
            cash_rounding_mode=cash_rounding_mode,
        )

        # 5. Validate payment method(s) and remarks
        raw_remarks = str(payload.get("remarks") or payload.get("notes") or "").strip()
        remarks = raw_remarks[:250] if raw_remarks else None

        # Accept payments array or fallback to single payment dict
        payments_data = payload.get("payments")
        if not payments_data and payload.get("payment"):
            payments_data = [payload["payment"]]
        if not payments_data or not isinstance(payments_data, list):
            raise ValueError("At least one payment line is required.")

        payable_total = calc_result.payable_total
        remaining_due = payable_total
        processed_payments = []
        total_applied = ZERO
        total_change = ZERO

        from app.services.payment_service import PaymentService
        PaymentService.ensure_seed_modes()

        for idx, p_line in enumerate(payments_data):
            mode_code = str(p_line.get("mode") or p_line.get("mode_code") or "").lower().strip()
            if not mode_code:
                raise ValueError("Payment mode is required for each payment line.")

            mode_obj = PaymentMode.query.filter_by(mode_code=mode_code, is_active=1).first()
            if not mode_obj:
                raise ValueError(f"Payment mode '{mode_code}' is invalid or deactivated.")

            raw_amt = p_line.get("amount")
            if raw_amt is None and len(payments_data) == 1:
                raw_amt = payable_total
            elif raw_amt is None:
                raise ValueError(f"Payment amount is required for {mode_obj.mode_name}.")

            line_amt = quantize_money(to_decimal(raw_amt))
            if line_amt <= ZERO:
                raise ValueError(f"Payment amount for {mode_obj.mode_name} must be greater than zero.")

            if mode_code == "cash":
                raw_tendered = p_line.get("tendered_amount")
                if raw_tendered is not None:
                    tendered_amt = quantize_money(to_decimal(raw_tendered))
                else:
                    tendered_amt = line_amt

                if tendered_amt < line_amt:
                    raise ValueError(f"Cash tendered amount ({tendered_amt}) cannot be less than applied amount ({line_amt}).")

                # Only cash may over-tender; applied amount covers remaining due up to line_amt
                applied_amt = min(line_amt, remaining_due)
                change_amt = quantize_money(tendered_amt - applied_amt)
                terminal_ref = str(p_line.get("reference") or p_line.get("terminal_ref") or "").strip() or None
                card_last4 = None
            else:
                # Non-cash lines cannot exceed remaining amount
                if line_amt > remaining_due:
                    raise ValueError(f"Non-cash payment for {mode_obj.mode_name} ({line_amt}) cannot exceed remaining due ({remaining_due}).")

                applied_amt = line_amt
                tendered_amt = line_amt
                change_amt = ZERO
                terminal_ref = str(p_line.get("reference") or p_line.get("terminal_ref") or "").strip() or None
                raw_last4 = str(p_line.get("card_last4") or "").strip()
                card_last4 = raw_last4[-4:] if raw_last4 else None

            remaining_due = quantize_money(remaining_due - applied_amt)
            total_applied = quantize_money(total_applied + applied_amt)
            total_change = quantize_money(total_change + change_amt)

            processed_payments.append({
                "payment_mode_id": mode_obj.id,
                "amount": applied_amt,
                "tendered_amt": tendered_amt,
                "change_amt": change_amt,
                "card_last4": card_last4,
                "terminal_ref": terminal_ref,
            })

        # Rule 6: Sum of applied amounts must equal payable total
        if total_applied != payable_total:
            raise ValueError(
                f"Total payments applied ({total_applied}) does not equal payable amount ({payable_total})."
            )

        # 6. Generate gapless document number
        fy = get_fiscal_year()
        invoice_no = DocCounter.allocate_next_number(
            terminal_code=terminal_code,
            doc_type="invoice",
            fiscal_year=fy,
            counter_code=counter_code
        )

        # 7. Create Invoice
        invoice = Invoice(
            invoice_no=invoice_no,
            invoice_date=utc_now(),
            idempotency_key=idempotency_key,
            terminal_code=terminal_code,
            counter_code=counter_code,
            cashier_id=cashier_id,
            subtotal=calc_result.gross_total,
            discount_rate=effective_percent,
            discount_amount=bill_discount_amount,
            taxable_amount=calc_result.taxable_total,
            tax_amount=calc_result.tax_total,
            round_off=calc_result.round_off,
            payable_amount=payable_total,
            paid_amount=payable_total,
            change_amount=total_change,
            notes=remarks,
            status_code="completed",
        )
        db.session.add(invoice)
        db.session.flush()

        # Audit log for discount
        if bill_discount_amount > Decimal("0.00"):
            import json
            audit_discount = AuditLog(
                user_id=cashier_id,
                action_name="discount_applied",
                module_name="pos",
                record_id=invoice.invoice_no,
                old_val=json.dumps({"subtotal": str(calc_result.gross_total)}),
                new_val=json.dumps({
                    "invoice_no": invoice.invoice_no,
                    "user_id": cashier_id,
                    "subtotal": str(calc_result.gross_total),
                    "discount_type": disc_type,
                    "discount_value": str(disc_val),
                    "discount_amount": str(bill_discount_amount),
                    "discount_rate": str(effective_percent),
                    "tax_amount": str(calc_result.tax_total),
                    "payable_amount": str(payable_total),
                }),
            )
            db.session.add(audit_discount)

        # 8. Create InvoiceItems, decrement stock, and record StockMovements
        for calc_line in calc_result.lines:
            item = db_items[calc_line.item_id]

            inv_item = InvoiceItem(
                invoice_id=invoice.id,
                item_id=item.id,
                terminal_code=terminal_code,
                snap_item_name=item.item_name,
                snap_sku=item.sku,
                quantity=calc_line.quantity,
                unit_price=calc_line.unit_price,
                cost_price=item.cost_price,
                line_disc_amt=calc_line.line_discount,
                taxable_val=calc_line.taxable_value,
                tax_pct=calc_line.tax_percent,
                tax_amount=calc_line.tax_amount,
                line_total=calc_line.line_total,
            )
            db.session.add(inv_item)

            # Decrement stock and record ledger movement only for tracked items
            # Untracked items (is_stock_tracked == 0) skip deduction and create no stock movement
            if getattr(item, "is_stock_tracked", 1) == 1:
                qty_before = item.current_stock
                qty_after = qty_before - calc_line.quantity
                item.current_stock = qty_after

                # Record ledger movement
                movement = StockMovement(
                    item_id=item.id,
                    terminal_code=terminal_code,
                    qty_before=qty_before,
                    qty_change=-calc_line.quantity,
                    qty_after=qty_after,
                    movement_type="sale",
                    ref_doc_type="invoice",
                    ref_doc_no=invoice.invoice_no,
                    unit_cost=item.cost_price,
                    user_id=cashier_id,
                    notes=f"POS Sale Invoice #{invoice.invoice_no}",
                )
                db.session.add(movement)

        # 9. Create InvoicePayment for each payment line
        for p_row in processed_payments:
            inv_payment = InvoicePayment(
                invoice_id=invoice.id,
                terminal_code=terminal_code,
                payment_mode_id=p_row["payment_mode_id"],
                amount=p_row["amount"],
                tendered_amt=p_row["tendered_amt"],
                change_amt=p_row["change_amt"],
                card_last4=p_row["card_last4"],
                terminal_ref=p_row["terminal_ref"],
                payment_date=utc_now(),
            )
            db.session.add(inv_payment)

        # 10. Commit all operations in one single atomic transaction
        db.session.commit()

        return POSService.get_receipt_data(invoice.id)

    @staticmethod
    def get_receipt_data(invoice_id: int) -> Dict[str, Any]:
        """
        Retrieves complete receipt structure for on-screen modal or browser printing.
        Converts timestamps to Pakistan Standard Time and fetches branding settings.
        """
        invoice = db.session.get(Invoice, invoice_id)
        if not invoice:
            raise ValueError(f"Invoice with ID {invoice_id} not found.")

        settings = SettingService.get_all_settings()
        pk_invoice_time = to_pakistan_time(invoice.invoice_date)
        formatted_date = pk_invoice_time.strftime("%d-%b-%Y %I:%M %p") if pk_invoice_time else ""

        cashier_name = invoice.cashier.full_name if invoice.cashier else "Cashier"

        # Format items list
        items_list = []
        for it in invoice.items:
            # Query item to get Urdu name if available
            urdu_name = it.item.item_name_ur if it.item and it.item.item_name_ur else ""
            items_list.append({
                "item_name": it.snap_item_name,
                "item_name_ur": urdu_name,
                "sku": it.snap_sku,
                "quantity": float(it.quantity),
                "unit_price": float(it.unit_price),
                "tax_percent": float(it.tax_pct),
                "tax_pct": float(it.tax_pct),
                "tax_amount": float(it.tax_amount),
                "line_total": float(it.line_total),
            })

        # Format payment list
        payments_list = []
        for p in invoice.payments:
            payments_list.append({
                "mode_code": p.payment_mode.mode_code if p.payment_mode else "cash",
                "mode_name": p.payment_mode.mode_name if p.payment_mode else "Cash",
                "amount": float(p.amount),
                "tendered_amt": float(p.tendered_amt),
                "change_amt": float(p.change_amt),
                "card_last4": p.card_last4,
                "reference": p.terminal_ref,
                "terminal_ref": p.terminal_ref,
            })

        return {
            "invoice_id": invoice.id,
            "invoice_no": invoice.invoice_no,
            "invoice_date_formatted": formatted_date,
            "terminal_code": invoice.terminal_code,
            "counter_code": invoice.counter_code,
            "cashier_name": cashier_name,
            "subtotal": float(invoice.subtotal),
            "discount_amount": float(invoice.discount_amount or 0),
            "discount_rate": float(invoice.discount_rate or 0),
            "taxable_amount": float(invoice.taxable_amount or 0),
            "tax_amount": float(invoice.tax_amount),
            "round_off": float(invoice.round_off),
            "payable_amount": float(invoice.payable_amount),
            "paid_amount": float(invoice.paid_amount),
            "change_amount": float(invoice.change_amount),
            "remarks": invoice.notes or "",
            "notes": invoice.notes or "",
            "items": items_list,
            "cart_items": items_list,
            "payments": payments_list,
            # Branding & metadata
            "org_name_en": settings.get("org_name_en", "My Store"),
            "org_name_ur": settings.get("org_name_ur", "ادارہ جاتی پوائنٹ آف سیل"),
            "brand_wordmark_black_url": settings.get("brand_wordmark_black_url", ""),
            "brand_icon_black_url": settings.get("brand_icon_black_url", ""),
            "org_logo_url": settings.get("brand_wordmark_black_url") or settings.get("org_logo_url", ""),
            "print_logo_on_receipt": str(settings.get("print_logo_on_receipt", "false")).lower() == "true",
            "org_address": settings.get("org_address", ""),
            "org_phone": settings.get("org_phone", ""),
            "org_ntn": settings.get("org_ntn", ""),
            "currency_symbol": settings.get("currency_symbol", "Rs."),
            "receipt_header": settings.get("receipt_header", ""),
            "receipt_footer": settings.get("receipt_footer", "Thank you for your visit! / تشریف لانے کا شکریہ"),
        }
