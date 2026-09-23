"""
Item, Category, and Unit Business Service Layer.
Encapsulates catalog operations, SKU auto-generation, Decimal price enforcement,
image storage, and comprehensive audit trail logging.
"""
import os
import json
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple
from flask import current_app
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload, joinedload
from app.extensions import db
from app.models.item import Item, Category, Unit, ItemImage
from app.models.audit import AuditLog
from app.utils.timezone import utc_now
from app.utils.upload import save_uploaded_image, delete_uploaded_file, promote_temp_image


class ItemService:
    @staticmethod
    def generate_next_sku(prefix: str = "SKU-") -> str:
        """
        Generates a non-colliding sequential SKU (e.g. SKU-00001) for catalog items.
        """
        # Query total count of items to seed next number
        total_items = db.session.query(func.count(Item.id)).scalar() or 0
        candidate_num = total_items + 1
        
        while True:
            candidate = f"{prefix}{candidate_num:05d}"
            exists = db.session.query(Item.id).filter(
                Item.sku == candidate,
                Item.deleted_at.is_(None)
            ).first()
            if not exists:
                return candidate
            candidate_num += 1

    @staticmethod
    def get_filtered_items(
        search: Optional[str] = None,
        category_id: Optional[int] = None,
        status: Optional[str] = None,
        stock_filter: Optional[str] = None,
        sort_by: str = "name_asc",
        page: int = 1,
        per_page: int = 20,
    ):
        """
        Returns a paginated list of catalog items with search, filters, and sorting.
        """
        query = Item.query.options(
            selectinload(Item.images),
            joinedload(Item.unit),
            joinedload(Item.category),
        ).filter(Item.deleted_at.is_(None))

        # Keyword search across English name, SKU, and Barcode
        if search and search.strip():
            term = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    func.upper(Item.item_name).like(term.upper()),
                    func.upper(Item.sku).like(term.upper()),
                    Item.barcode.like(term),
                )
            )

        # Category filter
        if category_id:
            query = query.filter(Item.category_id == category_id)

        # Active / Inactive status filter
        if status == "active":
            query = query.filter(Item.is_active == 1)
        elif status == "inactive":
            query = query.filter(Item.is_active == 0)

        # Stock alert filters
        if stock_filter == "low_stock":
            query = query.filter(Item.current_stock <= Item.reorder_level, Item.current_stock > 0)
        elif stock_filter == "out_of_stock":
            query = query.filter(Item.current_stock <= 0)

        # Sorting
        if sort_by == "name_desc":
            query = query.order_by(Item.item_name.desc())
        elif sort_by == "price_asc":
            query = query.order_by(Item.sale_price.asc())
        elif sort_by == "price_desc":
            query = query.order_by(Item.sale_price.desc())
        elif sort_by == "stock_asc":
            query = query.order_by(Item.current_stock.asc())
        elif sort_by == "newest":
            query = query.order_by(Item.id.desc())
        else:  # default: name_asc
            query = query.order_by(Item.item_name.asc())

        return query.paginate(page=page, per_page=per_page, error_out=False)

    @classmethod
    def create_item(
        cls,
        form_data: Dict[str, Any],
        image_file=None,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Item:
        """
        Creates an Item with Decimal financial values and writes an audit log.
        """
        sku = form_data.get("sku", "").strip() or cls.generate_next_sku()
        barcode = form_data.get("barcode", "").strip() or None
        item_name = form_data.get("item_name", "").strip()
        item_name_ur = form_data.get("item_name_ur", "").strip() or None
        description = form_data.get("description", "").strip() or None

        category_id = int(form_data["category_id"]) if form_data.get("category_id") else None
        unit_id = int(form_data["unit_id"]) if form_data.get("unit_id") else None

        # Financial values in Decimal
        cost_price = Decimal(str(form_data.get("cost_price") or "0.00"))
        sale_price = Decimal(str(form_data.get("sale_price") or "0.00"))
        wholesale_price = Decimal(str(form_data.get("wholesale_price"))) if form_data.get("wholesale_price") else None
        tax_percent = Decimal(str(form_data.get("tax_percent") or "0.00"))
        discount_limit = Decimal(str(form_data.get("discount_limit") or "0.00"))

        opening_stock = Decimal(str(form_data.get("opening_stock") or "0.000"))
        current_stock = opening_stock
        reorder_level = Decimal(str(form_data.get("reorder_level") or "10.000"))

        is_active = 1 if form_data.get("is_active") in ("1", True, "on") else 0
        is_stock_tracked = 1 if form_data.get("is_stock_tracked", "1") in ("1", True, "on") else 0

        item = Item(
            sku=sku,
            barcode=barcode,
            item_name=item_name,
            item_name_ur=item_name_ur,
            category_id=category_id,
            unit_id=unit_id,
            description=description,
            cost_price=cost_price,
            sale_price=sale_price,
            wholesale_price=wholesale_price,
            tax_percent=tax_percent,
            discount_limit=discount_limit,
            opening_stock=opening_stock,
            current_stock=current_stock,
            reorder_level=reorder_level,
            is_stock_tracked=is_stock_tracked,
            is_active=is_active,
            created_by=user_id,
            updated_by=user_id,
        )
        db.session.add(item)
        db.session.flush()

        # Handle image upload or promoted temp image
        temp_image_path = form_data.get("temp_image_path")
        if temp_image_path:
            promoted_path = promote_temp_image(temp_image_path, target_subfolder="items")
            if promoted_path:
                full_disk = os.path.join(current_app.root_path, "static", promoted_path)
                fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0
                img = ItemImage(
                    item_id=item.id,
                    file_name=os.path.basename(promoted_path),
                    file_path=promoted_path,
                    file_size=fsize,
                    mime_type="image/webp",
                    is_primary=1,
                    created_by=user_id,
                )
                db.session.add(img)
        elif image_file and image_file.filename:
            file_path = save_uploaded_image(image_file, subfolder="items", image_kind="item")
            full_disk = os.path.join(current_app.root_path, "static", file_path)
            fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0
            img = ItemImage(
                item_id=item.id,
                file_name=image_file.filename,
                file_path=file_path,
                file_size=fsize,
                mime_type="image/webp",
                is_primary=1,
                created_by=user_id,
            )
            db.session.add(img)

        # Audit log creation
        audit_payload = {
            "sku": sku,
            "item_name": item_name,
            "sale_price": str(sale_price),
            "cost_price": str(cost_price),
            "current_stock": str(current_stock),
        }
        audit = AuditLog(
            user_id=user_id,
            action_name="create_item",
            module_name="items",
            record_id=str(item.id),
            old_val=None,
            new_val=json.dumps(audit_payload, ensure_ascii=False),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.session.add(audit)
        db.session.commit()
        return item

    @classmethod
    def update_item(
        cls,
        item: Item,
        form_data: Dict[str, Any],
        image_file=None,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[Item, Dict[str, Any]]:
        """
        Updates an existing Item, records field-level diffs, and logs into 'audit_logs'.
        """
        old_data = {
            "sku": item.sku,
            "barcode": item.barcode,
            "item_name": item.item_name,
            "item_name_ur": item.item_name_ur,
            "category_id": item.category_id,
            "unit_id": item.unit_id,
            "cost_price": f"{Decimal(str(item.cost_price)):.2f}",
            "sale_price": f"{Decimal(str(item.sale_price)):.2f}",
            "wholesale_price": f"{Decimal(str(item.wholesale_price)):.2f}" if item.wholesale_price else None,
            "tax_percent": f"{Decimal(str(item.tax_percent)):.2f}",
            "discount_limit": f"{Decimal(str(item.discount_limit)):.2f}",
            "reorder_level": f"{Decimal(str(item.reorder_level)):.3f}",
            "is_stock_tracked": item.is_stock_tracked,
            "is_active": item.is_active,
            "description": item.description,
        }

        # Apply updates
        new_sku = form_data.get("sku", "").strip() or item.sku
        new_barcode = form_data.get("barcode", "").strip() or None
        new_name = form_data.get("item_name", "").strip() or item.item_name
        new_name_ur = form_data.get("item_name_ur", "").strip() or None
        new_cat = int(form_data["category_id"]) if form_data.get("category_id") else None
        new_unit = int(form_data["unit_id"]) if form_data.get("unit_id") else item.unit_id

        new_cost = Decimal(str(form_data.get("cost_price") or "0.00"))
        new_sale = Decimal(str(form_data.get("sale_price") or "0.00"))
        new_wholesale = Decimal(str(form_data.get("wholesale_price"))) if form_data.get("wholesale_price") else None
        new_tax = Decimal(str(form_data.get("tax_percent") or "0.00"))
        new_discount = Decimal(str(form_data.get("discount_limit") or "0.00"))
        new_reorder = Decimal(str(form_data.get("reorder_level") or "10.000"))
        new_stock_tracked = 1 if form_data.get("is_stock_tracked") in ("1", True, "on") else 0
        new_active = 1 if form_data.get("is_active") in ("1", True, "on") else 0
        new_desc = form_data.get("description", "").strip() or None

        item.sku = new_sku
        item.barcode = new_barcode
        item.item_name = new_name
        item.item_name_ur = new_name_ur
        item.category_id = new_cat
        item.unit_id = new_unit
        item.cost_price = new_cost
        item.sale_price = new_sale
        item.wholesale_price = new_wholesale
        item.tax_percent = new_tax
        item.discount_limit = new_discount
        item.reorder_level = new_reorder
        item.is_stock_tracked = new_stock_tracked
        item.is_active = new_active
        item.description = new_desc
        item.updated_by = user_id
        item.updated_at = utc_now()
        item.row_version = (item.row_version or 1) + 1

        # Handle image replacement or removal
        temp_image_path = form_data.get("temp_image_path")
        if form_data.get("remove_image") == "1":
            existing_img = item.images[0] if (hasattr(item, "images") and item.images) else None
            if existing_img:
                delete_uploaded_file(existing_img.file_path)
                db.session.delete(existing_img)
        elif temp_image_path:
            promoted_path = promote_temp_image(temp_image_path, target_subfolder="items")
            if promoted_path:
                existing_img = item.images[0] if (hasattr(item, "images") and item.images) else None
                if existing_img:
                    delete_uploaded_file(existing_img.file_path)
                full_disk = os.path.join(current_app.root_path, "static", promoted_path)
                fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0
                if existing_img:
                    existing_img.file_name = os.path.basename(promoted_path)
                    existing_img.file_path = promoted_path
                    existing_img.file_size = fsize
                    existing_img.mime_type = "image/webp"
                    existing_img.updated_by = user_id
                else:
                    img = ItemImage(
                        item_id=item.id,
                        file_name=os.path.basename(promoted_path),
                        file_path=promoted_path,
                        file_size=fsize,
                        mime_type="image/webp",
                        is_primary=1,
                        created_by=user_id,
                    )
                    db.session.add(img)
        elif image_file and image_file.filename:
            existing_img = item.images[0] if (hasattr(item, "images") and item.images) else None
            if existing_img:
                delete_uploaded_file(existing_img.file_path)
            file_path = save_uploaded_image(image_file, subfolder="items", image_kind="item")
            full_disk = os.path.join(current_app.root_path, "static", file_path)
            fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0
            if existing_img:
                existing_img.file_name = image_file.filename
                existing_img.file_path = file_path
                existing_img.file_size = fsize
                existing_img.mime_type = "image/webp"
                existing_img.updated_by = user_id
            else:
                img = ItemImage(
                    item_id=item.id,
                    file_name=image_file.filename,
                    file_path=file_path,
                    file_size=fsize,
                    mime_type="image/webp",
                    is_primary=1,
                    created_by=user_id,
                )
                db.session.add(img)

        # Detect diffs
        new_data = {
            "sku": item.sku,
            "barcode": item.barcode,
            "item_name": item.item_name,
            "item_name_ur": item.item_name_ur,
            "category_id": item.category_id,
            "unit_id": item.unit_id,
            "cost_price": f"{Decimal(str(item.cost_price)):.2f}",
            "sale_price": f"{Decimal(str(item.sale_price)):.2f}",
            "wholesale_price": f"{Decimal(str(item.wholesale_price)):.2f}" if item.wholesale_price else None,
            "tax_percent": f"{Decimal(str(item.tax_percent)):.2f}",
            "discount_limit": f"{Decimal(str(item.discount_limit)):.2f}",
            "reorder_level": f"{Decimal(str(item.reorder_level)):.3f}",
            "is_active": item.is_active,
            "description": item.description,
        }

        diffs_old = {}
        diffs_new = {}
        for k, v in old_data.items():
            if new_data[k] != v:
                diffs_old[k] = v
                diffs_new[k] = new_data[k]

        if diffs_new:
            audit = AuditLog(
                user_id=user_id,
                action_name="update_item",
                module_name="items",
                record_id=str(item.id),
                old_val=json.dumps(diffs_old, ensure_ascii=False),
                new_val=json.dumps(diffs_new, ensure_ascii=False),
                ip_address=ip_address,
                user_agent=user_agent,
            )
            db.session.add(audit)

        db.session.commit()
        return item, diffs_new

    @classmethod
    def soft_delete_item(
        cls,
        item_id: int,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> bool:
        """
        Soft deletes an item (tombstone pattern), freeing its SKU/barcode for future reuse.
        Writes audit trail record.
        """
        item = Item.query.filter_by(id=item_id, deleted_at=None).first()
        if not item:
            return False

        item.soft_delete(user_id=user_id)

        audit = AuditLog(
            user_id=user_id,
            action_name="delete_item",
            module_name="items",
            record_id=str(item.id),
            old_val=json.dumps({"sku": item.sku, "item_name": item.item_name}, ensure_ascii=False),
            new_val=json.dumps({"deleted_at": item.deleted_at.isoformat()}, ensure_ascii=False),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.session.add(audit)
        db.session.commit()
        return True
