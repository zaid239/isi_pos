"""
Item, Category, and Unit Blueprint Controller.
Provides catalog browsing, search/filtering, SKU auto-generation,
Urdu naming support, image uploads, and audit-logged CRUD.
"""
import os
from decimal import Decimal
from flask import render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from sqlalchemy import func
from app.extensions import db
from app.blueprints.items import items_bp
from app.utils.decorators import role_required
from app.models.item import Item, Category, Unit, ItemImage
from app.services.item_service import ItemService
from app.services.setting_service import SettingService
from app.utils.upload import (
    save_uploaded_image,
    delete_uploaded_file,
    resolve_image_url,
    resolve_fallback_url,
    save_temp_image,
    delete_temp_image,
)


# ==============================================================================
# Items Catalog Routes
# ==============================================================================
@items_bp.route("/")
@login_required
@role_required("super_admin", "manager", "store_keeper", "cashier")
def index():
    search = request.args.get("q", "").strip()
    category_id = request.args.get("category_id", type=int)
    status = request.args.get("status", "")
    stock_filter = request.args.get("stock", "")
    sort_by = request.args.get("sort", "name_asc")
    page = request.args.get("page", 1, type=int)

    items_pagination = ItemService.get_filtered_items(
        search=search,
        category_id=category_id,
        status=status,
        stock_filter=stock_filter,
        sort_by=sort_by,
        page=page,
        per_page=20,
    )

    categories = Category.query.filter_by(deleted_at=None, is_active=1).order_by(Category.cat_name).all()

    return render_template(
        "items/index.html",
        items=items_pagination.items,
        pagination=items_pagination,
        categories=categories,
        current_search=search,
        current_category=category_id,
        current_status=status,
        current_stock=stock_filter,
        current_sort=sort_by,
    )


