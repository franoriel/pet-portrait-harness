"""
Printful Mockup Generator — creates product mockups with the user's portrait.

Flow:
    1. Receive generated portrait filename + product type
    2. Build a public URL for the image (served via /preview/ on Railway)
    3. POST to Printful /v2/mockup-tasks with catalog IDs + image URL
    4. Poll until task completes
    5. Return mockup image URLs mapped to variant sizes

Environment variables:
    PRINTFUL_API_KEY  — Printful API bearer token
    RAILWAY_PUBLIC_URL — public base URL of this app (e.g. https://web-production-a392e.up.railway.app)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

PRINTFUL_API = "https://api.printful.com/v2"
PRINTFUL_API_V1 = "https://api.printful.com"


def _api_key() -> str:
    key = os.environ.get("PRINTFUL_API_KEY", "")
    if not key:
        raise RuntimeError("PRINTFUL_API_KEY not set")
    return key


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _public_base() -> str:
    return os.environ.get(
        "RAILWAY_PUBLIC_URL",
        "https://web-production-a392e.up.railway.app"
    )


# ---------------------------------------------------------------------------
# Catalog lookup — fetch variant IDs from Printful on first call
# ---------------------------------------------------------------------------

# Cache: { product_id: { "size_label": variant_id, ... } }
_variant_cache: dict[int, dict[str, int]] = {}


def get_catalog_variants(catalog_product_id: int) -> dict[str, int]:
    """Fetch variants for a catalog product. Returns { 'size_label': variant_id }."""
    if catalog_product_id in _variant_cache:
        return _variant_cache[catalog_product_id]

    # Use v1 API for catalog (more stable)
    url = f"{PRINTFUL_API_V1}/products/{catalog_product_id}"
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()

    variants = {}
    for v in data.get("result", {}).get("variants", []):
        size = v.get("size", "")
        vid = v.get("id")
        if size and vid:
            variants[size] = vid

    _variant_cache[catalog_product_id] = variants
    log.info(f"Cached {len(variants)} variants for product {catalog_product_id}: {list(variants.keys())}")
    return variants


# ---------------------------------------------------------------------------
# Product type → Printful catalog mapping
# ---------------------------------------------------------------------------

# Printful catalog product IDs (from their public catalog)
CATALOG_PRODUCTS = {
    "canvas": 3,    # Canvas print (stretched)
    "poster": 1,    # Enhanced matte poster
}

# Printful uses unicode: 10″×10″ (U+2033 double prime, U+00D7 multiplication sign)
# Map our variant labels to Printful's exact size labels
VARIANT_SIZE_MAP = {
    "canvas": {
        "10x10": '10\u2033\u00d710\u2033',
        "10x20": '10\u2033\u00d720\u2033',
        "12x18": '12\u2033\u00d718\u2033',
        "12x24": '12\u2033\u00d724\u2033',
    },
    "poster": {
        "default": None,  # single variant poster, use first available
    },
}

# Direct hardcoded variant IDs as fallback (from Printful catalog)
VARIANT_ID_FALLBACK = {
    "canvas": {
        "10x10": 19296,
        "10x20": 19297,
        "12x18": 19299,
        "12x24": 19300,
    },
    "poster": {
        "default": 3876,  # 12″×18″ poster
    },
}


def _resolve_variant_ids(product_type: str) -> dict[str, int]:
    """Get Printful catalog variant IDs for our product sizes."""
    catalog_id = CATALOG_PRODUCTS.get(product_type)
    if not catalog_id:
        raise ValueError(f"Unknown product type: {product_type}")

    size_map = VARIANT_SIZE_MAP.get(product_type, {})
    fallback = VARIANT_ID_FALLBACK.get(product_type, {})

    # Try API lookup first
    try:
        all_variants = get_catalog_variants(catalog_id)
    except Exception as e:
        log.warning(f"Catalog lookup failed, using fallback IDs: {e}")
        all_variants = {}

    resolved = {}
    for our_label, printful_label in size_map.items():
        if printful_label is None:
            # Use first available variant or fallback
            if all_variants:
                first_key = next(iter(all_variants))
                resolved[our_label] = all_variants[first_key]
            elif our_label in fallback:
                resolved[our_label] = fallback[our_label]
        elif printful_label in all_variants:
            resolved[our_label] = all_variants[printful_label]
        elif our_label in fallback:
            resolved[our_label] = fallback[our_label]

    log.info(f"Resolved variant IDs for {product_type}: {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# Mockup task creation & polling
# ---------------------------------------------------------------------------

def create_mockup_task(
    image_url: str,
    catalog_product_id: int,
    catalog_variant_ids: list[int],
    format: str = "jpg",
) -> str:
    """Create a Printful mockup generation task. Returns task ID."""
    payload = {
        "format": format,
        "products": [
            {
                "source": "catalog",
                "catalog_product_id": catalog_product_id,
                "catalog_variant_ids": catalog_variant_ids,
                "placements": [
                    {
                        "placement": "default",
                        "technique": "DIGITAL",
                        "layers": [
                            {
                                "type": "file",
                                "url": image_url,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    log.info(f"Mockup task payload: {payload}")

    resp = requests.post(
        f"{PRINTFUL_API}/mockup-tasks",
        json=payload,
        headers=_headers(),
        timeout=30,
    )
    if not resp.ok:
        log.error(f"Mockup task creation failed ({resp.status_code}): {resp.text}")
        raise RuntimeError(f"Printful API {resp.status_code}: {resp.text[:500]}")
    data = resp.json()

    task_id = data.get("data", {}).get("id") or data.get("id")
    if not task_id:
        # Try v1-style response
        task_id = data.get("result", {}).get("task_key")

    if not task_id:
        raise RuntimeError(f"No task ID in mockup response: {data}")

    log.info(f"Created mockup task: {task_id}")
    return str(task_id)


def poll_mockup_result(task_id: str, timeout: int = 60, interval: int = 3) -> dict:
    """Poll for mockup task completion. Returns the result data."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        resp = requests.get(
            f"{PRINTFUL_API}/mockup-tasks/{task_id}",
            headers=_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", resp.json())

        status = data.get("status", "")
        if status == "completed":
            log.info(f"Mockup task {task_id} completed")
            return data
        elif status == "failed":
            reasons = data.get("failure_reasons", [])
            raise RuntimeError(f"Mockup task failed: {reasons}")

        time.sleep(interval)

    raise TimeoutError(f"Mockup task {task_id} timed out after {timeout}s")


