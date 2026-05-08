"""
Fulfillment pipeline — Shopify webhook → hi-res generation → Printful order.

Flow:
    1. Shopify fires orders/create webhook
    2. We parse line item properties (Job ID, Style, Pet name, etc.)
    3. Download the customer's original photo from our preview storage
    4. Generate a hi-res print-ready portrait via Gemini
    5. Upload the print file to cloud storage
    6. Create a Printful order with the file URL

Environment variables:
    SHOPIFY_WEBHOOK_SECRET  — HMAC secret for verifying Shopify webhooks
    PRINTFUL_API_KEY        — Printful API token
    UPLOAD_BUCKET_URL       — Base URL for uploaded print files (S3/R2/etc.)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from generate import (
    OUTPUT_DIR,
    POST_PROCESS,
    PROMPTS,
    call_gemini,
    composite_name,
    crop_to_ratio,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Print-ready dimensions (pixels at 300 DPI)
#
# Canvas (in) — gallery-wrap canvas: file = front face + 3" bleed each
#   side. The bleed wraps around the 1.25" stretcher bars + onto the back.
# Framed canvas (in) — flat canvas in a frame: file = front face + 1.5"
#   bleed each side. Less bleed because the frame physically covers the
#   outer band rather than wrapping it around bars.
# Verified against the official Printful customer templates in
#   PetPrintables Admin/Canvas (in) templates  → +3" each side
#   …/Framed_canvas_in_guideline                → +1.5" each side
#
# Magnets ship at die-cut size + 1/8" bleed each side (legacy values kept).
# ---------------------------------------------------------------------------

# Bleed inset per side, in pixels at 300 DPI. Different per product class
# because gallery-wrap and framed canvas have physically different bleed
# requirements (Printful's customer templates confirm this).
GALLERY_WRAP_BLEED_PX = 900   # 3"  → canvas-*  (gallery wrap)
FRAMED_CANVAS_BLEED_PX = 450  # 1.5" → canvas-*-framed
# Backwards-compat alias (older code paths reference the unified name).
CANVAS_BLEED_PX = GALLERY_WRAP_BLEED_PX

PRINT_SIZES: dict[str, tuple[int, int]] = {
    # Canvas (in) — gallery wrap, +3" bleed each side (front_face + 6")
    "canvas-12x12":        (5400, 5400),   # front face 3600×3600
    "canvas-12x16":        (5400, 6600),   # front face 3600×4800
    "canvas-16x16":        (6600, 6600),   # front face 4800×4800
    "canvas-16x20":        (6600, 7800),   # front face 4800×6000
    # Framed canvas — +1.5" bleed each side (front_face + 3")
    "canvas-12x12-framed": (4500, 4500),   # front face 3600×3600
    "canvas-12x16-framed": (4500, 5700),   # front face 3600×4800
    "canvas-16x16-framed": (5700, 5700),   # front face 4800×4800
    "canvas-16x20-framed": (5700, 6900),   # front face 4800×6000
    # Die-Cut Magnets (300 DPI, +1/8" bleed each side → +0.25" total).
    "magnet-3x3":          (975,  975),
    "magnet-4x4":          (1275, 1275),
    "magnet-6x6":          (1875, 1875),
}

# Front-face dimensions (the customer-visible area — file minus bleed).
# This is the area the pet + name composition must fit inside; everything
# outside this rectangle either wraps around stretcher bars (gallery wrap)
# or sits behind the frame (framed canvas).
FRONT_FACE_SIZES: dict[str, tuple[int, int]] = {
    "canvas-12x12":        (3600, 3600),
    "canvas-12x16":        (3600, 4800),
    "canvas-16x16":        (4800, 4800),
    "canvas-16x20":        (4800, 6000),
    "canvas-12x12-framed": (3600, 3600),
    "canvas-12x16-framed": (3600, 4800),
    "canvas-16x16-framed": (4800, 4800),
    "canvas-16x20-framed": (4800, 6000),
    # Magnets have no wrap/frame; front face == file.
    "magnet-3x3":          (975,  975),
    "magnet-4x4":          (1275, 1275),
    "magnet-6x6":          (1875, 1875),
}

# Aspect ratios of the FRONT FACE (what the customer sees). Used to pick
# which per-aspect master to derive each variant's print file from.
PRODUCT_RATIOS: dict[str, tuple[int, int]] = {
    "canvas-12x12":        (1, 1),
    "canvas-12x16":        (3, 4),
    "canvas-16x16":        (1, 1),
    "canvas-16x20":        (4, 5),
    "canvas-12x12-framed": (1, 1),
    "canvas-12x16-framed": (3, 4),
    "canvas-16x16-framed": (1, 1),
    "canvas-16x20-framed": (4, 5),
    "magnet-3x3":          (1, 1),
    "magnet-4x4":          (1, 1),
    "magnet-6x6":          (1, 1),
}


def _is_canvas(product_key: str) -> bool:
    """True when this product needs a wrap-bleed border on the print file
    (3" each side for gallery-wrap canvas-*, 1.5" each side for
    canvas-*-framed). Magnets ship at file dimensions with no wrap, so
    they bypass the wrap-padding step in generate_print_file()."""
    return product_key.startswith("canvas-")

# Printful catalog variant IDs are resolved DYNAMICALLY at runtime via
# mockups._resolve_variant_ids() which hits GET /products/<catalog_id>.
# No hardcoded IDs — we fetch them fresh on each order to stay in sync
# with Printful's catalog. See: _get_printful_variant_id() below.

def _get_printful_variant_id(product_key: str) -> int:
    """Resolve the Printful catalog variant ID for a product-size key.
    e.g. 'canvas-12x16' -> 19298 (or whatever the live Printful ID is).

    Raises ValueError if the key is unknown or the API lookup fails.
    """
    from mockups import _resolve_variant_ids

    # Parse product_key into (product_type, size_label)
    # Supports: canvas-12x12, canvas-16x20, canvas-16x20-framed, magnet-{3x3|4x4|6x6}
    if product_key.endswith("-framed"):
        # e.g. "canvas-16x20-framed" → product_type="canvas-framed", size="16x20"
        size = product_key.rsplit("-", 1)[0].split("-", 1)[1]
        product_type = "canvas-framed"
    else:
        parts = product_key.split("-", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid product_key: {product_key}")
        product_type, size = parts

    variants = _resolve_variant_ids(product_type)
    variant_id = variants.get(size)
    if not variant_id:
        raise ValueError(
            f"No Printful variant for '{product_key}' "
            f"(resolved product_type={product_type}, size={size}, "
            f"available={list(variants.keys())})"
        )
    return variant_id


# ---------------------------------------------------------------------------
# Shopify webhook verification
# ---------------------------------------------------------------------------

def verify_shopify_webhook(data: bytes, hmac_header: str) -> bool:
    """Verify the Shopify webhook HMAC-SHA256 signature.
    In production SHOPIFY_WEBHOOK_SECRET must be set. If missing,
    this function returns False (fail-closed) to prevent accepting
    unverified webhooks in production."""
    secret = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
    if not secret:
        # Allow bypass only in explicit dev mode
        if os.environ.get("ALLOW_UNVERIFIED_WEBHOOKS") == "1":
            log.warning("SHOPIFY_WEBHOOK_SECRET not set — bypassing verification (DEV MODE)")
            return True
        log.error("SHOPIFY_WEBHOOK_SECRET not set — rejecting webhook")
        return False

    if not hmac_header:
        return False

    # Shopify sends base64-encoded HMAC — compare apples to apples
    import base64 as _b64
    digest_bytes = hmac.new(secret.encode("utf-8"), data, hashlib.sha256).digest()
    expected = _b64.b64encode(digest_bytes).decode()

    return hmac.compare_digest(expected, hmac_header)


def _sample_corner_color(img: Image.Image) -> tuple[int, int, int]:
    """Average RGB of the four corner regions — used to fill the wrap-bleed
    band around a canvas print. Stylised pet portraits sit on a calm,
    relatively flat background, so the four-corner average is a faithful
    extension of the painting beyond the front face.
    """
    rgb = img.convert("RGB") if img.mode != "RGB" else img
    w, h = rgb.size
    corner_size = max(8, min(w, h) // 50)
    pixels: list[tuple[int, int, int]] = []
    for x0, y0 in (
        (0, 0),
        (w - corner_size, 0),
        (0, h - corner_size),
        (w - corner_size, h - corner_size),
    ):
        pixels.extend(rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata())
    if not pixels:
        return (255, 255, 255)
    r = sum(p[0] for p in pixels) // len(pixels)
    g = sum(p[1] for p in pixels) // len(pixels)
    b = sum(p[2] for p in pixels) // len(pixels)
    return (r, g, b)


def wrap_print_file_with_bleed(
    front_face: Image.Image,
    product_key: str,
) -> Image.Image:
    """Place a front-face-correct composition into a Printful canvas
    print file with the required wrap bleed on every side: 3" for
    gallery-wrap (canvas-*), 1.5" for framed canvas (canvas-*-framed).

    The customer-visible art lives in the inner FRONT_FACE_SIZES rectangle.
    The surrounding bleed band is filled with the sampled corner
    background colour so the wrap (or frame-edge band) reads as a calm
    continuation of the painting rather than a visible seam.

    Bleed depth is implicit in PRINT_SIZES vs FRONT_FACE_SIZES — the
    helper just pads to whatever the file dims demand, so changing the
    bleed for a future product class is a one-line edit in the constants.

    No-op for non-canvas products (magnets/etc.) — they ship at file
    dimensions with no wrap.
    """
    if not _is_canvas(product_key):
        return front_face

    file_w, file_h = PRINT_SIZES[product_key]
    front_w, front_h = FRONT_FACE_SIZES[product_key]

    # Resize the front-face composition to exactly the front-face area
    # (handles both upscale + downscale; LANCZOS preserves edge sharpness
    # at the typical magnitudes — ~1.05–1.4× upscale from per-aspect master).
    if (front_face.width, front_face.height) != (front_w, front_h):
        front_face = front_face.resize((front_w, front_h), Image.LANCZOS)

    bg = _sample_corner_color(front_face)
    file_canvas = Image.new("RGB", (file_w, file_h), bg)
    inset_x = (file_w - front_w) // 2
    inset_y = (file_h - front_h) // 2
    file_canvas.paste(front_face, (inset_x, inset_y))
    return file_canvas


# ---------------------------------------------------------------------------
# Hi-res print file generation
# ---------------------------------------------------------------------------

def generate_print_file(
    photo_path: Path,
    pet_name: str,
    style: str,
    product_key: str,
    style_vars: Optional[dict] = None,
    composited_r2_key: Optional[str] = None,
    font_size: str = "medium",
    show_name: str = "Yes",
) -> Path:
    """
    Generate a print-ready hi-res portrait sized for the target product.

    Preferred path: download the composited PNG from R2 (already generated
    during the initial portrait flow) and upscale it with LANCZOS + sharpening.
    This avoids a second Gemini API call and halves the per-order cost.

    Fallback: if R2 image is unavailable, re-generate via Gemini (old path).

    Args:
        photo_path: Path to the customer's original photo (fallback source).
        pet_name: Pet's name for compositing.
        style: Style ID (e.g. 'soft-watercolour' from React → mapped to 'watercolor').
        product_key: Product-size key (e.g. 'canvas-12x24').
        style_vars: Optional watercolor-specific variables.
        composited_r2_key: R2 key of the composited PNG from initial generation.

    Returns:
        Path to the saved print-ready PNG.
    """
    from PIL import ImageFilter
    from storage import download_from_r2

    file_w, file_h = PRINT_SIZES[product_key]
    front_w, front_h = FRONT_FACE_SIZES[product_key]
    ratio = PRODUCT_RATIOS[product_key]

    log.info(
        "Generating print file: %s %s '%s' → %dx%d (front face %dx%d)",
        style, product_key, pet_name, file_w, file_h, front_w, front_h,
    )

    # ── Preferred path: upscale from R2 composited PNG ───────────
    r2_path = None
    if composited_r2_key:
        r2_path = download_from_r2(composited_r2_key)

    if r2_path and r2_path.exists():
        log.info("Using R2 composited image for upscale (no Gemini re-generation)")
        img = Image.open(r2_path)
        img.load()

        # Crop the source to the FRONT-FACE aspect (1:1 / 3:4 / 4:5).
        # The wrap padding is added later by wrap_print_file_with_bleed().
        img = crop_to_ratio(img, ratio, gravity="center")

        # Resize to FRONT-FACE pixel dimensions. The wrap step below pads
        # this up to file dimensions; the name is composited at front-face
        # coordinates so its position is independent of the wrap.
        needs_upscale = img.width < front_w or img.height < front_h
        if (img.width, img.height) != (front_w, front_h):
            img = img.resize((front_w, front_h), Image.LANCZOS)

        # Sharpen ONLY when upscaling — LANCZOS downscale + UnsharpMask
        # produces halos.
        if needs_upscale:
            img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        # Composite pet name on the front-face image (zone_top is a
        # fraction of the front face, not the file). Skip for mugs, and
        # honor the customer's _Show Name=No selection from the cart.
        style_key = _map_style_id(style)
        skip_name = product_key.startswith("mug") or str(show_name).strip().lower() == "no"
        if not skip_name:
            img = composite_name(img, pet_name, style=style_key, font_size_key=font_size)

        # Clean up downloaded file
        r2_path.unlink(missing_ok=True)

    else:
        # ── Fallback: re-generate via Gemini ─────────────────────
        log.warning("R2 composited image not available, falling back to Gemini re-generation")
        style_key = _map_style_id(style)

        raw_bytes = call_gemini(photo_path, style_key, style_vars)
        img = Image.open(BytesIO(raw_bytes))
        img.load()

        # Add background padding to give the pet breathing room — Gemini
        # routinely fills the canvas edge-to-edge regardless of prompt
        # instructions. Mirrors the preview-side padding in generate.py
        # so customer-facing previews and print files match.
        from generate import add_background_padding as _pad
        img = _pad(img, padding_ratio=0.12)

        # Crop to FRONT-FACE aspect ratio with center gravity. Wrap is
        # padded around this in wrap_print_file_with_bleed() below.
        img = crop_to_ratio(img, ratio, gravity="center")

        # Apply style-specific post-processing
        if style_key != "watercolor":
            img = POST_PROCESS.get(style_key, lambda x: x)(img)

        # Resize to FRONT-FACE dimensions (the customer-visible area).
        if (img.width, img.height) != (front_w, front_h):
            img = img.resize((front_w, front_h), Image.LANCZOS)

        # Composite pet name on the front-face image. Skip for mugs and
        # honor the customer's _Show Name=No selection from the cart.
        skip_name = product_key.startswith("mug") or str(show_name).strip().lower() == "no"
        if not skip_name:
            img = composite_name(img, pet_name, style=style_key, font_size_key=font_size)

    # For canvas variants, expand the front-face composition to the full
    # file dimensions by padding sampled-bg-coloured wrap around it. For
    # magnets (no wrap), wrap_print_file_with_bleed is a no-op.
    img = wrap_print_file_with_bleed(img, product_key)

    # Save with 300 DPI metadata embedded for print shops
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
    filename = f"print_{product_key}_{safe_name}_{uuid.uuid4().hex[:8]}.png"
    out_path = OUTPUT_DIR / filename
    img.save(out_path, "PNG", dpi=(300, 300))

    log.info("Print file saved: %s (%dx%d @ 300 DPI)", out_path, img.width, img.height)
    return out_path


def _map_style_id(react_style_id: str) -> str:
    """Map the React widget's style ID to the harness PROMPTS key."""
    mapping = {
        "soft-watercolour": "watercolor",
        "minimal-line-art": "minimal-line-art",
        "modern-shape-art": "modern-shape-art",
        "neon-pop-art": "neon-pop-art",
        "renaissance-royalty": "renaissance-royalty",
        "bold-graphic-poster": "bold-graphic-poster",
        "aura-gradient": "aura-gradient",
        "charcoal": "charcoal",
    }
    key = mapping.get(react_style_id, react_style_id)
    if key not in PROMPTS:
        log.warning("Unknown style '%s', falling back to 'classic'", react_style_id)
        return "classic"
    return key


