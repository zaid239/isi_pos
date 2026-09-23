"""
Business Partner Models: Customer and Supplier.
Includes monetary balances in NUMBER(12,2) and notes mapped to CLOB.
"""
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener


@register_outbox_listener
class Supplier(SyncMixin, db.Model):
    __tablename__ = "suppliers"

    supplier_name = db.Column(db.String(150), nullable=False)
    contact_person = db.Column(db.String(100), nullable=True)
    phone_no = db.Column(db.String(30), nullable=True)  # Avoid reserved word NUMBER
    email = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    ntn = db.Column(db.String(50), nullable=True)

    opening_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    current_payable = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes = db.Column(db.Text, nullable=True)  # CLOB in Oracle, avoid reserved word COMMENT
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)

    items = db.relationship("Item", back_populates="supplier", lazy="dynamic")
    purchases = db.relationship("Purchase", back_populates="supplier", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("is_active IN (0, 1)", name="ck_suppliers_is_active"),
    )

    def __repr__(self):
        return f"<Supplier {self.supplier_name}>"


@register_outbox_listener
class Customer(SyncMixin, db.Model):
    __tablename__ = "customers"

    customer_name = db.Column(db.String(150), nullable=False)
    phone_no = db.Column(db.String(30), nullable=True, index=True)
    email = db.Column(db.String(100), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    nic_no = db.Column(db.String(30), nullable=True)

    opening_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    current_balance = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes = db.Column(db.Text, nullable=True)
    is_walk_in = db.Column(db.SmallInteger, nullable=False, default=0)
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)

    invoices = db.relationship("Invoice", back_populates="customer", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("is_walk_in IN (0, 1)", name="ck_customers_is_walk_in"),
        db.CheckConstraint("is_active IN (0, 1)", name="ck_customers_is_active"),
    )

    def __repr__(self):
        return f"<Customer {self.customer_name}>"
