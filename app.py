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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger(__name__)

# Background executor for async fulfillment (webhook responds immediately)
_fulfillment_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fulfill")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

# CORS — allows the Shopify theme dev server (localhost:9292) and any origin
# to call /generate. Safe for local test use.
@app.after_request
def _add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/generate", methods=["OPTIONS"])
@app.route("/preview/<filename>", methods=["OPTIONS"])
@app.route("/download/<filename>", methods=["OPTIONS"])
def _options_preflight(**_):
    return "", 204

UPLOAD_DIR = Path("uploads")

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    has_key = "GEMINI_API_KEY" in os.environ
    key_preview = os.environ.get("GEMINI_API_KEY", "NOT SET")[:8] + "..." if has_key else "NOT SET"
    return jsonify(status="ok", gemini_key=key_preview, env_count=len(os.environ))


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

    try:
        raw_path, comp_path = generate(str(upload_path), pet_name, style)
        return jsonify(
            raw=f"/preview/{raw_path.name}",
            composited=f"/preview/{comp_path.name}",
            download=f"/download/{comp_path.name}",
            filename=comp_path.name,
        )
    except Exception as exc:
        return jsonify(error=str(exc)), 500
    finally:
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