# ---------------------------------------------------------------------------
# File upload (cloud storage)
# ---------------------------------------------------------------------------

def upload_print_file(local_path: Path) -> str:
    """
    Upload a print-ready file to Cloudflare R2 and return a public URL.

    Falls back to local /preview/ URL if R2 is not configured.
    Uses the print-files/ prefix to separate from preview portraits/.
    """
    from storage import upload_portrait as _upload_r2

    # Upload with a print-files/ prefix to keep separate from preview images
    cdn_url = _upload_r2(local_path, key=f"print-files/{local_path.name}")

    if cdn_url:
        log.info("Print file uploaded to R2: %s", cdn_url)
        return cdn_url

    # Fallback: serve from local Flask (dev only — won't survive restarts)
    log.warning("R2 not configured — using local preview URL for print file")
    return f"/preview/{local_path.name}"


# ---------------------------------------------------------------------------
# Shopify Admin API — order tagging
# ---------------------------------------------------------------------------

SHOPIFY_ADMIN_API_VERSION = "2024-10"


def tag_shopify_order(order_id: str, tags_to_add: list[str]) -> bool:
    """Append tags to a Shopify order via the Admin REST API.

    Merges with existing tags rather than overwriting. Safe to call
    multiple times — duplicate tags are de-duplicated by the API.

    Requires env vars:
      SHOPIFY_SHOP_DOMAIN       e.g. "pet-printables.myshopify.com"
      SHOPIFY_ADMIN_API_TOKEN   admin API access token with `write_orders`

    Returns True on success, False on any failure (logged — never raises).
    """
    if not tags_to_add:
        return True

    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()
    if not domain or not token:
        log.warning(
            "Order %s: skipping tagging — SHOPIFY_SHOP_DOMAIN or SHOPIFY_ADMIN_API_TOKEN not set",
            order_id,
        )
        return False

    # Normalize tag values: trim, collapse whitespace, lowercase. Shopify
    # tags are case-preserving but case-insensitive — lowercasing keeps the
    # admin UI/filter consistent.
    clean_tags = []
    for t in tags_to_add:
        if not t:
            continue
        t = " ".join(str(t).split()).lower().replace(",", "-")[:40]
        if t and t not in clean_tags:
            clean_tags.append(t)
    if not clean_tags:
        return True

    base = f"https://{domain}/admin/api/{SHOPIFY_ADMIN_API_VERSION}/orders/{order_id}.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        # 1. Read the order's current tags so we can merge instead of overwrite.
        r = requests.get(base, headers=headers, params={"fields": "id,tags"}, timeout=10)
        if r.status_code != 200:
            log.error(
                "Order %s: failed to read tags (HTTP %s): %s",
                order_id, r.status_code, r.text[:300],
            )
            return False
        existing = (r.json().get("order") or {}).get("tags") or ""
        existing_list = [t.strip() for t in existing.split(",") if t.strip()]
        existing_lower = {t.lower() for t in existing_list}

        # 2. Append any new tags that aren't already on the order.
        new_tags = [t for t in clean_tags if t not in existing_lower]
        if not new_tags:
            log.info("Order %s: tags already present (%s)", order_id, clean_tags)
            return True

        merged = ", ".join(existing_list + new_tags)

        # 3. Write the merged tag list back.
        payload = {"order": {"id": int(order_id), "tags": merged}}
        r2 = requests.put(base, headers=headers, json=payload, timeout=10)
        if r2.status_code not in (200, 201):
            log.error(
                "Order %s: failed to set tags (HTTP %s): %s",
                order_id, r2.status_code, r2.text[:300],
            )
            return False

        log.info("Order %s: tagged with %s", order_id, new_tags)
        return True

    except Exception:
        log.exception("Order %s: tagging failed unexpectedly", order_id)
        return False


