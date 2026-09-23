"""
Cashier Shift and Cash Drawer Session Model.
Tracks cash float, cash receipts, cash returns, cash expenses, and drawer discrepancies.
Follows institutional business day boundaries.
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener, get_current_counter_code
from app.utils.timezone import utc_now


@register_outbox_listener
class Shift(SyncMixin, db.Model):
    __tablename__ = "shifts"

    cashier_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False, index=True)
    counter_code = db.Column(db.String(10), nullable=False, default=get_current_counter_code, index=True)

    opened_at = db.Column(db.DateTime, nullable=False, default=utc_now)
    closed_at = db.Column(db.DateTime, nullable=True)

    opening_cash = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    closing_cash = db.Column(db.Numeric(12, 2), nullable=True)
    expected_cash = db.Column(db.Numeric(12, 2), nullable=True)
    cash_diff = db.Column(db.Numeric(12, 2), nullable=True)  # Short or Over

    shift_status = db.Column(db.String(20), nullable=False, default="open")  # open, closed
    notes = db.Column(db.Text, nullable=True)

    cashier = db.relationship("User")
    invoices = db.relationship("Invoice", back_populates="shift", lazy="dynamic")

    def __repr__(self):
        return f"<Shift cashier={self.cashier_id} status={self.shift_status} diff={self.cash_diff}>"
