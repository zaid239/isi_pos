"""
Test Image Processing, Upload Pipeline, WebP/JPEG Fallback, Backup-with-Uploads, and Orphan Pruner.
Verifies:
1. SVG rejection, extension whitelist, and size validation.
2. Pillow resizing on upload:
   - Product items: max 800px on long edge, no upscaling if smaller.
   - Logos: max 400px on long edge.
   - Favicon: exactly 64x64 square.
   - Strips EXIF metadata.
   - Primary .webp file + companion .jpg fallback file.
3. Path resolution & file deletion:
   - Graceful fallback when file missing on disk (returns None, prevents broken UI).
   - Companion fallback resolution.
   - delete_uploaded_file removes both primary and fallback files.
4. CLI Commands:
   - flask backup-db --with-uploads creates dump + uploads zip.
   - flask prune-orphan-uploads detects and removes unreferenced upload files.
5. AJAX endpoints for settings branding and item image uploads.
"""
import io
import os
import zipfile
import pytest
from PIL import Image
from flask import current_app
from werkzeug.datastructures import FileStorage
from app.extensions import db
from app.models.user import User, Role
from app.models.item import Item, Category, Unit, ItemImage
from app.services.setting_service import SettingService
from app.utils.upload import (
    save_uploaded_image,
    delete_uploaded_file,
    resolve_image_url,
    resolve_fallback_url,
    validate_image_file,
    save_temp_image,
    promote_temp_image,
    delete_temp_image,
    clean_old_temp_files,
)


def create_test_image(width=1200, height=800, color=(200, 100, 50), fmt="PNG"):
    """Helper to generate an in-memory image for upload tests."""
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf


def test_svg_strictly_rejected(app):
    """Verifies that SVG files are strictly prohibited per security specifications."""
    with app.app_context():
        svg_data = b"<svg xmlns='http://www.w3.org/2000/svg'><circle r='50'/></svg>"
        file = FileStorage(
            stream=io.BytesIO(svg_data),
            filename="malicious.svg",
            content_type="image/svg+xml"
        )
        with pytest.raises(ValueError, match="SVG files are strictly forbidden"):
            validate_image_file(file)

        with pytest.raises(ValueError, match="SVG files are strictly forbidden"):
            save_uploaded_image(file, subfolder="branding", image_kind="logo")


def test_file_extension_and_size_validation(app):
    """Verifies allowed extensions and file size limit enforcement."""
    with app.app_context():
        # Disallowed extension
        exe_file = FileStorage(
            stream=io.BytesIO(b"binary"),
            filename="payload.exe",
            content_type="application/octet-stream"
        )
        with pytest.raises(ValueError, match="not permitted"):
            validate_image_file(exe_file)

        # File exceeding 2MB
        big_buf = io.BytesIO(b"0" * (2 * 1024 * 1024 + 1024))
        big_file = FileStorage(
            stream=big_buf,
            filename="huge.png",
            content_type="image/png"
        )
        with pytest.raises(ValueError, match="exceeds maximum allowed limit"):
            validate_image_file(big_file, max_size_bytes=2 * 1024 * 1024)


def test_pillow_resize_and_webp_fallback(app):
    """
    Verifies Pillow image processing:
    - Items max 800px on long edge
    - Logo max 400px on long edge
    - Favicon 64x64 square
    - WebP primary file + companion .jpg fallback
    - No upscaling of smaller images
    """
    with app.app_context():
        uploads_root = os.path.join(current_app.root_path, "static", "uploads")

        # 1. Product Item: 1600x1200 -> max 800x600
        buf_item = create_test_image(width=1600, height=1200)
        file_item = FileStorage(stream=buf_item, filename="croissant.png", content_type="image/png")
        saved_item_rel = save_uploaded_image(file_item, subfolder="items", image_kind="item")

        assert saved_item_rel.endswith(".webp")
        full_item_webp = os.path.join(current_app.root_path, "static", saved_item_rel)
        full_item_jpg = os.path.splitext(full_item_webp)[0] + ".jpg"

        assert os.path.isfile(full_item_webp)
        assert os.path.isfile(full_item_jpg)

        with Image.open(full_item_webp) as img:
            assert max(img.size) <= 800
            assert img.size == (800, 600)
            assert img.format == "WEBP"

        with Image.open(full_item_jpg) as img_jpg:
            assert img_jpg.size == (800, 600)
            assert img_jpg.format == "JPEG"

        # 2. Logo: 1000x500 -> max 400x200
        buf_logo = create_test_image(width=1000, height=500)
        file_logo = FileStorage(stream=buf_logo, filename="store_logo.jpg", content_type="image/jpeg")
        saved_logo_rel = save_uploaded_image(file_logo, subfolder="branding", image_kind="logo")

        full_logo_webp = os.path.join(current_app.root_path, "static", saved_logo_rel)
        full_logo_jpg = os.path.splitext(full_logo_webp)[0] + ".jpg"

        with Image.open(full_logo_webp) as img:
            assert max(img.size) <= 400
            assert img.size == (400, 200)

        # 3. Favicon: 256x256 -> exactly 64x64 square
        buf_fav = create_test_image(width=256, height=256)
        file_fav = FileStorage(stream=buf_fav, filename="tab_icon.png", content_type="image/png")
        saved_fav_rel = save_uploaded_image(file_fav, subfolder="branding", image_kind="favicon")

        full_fav_webp = os.path.join(current_app.root_path, "static", saved_fav_rel)
        with Image.open(full_fav_webp) as img:
            assert img.size == (64, 64)

        # 4. Small image: 300x200 -> keep original dimensions (no upscaling)
        buf_small = create_test_image(width=300, height=200)
        file_small = FileStorage(stream=buf_small, filename="small_shot.png", content_type="image/png")
        saved_small_rel = save_uploaded_image(file_small, subfolder="items", image_kind="item")

        full_small_webp = os.path.join(current_app.root_path, "static", saved_small_rel)
        with Image.open(full_small_webp) as img:
            assert img.size == (300, 200)

        # Cleanup created test files
        delete_uploaded_file(saved_item_rel)
        delete_uploaded_file(saved_logo_rel)
        delete_uploaded_file(saved_fav_rel)
        delete_uploaded_file(saved_small_rel)

        assert not os.path.isfile(full_item_webp)
        assert not os.path.isfile(full_item_jpg)


