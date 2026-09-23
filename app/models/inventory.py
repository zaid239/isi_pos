"""
Inventory and Stock Ledger Models.
Includes StockMovement (audit-immutable ledger) and StockAdjustment.
Enforces row_version on stock_movements as required by user instruction g.
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener
from app.utils.timezone import utc_now


@register_outbox_listener
class StockMovement(SyncMixin, db.Model):
    __tablename__ = "stock_movements"

    item_id = db.Column(db.BigInteger, db.ForeignKey("items.id"), nullable=False, index=True)
    qty_before = db.Column(db.Numeric(12, 3), nullable=False)
    qty_change = db.Column(db.Numeric(12, 3), nullable=False)
    qty_after = db.Column(db.Numeric(12, 3), nullable=False)

    movement_type = db.Column(db.String(30), nullable=False)  # sale, purchase, adjustment, return, damage, wastage
    ref_doc_type = db.Column(db.String(30), nullable=True)  # invoice, purchase, adjustment, return
    ref_doc_no = db.Column(db.String(50), nullable=True, index=True)
    unit_cost = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    item = db.relationship("Item")
    user = db.relationship("User")

    __table_args__ = (
        db.Index("ix_stock_mvmt_item_date", "item_id", "created_at"),
    )

    def __repr__(self):
        return f"<StockMovement item={self.item_id} change={self.qty_change} after={self.qty_after}>"


@register_outbox_listener
class StockAdjustment(SyncMixin, db.Model):
    __tablename__ = "stock_adjustments"

    adj_no = db.Column(db.String(50), unique=True, nullable=False, index=True)
    item_id = db.Column(db.BigInteger, db.ForeignKey("items.id"), nullable=False)
    adj_qty = db.Column(db.Numeric(12, 3), nullable=False)  # positive or negative delta
    reason = db.Column(db.Text, nullable=False)

    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)
    approved_by = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    status_code = db.Column(db.String(20), nullable=False, default="pending")  # pending, approved, rejected

    item = db.relationship("Item")
    created_by_user = db.relationship("User", foreign_keys=[user_id])
    approved_by_user = db.relationship("User", foreign_keys=[approved_by])

    def __repr__(self):
        return f"<StockAdjustment {self.adj_no} item={self.item_id} qty={self.adj_qty}>"
