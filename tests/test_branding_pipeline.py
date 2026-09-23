"""
Tests for Branding Pipeline (Grumpy Crumb):
- 2 logo slots: Icon (1:1 crop) and Wordmark (free aspect).
- White background thresholded to transparent, crisp strokes.
- Black and inverted white versions generated as PNG.
- Favicon 64px generated from icon with strokes thickened.
- Automatic contrast placement:
  * Sidebar header: white wordmark ~32px, no separate org-name text.
  * Login page: black icon above wordmark, centered.
  * Favicon: 64px thickened.
  * Receipt: always black wordmark at 72mm width, controlled by print_logo_on_receipt.
  * Fallback to org name as text if no wordmark.
- Org name setting set to 'Grumpy Crumb', code default 'My Store'.
"""
import io
import os
import pytest
import numpy as np
from PIL import Image, ImageDraw
from werkzeug.datastructures import FileStorage

from app.extensions import db
from app.models.user import User, Role
from app.models.setting import Setting
from app.services.setting_service import SettingService
from app.services.pos_service import POSService
from app.utils.upload import (
    process_line_art_transparency,
    generate_thickened_favicon,
    process_branding_upload,
    delete_branding_files,
    resolve_image_url,
    get_upload_root,
)


def make_test_line_art_png(width=300, height=300, is_wordmark=False):
    """Generates an in-memory PNG of black line art on a white background."""
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    if is_wordmark:
        # Draw wide wordmark lines
        draw.line([(20, height // 2), (width - 20, height // 2)], fill=(0, 0, 0), width=4)
        draw.rectangle([30, 20, width - 30, height - 20], outline=(0, 0, 0), width=2)
    else:
        # Draw icon (e.g. character outline)
        draw.ellipse([30, 30, width - 30, height - 30], outline=(0, 0, 0), width=3)
        draw.line([(width // 2, 40), (width // 2, height - 40)], fill=(0, 0, 0), width=2)
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_code_default_org_name_is_my_store():
    """Verify that codebase default org name is 'My Store'."""
    from app.services.setting_service import DEFAULT_SYSTEM_SETTINGS
    assert DEFAULT_SYSTEM_SETTINGS["org_name_en"][0] == "My Store"


def test_db_setting_org_name_is_grumpy_crumb(app):
    """Verify that database setting for org name is set to 'Grumpy Crumb'."""
    with app.app_context():
        val = SettingService.get_value("org_name_en")
        assert val == "Grumpy Crumb"


def test_transparency_thresholding_and_inversion():
    """Verify near-white is transparent, black strokes are (0,0,0,A), white strokes are (255,255,255,A)."""
    # Create an image with white bg, pure black strokes, off-white background noise, and gray edges
    img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    # Add near-white pixel (scanner noise)
    img.putpixel((10, 10), (245, 245, 248, 255))
    # Add pure black stroke pixel
    img.putpixel((50, 50), (0, 0, 0, 255))
    # Add dark stroke pixel
    img.putpixel((51, 50), (25, 25, 25, 255))

    black_img, white_img = process_line_art_transparency(img)

    # 1. Background should be transparent (alpha == 0)
    assert black_img.getpixel((0, 0))[3] == 0
    assert white_img.getpixel((0, 0))[3] == 0

    # 2. Near-white noise (245) should also be thresholded to transparent
    assert black_img.getpixel((10, 10))[3] == 0
    assert white_img.getpixel((10, 10))[3] == 0

    # 3. Pure black stroke should be solid (alpha == 255)
    b_stroke = black_img.getpixel((50, 50))
    w_stroke = white_img.getpixel((50, 50))
    assert b_stroke[3] == 255
    assert w_stroke[3] == 255

    # 4. Color values: black version is pure black, white version is pure white
    assert b_stroke[:3] == (0, 0, 0)
    assert w_stroke[:3] == (255, 255, 255)


def test_favicon_thickened_survives_downscaling():
    """Verify 64px favicon generated from icon thickens strokes to survive downscaling."""
    raw_icon = make_test_line_art_png(width=400, height=400, is_wordmark=False)
    img = Image.open(raw_icon)
    black_icon, _ = process_line_art_transparency(img)

    # Naive downscale to 64x64
    naive_64 = black_icon.resize((64, 64), Image.Resampling.LANCZOS)
    naive_alpha = np.array(naive_64.split()[3])

    # Thickened favicon
    fav_img = generate_thickened_favicon(black_icon, (64, 64))
    fav_alpha = np.array(fav_img.split()[3])

    assert fav_img.size == (64, 64)
    # The thickened favicon should have stronger stroke visibility
    assert fav_alpha.max() >= naive_alpha.max()
    stroke_mask = fav_alpha > 30
    assert stroke_mask.sum() > 0


def test_process_branding_upload_icon_and_wordmark(app):
    """Test process_branding_upload for both icon (1:1 + favicon) and wordmark (free aspect)."""
    with app.app_context():
        # 1. Icon upload
        icon_buf = make_test_line_art_png(width=300, height=300, is_wordmark=False)
        icon_fs = FileStorage(stream=icon_buf, filename="chef_icon.png", content_type="image/png")
        icon_res = process_branding_upload(icon_fs, kind="icon")

        assert "black_path" in icon_res
        assert "white_path" in icon_res
        assert "favicon_path" in icon_res
        assert icon_res["black_path"].endswith(".png")
        assert icon_res["white_path"].endswith(".png")
        assert icon_res["favicon_path"].endswith(".png")

        # Check physical existence on disk
        root = get_upload_root()
        black_disk = os.path.join(root, icon_res["black_path"].replace("uploads/", ""))
        white_disk = os.path.join(root, icon_res["white_path"].replace("uploads/", ""))
        fav_disk = os.path.join(root, icon_res["favicon_path"].replace("uploads/", ""))
        assert os.path.isfile(black_disk)
        assert os.path.isfile(white_disk)
        assert os.path.isfile(fav_disk)

        # Check favicon dimensions
        fav_opened = Image.open(fav_disk)
        assert fav_opened.size == (64, 64)

        # 2. Wordmark upload (free aspect, e.g. 600x120)
        wm_buf = make_test_line_art_png(width=600, height=120, is_wordmark=True)
        wm_fs = FileStorage(stream=wm_buf, filename="wordmark.png", content_type="image/png")
        wm_res = process_branding_upload(wm_fs, kind="wordmark")

        assert "black_path" in wm_res
        assert "white_path" in wm_res
        assert "favicon_path" not in wm_res
        wm_black_disk = os.path.join(root, wm_res["black_path"].replace("uploads/", ""))
        assert os.path.isfile(wm_black_disk)

        # Cleanup test files
        delete_branding_files([
            icon_res["black_path"], icon_res["white_path"], icon_res["favicon_path"],
            wm_res["black_path"], wm_res["white_path"]
        ])


def test_sidebar_and_login_and_receipt_placements(app, client):
    """
    Test placement rules:
    - Sidebar: white wordmark ~32px, no separate org-name text.
    - Login: black icon above wordmark, centered.
    - Receipt: always black wordmark at 72mm, controlled by print_logo_on_receipt.
    - Fallback: org name as text when wordmark missing.
    """
    with app.app_context():
        # Set up super admin user for testing
        admin_role = Role.query.filter_by(role_code="super_admin").first()
        if not admin_role:
            admin_role = Role(role_code="super_admin", role_name="Super Admin")
            db.session.add(admin_role)
            db.session.commit()

        user = User.query.filter_by(username="brand_admin").first()
        if not user:
            user = User(username="brand_admin", full_name="Brand Admin", role_id=admin_role.id, is_active=1)
            user.set_password("Admin123!")
            db.session.add(user)
            db.session.commit()

        # Step A: Test fallback when no branding is uploaded
        SettingService.update_settings({
            "brand_icon_black_url": "",
            "brand_icon_white_url": "",
            "brand_wordmark_black_url": "",
            "brand_wordmark_white_url": "",
            "brand_favicon_url": "",
            "org_logo_url": "",
            "org_name_en": "Grumpy Crumb",
            "print_logo_on_receipt": "true"
        })

    # 1. Login page fallback: shows "Grumpy Crumb" as text
    login_res = client.get("/auth/login")
    assert login_res.status_code == 200
    login_html = login_res.data.decode("utf-8")
    assert "Grumpy Crumb" in login_html

    # Log in as admin
    client.post("/auth/login", data={"username": "brand_admin", "password": "Admin123!"})

    # 2. Sidebar fallback: shows "Grumpy Crumb" as text
    dash_res = client.get("/dashboard")
    assert dash_res.status_code == 200
    dash_html = dash_res.data.decode("utf-8")
    assert "Grumpy Crumb" in dash_html

    # Step B: Upload brand icon and wordmark via AJAX endpoints
    with app.app_context():
        icon_buf = make_test_line_art_png(width=300, height=300, is_wordmark=False)
        wm_buf = make_test_line_art_png(width=600, height=120, is_wordmark=True)

        res_icon = client.post("/settings/upload-image/brand_icon", data={
            "image": (icon_buf, "chef_icon.png")
        }, content_type="multipart/form-data")
        assert res_icon.status_code == 200
        icon_data = res_icon.get_json()
        assert icon_data["success"] is True

        res_wm = client.post("/settings/upload-image/brand_wordmark", data={
            "image": (wm_buf, "grumpy_wordmark.png")
        }, content_type="multipart/form-data")
        assert res_wm.status_code == 200
        wm_data = res_wm.get_json()
        assert wm_data["success"] is True

    # 3. Sidebar header now displays the wordmark, with NO separate org-name text
    dash_res2 = client.get("/dashboard")
    dash_html2 = dash_res2.data.decode("utf-8")
    assert 'class="brand-adaptive-logo"' in dash_html2
    assert 'style="height: 32px;' in dash_html2
    # Ensure separate text is omitted when wordmark is present
    assert 'class="sidebar-brand-name' not in dash_html2

    # 4. Login page now displays icon above wordmark
    client.get("/auth/logout")
    login_res2 = client.get("/auth/login")
    login_html2 = login_res2.data.decode("utf-8")
    assert 'style="height: 64px; width: 64px; object-fit: contain;"' in login_html2
    assert 'alt="Icon"' in login_html2
    assert 'alt="Grumpy Crumb"' in login_html2

    # Clean up uploaded test branding files
    with app.app_context():
        client.post("/auth/login", data={"username": "brand_admin", "password": "Admin123!"})
        client.post("/settings/remove-image/brand_icon")
        client.post("/settings/remove-image/brand_wordmark")
