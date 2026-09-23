"""
Sales Return Models: SalesReturn and SalesReturnItem.
Complies with Oracle 19c reserved word rules:
- Table name is sales_returns (avoid reserved word RETURNS)
- Table name is sales_return_items (avoid reserved word RETURNS)
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener
from app.utils.timezone import utc_now


@register_outbox_listener
class SalesReturn(SyncMixin, db.Model):
    __tablename__ = "sales_returns"

    return_no = db.Column(db.String(50), unique=True, nullable=False, index=True)
    invoice_id = db.Column(db.BigInteger, db.ForeignKey("invoices.id"), nullable=False)
    return_date = db.Column(db.DateTime, nullable=False, default=utc_now)
    cashier_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)

    total_refund = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes = db.Column(db.Text, nullable=True)
    status_code = db.Column(db.String(20), nullable=False, default="completed")  # completed, cancelled

    invoice = db.relationship("Invoice")
    cashier = db.relationship("User")
    items = db.relationship("SalesReturnItem", back_populates="sales_return", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<SalesReturn {self.return_no}: refund={self.total_refund}>"


@register_outbox_listener
class SalesReturnItem(SyncMixin, db.Model):
    __tablename__ = "sales_return_items"

    return_id = db.Column(db.BigInteger, db.ForeignKey("sales_returns.id"), nullable=False)
    invoice_item_id = db.Column(db.BigInteger, db.ForeignKey("invoice_items.id"), nullable=True)
    item_id = db.Column(db.BigInteger, db.ForeignKey("items.id"), nullable=False)

    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    refund_amount = db.Column(db.Numeric(12, 2), nullable=False)
    reason = db.Column(db.Text, nullable=True)
    restock_flag = db.Column(db.SmallInteger, nullable=False, default=1)  # 1 = restock into inventory

    sales_return = db.relationship("SalesReturn", back_populates="items")
    item = db.relationship("Item")
    invoice_item = db.relationship("InvoiceItem")

    __table_args__ = (
        db.CheckConstraint("restock_flag IN (0, 1)", name="ck_sales_ret_restock"),
    )

    def __repr__(self):
        return f"<SalesReturnItem ret={self.return_id} item={self.item_id} qty={self.quantity}>"