def extract_mockup_urls(result: dict) -> list[dict]:
    """Extract mockup image URLs from completed task result.
    Returns: [{ 'variant_id': int, 'url': str, 'placement': str }, ...]
    """
    mockups = []
    for variant_mockup in result.get("catalog_variant_mockups", []):
        variant_id = variant_mockup.get("catalog_variant_id")
        for mockup in variant_mockup.get("mockups", []):
            url = mockup.get("url", "")
            placement = mockup.get("placement", "default")
            if url:
                mockups.append({
                    "variant_id": variant_id,
                    "url": url,
                    "placement": placement,
                })
            # Also grab extra mockups (different angles)
            for extra in mockup.get("extra_mockups", []):
                if extra.get("url"):
                    mockups.append({
                        "variant_id": variant_id,
                        "url": extra["url"],
                        "placement": extra.get("placement", "extra"),
                    })
    return mockups


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def generate_mockups(image_filename: str, product_type: str) -> list[dict]:
    """
    Generate Printful mockups for a portrait image.

    Args:
        image_filename: filename in the output/ directory (e.g. 'photo_watercolor_raw.png')
        product_type: 'canvas' or 'poster'

    Returns:
        List of { 'variant': '10x10', 'url': 'https://...', 'placement': 'default' }
    """
    catalog_id = CATALOG_PRODUCTS.get(product_type)
    if not catalog_id:
        raise ValueError(f"Unknown product type: {product_type}")

    # Build public URL for the image
    image_url = f"{_public_base()}/preview/{image_filename}"

    # Resolve variant IDs
    variant_map = _resolve_variant_ids(product_type)
    if not variant_map:
        raise RuntimeError(f"No variant IDs resolved for {product_type}")

    # Generate mockups one variant at a time to avoid style compatibility issues
    id_to_label = {vid: label for label, vid in variant_map.items()}
    final = []

    for label, variant_id in variant_map.items():
        try:
            task_id = create_mockup_task(image_url, catalog_id, [variant_id])
            result = poll_mockup_result(task_id)
            raw_mockups = extract_mockup_urls(result)

            for m in raw_mockups:
                final.append({
                    "variant": label,
                    "url": m["url"],
                    "placement": m["placement"],
                })
                break  # Just take the first mockup per variant
        except Exception as e:
            log.warning(f"Mockup failed for {label} (variant {variant_id}): {e}")
            continue

    log.info(f"Generated {len(final)} mockups for {product_type}")
    return final
