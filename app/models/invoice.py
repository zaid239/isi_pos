"""
Sales and Invoice Models: Invoice, InvoiceItem, and HeldBill.
Complies with Oracle 19c reserved word rules and explicit precision:
- invoices.idempotency_key UNIQUE index (instruction k)
- terminal_code (site) and counter_code (POS lane) clearly defined (instruction h)
- doc numbering, FBR readiness columns, and round_off tracking
- HeldBill is local-only (never synced to central)
"""
from sqlalchemy import Index
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener, get_current_terminal_code, get_current_counter_code
from app.utils.timezone import utc_now


@register_outbox_listener
class Invoice(SyncMixin, db.Model):
    __tablename__ = "invoices"

    invoice_no = db.Column(db.String(50), unique=True, nullable=False, index=True)  # Avoid reserved NUMBER
    invoice_date = db.Column(db.DateTime, nullable=False, default=utc_now, index=True)  # Avoid reserved DATE

    # Idempotency Key (instruction k: UNIQUE index)
    idempotency_key = db.Column(db.String(64), unique=True, nullable=False, index=True)

    # Counter and Site Identifiers (instruction h)
    # terminal_code (inherited from SyncMixin) identifies the site/store location (e.g. 'T01')
    # counter_code identifies the physical checkout counter/workstation lane (e.g. 'C01')
    counter_code = db.Column(db.String(10), nullable=False, default=get_current_counter_code, index=True)

    customer_id = db.Column(db.BigInteger, db.ForeignKey("customers.id"), nullable=True)
    cashier_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    shift_id = db.Column(db.BigInteger, db.ForeignKey("shifts.id"), nullable=True)

    # Financial Columns (NUMBER(12,2))
    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    discount_rate = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    discount_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    taxable_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    round_off = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # Cash rounding difference
    payable_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    paid_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    change_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    status_code = db.Column(db.String(20), nullable=False, default="completed")  # completed, voided, returned
    notes = db.Column(db.Text, nullable=True)

    # FBR / Tax Compliance Readiness (Section 25 & 38.5)
    fbr_invoice_no = db.Column(db.String(100), nullable=True)
    fbr_status = db.Column(db.String(20), nullable=False, default="not_applicable")  # not_applicable, pending, sent, failed
    fbr_response = db.Column(db.Text, nullable=True)  # CLOB for raw FBR response JSON
    fbr_synced_at = db.Column(db.DateTime, nullable=True)
    pos_reg_no = db.Column(db.String(50), nullable=True)

    customer = db.relationship("Customer", back_populates="invoices")
    cashier = db.relationship("User", foreign_keys=[cashier_id])
    shift = db.relationship("Shift", back_populates="invoices")
    items = db.relationship("InvoiceItem", back_populates="invoice", cascade="all, delete-orphan", lazy="joined")
    payments = db.relationship("InvoicePayment", back_populates="invoice", cascade="all, delete-orphan", lazy="joined")

    def __repr__(self):
        return f"<Invoice {self.invoice_no}: payable={self.payable_amount}>"


@register_outbox_listener
class InvoiceItem(SyncMixin, db.Model):
    __tablename__ = "invoice_items"

    invoice_id = db.Column(db.BigInteger, db.ForeignKey("invoices.id"), nullable=False, index=True)
    item_id = db.Column(db.BigInteger, db.ForeignKey("items.id"), nullable=False)

    # Snapshot of Item Details at Time of Sale (Never mutated by subsequent item changes)
    snap_item_name = db.Column(db.String(200), nullable=False)
    snap_sku = db.Column(db.String(50), nullable=False)

    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    cost_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_disc_amt = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    taxable_val = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    tax_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(12, 2), nullable=False)

    invoice = db.relationship("Invoice", back_populates="items")
    item = db.relationship("Item")

    def __repr__(self):
        return f"<InvoiceItem inv={self.invoice_id} item={self.snap_sku} total={self.line_total}>"


class HeldBill(db.Model):
    """
    Local-Only Parked / Held Cart.
    Never synchronized to central server (instruction i).
    """
    __tablename__ = "held_bills"

    id = db.Column(db.BigInteger().with_variant(db.Integer, "sqlite"), db.Identity(always=False), primary_key=True, autoincrement=True)
    terminal_code = db.Column(db.String(10), nullable=False, default=get_current_terminal_code)
    counter_code = db.Column(db.String(10), nullable=False, default=get_current_counter_code)
    hold_reference = db.Column(db.String(100), nullable=False)
    cashier_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    cart_payload = db.Column(db.Text, nullable=False)  # JSON representation of parked cart (CLOB in Oracle)
    created_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime, nullable=False, default=utc_now, onupdate=utc_now)

    cashier = db.relationship("User")

    def __repr__(self):
        return f"<HeldBill {self.hold_reference}>"