def test_url_resolution_and_missing_file_gracefulness(app):
    """
    Verifies resolve_image_url and resolve_fallback_url:
    - Valid URL if file exists
    - None if file is missing (prevents broken image icons in UI)
    """
    with app.test_request_context():
        buf = create_test_image(width=100, height=100)
        f = FileStorage(stream=buf, filename="test_res.png", content_type="image/png")
        rel_path = save_uploaded_image(f, subfolder="items", image_kind="item")

        # Exists
        url = resolve_image_url(rel_path)
        assert url is not None
        assert url.startswith("/static/uploads/items/")

        fallback_url = resolve_fallback_url(rel_path)
        assert fallback_url is not None
        assert fallback_url.endswith(".jpg")

        # Delete files
        delete_uploaded_file(rel_path)

        # Missing files must gracefully return None
        assert resolve_image_url(rel_path) is None
        assert resolve_fallback_url(rel_path) is None
        assert resolve_image_url("uploads/nonexistent/missing.png") is None
        assert resolve_image_url(None) is None
        assert resolve_image_url("") is None


def test_backup_db_with_uploads_cli(app, runner):
    """Verifies that flask backup-db creates a companion _uploads.zip by default."""
    with app.app_context():
        # Ensure an upload exists in uploads directory
        buf = create_test_image(width=50, height=50)
        f = FileStorage(stream=buf, filename="backup_sample.png", content_type="image/png")
        sample_path = save_uploaded_image(f, subfolder="items", image_kind="item")

        from unittest.mock import MagicMock, patch
        mock_conn = MagicMock()
        # Mock connection context manager
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = [("CATEGORIES",)]
        mock_conn.execute.return_value.keys.return_value = ["id", "cat_name"]

        try:
            with patch.object(db.engine, "connect", return_value=mock_conn):
                result = runner.invoke(args=["backup-db"])
            assert result.exit_code == 0
            assert "Uploads Archive:" in result.output
            assert "_uploads.zip" in result.output

            # Find the generated zip and verify it contains files
            backup_dir = os.path.join(os.getcwd(), "backups")
            zip_files = [f for f in os.listdir(backup_dir) if f.endswith("_uploads.zip")]
            assert len(zip_files) > 0
            latest_zip = os.path.join(backup_dir, sorted(zip_files)[-1])

            with zipfile.ZipFile(latest_zip, "r") as zf:
                names = zf.namelist()
                assert any(name.startswith("items/") or name.startswith("branding/") for name in names)

        finally:
            delete_uploaded_file(sample_path)


