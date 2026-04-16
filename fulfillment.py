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
# ---------------------------------------------------------------------------

PRINT_SIZES: dict[str, tuple[int, int]] = {
    # Canvas — matches Shopify storefront sizes (pixels at 300 DPI)
    "canvas-10x10": (3000, 3000),
    "canvas-10x20": (3000, 6000),
    "canvas-12x18": (3600, 5400),
    "canvas-12x24": (3600, 7200),
    # Poster — Printful 12x16 standard
    "poster-default": (3600, 4800),
    # Mug — Printful 11oz white mug print area
    "mug-11oz": (4500, 1876),
}

# Aspect ratios for each product type (width:height)
PRODUCT_RATIOS: dict[str, tuple[int, int]] = {
    "canvas-10x10": (1, 1),
    "canvas-10x20": (1, 2),
    "canvas-12x18": (2, 3),
    "canvas-12x24": (1, 2),
    "poster-default": (3, 4),
    "mug-11oz": (12, 5),  # Mug wrap: wide and short
}

# TODO: Replace with real Printful variant IDs from their catalog API
# Find these at: GET https://api.printful.com/products
PRINTFUL_VARIANT_MAP: dict[str, int] = {
    "canvas-10x10": 1,    # placeholder
    "canvas-10x20": 2,    # placeholder
    "canvas-12x18": 3,    # placeholder
    "canvas-12x24": 4,    # placeholder
    "poster-default": 5,  # placeholder
    "mug-11oz": 6,        # placeholder
}


# ---------------------------------------------------------------------------
# Shopify webhook verification
# ---------------------------------------------------------------------------

def verify_shopify_webhook(data: bytes, hmac_header: str) -> bool:
    """Verify the Shopify webhook HMAC-SHA256 signature."""
    secret = os.environ.get("SHOPIFY_WEBHOOK_SECRET", "")
    if not secret:
        log.warning("SHOPIFY_WEBHOOK_SECRET not set — skipping verification")
        return True

    digest = hmac.new(
        secret.encode("utf-8"),
        data,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(digest, hmac_header)


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

    target_w, target_h = PRINT_SIZES[product_key]
    ratio = PRODUCT_RATIOS[product_key]

    log.info(
        "Generating print file: %s %s '%s' → %dx%d",
        style, product_key, pet_name, target_w, target_h,
    )

    # ── Preferred path: upscale from R2 composited PNG ───────────
    r2_path = None
    if composited_r2_key:
        r2_path = download_from_r2(composited_r2_key)

    if r2_path and r2_path.exists():
        log.info("Using R2 composited image for upscale (no Gemini re-generation)")
        img = Image.open(r2_path)
        img.load()

        # Crop to product aspect ratio — top-weighted to keep pet's face
        # Use center gravity to preserve both the pet (top) and the name (bottom)
        img = crop_to_ratio(img, ratio, gravity="center")

        # Upscale to print dimensions
        if img.width < target_w or img.height < target_h:
            img = img.resize((target_w, target_h), Image.LANCZOS)

        # Sharpen to recover detail lost in upscaling
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        # Composite pet name AFTER cropping to correct ratio (skip for mugs)
        style_key = _map_style_id(style)
        if not product_key.startswith("mug"):
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

        # Crop to product aspect ratio — top-weighted to keep pet's face
        # Use center gravity to preserve both the pet (top) and the name (bottom)
        img = crop_to_ratio(img, ratio, gravity="center")

        # Apply style-specific post-processing
        if style_key != "watercolor":
            img = POST_PROCESS.get(style_key, lambda x: x)(img)

        # Upscale to print dimensions
        if img.width < target_w or img.height < target_h:
            img = img.resize((target_w, target_h), Image.LANCZOS)

        # Composite pet name AFTER cropping (skip for mugs)
        if not product_key.startswith("mug"):
            img = composite_name(img, pet_name, style=style_key, font_size_key=font_size)

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
        "modern-oil-paint": "modern-oil-paint",
        "neon-pop-art": "neon-pop-art",
        "renaissance-royalty": "renaissance-royalty",
        "cozy-film-grain": "cozy-film-grain",
        "rainbow-bridge": "rainbow-bridge",
        "bold-graphic-poster": "bold-graphic-poster",
        "aura-gradient": "aura-gradient",
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
    product_key: str,
    print_file_url: str,
    quantity: int = 1,
) -> dict:
    """
    Create a draft order in Printful.

    Args:
        shopify_order_id: The Shopify order ID for cross-referencing.
        recipient: Shipping address dict with name, address1, city, etc.
        product_key: Product-size key (e.g. 'canvas-18x18').
        print_file_url: Public URL of the print-ready image.
        quantity: Number of units.

    Returns:
        Printful API response as dict.
    """
    variant_id = PRINTFUL_VARIANT_MAP.get(product_key)
    if not variant_id:
        raise ValueError(f"No Printful variant for '{product_key}'")

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
        "items": [
            {
                "variant_id": variant_id,
                "quantity": quantity,
                "files": [
                    {
                        "type": "default",
                        "url": print_file_url,
                    }
                ],
            }
        ],
    }

    log.info("Creating Printful order for Shopify #%s", shopify_order_id)

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
# Full fulfillment pipeline
# ---------------------------------------------------------------------------

def fulfill_order_item(
    photo_path: Path,
    pet_name: str,
    style: str,
    product_type: str,
    size: str,
    shopify_order_id: str,
    recipient: dict,
    style_vars: Optional[dict] = None,
    composited_r2_key: Optional[str] = None,
) -> dict:
    """
    End-to-end fulfillment for a single line item.

    1. Generate hi-res print file (upscale from R2, or Gemini fallback)
    2. Upload to cloud storage
    3. Create Printful order

    Returns:
        Printful API response dict.
    """
    product_key = f"{product_type}-{size}"

    if product_key not in PRINT_SIZES:
        raise ValueError(f"Unknown product configuration: {product_key}")

    # Step 1: Generate (prefers R2 upscale over Gemini re-generation)
    print_path = generate_print_file(
        photo_path=photo_path,
        pet_name=pet_name,
        style=style,
        product_key=product_key,
        style_vars=style_vars,
        composited_r2_key=composited_r2_key,
    )

    # Step 2: Upload
    print_url = upload_print_file(print_path)

    # Step 3: Send to Printful
    result = create_printful_order(
        shopify_order_id=shopify_order_id,
        recipient=recipient,
        product_key=product_key,
        print_file_url=print_url,
    )

    return result


# ---------------------------------------------------------------------------
# Parse Shopify order webhook into fulfillment tasks
# ---------------------------------------------------------------------------

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

        # Skip line items that aren't portrait orders (no Job ID property)
        job_id = props.get("Job ID")
        if not job_id:
            continue

        items.append({
            "pet_name": props.get("Pet name", props.get("Pet Name", "Pet")),
            "style": props.get("Style", props.get("_Style", "soft-watercolour")),
            "font_size": props.get("Font Size", props.get("_Font Size", "medium")),
            "job_id": job_id,
            "preview_url": props.get("Preview URL", props.get("_Portrait URL", "")),
            "print_file_url": props.get("_Print File URL", ""),  # hi-res 300 DPI PNG
            "product_type": props.get("Product type", "poster"),
            "size": props.get("Size", "12x16"),
            "quantity": li.get("quantity", 1),
        })

    return items
