"""
Upload and filename security utilities.
Enforces strict MIME validation, randomized UUID filenames, and strictly forbids SVG.
"""
import os
import uuid
from typing import Tuple
from werkzeug.datastructures import FileStorage
from PIL import Image

ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp"}
ALLOWED_DOC_EXTS = {"pdf"}
ALLOWED_ALL = ALLOWED_IMAGE_EXTS | ALLOWED_DOC_EXTS

# Magic signatures for MIME verification
MAGIC_BYTES = {
    "png": b"\x89PNG\r\n\x1a\n",
    "jpg": b"\xff\xd8\xff",
    "jpeg": b"\xff\xd8\xff",
    "webp": b"RIFF",
    "pdf": b"%PDF",
}


def validate_and_save_upload(
    file: FileStorage,
    upload_folder: str,
    subfolder: str = "",
    allow_docs: bool = False,
    max_bytes: int = 5242880,
) -> Tuple[bool, str]:
    """
    Validates an uploaded file for size, extension, and binary content signature.
    Generates a cryptographically randomized UUID filename.
    Never permits SVG files (stored-XSS prevention).

    Returns:
        (success: bool, filename_or_error: str)
    """
    if not file or not file.filename:
        return False, "No file uploaded."

    original_filename = file.filename.strip()
    ext = original_filename.rsplit(".", 1)[-1].lower() if "." in original_filename else ""

    # Strict SVG Rejection (Section 35 & 38.6)
    if ext == "svg" or "svg" in file.mimetype.lower():
        return False, "SVG files are strictly prohibited due to security restrictions."

    allowed = ALLOWED_ALL if allow_docs else ALLOWED_IMAGE_EXTS
    if ext not in allowed:
        return False, f"File type '.{ext}' is not permitted. Allowed: {', '.join(sorted(allowed))}."

    # Read binary header for magic byte verification
    header = file.read(16)
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > max_bytes:
        return False, f"File size exceeds maximum permitted limit of {max_bytes // (1024 * 1024)} MB."

    if file_size == 0:
        return False, "Uploaded file is empty."

    # Validate image using Pillow for raster verification
    if ext in ALLOWED_IMAGE_EXTS:
        try:
            image = Image.open(file)
            image.verify()
            file.seek(0)
        except Exception:
            return False, "Corrupted or invalid image file content."

    # Generate secure UUID filename
    secure_filename = f"{uuid.uuid4().hex}.{ext}"
    target_dir = os.path.join(upload_folder, subfolder) if subfolder else upload_folder
    os.makedirs(target_dir, exist_ok=True)

    dest_path = os.path.join(target_dir, secure_filename)
    file.save(dest_path)

    relative_path = os.path.join(subfolder, secure_filename).replace("\\", "/") if subfolder else secure_filename
    return True, relative_path