@items_bp.route("/create", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def create():
    if request.method == "POST":
        image_file = request.files.get("item_image")
        try:
            item = ItemService.create_item(
                form_data=request.form,
                image_file=image_file,
                user_id=current_user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None,
            )
            flash(f"Item '{item.item_name}' (SKU: {item.sku}) created successfully.", "success")
            return redirect(url_for("items.index"))
        except ValueError as err:
            flash(f"Validation error: {err}", "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Failed to create item: {exc}", "danger")

    # Suggested SKU and select options
    suggested_sku = ItemService.generate_next_sku()
    categories = Category.query.filter_by(deleted_at=None, is_active=1).order_by(Category.cat_name).all()
    units = Unit.query.filter_by(deleted_at=None, is_active=1).order_by(Unit.unit_name).all()
    default_tax = SettingService.get_value("default_tax_percent", Decimal("0.00"))
    default_reorder = SettingService.get_value("low_stock_threshold", 10)

    return render_template(
        "items/form.html",
        action="create",
        item=None,
        suggested_sku=suggested_sku,
        categories=categories,
        units=units,
        default_tax=default_tax,
        default_reorder=default_reorder,
    )


@items_bp.route("/<int:id>/edit", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def edit(id):
    item = Item.query.filter_by(id=id, deleted_at=None).first_or_404()

    if request.method == "POST":
        image_file = request.files.get("item_image")
        try:
            updated_item, diffs = ItemService.update_item(
                item=item,
                form_data=request.form,
                image_file=image_file,
                user_id=current_user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None,
            )
            flash(f"Item '{updated_item.item_name}' updated successfully.", "success")
            return redirect(url_for("items.index"))
        except ValueError as err:
            flash(f"Validation error: {err}", "danger")
        except Exception as exc:
            db.session.rollback()
            flash(f"Failed to update item: {exc}", "danger")

    categories = Category.query.filter_by(deleted_at=None, is_active=1).order_by(Category.cat_name).all()
    units = Unit.query.filter_by(deleted_at=None, is_active=1).order_by(Unit.unit_name).all()

    return render_template(
        "items/form.html",
        action="edit",
        item=item,
        categories=categories,
        units=units,
    )


@items_bp.route("/<int:id>/delete", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def delete(id):
    success = ItemService.soft_delete_item(
        item_id=id,
        user_id=current_user.id,
        ip_address=request.remote_addr,
        user_agent=request.user_agent.string if request.user_agent else None,
    )
    if success:
        flash("Item soft-deleted successfully. Natural keys (SKU/Barcode) released for reuse.", "success")
    else:
        flash("Item could not be found or was already deleted.", "warning")

    return redirect(url_for("items.index"))


# ==============================================================================
# Categories Management Routes
# ==============================================================================
@items_bp.route("/categories", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def categories():
    if request.method == "POST":
        cat_name = request.form.get("cat_name", "").strip()
        cat_code = request.form.get("cat_code", "").strip().upper()
        cat_name_ur = request.form.get("cat_name_ur", "").strip() or None
        parent_id = int(request.form.get("parent_id")) if request.form.get("parent_id") else None
        sort_order = int(request.form.get("sort_order", 0))

        if not cat_name or not cat_code:
            flash("Category Name and Code are required.", "danger")
            return redirect(url_for("items.categories"))

        existing = Category.query.filter_by(cat_code=cat_code).first()
        if existing:
            flash(f"Category Code '{cat_code}' is already in use.", "danger")
            return redirect(url_for("items.categories"))

        category = Category(
            cat_name=cat_name,
            cat_code=cat_code,
            cat_name_ur=cat_name_ur,
            parent_id=parent_id,
            sort_order=sort_order,
            is_active=1,
            created_by=current_user.id,
        )
        db.session.add(category)
        db.session.commit()
        flash(f"Category '{cat_name}' created successfully.", "success")
        return redirect(url_for("items.categories"))

    categories_list = Category.query.order_by(Category.sort_order, Category.cat_name).all()
    return render_template("items/categories.html", categories=categories_list)


@items_bp.route("/categories/<int:id>/edit", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def edit_category(id):
    category = Category.query.get_or_404(id)
    category.cat_name = request.form.get("cat_name", "").strip()
    category.cat_name_ur = request.form.get("cat_name_ur", "").strip() or None
    parent_id = int(request.form.get("parent_id")) if request.form.get("parent_id") else None
    if parent_id != category.id:  # Prevent circular hierarchy
        category.parent_id = parent_id
    category.sort_order = int(request.form.get("sort_order", 0))
    category.is_active = 1 if request.form.get("is_active") in ("1", "on", True) else 0
    category.updated_by = current_user.id

    db.session.commit()
    flash(f"Category '{category.cat_name}' updated successfully.", "success")
    return redirect(url_for("items.categories"))


# ==============================================================================
# Units Management Routes
# ==============================================================================
@items_bp.route("/units", methods=["GET", "POST"])
@login_required
@role_required("super_admin", "manager")
def units():
    if request.method == "POST":
        unit_name = request.form.get("unit_name", "").strip()
        unit_code = request.form.get("unit_code", "").strip().lower()
        allow_decimal = 1 if request.form.get("allow_decimal") in ("1", "on", True) else 0

        if not unit_name or not unit_code:
            flash("Unit Name and Code are required.", "danger")
            return redirect(url_for("items.units"))

        existing = Unit.query.filter_by(unit_code=unit_code).first()
        if existing:
            flash(f"Unit Code '{unit_code}' already exists.", "danger")
            return redirect(url_for("items.units"))

        unit = Unit(
            unit_name=unit_name,
            unit_code=unit_code,
            allow_decimal=allow_decimal,
            is_active=1,
            created_by=current_user.id,
        )
        db.session.add(unit)
        db.session.commit()
        flash(f"Unit '{unit_name}' created successfully.", "success")
        return redirect(url_for("items.units"))

    units_list = Unit.query.order_by(Unit.unit_name).all()
    return render_template("items/units.html", units=units_list)


@items_bp.route("/units/<int:id>/edit", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def edit_unit(id):
    unit = Unit.query.get_or_404(id)
    unit.unit_name = request.form.get("unit_name", "").strip()
    unit.allow_decimal = 1 if request.form.get("allow_decimal") in ("1", "on", True) else 0
    unit.is_active = 1 if request.form.get("is_active") in ("1", "on", True) else 0
    unit.updated_by = current_user.id

    db.session.commit()
    flash(f"Unit '{unit.unit_name}' updated successfully.", "success")
    return redirect(url_for("items.units"))


@items_bp.route("/<int:id>/upload-image", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def upload_image(id):
    """
    Dedicated AJAX endpoint for item image uploads with progress.
    Resizes, creates WebP + JPEG fallback, and replaces any existing image on disk.
    """
    item = Item.query.filter_by(id=id, deleted_at=None).first_or_404()
    file = request.files.get("image") or request.files.get("file") or request.files.get("item_image")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    try:
        # Delete previous image file from disk if present
        existing_img = item.images[0] if (hasattr(item, "images") and item.images) else None
        if existing_img:
            delete_uploaded_file(existing_img.file_path)

        new_path = save_uploaded_image(file, subfolder="items", image_kind="item")
        full_disk = os.path.join(current_app.root_path, "static", new_path)
        fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0

        if existing_img:
            existing_img.file_name = file.filename
            existing_img.file_path = new_path
            existing_img.file_size = fsize
            existing_img.mime_type = "image/webp"
            existing_img.updated_by = current_user.id
        else:
            img = ItemImage(
                item_id=item.id,
                file_name=file.filename,
                file_path=new_path,
                file_size=fsize,
                mime_type="image/webp",
                is_primary=1,
                created_by=current_user.id,
            )
            db.session.add(img)

        db.session.commit()

        return jsonify({
            "success": True,
            "file_path": new_path,
            "file_size": fsize,
            "file_size_formatted": f"{fsize / 1024:.1f} KB",
            "url": resolve_image_url(new_path),
            "fallback_url": resolve_fallback_url(new_path),
            "message": f"Product photo uploaded ({fsize / 1024:.1f} KB WebP)."
        })
    except ValueError as val_err:
        return jsonify({"success": False, "error": str(val_err)}), 400
    except Exception as exc:
        db.session.rollback()
        return jsonify({"success": False, "error": f"Failed to save image: {exc}"}), 500


@items_bp.route("/<int:id>/remove-image", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def remove_image(id):
    """
    Removes existing item image, deletes file from disk, and deletes ItemImage row.
    """
    item = Item.query.filter_by(id=id, deleted_at=None).first_or_404()
    existing_img = item.images[0] if (hasattr(item, "images") and item.images) else None
    if existing_img:
        delete_uploaded_file(existing_img.file_path)
        db.session.delete(existing_img)
        db.session.commit()

    return jsonify({
        "success": True,
        "message": "Product photo removed successfully."
    })


@items_bp.route("/upload-temp-image", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def upload_temp_image():
    """
    Dedicated AJAX endpoint for uploading item images on Create Item page
    before an item ID exists. Saves to uploads/temp/ with quality 80 WebP + fallback,
    and runs opportunistic cleanup of stale (>1h) temp files.
    """
    file = request.files.get("image") or request.files.get("file") or request.files.get("item_image")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    try:
        rel_path, fsize, fsize_str = save_temp_image(file)
        return jsonify({
            "success": True,
            "temp_path": rel_path,
            "file_path": rel_path,
            "file_size": fsize,
            "file_size_formatted": fsize_str,
            "url": resolve_image_url(rel_path),
            "fallback_url": resolve_fallback_url(rel_path),
            "message": f"Photo ready ({fsize_str} WebP). Save item to attach."
        })
    except ValueError as val_err:
        return jsonify({"success": False, "error": str(val_err)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to save temporary image: {exc}"}), 500


@items_bp.route("/remove-temp-image", methods=["POST"])
@login_required
@role_required("super_admin", "manager")
def remove_temp_image():
    """
    Deletes a temporary image from uploads/temp/ when user removes before saving.
    """
    temp_path = request.form.get("temp_path") or (request.get_json(silent=True) or {}).get("temp_path")
    if temp_path:
        delete_temp_image(temp_path)
    return jsonify({
        "success": True,
        "message": "Temporary photo cleared."
    })
