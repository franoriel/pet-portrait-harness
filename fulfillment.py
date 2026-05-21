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
    PROMPTS,
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
    """Average RGB of the four corner regions — used as a final fallback
    fill colour. Prefer _edge_replicate_pad for actual wrap bleed since
    it correctly handles split / multi-colour backgrounds (e.g. modern
    shape art's half-yellow / half-orange canvas).
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


def _edge_replicate_pad(
    front: Image.Image,
    target_w: int,
    target_h: int,
) -> Image.Image:
    """Pad `front` to (target_w, target_h) by replicating its outermost
    edge pixels into the surrounding bleed band. Each side extends the
    adjacent column/row, so a horizontally-split background (e.g. yellow
    left / orange right) cleanly extends both colours into the wrap
    rather than being filled with a single averaged tone."""
    rgb = front.convert("RGB") if front.mode != "RGB" else front
    w, h = rgb.size
    pad_l = (target_w - w) // 2
    pad_t = (target_h - h) // 2
    pad_r = target_w - w - pad_l
    pad_b = target_h - h - pad_t

    out = Image.new("RGB", (target_w, target_h))
    out.paste(rgb, (pad_l, pad_t))

    # Left / right bleed — replicate the 1px edge column out to the bleed width.
    if pad_l > 0:
        left_col = rgb.crop((0, 0, 1, h)).resize((pad_l, h), Image.NEAREST)
        out.paste(left_col, (0, pad_t))
    if pad_r > 0:
        right_col = rgb.crop((w - 1, 0, w, h)).resize((pad_r, h), Image.NEAREST)
        out.paste(right_col, (pad_l + w, pad_t))
    # Top / bottom bleed — replicate the (now full-width) 1px row out vertically,
    # so the corners inherit the colour from whichever side they sit on.
    if pad_t > 0:
        top_row = out.crop((0, pad_t, target_w, pad_t + 1)).resize((target_w, pad_t), Image.NEAREST)
        out.paste(top_row, (0, 0))
    if pad_b > 0:
        bot_row = out.crop((0, pad_t + h - 1, target_w, pad_t + h)).resize((target_w, pad_b), Image.NEAREST)
        out.paste(bot_row, (0, pad_t + h))
    return out


def wrap_print_file_with_bleed(
    front_face: Image.Image,
    product_key: str,
    style: Optional[str] = None,
) -> Image.Image:
    """Place a front-face-correct composition into a Printful canvas
    print file with the required wrap bleed on every side: 3" for
    gallery-wrap (canvas-*), 1.5" for framed canvas (canvas-*-framed).

    The customer-visible art lives in the inner FRONT_FACE_SIZES rectangle.
    The surrounding bleed band continues the front face's bg outward.

    Two strategies depending on style:

    - **bold-graphic-poster** (and any future flat-bg style): SAMPLE the
      median colour of the front face's left + right edge columns and
      PAINT the bleed with that exact colour, split at 50% width.
      Edge replication on these styles can propagate ~1-2 RGB shifts
      from the LANCZOS resize / UnsharpMask path, which produces a
      visible "darker frame" between the bleed and the front face.
      Sampling a robust median + painting flat eliminates that artifact.

    - **everything else** (watercolor, charcoal, aura, etc.): edge-
      replicate the outermost column outward. Right call for organic
      backgrounds where the bg has natural texture / wash variation
      that should continue smoothly into the bleed.

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

    # Bold Graphic Poster: paint bleed with canonical split colours
    # sampled from the front face's left + right edge medians.
    if style == "bold-graphic-poster":
        return _split_bg_pad(front_face, file_w, file_h)

    return _edge_replicate_pad(front_face, file_w, file_h)