def test_prune_orphan_uploads_cli(app, runner, tmp_path):
    """
    Verifies that flask prune-orphan-uploads identifies and deletes orphaned files on disk.
    Uses an isolated temporary directory to protect live/development catalogue files.
    """
    with app.app_context():
        test_uploads = str(tmp_path / "uploads")
        test_items = os.path.join(test_uploads, "items")
        os.makedirs(test_items, exist_ok=True)
        old_cfg = current_app.config.get("UPLOAD_FOLDER")
        current_app.config["UPLOAD_FOLDER"] = test_uploads

        try:
            dummy_orphan_webp = os.path.join(test_items, "orphan_test_file.webp")
            dummy_orphan_jpg = os.path.join(test_items, "orphan_test_file.jpg")

            with open(dummy_orphan_webp, "wb") as f:
                f.write(b"dummy webp content")
            with open(dummy_orphan_jpg, "wb") as f:
                f.write(b"dummy jpg content")

            # 1. Dry run (default): detects orphans but does not delete
            res_dry = runner.invoke(args=["prune-orphan-uploads"])
            assert res_dry.exit_code == 0
            assert "Orphan files found" in res_dry.output
            assert "--confirm" in res_dry.output
            assert "orphan_test_file" in res_dry.output
            assert os.path.isfile(dummy_orphan_webp)
            assert os.path.isfile(dummy_orphan_jpg)

            # 2. Confirmed run: deletes orphans
            res_confirm = runner.invoke(args=["prune-orphan-uploads", "--confirm"])
            assert res_confirm.exit_code == 0
            assert "Successfully pruned" in res_confirm.output
            assert not os.path.isfile(dummy_orphan_webp)
            assert not os.path.isfile(dummy_orphan_jpg)
        finally:
            current_app.config["UPLOAD_FOLDER"] = old_cfg


def test_ajax_settings_branding_upload_and_remove(app, client):
    """Verifies AJAX logo/favicon endpoints: upload with progress and deletion."""
    with app.app_context():
        sa_role = Role.query.filter_by(role_code="super_admin").first()
        if not sa_role:
            sa_role = Role(role_code="super_admin", role_name="Super Admin", is_system=1)
            db.session.add(sa_role)
            db.session.commit()

        admin = User.query.filter_by(username="img_admin").first()
        if not admin:
            admin = User(
                username="img_admin",
                full_name="Image Admin",
                role_id=sa_role.id,
                is_active=1,
                must_change_pwd=0,
            )
            admin.set_password("Admin123!")
            db.session.add(admin)
            db.session.commit()
    login_res = client.post("/auth/login", data={"username": "dash_admin", "password": "Admin123!"}, follow_redirects=False)
    assert login_res.status_code == 302

    # 1. AJAX Logo Upload
    logo_buf = create_test_image(width=500, height=250)
    res = client.post("/settings/upload-image/logo", data={"image": (logo_buf, "brand_test.png")})
    assert res.status_code == 200
    json_data = res.get_json()
    assert json_data["success"] is True
    assert "url" in json_data
    assert json_data["url"].endswith(".webp")

    logo_path = json_data["file_path"]

    # 2. AJAX Logo Remove
    res_del = client.post("/settings/remove-image/logo")
    assert res_del.status_code == 200
    assert res_del.get_json()["success"] is True

    # 3. Verify in DB & Disk
    with app.app_context():
        assert SettingService.get_value("org_logo_url") != logo_path
        full_path = os.path.join(current_app.root_path, "static", logo_path)
        assert not os.path.isfile(full_path)
        db.session.remove()


def test_phone_photo_exif_orientation_transposed(app):
    """
    Verifies that real phone portrait photos carrying an EXIF orientation tag (e.g. tag 6 = 90 CW)
    are transposed to upright orientation with ImageOps.exif_transpose() BEFORE EXIF metadata is stripped,
    preventing portrait photos from coming out sideways.
    """
    with app.app_context():
        # Create a 1200x800 image (landscape in raw buffer)
        # But tag it with EXIF Orientation = 6 (meaning the camera was held in portrait)
        img = Image.new("RGB", (1200, 800), color=(80, 140, 200))
        exif = img.getexif()
        exif[0x0112] = 6  # Orientation tag 6: Rotated 90 CW
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        buf.seek(0)

        file = FileStorage(stream=buf, filename="phone_portrait.jpg", content_type="image/jpeg")
        rel_path = save_uploaded_image(file, subfolder="items", image_kind="item")

        full_webp = os.path.join(current_app.root_path, "static", rel_path)
        assert os.path.isfile(full_webp)

        with Image.open(full_webp) as saved_img:
            # If exif_transpose was applied: (1200, 800) rotated 90 deg -> (800, 1200)
            # Then thumbnail to max 800px on long edge: (533, 800) [PORTRAIT, height=800, width=533]
            # If exif_transpose was NOT applied: size would be (800, 533) [LANDSCAPE, sideways]
            w, h = saved_img.size
            assert h == 800, f"Expected upright portrait height 800, got {h}"
            assert w == 533, f"Expected upright portrait width 533, got {w}"
            assert h > w, "Image should be in portrait orientation (height > width)"

            # Verify EXIF is now stripped in the resulting webp
            saved_exif = saved_img.getexif()
            assert 0x0112 not in saved_exif, "EXIF orientation tag should be stripped from final image"

        delete_uploaded_file(rel_path)


