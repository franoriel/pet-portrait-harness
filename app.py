#!/usr/bin/env python3
"""
Flask web UI for the pet portrait generator.

    python app.py          → http://localhost:5000
"""

import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from generate import ALLOWED_SUFFIXES, OUTPUT_DIR, PROMPTS, generate
from fulfillment import (
    fulfill_order_item,
    parse_order_items,
    verify_shopify_webhook,
)
from mockups import generate_mockups
from storage import upload_portrait

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger(__name__)

# Background executor for async fulfillment (webhook responds immediately)
_fulfillment_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fulfill")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

# ── Request queue tracking ────────────────────────────────────────────────────
# Tracks active generation requests so we can return queue position to clients.
import threading as _thr
_active_generations = 0
_active_lock = _thr.Lock()
_peak_generations = 0

# CORS — allows the Shopify storefront and any origin to call API endpoints.
@app.after_request
def _add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    # Cache preflight for 1 hour to reduce OPTIONS roundtrips
    response.headers["Access-Control-Max-Age"] = "3600"
    return response

@app.route("/generate", methods=["OPTIONS"])
@app.route("/preview/<filename>", methods=["OPTIONS"])
@app.route("/download/<filename>", methods=["OPTIONS"])
def _options_preflight(**_):
    return "", 204

UPLOAD_DIR = Path("uploads")

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Output file cleanup — remove files older than 24 hours every 30 minutes
# ---------------------------------------------------------------------------
import time as _time, threading as _threading

def _cleanup_old_outputs():
    while True:
        _time.sleep(1800)  # 30 min
        try:
            cutoff = _time.time() - 86400  # 24h
            for f in OUTPUT_DIR.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
            for f in UPLOAD_DIR.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception:
            pass

_cleanup_thread = _threading.Thread(target=_cleanup_old_outputs, daemon=True)
_cleanup_thread.start()


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    has_key = "GEMINI_API_KEY" in os.environ
    key_preview = os.environ.get("GEMINI_API_KEY", "NOT SET")[:8] + "..." if has_key else "NOT SET"
    from generate import MAX_CONCURRENT_GENERATIONS
    return jsonify(
        status="ok",
        gemini_key=key_preview,
        active_generations=_active_generations,
        peak_generations=_peak_generations,
        max_concurrent=MAX_CONCURRENT_GENERATIONS,
    )


