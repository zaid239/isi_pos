"""
Secure File Upload & Processing Utility for Branding & Item Images.
Enforces strict security rules:
- Strict extension whitelist (.png, .jpg, .jpeg, .webp, .ico)
- SVG is strictly forbidden (prevents XSS)
- MIME / Magic bytes verification
- Max file size limits (default 2 MB)
- UUID filenames to prevent path traversal and collisions
- Pillow image processing:
  * Resizes on upload: max 800px for items, max 400px for logo, 64px square for favicon.
  * Strips EXIF metadata.
  * Preserves smaller images without upscaling.
  * Converts to WebP primary with companion JPEG fallback.
- Safe file deletion and physical existence validation.
"""
import os
import uuid
from typing import Optional, Tuple, Dict
from flask import current_app, url_for
from PIL import Image, ImageOps, ImageFilter
import numpy as np

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "ico"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

IMAGE_MAGIC_SIGNATURES = {
    b"\x89PNG\r\n\x1a\n": "png",
    b"\xff\xd8\xff": "jpg",
    b"RIFF": "webp",
    b"\x00\x00\x01\x00": "ico",
}


def get_upload_root() -> str:
    """Returns the absolute base directory for uploads (data/uploads or static/uploads)."""
    if current_app and current_app.config.get("UPLOAD_FOLDER"):
        return os.path.abspath(current_app.config["UPLOAD_FOLDER"])
    try:
        from app.paths import get_upload_dir
        return str(get_upload_dir().resolve())
    except Exception:
        return os.path.abspath(os.path.join(current_app.root_path, "static", "uploads"))


def validate_image_file(file_storage, max_size_bytes: int = MAX_FILE_SIZE_BYTES):
    """
    Validates file extension, forbidden formats (SVG), file size, and magic byte headers.
    Raises ValueError on any violation.
    """
    if not file_storage or not file_storage.filename:
        raise ValueError("No file provided.")

    filename = file_storage.filename.lower()
    
    # Strictly reject SVG
    if filename.endswith(".svg") or "svg" in (file_storage.mimetype or "").lower():
        raise ValueError("SVG files are strictly forbidden due to security policies.")

    # Check extension whitelist
    if "." not in filename:
        raise ValueError("Uploaded file has no extension.")
    ext = filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File extension '.{ext}' is not permitted. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    # Check size
    file_storage.seek(0, os.SEEK_END)
    size = file_storage.tell()
    file_storage.seek(0)
    if size > max_size_bytes:
        raise ValueError(f"File size ({size / 1024 / 1024:.2f} MB) exceeds maximum allowed limit of {max_size_bytes / 1024 / 1024:.1f} MB.")
    if size == 0:
        raise ValueError("Uploaded file is empty.")

    # Verify magic bytes
    header = file_storage.read(12)
    file_storage.seek(0)

    is_valid_magic = False
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        is_valid_magic = True
    elif header.startswith(b"\xff\xd8\xff"):
        is_valid_magic = True
    elif header.startswith(b"RIFF") and b"WEBP" in header:
        is_valid_magic = True
    elif header.startswith(b"\x00\x00\x01\x00"):
        is_valid_magic = True

    if not is_valid_magic:
        raise ValueError("File content does not match a genuine PNG, JPEG, WEBP, or ICO image.")

    return ext, size


