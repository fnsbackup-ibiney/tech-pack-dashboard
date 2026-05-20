"""
Image helpers — compress uploaded images and encode them as base64 strings.

The tech pack dashboard stores images inline in Firestore (base64) rather than
in a separate object store. To keep documents under Firestore's 1 MB limit,
every uploaded image is resized to ``MAX_DIM`` pixels on the long edge and
re-encoded as a moderate-quality JPEG (or PNG if it has transparency).
"""

from __future__ import annotations

import base64
import uuid
from io import BytesIO

from PIL import Image, ImageOps

# Long-edge limit for stored images. 800 keeps each one well under 200 KB
# even for busy photos, leaving plenty of headroom inside a Firestore doc.
MAX_DIM = 800
JPEG_QUALITY = 82


def compress_to_base64(file_bytes: bytes, filename: str = "") -> tuple[str, str]:
    """Compress an image and return (base64_data, mime_type).

    - Preserves transparency by saving as PNG when the source has an alpha channel.
    - Otherwise saves as JPEG for smaller size.
    - Auto-rotates based on EXIF orientation so phone photos don't show sideways.
    """
    img = Image.open(BytesIO(file_bytes))
    img = ImageOps.exif_transpose(img)  # respect EXIF rotation tags

    # Resize in-place, preserving aspect ratio
    img.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)

    buf = BytesIO()
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    if has_alpha:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        mime = "image/jpeg"

    return base64.b64encode(buf.getvalue()).decode("ascii"), mime


def make_image_entry(file_bytes: bytes, filename: str = "", caption: str = "") -> dict:
    """Build the dict we store in session_state / Firestore for one image."""
    data, mime = compress_to_base64(file_bytes, filename)
    # If no caption was provided, fall back to the filename (sans extension)
    if not caption and filename:
        caption = filename.rsplit(".", 1)[0]
    return {
        "id": uuid.uuid4().hex[:12],
        "caption": caption,
        "data": data,
        "mime": mime,
    }


def to_data_url(image: dict) -> str:
    """Return a data: URL that ``st.image`` and HTML <img> tags both accept."""
    return f"data:{image['mime']};base64,{image['data']}"


def to_bytes(image: dict) -> bytes:
    """Decode the base64 payload back to raw image bytes — for PDF/Word embedding."""
    return base64.b64decode(image["data"])


def approximate_size_kb(image: dict) -> float:
    """Estimate the decoded size in KB. Base64 is ~33% bigger than raw."""
    return len(image["data"]) * 0.75 / 1024