@app.route("/debug/catalog/<int:product_id>")
def debug_catalog(product_id):
    """Temporary debug endpoint to inspect Printful catalog variants and techniques."""
    import requests as req
    from mockups import _headers, PRINTFUL_API_V1
    try:
        resp = req.get(f"{PRINTFUL_API_V1}/products/{product_id}", headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json().get("result", {})
        product = data.get("product", {})
        variants = {v.get("size", ""): v.get("id") for v in data.get("variants", []) if v.get("size")}
        return jsonify(
            product_id=product_id,
            type=product.get("type", ""),
            techniques=product.get("techniques", []),
            variants=variants,
        )
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route("/generate", methods=["POST"])
def generate_route():
    # ── Validate inputs ──────────────────────────────────────────────────────
    if "photo" not in request.files:
        return jsonify(error="No photo file received."), 400

    file     = request.files["photo"]
    pet_name = request.form.get("pet_name", "").strip()
    style    = request.form.get("style", "classic")

    if not pet_name:
        return jsonify(error="Pet name is required."), 400
    if style not in PROMPTS:
        return jsonify(error=f"Unknown style: {style}"), 400

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify(error=f"Unsupported file type '{suffix}'. Use JPG, PNG, or WebP."), 400

    # ── Save upload temporarily ───────────────────────────────────────────────
    upload_path = UPLOAD_DIR / f"{uuid.uuid4()}{suffix}"
    file.save(upload_path)

    global _active_generations, _peak_generations
    with _active_lock:
        _active_generations += 1
        if _active_generations > _peak_generations:
            _peak_generations = _active_generations

    try:
        raw_path, comp_path = generate(str(upload_path), pet_name, style)

        # Upload to R2 for permanent CDN URLs (falls back gracefully if not configured)
        raw_cdn = upload_portrait(raw_path)
        comp_cdn = upload_portrait(comp_path)

        return jsonify(
            raw=raw_cdn or f"/preview/{raw_path.name}",
            composited=comp_cdn or f"/preview/{comp_path.name}",
            download=f"/download/{comp_path.name}",
            filename=comp_path.name,
            cdn=bool(comp_cdn),  # tells frontend whether URLs are permanent
        )
    except RuntimeError as exc:
        if "BUSY" in str(exc):
            return jsonify(
                error="Server is at capacity. Please try again in a few seconds.",
                retry_after=5,
                queue_depth=_active_generations,
            ), 503
        log.exception("Generation failed for style=%s", style)
        return jsonify(error=str(exc)), 500
    except Exception as exc:
        log.exception("Generation failed for style=%s", style)
        return jsonify(error=str(exc)), 500
    finally:
        with _active_lock:
            _active_generations -= 1
        upload_path.unlink(missing_ok=True)


@app.route("/preview/<filename>")
def preview(filename):
    """Serve images from output/ for in-browser display."""
    path = OUTPUT_DIR / Path(filename).name          # prevent path traversal
    try:
        return send_file(path, mimetype="image/png")
    except FileNotFoundError:
        return "Not found", 404


@app.route("/download/<filename>")
def download(filename):
    """Serve images as attachment (triggers browser download)."""
    path = OUTPUT_DIR / Path(filename).name
    try:
        return send_file(path, mimetype="image/png", as_attachment=True,
                         download_name=filename)
    except FileNotFoundError:
        return "Not found", 404


# ---------------------------------------------------------------------------
# Printful mockup generation
# ---------------------------------------------------------------------------

@app.route("/debug/mockup-poll/<int:task_id>")
def debug_mockup_poll(task_id):
    """Debug: poll a mockup task and return raw Printful response."""
    import requests as req
    from mockups import _headers, PRINTFUL_API
    resp = req.get(f"{PRINTFUL_API}/mockup-tasks", params={"id": task_id}, headers=_headers(), timeout=15)
    return jsonify(status_code=resp.status_code, raw=resp.json() if resp.ok else resp.text[:1000])


@app.route("/debug/mockup-raw", methods=["POST"])
def debug_mockup_raw():
    """Debug: create a single mockup task and return the RAW Printful response."""
    import requests as req
    from mockups import _headers, PRINTFUL_API
    data = request.get_json(silent=True) or {}
    image_url = data.get("image_url", "")
    variant_id = data.get("variant_id", 19296)
    catalog_product_id = data.get("catalog_product_id", 3)

    payload = {
        "format": "jpg",
        "products": [{
            "source": "catalog",
            "catalog_product_id": catalog_product_id,
            "catalog_variant_ids": [variant_id],
            "placements": [{
                "placement": "default",
                "technique": "DIGITAL",
                "layers": [{"type": "file", "url": image_url}],
            }],
        }],
    }
    resp = req.post(f"{PRINTFUL_API}/mockup-tasks", json=payload, headers=_headers(), timeout=30)
    return jsonify(
        status_code=resp.status_code,
        raw_response=resp.json() if resp.ok else resp.text[:1000],
        payload_sent=payload,
    )


@app.route("/mockups", methods=["POST", "OPTIONS"])
def mockups():
    """Generate Printful product mockups with the user's portrait."""
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    image_filename = data.get("image_filename", "")
    product_type = data.get("product_type", "canvas")

    if not image_filename:
        return jsonify(error="image_filename required"), 400

    # Verify file exists
    path = OUTPUT_DIR / Path(image_filename).name
    if not path.exists():
        return jsonify(error="Image not found"), 404

    try:
        results = generate_mockups(path.name, product_type)
        return jsonify(mockups=results)
    except Exception as e:
        log.exception("Mockup generation failed")
        return jsonify(error=str(e)), 500


# ---------------------------------------------------------------------------
# Shopify webhook — orders/create
# ---------------------------------------------------------------------------

@app.route("/webhooks/shopify/order-created", methods=["POST"])
def webhook_order_created():
    """
    Receive a Shopify orders/create webhook, verify it, and kick off
    fulfillment in a background thread (so we respond within Shopify's
    5-second timeout).

    Setup:
        1. In Shopify Admin → Settings → Notifications → Webhooks,
           create a webhook for "Order creation" pointing to:
           https://<your-domain>/webhooks/shopify/order-created
        2. Set SHOPIFY_WEBHOOK_SECRET env var to the webhook signing secret.
    """
    body = request.get_data()
    hmac_header = request.headers.get("X-Shopify-Hmac-Sha256", "")

    if not verify_shopify_webhook(body, hmac_header):
        log.warning("Webhook HMAC verification failed")
        return jsonify(error="Unauthorized"), 401

    try:
        order = request.get_json(force=True)
    except Exception:
        return jsonify(error="Invalid JSON"), 400

    order_id = str(order.get("id", "unknown"))
    items = parse_order_items(order)

    if not items:
        log.info("Order #%s has no portrait line items — skipping", order_id)
        return jsonify(status="skipped", reason="no portrait items"), 200

    log.info("Order #%s — %d portrait item(s), dispatching fulfillment", order_id, len(items))

    # Dispatch fulfillment in background so we respond to Shopify quickly
    shipping = order.get("shipping_address", {})
    recipient = {
        "name": f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip(),
        "address1": shipping.get("address1", ""),
        "address2": shipping.get("address2", ""),
        "city": shipping.get("city", ""),
        "province_code": shipping.get("province_code", ""),
        "country_code": shipping.get("country_code", "CA"),
        "zip": shipping.get("zip", ""),
        "phone": shipping.get("phone", ""),
        "email": order.get("email", ""),
    }

    for item in items:
        _fulfillment_pool.submit(
            _process_fulfillment, order_id, item, recipient,
        )

    return jsonify(status="accepted", items=len(items)), 200


def _process_fulfillment(order_id: str, item: dict, recipient: dict):
    """
    Background task: download the customer's original photo (from the
    preview that was stored during generation), generate the hi-res print
    file, and send to Printful.
    """
    try:
        # The preview URL points to our /preview/ endpoint which serves from
        # output/. The original upload is deleted after preview generation,
        # so we re-download the preview and use it as the source photo.
        #
        # TODO: In production, store the original upload in cloud storage
        # during the /generate call and reference it here by Job ID.
        preview_url = item.get("preview_url", "")
        if preview_url.startswith("/"):
            # Local path — resolve to output dir
            photo_path = OUTPUT_DIR / Path(preview_url).name
        else:
            # Remote URL — download to temp file
            import tempfile
            import requests as req
            resp = req.get(preview_url, timeout=30)
            resp.raise_for_status()
            suffix = ".png"
            tmp = Path(tempfile.mktemp(suffix=suffix, dir="uploads"))
            tmp.write_bytes(resp.content)
            photo_path = tmp

        if not photo_path.exists():
            log.error("Order #%s — photo not found: %s", order_id, photo_path)
            return

        result = fulfill_order_item(
            photo_path=photo_path,
            pet_name=item["pet_name"],
            style=item["style"],
            product_type=item["product_type"],
            size=item["size"],
            shopify_order_id=order_id,
            recipient=recipient,
        )

        log.info(
            "Order #%s — fulfillment complete: Printful order %s",
            order_id,
            result.get("result", {}).get("id", "?"),
        )

    except Exception:
        log.exception("Order #%s — fulfillment failed for item %s", order_id, item)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