def test_logo_transparency_and_png_fallback(app):
    """
    Verifies that for logos:
    - RGBA alpha transparency is preserved in the primary WebP
    - The fallback companion file is saved as a transparent PNG (NOT JPEG) to prevent black background
    """
    with app.app_context():
        # Create an RGBA image with transparent background
        img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
        # Draw a small opaque square in the center
        for x in range(100, 200):
            for y in range(100, 200):
                img.putpixel((x, y), (255, 0, 0, 255))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        file = FileStorage(stream=buf, filename="transparent_logo.png", content_type="image/png")
        rel_path = save_uploaded_image(file, subfolder="branding", image_kind="logo")

        full_webp = os.path.join(current_app.root_path, "static", rel_path)
        assert os.path.isfile(full_webp)

        # Verify fallback is .png (NOT .jpg)
        png_path = os.path.splitext(full_webp)[0] + ".png"
        assert os.path.isfile(png_path), "Logo companion fallback must be PNG to preserve alpha"
        jpg_path = os.path.splitext(full_webp)[0] + ".jpg"
        assert not os.path.isfile(jpg_path), "Logo should not generate JPEG fallback"

        # Verify fallback has RGBA mode and preserves transparent pixels
        with Image.open(png_path) as fallback_img:
            assert fallback_img.mode == "RGBA"
            corner_pixel = fallback_img.getpixel((10, 10))
            assert corner_pixel[3] == 0, f"Corner should be transparent, got {corner_pixel}"

        delete_uploaded_file(rel_path)


def test_temp_image_workflow_and_routes(app, client):
    """
    Verifies temporary image upload, promotion to catalog item, removal, and cleanup.
    """
    with app.app_context():
        from app.models.user import Role
        sa_role = Role.query.filter_by(role_code="super_admin").first()
        if not sa_role:
            sa_role = Role(role_code="super_admin", role_name="Super Admin", is_system=1)
            db.session.add(sa_role)
            db.session.commit()
        user = User.query.filter_by(username="dash_admin").first()
        if not user:
            user = User(
                username="dash_admin",
                full_name="Dashboard Administrator",
                email="dash@pos.local",
                role_id=sa_role.id,
                is_active=1,
            )
            user.set_password("Admin123!")
            db.session.add(user)
            db.session.commit()

        unit = Unit.query.first()
        if not unit:
            unit = Unit(unit_code="PCS", unit_name="Pieces", is_active=1)
            db.session.add(unit)
            db.session.commit()
        category = Category.query.first()
        if not category:
            category = Category(cat_code="BAK", cat_name="Bakery", is_active=1)
            db.session.add(category)
            db.session.commit()
        cat_id = category.id
        unit_id = unit.id

    # 1. Login as super admin
    client.post("/auth/login", data={"username": "dash_admin", "password": "Admin123!"})

    # 2. Upload to temp
    buf = create_test_image(width=600, height=600, color=(120, 200, 80))
    res = client.post("/items/upload-temp-image", data={"image": (buf, "temp_croissant.png")})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    temp_path = data["temp_path"]
    assert "uploads/temp/" in temp_path

    full_temp = os.path.join(app.root_path, "static", temp_path)
    assert os.path.isfile(full_temp)

    # 3. Create an item using this temp_image_path

    import uuid
    unique_sku = f"SKU-TEMP-{uuid.uuid4().hex[:6].upper()}"

    item_res = client.post("/items/create", data={
        "sku": unique_sku,
        "item_name": "Temp Test Croissant",
        "cost_price": "100.00",
        "sale_price": "150.00",
        "category_id": str(cat_id),
        "unit_id": str(unit_id),
        "temp_image_path": temp_path,
        "is_active": "1",
    }, follow_redirects=True)
    assert item_res.status_code == 200

    # Verify temp file was promoted to items/
    with app.app_context():
        item = Item.query.filter_by(sku=unique_sku).first()
        assert item is not None
        assert len(item.images) == 1
        img_record = item.images[0]
        assert "uploads/items/" in img_record.file_path
        promoted_full = os.path.join(app.root_path, "static", img_record.file_path)
        assert os.path.isfile(promoted_full)
        # Verify old temp file is gone
        assert not os.path.isfile(full_temp)

        # Cleanup created item and files
        delete_uploaded_file(img_record.file_path)
        db.session.delete(img_record)
        db.session.delete(item)
        db.session.commit()

    # 4. Test remove-temp-image endpoint
    buf2 = create_test_image(width=400, height=400)
    res2 = client.post("/items/upload-temp-image", data={"image": (buf2, "to_delete.png")})
    temp2 = res2.get_json()["temp_path"]
    full_temp2 = os.path.join(app.root_path, "static", temp2)
    assert os.path.isfile(full_temp2)

    del_res = client.post("/items/remove-temp-image", data={"temp_path": temp2})
    assert del_res.status_code == 200
    assert not os.path.isfile(full_temp2)

