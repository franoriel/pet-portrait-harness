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
    # Canvas
    "canvas-12x12": (3600, 3600),
    "canvas-18x18": (5400, 5400),
    # Poster
    "poster-12x16": (3600, 4800),
    "poster-18x24": (5400, 7200),
    # Mug — Printful 11oz white mug print area
    "mug-11oz": (4500, 1876),
}

# Aspect ratios for each product type (width:height)
PRODUCT_RATIOS: dict[str, tuple[int, int]] = {
    "canvas-12x12": (1, 1),
    "canvas-18x18": (1, 1),
    "poster-12x16": (3, 4),
    "poster-18x24": (3, 4),
    "mug-11oz": (12, 5),  # Mug wrap: wide and short
}

# TODO: Replace with real Printful variant IDs from their catalog API
# Find these at: GET https://api.printful.com/products
PRINTFUL_VARIANT_MAP: dict[str, int] = {
    "canvas-12x12": 1,    # placeholder
    "canvas-18x18": 2,    # placeholder
    "poster-12x16": 3,    # placeholder
    "poster-18x24": 4,    # placeholder
    "mug-11oz": 5,        # placeholder
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
) -> Path:
    """
    Generate a print-ready hi-res portrait sized for the target product.

    Args:
        photo_path: Path to the customer's original photo.
        pet_name: Pet's name for compositing.
        style: Style ID (e.g. 'soft-watercolour' from React → mapped to 'watercolor').
        product_key: Product-size key (e.g. 'canvas-18x18').
        style_vars: Optional watercolor-specific variables.

    Returns:
        Path to the saved print-ready PNG.
    """
    target_w, target_h = PRINT_SIZES[product_key]
    ratio = PRODUCT_RATIOS[product_key]

    log.info(
        "Generating print file: %s %s '%s' → %dx%d",
        style, product_key, pet_name, target_w, target_h,
    )

    # Map React style IDs to harness style keys
    style_key = _map_style_id(style)

    # Generate via Gemini
    raw_bytes = call_gemini(photo_path, style_key, style_vars)
    img = Image.open(BytesIO(raw_bytes))
    img.load()

    # Crop to product aspect ratio
    img = crop_to_ratio(img, ratio)

    # Apply style-specific post-processing (skip watercolor's own crop since we
    # already cropped to the product ratio above)
    if style_key != "watercolor":
        img = POST_PROCESS.get(style_key, lambda x: x)(img)

    # Upscale to print dimensions using LANCZOS
    if img.width < target_w or img.height < target_h:
        img = img.resize((target_w, target_h), Image.LANCZOS)

    # Composite pet name (skip for mugs — name goes on a separate text area)
    if not product_key.startswith("mug"):
        img = composite_name(img, pet_name)

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
    filename = f"print_{product_key}_{safe_name}_{uuid.uuid4().hex[:8]}.png"
    out_path = OUTPUT_DIR / filename
    img.save(out_path, "PNG")

    log.info("Print file saved: %s (%dx%d)", out_path, img.width, img.height)
    return out_path


def _map_style_id(react_style_id: str) -> str:
    """Map the React widget's style ID to the harness PROMPTS key."""
    mapping = {
        "soft-watercolour": "watercolor",
        "minimal-line-art": "minimal",
        "modern-oil-paint": "classic",       # fallback until dedicated prompts exist
        "neon-pop-art": "classic",
        "renaissance-royalty": "classic",
        "cozy-film-grain": "classic",
        "rainbow-bridge": "watercolor",
        "bold-graphic-poster": "minimal",
        "aura-gradient": "watercolor",
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
    Upload a print-ready file to cloud storage and return a public URL.

    TODO: Implement with your storage provider (S3, Cloudflare R2, GCS, etc.).
    For now, returns a local file URL for development.
    """
    bucket_url = os.environ.get("UPLOAD_BUCKET_URL", "")

    if not bucket_url:
        # Dev mode: serve from local Flask
        log.warning("UPLOAD_BUCKET_URL not set — using local preview URL")
        return f"/preview/{local_path.name}"

    # TODO: Replace with actual upload logic. Example for S3:
    #
    # import boto3
    # s3 = boto3.client('s3')
    # key = f"print-files/{local_path.name}"
    # s3.upload_file(str(local_path), BUCKET_NAME, key,
    #                ExtraArgs={'ContentType': 'image/png'})
    # return f"{bucket_url}/{key}"

    log.info("Would upload %s to %s", local_path.name, bucket_url)
    return f"{bucket_url}/{local_path.name}"


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
) -> dict:
    """
    End-to-end fulfillment for a single line item.

    1. Generate hi-res print file
    2. Upload to cloud storage
    3. Create Printful order

    Returns:
        Printful API response dict.
    """
    product_key = f"{product_type}-{size}"

    if product_key not in PRINT_SIZES:
        raise ValueError(f"Unknown product configuration: {product_key}")

    # Step 1: Generate
    print_path = generate_print_file(
        photo_path=photo_path,
        pet_name=pet_name,
        style=style,
        product_key=product_key,
        style_vars=style_vars,
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
            "pet_name": props.get("Pet name", "Pet"),
            "style": props.get("Style", "soft-watercolour"),
            "job_id": job_id,
            "preview_url": props.get("Preview URL", ""),
            "product_type": props.get("Product type", "poster"),
            "size": props.get("Size", "12x16"),
            "quantity": li.get("quantity", 1),
        })

    return items
