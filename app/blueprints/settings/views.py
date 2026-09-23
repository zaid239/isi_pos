"""
Settings Views & Administration Controller.
Restricted exclusively to Super Admin.
Handles branding, financial defaults, thermal receipt configuration, and image uploads.
"""
import os
from flask import render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from app.blueprints.settings import settings_bp
from app.utils.decorators import role_required
from app.services.setting_service import SettingService
from app.utils.upload import (
    save_uploaded_image,
    delete_uploaded_file,
    resolve_image_url,
    resolve_fallback_url,
    process_branding_upload,
    delete_branding_files,
)


@settings_bp.route("/", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def index():
    if request.method == "POST":
        current_settings = SettingService.get_all_settings()
        updates = {}

        # 1. Branding Text Fields
        text_fields = [
            "org_name_en", "org_name_ur", "org_address", "org_phone",
            "org_email", "org_ntn", "org_strn", "theme_color_primary",
            "theme_color_secondary", "currency_symbol", "thousand_separator",
            "cash_rounding_mode", "invoice_prefix", "invoice_reset_rule",
            "fiscal_year_start", "business_day_cutoff", "receipt_header",
            "receipt_footer", "return_policy", "receipt_paper_size",
            "pos_reg_no",
        ]
        for field in text_fields:
            if field in request.form:
                updates[field] = request.form.get(field, "").strip()

        # 2. Integer & Numeric Fields
        if "decimal_places" in request.form:
            try:
                updates["decimal_places"] = int(request.form.get("decimal_places", 2))
            except ValueError:
                pass

        if "invoice_padding" in request.form:
            try:
                updates["invoice_padding"] = int(request.form.get("invoice_padding", 6))
            except ValueError:
                pass

        if "low_stock_threshold" in request.form:
            try:
                updates["low_stock_threshold"] = int(request.form.get("low_stock_threshold", 10))
            except ValueError:
                pass

        if "default_tax_percent" in request.form:
            try:
                updates["default_tax_percent"] = request.form.get("default_tax_percent", "0.00").strip()
            except ValueError:
                pass

        if "backup_usb_drive" in request.form:
            updates["backup_usb_drive"] = request.form.get("backup_usb_drive", "").strip().upper()

        # 3. Boolean Flags
        updates["tax_inclusive"] = "true" if request.form.get("tax_inclusive") == "1" else "false"
        updates["fbr_enabled"] = "true" if request.form.get("fbr_enabled") == "1" else "false"
        updates["print_logo_on_receipt"] = "true" if request.form.get("print_logo_on_receipt") == "1" else "false"

        # 4. File Uploads (Brand Icon, Brand Wordmark, Legacy Logo & Favicon - Form Submit Fallback)
        if "brand_icon" in request.files or "org_favicon" in request.files:
            icon_file = request.files.get("brand_icon") or request.files.get("org_favicon")
            if icon_file and icon_file.filename:
                try:
                    old_black = SettingService.get_value("brand_icon_black_url")
                    old_white = SettingService.get_value("brand_icon_white_url")
                    old_fav = SettingService.get_value("brand_favicon_url")
                    old_legacy = SettingService.get_value("org_favicon_url")
                    delete_branding_files([old_black, old_white, old_fav, old_legacy])

                    res = process_branding_upload(icon_file, kind="icon")
                    updates["brand_icon_black_url"] = res["black_path"]
                    updates["brand_icon_white_url"] = res["white_path"]
                    updates["brand_favicon_url"] = res.get("favicon_path", "")
                    updates["org_favicon_url"] = res.get("favicon_path", "")
                except ValueError as err:
                    flash(f"Icon upload failed: {err}", "danger")

        if "brand_wordmark" in request.files or "org_logo" in request.files:
            wordmark_file = request.files.get("brand_wordmark") or request.files.get("org_logo")
            if wordmark_file and wordmark_file.filename:
                try:
                    old_black = SettingService.get_value("brand_wordmark_black_url")
                    old_white = SettingService.get_value("brand_wordmark_white_url")
                    old_logo = SettingService.get_value("org_logo_url")
                    delete_branding_files([old_black, old_white, old_logo])

                    res = process_branding_upload(wordmark_file, kind="wordmark")
                    updates["brand_wordmark_black_url"] = res["black_path"]
                    updates["brand_wordmark_white_url"] = res["white_path"]
                    updates["org_logo_url"] = res["black_path"]
                except ValueError as err:
                    flash(f"Wordmark upload failed: {err}", "danger")

        # 5. Commit via SettingService
        try:
            SettingService.update_settings(
                new_values=updates,
                user_id=current_user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None,
            )
            flash("System settings and branding successfully updated.", "success")
        except Exception as exc:
            flash(f"Failed to update settings: {exc}", "danger")

        return redirect(url_for("settings.index"))

    settings_dict = SettingService.get_all_settings()
    return render_template("settings/index.html", settings=settings_dict)


@settings_bp.route("/upload-image/<image_type>", methods=["POST"])
@login_required
@role_required("super_admin")
def upload_branding_image(image_type: str):
    """
    Dedicated AJAX endpoint for branding uploads:
    - 'brand_icon' / 'icon': 1:1 square crop, generates black, white, and 64px thickened favicon PNG.
    - 'brand_wordmark' / 'wordmark' / 'logo': free aspect crop, generates black and white PNGs.
    """
    from flask import jsonify
    valid_types = ("brand_icon", "icon", "brand_wordmark", "wordmark", "logo", "favicon")
    if image_type not in valid_types:
        return jsonify({"success": False, "error": "Invalid image type."}), 400

    file = request.files.get("image") or request.files.get("file")
    if not file or not file.filename:
        return jsonify({"success": False, "error": "No file uploaded."}), 400

    is_icon = image_type in ("brand_icon", "icon", "favicon")

    try:
        if is_icon:
            old_paths = [
                SettingService.get_value("brand_icon_black_url"),
                SettingService.get_value("brand_icon_white_url"),
                SettingService.get_value("brand_favicon_url"),
                SettingService.get_value("org_favicon_url"),
            ]
            delete_branding_files(old_paths)

            res = process_branding_upload(file, kind="icon")
            new_values = {
                "brand_icon_black_url": res["black_path"],
                "brand_icon_white_url": res["white_path"],
                "brand_favicon_url": res.get("favicon_path", ""),
                "org_favicon_url": res.get("favicon_path", ""),
            }
            SettingService.update_settings(
                new_values=new_values,
                user_id=current_user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None,
            )

            full_disk = os.path.join(current_app.root_path, "static", res["black_path"])
            fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0

            return jsonify({
                "success": True,
                "file_path": res["black_path"],
                "black_url": resolve_image_url(res["black_path"]),
                "white_url": resolve_image_url(res["white_path"]),
                "favicon_url": resolve_image_url(res.get("favicon_path")),
                "url": resolve_image_url(res["black_path"]),
                "fallback_url": resolve_image_url(res["black_path"]),
                "file_size": fsize,
                "file_size_formatted": f"{fsize / 1024:.1f} KB",
                "message": "Brand icon & 64px favicon successfully processed with transparent background."
            })
        else:
            old_paths = [
                SettingService.get_value("brand_wordmark_black_url"),
                SettingService.get_value("brand_wordmark_white_url"),
                SettingService.get_value("org_logo_url"),
            ]
            delete_branding_files(old_paths)

            res = process_branding_upload(file, kind="wordmark")
            new_values = {
                "brand_wordmark_black_url": res["black_path"],
                "brand_wordmark_white_url": res["white_path"],
                "org_logo_url": res["black_path"],
            }
            SettingService.update_settings(
                new_values=new_values,
                user_id=current_user.id,
                ip_address=request.remote_addr,
                user_agent=request.user_agent.string if request.user_agent else None,
            )

            full_disk = os.path.join(current_app.root_path, "static", res["black_path"])
            fsize = os.path.getsize(full_disk) if os.path.isfile(full_disk) else 0

            return jsonify({
                "success": True,
                "file_path": res["black_path"],
                "black_url": resolve_image_url(res["black_path"]),
                "white_url": resolve_image_url(res["white_path"]),
                "url": resolve_image_url(res["black_path"]),
                "fallback_url": resolve_image_url(res["black_path"]),
                "file_size": fsize,
                "file_size_formatted": f"{fsize / 1024:.1f} KB",
                "message": "Brand wordmark successfully processed (black & white transparent versions)."
            })
    except ValueError as val_err:
        return jsonify({"success": False, "error": str(val_err)}), 400
    except Exception as exc:
        return jsonify({"success": False, "error": f"Failed to process branding image: {exc}"}), 500


@settings_bp.route("/remove-image/<image_type>", methods=["POST"])
@login_required
@role_required("super_admin")
def remove_branding_image(image_type: str):
    """
    Removes existing branding image, deletes generated PNG files from disk, and clears DB settings.
    """
    from flask import jsonify
    valid_types = ("brand_icon", "icon", "brand_wordmark", "wordmark", "logo", "favicon")
    if image_type not in valid_types:
        return jsonify({"success": False, "error": "Invalid image type."}), 400

    is_icon = image_type in ("brand_icon", "icon", "favicon")

    if is_icon:
        old_paths = [
            SettingService.get_value("brand_icon_black_url"),
            SettingService.get_value("brand_icon_white_url"),
            SettingService.get_value("brand_favicon_url"),
            SettingService.get_value("org_favicon_url"),
        ]
        delete_branding_files(old_paths)
        SettingService.update_settings(
            new_values={
                "brand_icon_black_url": "",
                "brand_icon_white_url": "",
                "brand_favicon_url": "",
                "org_favicon_url": "",
            },
            user_id=current_user.id,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None,
        )
        msg = "Brand icon removed successfully."
    else:
        old_paths = [
            SettingService.get_value("brand_wordmark_black_url"),
            SettingService.get_value("brand_wordmark_white_url"),
            SettingService.get_value("org_logo_url"),
        ]
        delete_branding_files(old_paths)
        SettingService.update_settings(
            new_values={
                "brand_wordmark_black_url": "",
                "brand_wordmark_white_url": "",
                "org_logo_url": "",
            },
            user_id=current_user.id,
            ip_address=request.remote_addr,
            user_agent=request.user_agent.string if request.user_agent else None,
        )
        msg = "Brand wordmark removed successfully."

    return jsonify({
        "success": True,
        "message": msg
    })


@settings_bp.route("/payment-modes", methods=["GET", "POST"])
@login_required
@role_required("super_admin")
def payment_modes():
    """Payment Modes Administration: add, rename, reorder, and activate/deactivate."""
    from app.services.payment_service import PaymentService
    if request.method == "POST":
        mode_code = request.form.get("mode_code", "").strip()
        mode_name = request.form.get("mode_name", "").strip()
        requires_ref = 1 if request.form.get("requires_ref") == "1" else 0
        try:
            sort_order = int(request.form.get("sort_order", 0) or 0)
        except ValueError:
            sort_order = 0

        try:
            PaymentService.create_mode(
                mode_code=mode_code,
                mode_name=mode_name,
                requires_ref=requires_ref,
                sort_order=sort_order
            )
            flash(f"Payment mode '{mode_name}' added successfully.", "success")
        except ValueError as err:
            flash(str(err), "danger")
        return redirect(url_for("settings.payment_modes"))

    modes = PaymentService.get_all_modes()
    return render_template("settings/payment_modes.html", modes=modes)


@settings_bp.route("/payment-modes/<int:mode_id>/edit", methods=["POST"])
@login_required
@role_required("super_admin")
def edit_payment_mode(mode_id: int):
    """Updates display name, reference requirement, and sort order of a payment mode."""
    from app.services.payment_service import PaymentService
    mode_name = request.form.get("mode_name", "").strip()
    requires_ref = 1 if request.form.get("requires_ref") == "1" else 0
    try:
        sort_order = int(request.form.get("sort_order", 0) or 0)
    except ValueError:
        sort_order = 0

    try:
        PaymentService.update_mode(
            mode_id=mode_id,
            mode_name=mode_name,
            requires_ref=requires_ref,
            sort_order=sort_order
        )
        flash("Payment mode updated successfully.", "success")
    except ValueError as err:
        flash(str(err), "danger")
    return redirect(url_for("settings.payment_modes"))


@settings_bp.route("/payment-modes/<int:mode_id>/toggle", methods=["POST"])
@login_required
@role_required("super_admin")
def toggle_payment_mode(mode_id: int):
    """Toggles active status of a payment mode. Enforces that Cash cannot be deactivated."""
    from app.services.payment_service import PaymentService
    try:
        mode = PaymentService.toggle_mode_active(mode_id)
        status = "activated" if mode.is_active else "deactivated"
        flash(f"Payment mode '{mode.mode_name}' {status}.", "info")
    except ValueError as err:
        flash(str(err), "danger")
    return redirect(url_for("settings.payment_modes"))