def set_order_metafield(
    order_id: str,
    namespace: str,
    key: str,
    value,
    value_type: str = "json",
) -> bool:
    """Upsert a metafield on a Shopify order via the Admin REST API.

    Tries POST first (creates new). If Shopify returns 422 (already
    exists), GETs the order's metafields, finds the matching
    namespace/key, and PUTs to its id. Both paths are idempotent — safe
    to call repeatedly with the same value.

    For value_type='json', a non-string value is JSON-encoded
    automatically. Strings are passed through unchanged.

    Returns True on success, False on any failure (logged — never raises).
    """
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()
    if not domain or not token:
        log.warning(
            "Order %s: skipping metafield write — SHOPIFY_SHOP_DOMAIN or SHOPIFY_ADMIN_API_TOKEN not set",
            order_id,
        )
        return False

    if value_type == "json" and not isinstance(value, str):
        value_str = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    else:
        value_str = str(value)

    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    base = f"https://{domain}/admin/api/{SHOPIFY_ADMIN_API_VERSION}"
    create_url = f"{base}/orders/{order_id}/metafields.json"
    payload = {
        "metafield": {
            "namespace": namespace,
            "key": key,
            "value": value_str,
            "type": value_type,
        }
    }

    try:
        r = requests.post(create_url, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            log.info("Order %s: metafield %s.%s created", order_id, namespace, key)
            return True

        if r.status_code != 422:
            log.error(
                "Order %s: metafield create failed (HTTP %s): %s",
                order_id, r.status_code, r.text[:300],
            )
            return False

        # 422 — likely a duplicate. Find existing metafield id and PUT.
        list_url = f"{base}/orders/{order_id}/metafields.json"
        rl = requests.get(
            list_url, headers=headers,
            params={"namespace": namespace, "key": key},
            timeout=15,
        )
        if rl.status_code != 200:
            log.error(
                "Order %s: metafield lookup failed (HTTP %s): %s",
                order_id, rl.status_code, rl.text[:300],
            )
            return False
        existing = (rl.json().get("metafields") or [])
        match = next(
            (m for m in existing if m.get("namespace") == namespace and m.get("key") == key),
            None,
        )
        if not match:
            log.error("Order %s: 422 on create but no existing metafield found", order_id)
            return False
        mf_id = match["id"]
        update_url = f"{base}/metafields/{mf_id}.json"
        ru = requests.put(
            update_url, headers=headers,
            json={"metafield": {"id": mf_id, "value": value_str, "type": value_type}},
            timeout=15,
        )
        if ru.status_code in (200, 201):
            log.info("Order %s: metafield %s.%s updated (id=%s)", order_id, namespace, key, mf_id)
            return True
        log.error(
            "Order %s: metafield update failed (HTTP %s): %s",
            order_id, ru.status_code, ru.text[:300],
        )
        return False
    except Exception:
        log.exception("Order %s: metafield write failed unexpectedly", order_id)
        return False


def tags_from_order_items(items: list[dict]) -> list[str]:
    """Build a tag list from parsed order items.

    Emits:
        style:<style>     — one per unique portrait style
        product:<type>    — one per unique product_type
    """
    styles = {str(i.get("style") or "").strip() for i in items if i.get("style")}
    products = {str(i.get("product_type") or "").strip() for i in items if i.get("product_type")}
    tags = []
    tags += [f"style:{s}" for s in sorted(styles) if s]
    tags += [f"product:{p}" for p in sorted(products) if p]
    return tags


# ---------------------------------------------------------------------------
# Printful API client
# ---------------------------------------------------------------------------

PRINTFUL_BASE = "https://api.printful.com"


def _printful_headers() -> dict:
    api_key = os.environ.get("PRINTFUL_API_KEY", "")
    if not api_key:
        raise RuntimeError("PRINTFUL_API_KEY not set")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def create_printful_order(
    shopify_order_id: str,
    recipient: dict,
    items: list[dict],
) -> dict:
    """
    Create a draft order in Printful with one or more items.

    A Shopify order maps to ONE Printful order regardless of line item
    count — sending separate Printful POSTs with the same external_id
    triggers duplicate-key collisions and silently drops items.

    Args:
        shopify_order_id: Shopify order ID — used as Printful external_id.
        recipient: Shipping address dict.
        items: List of dicts, each with keys:
                 variant_id (int) — Printful catalog variant
                 quantity (int)
                 print_file_url (str) — public URL of the print-ready PNG

    Returns:
        Printful API response as dict.
    """
    if not items:
        raise ValueError("create_printful_order requires at least one item")

    pf_items = [
        {
            "variant_id": it["variant_id"],
            "quantity": int(it.get("quantity", 1) or 1),
            "files": [{"type": "default", "url": it["print_file_url"]}],
        }
        for it in items
    ]

    payload = {
        "external_id": shopify_order_id,
        "recipient": {
            "name": recipient.get("name", ""),
            "address1": recipient.get("address1", ""),
            "address2": recipient.get("address2", ""),
            "city": recipient.get("city", ""),
            "state_code": recipient.get("province_code", ""),
            "country_code": recipient.get("country_code", "CA"),
            "zip": recipient.get("zip", ""),
            "phone": recipient.get("phone", ""),
            "email": recipient.get("email", ""),
        },
        "items": pf_items,
    }

    log.info(
        "Creating Printful order for Shopify #%s (%d item%s)",
        shopify_order_id, len(pf_items), "" if len(pf_items) == 1 else "s",
    )

    resp = requests.post(
        f"{PRINTFUL_BASE}/orders",
        headers=_printful_headers(),
        json=payload,
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        log.error("Printful error %d: %s", resp.status_code, resp.text)
        resp.raise_for_status()

    result = resp.json()
    log.info("Printful order created: %s", result.get("result", {}).get("id"))
    return result


# ---------------------------------------------------------------------------
# Helpers — used by app._process_fulfillment to assemble a multi-item order
# ---------------------------------------------------------------------------

def build_product_key(product_type: str, size: str) -> str:
    """Translate Shopify line-item (product_type, size) → PRINT_SIZES key."""
    if product_type.endswith("-framed"):
        base = product_type[:-len("-framed")]
        return f"{base}-{size}-framed"
    return f"{product_type}-{size}"


# ---------------------------------------------------------------------------
# Parse Shopify order webhook into fulfillment tasks
# ---------------------------------------------------------------------------

import re

# Match W×H / WxH / 16"x20" / 16″×20″ with optional inch marks + whitespace.
_SIZE_RE = re.compile(
    r"(\d{1,2})\s*[\"'\u2032\u2033]?\s*[x\u00d7]\s*(\d{1,2})",
    re.IGNORECASE,
)


def _extract_size_from_variant(li: dict) -> Optional[str]:
    """Parse W×H size out of a Shopify line item's variant/title fields."""
    for field in ("variant_title", "name", "title", "sku"):
        text = li.get(field) or ""
        m = _SIZE_RE.search(text)
        if m:
            return f"{m.group(1)}x{m.group(2)}"
    return None


def parse_order_items(order: dict) -> list[dict]:
    """
    Extract portrait line items from a Shopify order payload.

    Returns a list of dicts, each with:
        pet_name, style, job_id, preview_url, product_type, size
    """
    items = []

    for li in order.get("line_items", []):
        props = {}
        for p in li.get("properties", []):
            props[p["name"]] = p["value"]

        # Frontend writes `_Job ID` (underscored = hidden prop in Shopify UI);
        # accept the unprefixed form for legacy/test data too.
        job_id = props.get("_Job ID") or props.get("Job ID")
        if not job_id:
            continue

        # Derive product_type. Frontend doesn't send "Product type" today —
        # default to canvas, then switch to canvas-framed when _Frame says so
        # OR the line item's product handle is framed-canvas.
        base_product = props.get("Product type", props.get("_Product type", "canvas"))
        frame_pref = props.get("_Frame", props.get("Frame", "")) or ""
        handle = (li.get("handle") or "").lower()
        # Magnet upsell is a separate Shopify product (handle=magnet). It
        # carries the portrait properties copied from the source line item.
        is_magnet = handle == "magnet" or base_product == "magnet"
        is_framed = (
            not is_magnet
            and (
                "framed" in frame_pref.lower()
                or handle == "framed-canvas"
            )
        )
        if is_magnet:
            product_type = "magnet"
        else:
            product_type = (
                f"{base_product}-framed"
                if is_framed and not base_product.endswith("-framed")
                else base_product
            )

        # Size precedence: explicit prop → variant_title parse → warn + default.
        if is_magnet:
            # Customer can pick 3x3 / 4x4 / 6x6 from the cart upsell.
            # Parse from variant_title so each size routes to the correct
            # Printful variant. Default to 4x4 if parsing fails.
            size = (
                props.get("Size")
                or props.get("_Size")
                or _extract_size_from_variant(li)
                or "4x4"
            )
        else:
            size = props.get("Size") or props.get("_Size")
            if not size:
                size = _extract_size_from_variant(li)
            if not size:
                log.warning(
                    "Order line %s: could not determine size from props or variant; defaulting to 16x20",
                    li.get("id"),
                )
                size = "16x20"

        # Prefer the hi-res print file URL the cart captured over the preview.
        preview_url = (
            props.get("_Print File URL")
            or props.get("Print File URL")
            or props.get("Preview URL")
            or props.get("_Portrait URL")
            or ""
        )

        # Per-aspect print URLs. The 1×1 derivatives ship with square
        # canvas variants (12×12, 16×16) so the print fills the canvas
        # without aspect-mismatch loss; tall variants and posters
        # continue to use the 4:5 master. Older orders placed before
        # per-aspect files existed only have the 4:5 fields — those
        # fall back transparently in _resolve_print_url() below.
        items.append({
            "pet_name": props.get("Pet Name", props.get("Pet name", "Pet")),
            "style": props.get("Style", props.get("_Style", "soft-watercolour")),
            "font_size": props.get("Font Size", props.get("_Font Size", "medium")),
            "show_name": props.get("_Show Name", "Yes"),
            "job_id": job_id,
            "preview_url": preview_url,
            "print_file_url": props.get("_Print File URL", ""),
            "no_name_url": props.get("_No Name URL", ""),
            "print_file_url_3x4": props.get("_Print File URL 3x4", ""),
            "no_name_url_3x4": props.get("_No Name URL 3x4", ""),
            "print_file_url_1x1": props.get("_Print File URL 1x1", ""),
            "no_name_url_1x1": props.get("_No Name URL 1x1", ""),
            "product_type": product_type,
            "size": size,
            "quantity": li.get("quantity", 1),
        })

    return items
