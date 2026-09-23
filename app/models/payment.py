"""
Payment Models: PaymentMode, InvoicePayment, and PaymentTransaction.
Avoids Oracle reserved word 'MODE' (table payment_modes, column mode_name).
PaymentTransaction carries CLOB raw payloads and full sync columns (instruction i).
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener
from app.utils.timezone import utc_now


@register_outbox_listener
class PaymentMode(SyncMixin, db.Model):
    __tablename__ = "payment_modes"

    mode_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    mode_name = db.Column(db.String(50), nullable=False)  # Avoid reserved word MODE
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)
    requires_ref = db.Column(db.SmallInteger, nullable=False, default=0)
    sort_order = db.Column(db.Integer, nullable=False, default=0)  # Avoid reserved word ORDER

    __table_args__ = (
        db.CheckConstraint("is_active IN (0, 1)", name="ck_pay_modes_is_active"),
        db.CheckConstraint("requires_ref IN (0, 1)", name="ck_pay_modes_req_ref"),
    )

    def soft_delete(self, user_id=None):
        raise NotImplementedError(
            "PaymentMode does not support soft deletion. Use is_active=0 for status deactivation "
            "to avoid permanently blocking unique mode_code re-use."
        )

    def __repr__(self):
        return f"<PaymentMode {self.mode_code}: {self.mode_name}>"


@register_outbox_listener
class InvoicePayment(SyncMixin, db.Model):
    __tablename__ = "invoice_payments"

    invoice_id = db.Column(db.BigInteger, db.ForeignKey("invoices.id"), nullable=False, index=True)
    payment_mode_id = db.Column(db.BigInteger, db.ForeignKey("payment_modes.id"), nullable=False)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    tendered_amt = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    change_amt = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    # Card / POS Reference Details (Never store full PAN, CVV, or PIN)
    card_type = db.Column(db.String(30), nullable=True)
    card_last4 = db.Column(db.String(4), nullable=True)
    bank_name = db.Column(db.String(100), nullable=True)
    terminal_ref = db.Column(db.String(100), nullable=True)

    payment_date = db.Column(db.DateTime, nullable=False, default=utc_now)  # Avoid reserved DATE

    invoice = db.relationship("Invoice", back_populates="payments")
    payment_mode = db.relationship("PaymentMode", lazy="joined")

    def __repr__(self):
        return f"<InvoicePayment inv={self.invoice_id} mode={self.payment_mode_id} amt={self.amount}>"


@register_outbox_listener
class PaymentTransaction(SyncMixin, db.Model):
    """
    Online Payment Gateway Transaction Log (Future Gateway readiness).
    Carries full sync columns (instruction i) and CLOB raw request/response payloads (instruction j).
    """
    __tablename__ = "payment_transactions"

    gateway_name = db.Column(db.String(50), nullable=False)  # manual, hblpay, easypaisa, jazzcash
    gateway_txn_id = db.Column(db.String(100), nullable=True, index=True)
    invoice_id = db.Column(db.BigInteger, db.ForeignKey("invoices.id"), nullable=True)

    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency_code = db.Column(db.String(10), nullable=False, default="PKR")
    status_code = db.Column(db.String(30), nullable=False, default="pending")  # pending, authorized, captured, failed, refunded

    raw_request = db.Column(db.Text, nullable=True)  # CLOB in Oracle
    raw_response = db.Column(db.Text, nullable=True)  # CLOB in Oracle

    invoice = db.relationship("Invoice")

    def __repr__(self):
        return f"<PaymentTransaction {self.gateway_name} txn={self.gateway_txn_id} status={self.status_code}>"