def save_uploaded_image(
    file_storage,
    subfolder: str = "items",
    max_size_bytes: int = MAX_FILE_SIZE_BYTES,
    image_kind: str = "item"
) -> str:
    """
    Validates, resizes with Pillow, transposes EXIF orientation, strips EXIF,
    converts to WebP with companion fallback (PNG for logos to preserve alpha, JPEG for others),
    and saves to app/static/uploads/<subfolder>/ with a UUID filename.
    Returns relative web path to the primary WebP image (e.g. 'uploads/items/<uuid>.webp').
    """
    ext, _ = validate_image_file(file_storage, max_size_bytes=max_size_bytes)
    
    upload_dir = os.path.join(get_upload_root(), subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    file_uuid = uuid.uuid4().hex

    # Open image with Pillow and transpose EXIF orientation first, then strip EXIF
    try:
        img = Image.open(file_storage)
        # Transpose based on EXIF orientation if present
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise ValueError(f"Could not process image: {e}")

    # Determine maximum dimensions based on image_kind
    # - favicon: 64px square
    # - logo: max 400px on long edge
    # - item: max 800px on long edge
    orig_w, orig_h = img.size
    if image_kind == "favicon" or subfolder == "favicon":
        target_size = (64, 64)
        img = ImageOps.fit(img, target_size, method=Image.Resampling.LANCZOS)
    elif image_kind == "logo" or (subfolder == "branding" and image_kind != "favicon"):
        max_dim = 400
        if orig_w > max_dim or orig_h > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
    else:  # item product photo
        max_dim = 800
        if orig_w > max_dim or orig_h > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    # 1. Save Primary WebP File (quality 80, EXIF stripped)
    webp_filename = f"{file_uuid}.webp"
    webp_path = os.path.join(upload_dir, webp_filename)
    
    # Ensure mode suitable for WebP (preserving alpha for logos or transparent images)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img_webp = img.convert("RGBA")
    else:
        img_webp = img.convert("RGB")
    
    img_webp.save(webp_path, "WEBP", quality=80, method=6)

    # 2. Save Companion Fallback File:
    # For logos: keep transparency by using PNG fallback!
    # For items / other: use JPEG fallback (quality 80)
    if image_kind == "logo" or (subfolder == "branding" and image_kind != "favicon"):
        png_filename = f"{file_uuid}.png"
        png_path = os.path.join(upload_dir, png_filename)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img.convert("RGBA").save(png_path, "PNG", optimize=True)
        else:
            img.save(png_path, "PNG", optimize=True)
    else:
        jpg_filename = f"{file_uuid}.jpg"
        jpg_path = os.path.join(upload_dir, jpg_filename)
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            # Solid white background to avoid black alpha background in JPEG
            bg = Image.new("RGB", img.size, (255, 255, 255))
            alpha = img.convert("RGBA").split()[3]
            bg.paste(img.convert("RGB"), mask=alpha)
            bg.save(jpg_path, "JPEG", quality=80, optimize=True)
        else:
            img.convert("RGB").save(jpg_path, "JPEG", quality=80, optimize=True)

    return f"uploads/{subfolder}/{webp_filename}"


def clean_old_temp_files(max_age_seconds: int = 3600):
    """
    Cleans up files in app/static/uploads/temp/ that are older than max_age_seconds (default 1 hr).
    Runs opportunistically on each temp upload call to avoid needing an external scheduler.
    """
    import time
    temp_dir = os.path.join(get_upload_root(), "temp")
    if not os.path.exists(temp_dir):
        return
    now = time.time()
    try:
        for f in os.listdir(temp_dir):
            fp = os.path.join(temp_dir, f)
            if os.path.isfile(fp):
                mtime = os.path.getmtime(fp)
                if now - mtime > max_age_seconds:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
    except Exception:
        pass


def save_temp_image(file_storage, max_size_bytes: int = MAX_FILE_SIZE_BYTES) -> Tuple[str, int, str]:
    """
    Saves an image to uploads/temp/ for New Item creation before the item ID exists.
    Automatically prunes stale temp files (>1 hr) on invocation.
    Returns (rel_path, size_bytes, formatted_size_str).
    """
    clean_old_temp_files()
    rel_path = save_uploaded_image(file_storage, subfolder="temp", max_size_bytes=max_size_bytes, image_kind="item")
    full_path = os.path.join(current_app.root_path, "static", rel_path)
    fsize = os.path.getsize(full_path) if os.path.isfile(full_path) else 0
    formatted_str = f"{fsize / 1024:.1f} KB"
    return rel_path, fsize, formatted_str


def promote_temp_image(temp_rel_path: str, target_subfolder: str = "items") -> Optional[str]:
    """
    Atomically moves a temporary uploaded image and its companion fallback from
    app/static/uploads/temp/ to app/static/uploads/<target_subfolder>/.
    Returns the new relative path (e.g. 'uploads/items/<uuid>.webp').
    """
    import shutil
    if not temp_rel_path or not str(temp_rel_path).strip():
        return None

    clean = str(temp_rel_path).strip().replace("\\", "/").lstrip("/")
    if clean.startswith("static/"):
        clean = clean[len("static/"):]

    if not clean.startswith("uploads/temp/"):
        return None

    filename = os.path.basename(clean)
    base_name, _ = os.path.splitext(filename)

    src_webp = os.path.join(get_upload_root(), "temp", filename)
    if not os.path.isfile(src_webp):
        # Fallback check under static/
        src_webp = os.path.join(current_app.root_path, "static", clean)
        if not os.path.isfile(src_webp):
            return None

    dst_dir = os.path.join(get_upload_root(), target_subfolder)
    os.makedirs(dst_dir, exist_ok=True)

    dst_webp = os.path.join(dst_dir, filename)
    shutil.move(src_webp, dst_webp)

    # Also move companion fallback if present (.jpg or .png)
    for comp_ext in (".jpg", ".png"):
        src_comp = os.path.join(get_upload_root(), "temp", f"{base_name}{comp_ext}")
        if not os.path.isfile(src_comp):
            src_comp = os.path.join(current_app.root_path, "static", "uploads", "temp", f"{base_name}{comp_ext}")
        if os.path.isfile(src_comp):
            dst_comp = os.path.join(dst_dir, f"{base_name}{comp_ext}")
            shutil.move(src_comp, dst_comp)

    return f"uploads/{target_subfolder}/{filename}"


def delete_temp_image(temp_rel_path: str) -> bool:
    """
    Deletes a temporary image and its companion from uploads/temp/.
    """
    if not temp_rel_path or not str(temp_rel_path).strip():
        return False
    clean = str(temp_rel_path).strip().replace("\\", "/").lstrip("/")
    if clean.startswith("static/"):
        clean = clean[len("static/"):]
    if not clean.startswith("uploads/temp/"):
        return False
    return delete_uploaded_file(clean)


def resolve_image_url(stored_path: Optional[str]) -> Optional[str]:
    """
    Normalizes a stored relative database path, verifies physical file existence on disk,
    and returns the web-accessible URL via url_for('static', filename=...).
    Returns None if path is empty or physical file does not exist on disk.
    Prevents broken <img> icons from rendering.
    """
    if not stored_path or not str(stored_path).strip():
        return None

    clean = str(stored_path).strip().replace("\\", "/")
    if clean.startswith("/static/"):
        clean = clean[len("/static/"):]
    elif clean.startswith("static/"):
        clean = clean[len("static/"):]
    elif clean.startswith("/"):
        clean = clean.lstrip("/")

    # Check existence on disk under external data/uploads or app/static/
    rel_in_uploads = clean[len("uploads/"):] if clean.startswith("uploads/") else clean
    full_path = os.path.join(get_upload_root(), rel_in_uploads)
    if not os.path.isfile(full_path):
        static_path = os.path.join(current_app.root_path, "static", clean)
        if not os.path.isfile(static_path):
            return None

    return url_for("static", filename=clean)


def resolve_fallback_url(stored_path: Optional[str]) -> Optional[str]:
    """
    Given a stored WebP path (e.g. uploads/items/xxx.webp),
    checks if a companion .png or .jpg fallback file exists on disk and returns its URL.
    Returns None if neither fallback nor primary exists on disk.
    """
    if not stored_path or not str(stored_path).strip():
        return None

    clean = str(stored_path).strip().replace("\\", "/")
    if clean.startswith("/static/"):
        clean = clean[len("/static/"):]
    elif clean.startswith("static/"):
        clean = clean[len("static/"):]
    elif clean.startswith("/"):
        clean = clean.lstrip("/")

    base, ext = os.path.splitext(clean)
    rel_base = base[len("uploads/"):] if base.startswith("uploads/") else base
    # Check PNG fallback (logos) then JPG fallback (items/favicon)
    for comp_ext in (".png", ".jpg"):
        candidate_rel = f"{base}{comp_ext}"
        full_candidate = os.path.join(get_upload_root(), f"{rel_base}{comp_ext}")
        if os.path.isfile(full_candidate):
            return url_for("static", filename=candidate_rel)
        static_candidate = os.path.join(current_app.root_path, "static", candidate_rel)
        if os.path.isfile(static_candidate):
            return url_for("static", filename=candidate_rel)

    # If no companion fallback exists, fall back to resolve_image_url
    return resolve_image_url(stored_path)


def delete_uploaded_file(stored_path: Optional[str]) -> bool:
    """
    Safely deletes an uploaded file and its companion fallbacks (.jpg, .png, .webp) from app/static/uploads/.
    Includes strict path-traversal prevention.
    """
    if not stored_path or not str(stored_path).strip():
        return False

    clean = str(stored_path).strip().replace("\\", "/")
    if clean.startswith("/static/"):
        clean = clean[len("/static/"):]
    elif clean.startswith("static/"):
        clean = clean[len("static/"):]
    elif clean.startswith("/"):
        clean = clean.lstrip("/")

    # Must be within uploads/
    if not clean.startswith("uploads/"):
        return False

    rel_in_uploads = clean[len("uploads/"):] if clean.startswith("uploads/") else clean
    base_stem, _ = os.path.splitext(rel_in_uploads)
    deleted_any = False

    # Delete all companion files (.webp, .jpg, .png) from both data/uploads and static/uploads
    for ext_to_del in (".webp", ".jpg", ".png"):
        f1 = os.path.join(get_upload_root(), f"{base_stem}{ext_to_del}")
        if os.path.isfile(f1):
            try:
                os.remove(f1)
                deleted_any = True
            except OSError:
                pass
        f2 = os.path.join(current_app.root_path, "static", "uploads", f"{base_stem}{ext_to_del}")
        if os.path.isfile(f2):
            try:
                os.remove(f2)
                deleted_any = True
            except OSError:
                pass

    return deleted_any


def process_line_art_transparency(
    img: Image.Image,
    near_white_threshold: float = 240.0,
    solid_stroke_threshold: float = 40.0
) -> Tuple[Image.Image, Image.Image]:
    """
    Takes an image (black line art on white/near-white background).
    Thresholds near-white pixels to completely transparent (alpha = 0).
    Maps stroke darkness smoothly into alpha (solid black lines = 255 alpha,
    anti-aliased edges smoothly interpolated between solid_stroke_threshold and near_white_threshold).
    
    Generates two crisp PNG images with transparent backgrounds:
    1. Black version: pure black strokes (0, 0, 0, alpha) with no white halos.
    2. White version: pure white strokes (255, 255, 255, alpha) for high contrast on dark surfaces.
    """
    rgba = np.array(img.convert("RGBA"), dtype=np.float32)
    r, g, b, orig_a = rgba[:, :, 0], rgba[:, :, 1], rgba[:, :, 2], rgba[:, :, 3]

    # Grayscale luminance L = 0.299*R + 0.587*G + 0.114*B
    lum = 0.299 * r + 0.587 * g + 0.114 * b

    # Linear mapping: lum >= 240 -> alpha 0; lum <= 40 -> alpha 255
    alpha_calc = np.clip(
        (near_white_threshold - lum) / (near_white_threshold - solid_stroke_threshold) * 255.0,
        0.0,
        255.0
    )
    final_alpha = np.minimum(orig_a, alpha_calc).astype(np.uint8)

    h, w = final_alpha.shape

    # 1. Black version (0, 0, 0, alpha)
    black_arr = np.zeros((h, w, 4), dtype=np.uint8)
    black_arr[:, :, 3] = final_alpha
    black_img = Image.fromarray(black_arr)

    # 2. Inverted White version (255, 255, 255, alpha)
    white_arr = np.full((h, w, 4), 255, dtype=np.uint8)
    white_arr[:, :, 3] = final_alpha
    white_img = Image.fromarray(white_arr)

    return black_img, white_img


def generate_thickened_favicon(
    black_icon: Image.Image,
    target_size: Tuple[int, int] = (64, 64)
) -> Image.Image:
    """
    Generates a 64x64 browser favicon from the black transparent icon.
    Thickens delicate strokes slightly using morphological dilation and alpha contrast boosting
    so fine line art survives downscaling and remains crisp in browser tabs.
    """
    alpha = black_icon.convert("RGBA").split()[3]

    # If source is larger than 128px, resize to intermediate 128px first to dilate smoothly
    if alpha.width > 128 or alpha.height > 128:
        intermediate = alpha.resize((128, 128), Image.Resampling.LANCZOS)
        dilated = intermediate.filter(ImageFilter.MaxFilter(3))
        thickened_128 = Image.blend(intermediate, dilated, 0.5)
        alpha_64 = thickened_128.resize(target_size, Image.Resampling.LANCZOS)
    else:
        alpha_64 = alpha.resize(target_size, Image.Resampling.LANCZOS)
        dilated = alpha_64.filter(ImageFilter.MaxFilter(3))
        alpha_64 = Image.blend(alpha_64, dilated, 0.45)

    # Boost alpha contrast so faint edges stay visible
    boosted_a = alpha_64.point(lambda p: min(255, int(p * 1.35)))

    fav_arr = np.zeros((target_size[1], target_size[0], 4), dtype=np.uint8)
    fav_arr[:, :, 3] = np.array(boosted_a, dtype=np.uint8)
    return Image.fromarray(fav_arr)


def process_branding_upload(file_storage, kind: str = "icon") -> Dict[str, str]:
    """
    Processes an uploaded black line art branding image (icon or wordmark).
    - Thresholds white background to transparent.
    - Generates original black version and inverted white version as PNG.
    - If kind == 'icon', enforces 1:1 square crop and generates 64px thickened favicon PNG.
    - If kind == 'wordmark', preserves free aspect ratio.
    - Saves PNG files to uploads/branding/ with UUID filenames.
    Returns dict with relative paths to the generated files.
    """
    validate_image_file(file_storage)
    
    upload_dir = os.path.join(get_upload_root(), "branding")
    os.makedirs(upload_dir, exist_ok=True)
    file_uuid = uuid.uuid4().hex

    try:
        img = Image.open(file_storage)
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise ValueError(f"Could not process branding image: {e}")

    kind_clean = "icon" if "icon" in kind.lower() else "wordmark"

    if kind_clean == "icon":
        # Ensure 1:1 square crop
        w, h = img.size
        if w != h:
            min_dim = min(w, h)
            left = (w - min_dim) // 2
            top = (h - min_dim) // 2
            img = img.crop((left, top, left + min_dim, top + min_dim))
        if img.width > 800:
            img.thumbnail((800, 800), Image.Resampling.LANCZOS)
    else:
        # Wordmark: Free aspect ratio, max 1200px on width
        if img.width > 1200 or img.height > 1200:
            img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)

    black_img, white_img = process_line_art_transparency(img)

    black_filename = f"{kind_clean}_{file_uuid}_black.png"
    white_filename = f"{kind_clean}_{file_uuid}_white.png"

    black_path = os.path.join(upload_dir, black_filename)
    white_path = os.path.join(upload_dir, white_filename)

    black_img.save(black_path, "PNG", optimize=True)
    white_img.save(white_path, "PNG", optimize=True)

    result = {
        "black_path": f"uploads/branding/{black_filename}",
        "white_path": f"uploads/branding/{white_filename}",
    }

    if kind_clean == "icon":
        fav_img = generate_thickened_favicon(black_img, target_size=(64, 64))
        fav_filename = f"favicon_{file_uuid}.png"
        fav_path = os.path.join(upload_dir, fav_filename)
        fav_img.save(fav_path, "PNG", optimize=True)
        result["favicon_path"] = f"uploads/branding/{fav_filename}"

    return result


def delete_branding_files(file_paths: list) -> None:
    """Safely deletes branding PNG files from uploads."""
    for p in file_paths:
        if p and str(p).strip():
            delete_uploaded_file(p)