def _split_bg_pad(
    front: Image.Image,
    target_w: int,
    target_h: int,
) -> Image.Image:
    """Pad `front` to (target_w, target_h) by painting the bleed with
    the median colour of `front`'s left + right edge columns. Used for
    Bold Graphic Poster's 2-tone vertical split: left half of bleed
    gets left-edge median, right half gets right-edge median.

    This sidesteps edge-replication artifacts where 1-2 RGB shifts
    introduced by the LANCZOS resize + UnsharpMask path produce a
    visible "darker frame" between the bleed and the front face.
    Sampling the median (not mean — robust to anti-aliased outliers)
    and painting flat keeps the bleed exactly the canonical bg colour."""
    rgb = front.convert("RGB") if front.mode != "RGB" else front
    w, h = rgb.size

    def _median_color(strip):
        # Median per channel — robust to outliers (anti-aliased edge
        # pixels at the seam between bg_left and bg_right halves).
        pixels = list(strip.getdata())
        if not pixels:
            return (0, 0, 0)
        n = len(pixels)
        rs = sorted(p[0] for p in pixels)
        gs = sorted(p[1] for p in pixels)
        bs = sorted(p[2] for p in pixels)
        return (rs[n // 2], gs[n // 2], bs[n // 2])

    left_color = _median_color(rgb.crop((0, 0, 1, h)))
    right_color = _median_color(rgb.crop((w - 1, 0, w, h)))

    pad_l = (target_w - w) // 2
    pad_t = (target_h - h) // 2

    # Build target: left half = left_color, right half = right_color.
    # The seam is at the centre of the TARGET canvas, which lines up
    # with the seam in the front face when pasted centred.
    out = Image.new("RGB", (target_w, target_h), left_color)
    mid = target_w // 2
    right_half = Image.new("RGB", (target_w - mid, target_h), right_color)
    out.paste(right_half, (mid, 0))
    out.paste(rgb, (pad_l, pad_t))
    return out


# ---------------------------------------------------------------------------
# Hi-res print file generation
# ---------------------------------------------------------------------------

def _center_noname_watercolor(img: Image.Image) -> Image.Image:
    """Centre-shift a no-name watercolor source before print scaling.

    The watercolor AI reserves a ~22% empty name-safe band at the top.
    Cropping 11% (half the band) from the top and padding 11% white at
    the bottom preserves the 4:5 aspect ratio and centres the pet in the
    print area — matching what the Step 3/4 mockup previews show.
    Only applied to no-name orders; named prints have the band composited.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    crop_px = max(1, int(round(h * 0.11)))
    cropped = img.crop((0, crop_px, w, h))
    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    canvas.paste(cropped, (0, 0))
    return canvas


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

    Single path: download the composited PNG from R2 (the same source the
    customer's preview was derived from), LANCZOS-upscale to FRONT_FACE_SIZES,
    wrap with bleed, save at 300 DPI. Never re-runs Gemini and never
    re-composites the name — the R2 PNG already has it baked in by /add-name.

    Raises RuntimeError if the R2 source is missing. The previous Gemini-regen
    fallback was removed: it fed the named composited PNG back to Gemini as a
    "photo," which produced double-name ghosting on the print. There is no
    clean fallback source today (the customer's original uploaded photo URL
    is not persisted to a cart property), so a missing R2 file must surface
    as a fulfillment error rather than a silently corrupted print.

    Args:
        photo_path: Vestigial; not used. The R2 PNG is the only source.
        pet_name: Pet's name — used only for the output filename.
        style: Style ID (e.g. 'soft-watercolour' from React → mapped internally).
        product_key: Product-size key (e.g. 'canvas-12x24').
        style_vars: Vestigial; not used.
        composited_r2_key: R2 key of the composited PNG from initial generation.
        font_size: Vestigial; not used (name was already composited at /add-name).
        show_name: Vestigial; not used (R2 PNG already reflects the choice).

    Returns:
        Path to the saved print-ready PNG.

    Raises:
        RuntimeError: composited_r2_key is None or the file is unreachable.
    """
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

        # No-name watercolor: shift image up 11% to centre the pet and remove
        # the empty name-safe band before scaling to print dimensions.
        if show_name.strip().lower() == "no" and _map_style_id(style) == "watercolor":
            img = _center_noname_watercolor(img)
            log.info("           applied no-name watercolor centering shift")

        # Crop the source to the FRONT-FACE aspect (1:1 / 3:4 / 4:5).
        # The wrap padding is added later by wrap_print_file_with_bleed().
        # Skip the crop entirely when the source already matches the target
        # aspect (per-aspect URLs picked by _pick_source_url usually hit
        # this path) — avoids a redundant pass that could subtly shift
        # composition if the source dims are unexpected. "Already matches"
        # = within 1% tolerance to allow for minor sub-pixel rounding.
        target_ratio = ratio[0] / ratio[1]
        source_ratio = img.width / img.height
        if abs(source_ratio - target_ratio) > 0.01:
            img = crop_to_ratio(img, ratio, gravity="center")

        # Scale source to cover the full print-file dimensions so actual
        # artwork fills every pixel of the wrap — no edge-pixel drag.
        #
        # Strategy: scale to whichever axis the source is proportionally
        # smaller in (so it over-fills the other axis), then center-crop
        # the overflow. The net result is that the front face maps to
        # FRONT_FACE_SIZES exactly and the bleed band shows real image
        # content instead of a 1-pixel stretched edge.
        #
        # For magnets PRINT_SIZES == FRONT_FACE_SIZES so this is a
        # straight resize with no crop.
        src_aspect = img.width / img.height
        file_aspect = file_w / file_h
        if src_aspect >= file_aspect:
            # Source is wider-or-equal → scale to fill height
            scaled_h = file_h
            scaled_w = max(file_w, int(round(file_h * src_aspect)))
        else:
            # Source is narrower → scale to fill width
            scaled_w = file_w
            scaled_h = max(file_h, int(round(file_w / src_aspect)))
        needs_upscale = img.width < scaled_w or img.height < scaled_h
        _ = needs_upscale  # noqa: F841 — kept for future use
        img = img.resize((scaled_w, scaled_h), Image.LANCZOS)
        crop_x = (scaled_w - file_w) // 2
        crop_y = (scaled_h - file_h) // 2
        if crop_x or crop_y:
            img = img.crop((crop_x, crop_y, crop_x + file_w, crop_y + file_h))

        # The R2 source is already-composited from preview generation —
        # it has the customer's name baked in (or is the no-name variant
        # when show_name=No). We never re-composite here — that would
        # burn the name on top of itself, and any cart-side rename would
        # produce a double-name overlap (JEWEL + WILDER → JEWILDER, or
        # the JJ ÇK Modern Shape Art ghost we hit when the old Gemini
        # fallback fed the named PNG back to Gemini).
        style_key = _map_style_id(style)

        # Clean up downloaded file
        r2_path.unlink(missing_ok=True)

    else:
        # NO FALLBACK. The composited PNG that was the source of truth
        # for the customer's preview is the ONLY clean source for the
        # print. Without it we cannot produce a print that matches what
        # the customer saw, and the watermarked WebP preview is unfit
        # for print use directly (baked-in watermark, ≤800 px lossy
        # source, no DPI metadata, no wrap bleed).
        #
        # The previous fallback re-ran Gemini against `photo_path`, but
        # `photo_path` is the named composited PNG (parse_order_items
        # writes `_Print File URL` into preview_url). Feeding that to
        # Gemini left the existing pet-name text visible in the
        # regenerated image; composite_name then added a second name
        # on top, producing visibly ghost-doubled letters on the
        # printed canvas — the bug surfaced on a Modern Shape Art
        # canvas where "JACK" rendered as "JJ ÇK". The customer's
        # original uploaded photo URL is not persisted to a cart
        # property, so there is no clean source to fall back to.
        #
        # Fail loudly instead. The order surfaces in
        # `_process_fulfillment`'s exception handler (logged as
        # "fulfillment failed"); the order is NOT submitted to
        # Printful; ops investigates the missing R2 file and retries.
        # Better a paused order than a corrupted print shipped silently.
        raise RuntimeError(
            f"Print file generation failed for {product_key}: composited "
            f"R2 key {composited_r2_key!r} is missing or unreachable. "
            f"Refusing to regenerate via Gemini — that path produces "
            f"double-name ghosting when fed the named composited PNG. "
            f"Investigate R2 (key/bucket/prefix) and retry the order."
        )

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

def generate_bleed_file(print_path: Path, product_key: str, style: Optional[str] = None) -> Path:
    """Generate a bleed variant of the print file with 30% extra canvas on every
    side (measured against the front-face dimension). Intended for manual
    editing before re-uploading to Printful — never sent automatically.

    30% per side is a uniform scale factor: total = front × 1.6 on each axis,
    which preserves the original aspect ratio for all canvas sizes. The bleed
    area contains real image content (same cover-scale approach as the print
    file) rather than edge-pixel drag — we extract the front face from the
    wrapped print file and scale it up to the bleed dimensions via LANCZOS.

    Output DPI matches the source (300 DPI).
    """
    file_w, file_h = PRINT_SIZES[product_key]
    front_w, front_h = FRONT_FACE_SIZES[product_key]

    img = Image.open(print_path)
    img.load()

    # Extract the front face from the centre of the wrapped print file.
    # The print file is already at file_w × file_h with the front face
    # centred; bleed bands on all sides contain real artwork content.
    offset_x = (file_w - front_w) // 2
    offset_y = (file_h - front_h) // 2
    front = img.crop((offset_x, offset_y, offset_x + front_w, offset_y + front_h))
    img.close()

    # 30% per side → total = front × 1.6 on each axis.
    # Because the scale is uniform, the bleed-file aspect ratio equals the
    # front-face aspect ratio — no crop needed, straight LANCZOS resize.
    total_w = int(round(front_w * 1.6))
    total_h = int(round(front_h * 1.6))
    out = front.convert("RGB").resize((total_w, total_h), Image.LANCZOS)

    bleed_path = print_path.with_name(print_path.stem + "_bleed30.png")
    out.save(bleed_path, "PNG", dpi=(300, 300))
    log.info(
        "Bleed file saved: %s (%dx%d, 30%% per side)", bleed_path, total_w, total_h
    )
    return bleed_path


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
        gift              — if any item has _Gift=Yes
        memorial          — if any item has _Memorial=Yes
    """
    styles = {str(i.get("style") or "").strip() for i in items if i.get("style")}
    products = {str(i.get("product_type") or "").strip() for i in items if i.get("product_type")}
    tags = []
    tags += [f"style:{s}" for s in sorted(styles) if s]
    tags += [f"product:{p}" for p in sorted(products) if p]
    if any(str(i.get("gift") or "").strip().lower() == "yes" for i in items):
        tags.append("gift")
    if any(str(i.get("memorial") or "").strip().lower() == "yes" for i in items):
        tags.append("memorial")
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
    gift_message: str = "",
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
        gift_message: Optional packing slip message (gift or memorial note).

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

    if gift_message:
        payload["packing_slip"] = {"message": gift_message[:300]}

    log.info(
        "Creating Printful order for Shopify #%s (%d item%s%s)",
        shopify_order_id, len(pf_items), "" if len(pf_items) == 1 else "s",
        ", with packing slip" if gift_message else "",
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
            # Gift / memorial — order-level metadata; same on all line items.
            # gift_message is included in the Printful packing slip when non-empty.
            "gift": props.get("_Gift", "No"),
            "memorial": props.get("_Memorial", "No"),
            "gift_message": (props.get("_Gift Message") or "").strip(),
        })

    return items
