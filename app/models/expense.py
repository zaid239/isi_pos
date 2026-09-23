"""
Expense Tracking Models: ExpenseHead and Expense.
Reflected in cashier shift drawer reconciliations and institutional financial reports.
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener
from app.utils.timezone import utc_now


@register_outbox_listener
class ExpenseHead(SyncMixin, db.Model):
    __tablename__ = "expense_heads"

    head_name = db.Column(db.String(100), nullable=False)
    head_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)

    expenses = db.relationship("Expense", back_populates="head", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("is_active IN (0, 1)", name="ck_exp_heads_is_active"),
    )

    def soft_delete(self, user_id=None):
        raise NotImplementedError(
            "ExpenseHead does not support soft deletion. Use is_active=0 for status deactivation "
            "to avoid permanently blocking unique head_code re-use."
        )

    def __repr__(self):
        return f"<ExpenseHead {self.head_code}: {self.head_name}>"


@register_outbox_listener
class Expense(SyncMixin, db.Model):
    __tablename__ = "expenses"

    expense_no = db.Column(db.String(50), unique=True, nullable=False, index=True)
    expense_date = db.Column(db.DateTime, nullable=False, default=utc_now)
    expense_head_id = db.Column(db.BigInteger, db.ForeignKey("expense_heads.id"), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)

    payment_mode_id = db.Column(db.BigInteger, db.ForeignKey("payment_modes.id"), nullable=False)
    recipient = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    receipt_img = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.BigInteger, db.ForeignKey("users.id"), nullable=False)

    head = db.relationship("ExpenseHead", back_populates="expenses")
    payment_mode = db.relationship("PaymentMode")
    user = db.relationship("User")

    def __repr__(self):
        return f"<Expense {self.expense_no}: amt={self.amount}>"
