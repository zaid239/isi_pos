"""
Purchasing Models: Purchase and PurchaseItem.
Captures stock-in vendor invoices with weighted average costing attributes.
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener
from app.utils.timezone import utc_now


@register_outbox_listener
class Purchase(SyncMixin, db.Model):
    __tablename__ = "purchases"

    purchase_no = db.Column(db.String(50), unique=True, nullable=False, index=True)  # Avoid reserved NUMBER
    supplier_id = db.Column(db.BigInteger, db.ForeignKey("suppliers.id"), nullable=False)
    purchase_date = db.Column(db.DateTime, nullable=False, default=utc_now)  # Avoid reserved DATE
    supplier_inv_no = db.Column(db.String(50), nullable=True)

    subtotal = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    freight_charges = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    other_charges = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    tax_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    notes = db.Column(db.Text, nullable=True)
    status_code = db.Column(db.String(20), nullable=False, default="received")  # draft, received, cancelled

    supplier = db.relationship("Supplier", back_populates="purchases")
    items = db.relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan", lazy="dynamic")

    def __repr__(self):
        return f"<Purchase {self.purchase_no}>"


@register_outbox_listener
class PurchaseItem(SyncMixin, db.Model):
    __tablename__ = "purchase_items"

    purchase_id = db.Column(db.BigInteger, db.ForeignKey("purchases.id"), nullable=False)
    item_id = db.Column(db.BigInteger, db.ForeignKey("items.id"), nullable=False)

    quantity = db.Column(db.Numeric(12, 3), nullable=False)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False)
    line_tax_pct = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    line_tax_amt = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    line_total = db.Column(db.Numeric(12, 2), nullable=False)

    purchase = db.relationship("Purchase", back_populates="items")
    item = db.relationship("Item")

    def __repr__(self):
        return f"<PurchaseItem {self.purchase_id}:{self.item_id}>"
