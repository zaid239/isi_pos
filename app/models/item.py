"""
Catalog Models: Category, Unit, Item, ItemImage.
Includes CLOB description, NUMBER(12,2) money, NUMBER(12,3) quantities,
and function-based UPPER(item_name) index.
"""
from sqlalchemy import Index, func
from sqlalchemy.orm import validates
from app.extensions import db
from app.models.base import SyncMixin, register_outbox_listener


@register_outbox_listener
class Category(SyncMixin, db.Model):
    __tablename__ = "categories"

    parent_id = db.Column(db.BigInteger, db.ForeignKey("categories.id"), nullable=True)
    cat_name = db.Column(db.String(100), nullable=False)
    cat_name_ur = db.Column(db.String(150), nullable=True)
    cat_code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)  # Avoid reserved word ORDER
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)

    parent = db.relationship("Category", remote_side="Category.id", backref="subcategories")
    items = db.relationship("Item", back_populates="category", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("is_active IN (0, 1)", name="ck_categories_is_active"),
    )

    @validates("cat_name", "cat_code")
    def validate_required_strings(self, key, value):
        if value is None or not str(value).strip():
            raise ValueError(f"'{key}' is a required field and cannot be empty or whitespace.")
        return str(value).strip()

    def soft_delete(self, user_id=None):
        raise NotImplementedError(
            "Category does not support soft deletion. Use is_active=0 for status deactivation "
            "to avoid permanently blocking unique cat_code re-use."
        )

    def __repr__(self):
        return f"<Category {self.cat_code}: {self.cat_name}>"


@register_outbox_listener
class Unit(SyncMixin, db.Model):
    __tablename__ = "units"

    unit_name = db.Column(db.String(50), nullable=False)
    unit_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    allow_decimal = db.Column(db.SmallInteger, nullable=False, default=0)
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)

    items = db.relationship("Item", back_populates="unit", lazy="dynamic")

    __table_args__ = (
        db.CheckConstraint("allow_decimal IN (0, 1)", name="ck_units_allow_decimal"),
        db.CheckConstraint("is_active IN (0, 1)", name="ck_units_is_active"),
    )

    @validates("unit_name", "unit_code")
    def validate_required_strings(self, key, value):
        if value is None or not str(value).strip():
            raise ValueError(f"'{key}' is a required field and cannot be empty or whitespace.")
        return str(value).strip()

    def soft_delete(self, user_id=None):
        raise NotImplementedError(
            "Unit does not support soft deletion. Use is_active=0 for status deactivation "
            "to avoid permanently blocking unique unit_code re-use."
        )

    def __repr__(self):
        return f"<Unit {self.unit_code}>"


class ItemImageList(list):
    """List collection that provides .first() for backward-compatibility with dynamic queries."""
    def first(self):
        return self[0] if self else None


@register_outbox_listener
class Item(SyncMixin, db.Model):
    __tablename__ = "items"

    sku = db.Column(db.String(50), nullable=False, index=True)
    barcode = db.Column(db.String(100), nullable=True, index=True)
    item_name = db.Column(db.String(200), nullable=False)
    item_name_ur = db.Column(db.String(250), nullable=True)

    category_id = db.Column(db.BigInteger, db.ForeignKey("categories.id"), nullable=True)
    unit_id = db.Column(db.BigInteger, db.ForeignKey("units.id"), nullable=False)
    supplier_id = db.Column(db.BigInteger, db.ForeignKey("suppliers.id"), nullable=True)

    description = db.Column(db.Text, nullable=True)  # CLOB in Oracle (instruction j)

    # Money: NUMBER(12,2)
    cost_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    sale_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    wholesale_price = db.Column(db.Numeric(12, 2), nullable=True)

    # Tax & Discount: NUMBER(5,2)
    tax_percent = db.Column(db.Numeric(5, 2), nullable=False, default=0)
    discount_limit = db.Column(db.Numeric(5, 2), nullable=False, default=0)

    # Quantities: NUMBER(12,3)
    opening_stock = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    current_stock = db.Column(db.Numeric(12, 3), nullable=False, default=0)
    reorder_level = db.Column(db.Numeric(12, 3), nullable=False, default=0)  # Avoid reserved word LEVEL

    expiry_date = db.Column(db.Date, nullable=True)
    is_stock_tracked = db.Column(db.SmallInteger, nullable=False, default=1)
    is_active = db.Column(db.SmallInteger, nullable=False, default=1)

    category = db.relationship("Category", back_populates="items")
    unit = db.relationship("Unit", back_populates="items")
    supplier = db.relationship("Supplier", back_populates="items")
    images = db.relationship(
        "ItemImage",
        back_populates="item",
        cascade="all, delete-orphan",
        collection_class=ItemImageList,
        order_by="ItemImage.is_primary.desc(), ItemImage.id.asc()",
    )

    __table_args__ = (
        db.CheckConstraint("is_stock_tracked IN (0, 1)", name="ck_items_is_stock_tracked"),
        db.CheckConstraint("is_active IN (0, 1)", name="ck_items_is_active"),
        Index("uq_items_sku_live", db.text("CASE WHEN deleted_at IS NULL THEN sku END"), unique=True),
        Index("uq_items_barcode_live", db.text("CASE WHEN deleted_at IS NULL THEN barcode END"), unique=True),
        Index("ix_items_upper_name", func.upper(item_name)),
    )

    @validates("sku", "item_name")
    def validate_required_strings(self, key, value):
        if value is None or not str(value).strip():
            raise ValueError(f"'{key}' is a required field and cannot be empty or whitespace.")
        return str(value).strip()

    def __repr__(self):
        return f"<Item {self.sku}: {self.item_name}>"


@register_outbox_listener
class ItemImage(SyncMixin, db.Model):
    __tablename__ = "item_images"

    item_id = db.Column(db.BigInteger, db.ForeignKey("items.id"), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)  # Avoid reserved word FILE
    file_path = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.BigInteger, nullable=False, default=0)
    mime_type = db.Column(db.String(50), nullable=False, default="image/webp")
    is_primary = db.Column(db.SmallInteger, nullable=False, default=1)

    item = db.relationship("Item", back_populates="images")

    __table_args__ = (
        db.CheckConstraint("is_primary IN (0, 1)", name="ck_item_images_is_primary"),
    )

    def __repr__(self):
        return f"<ItemImage {self.file_name}>"
