#!/usr/bin/env python3
"""
Flask web UI for the pet portrait generator.

    python app.py          → http://localhost:5000
"""

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Optional

import requests as _req
from flask import Flask, jsonify, render_template, request, send_file

from generate import ALLOWED_SUFFIXES, OUTPUT_DIR, PROMPTS, generate, generate_with_name_on_demand, verify_image_is_pet
from fulfillment import (
    fulfill_order_item,
    parse_order_items,
    verify_shopify_webhook,
)
from mockups import generate_mockups
from storage import upload_portrait
from jobs import create_job, get_job, dequeue_job, update_job, queue_depth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger(__name__)

# Background executor for async fulfillment (webhook responds immediately)
_fulfillment_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fulfill")

# ─────────────────────────────────────────────────────────────
# Rate limiting (per-IP, in-memory sliding window)
# ─────────────────────────────────────────────────────────────
RATE_LIMIT_HOURLY = int(os.environ.get("RATE_LIMIT_HOURLY", "5"))
RATE_LIMIT_DAILY  = int(os.environ.get("RATE_LIMIT_DAILY", "15"))

# Per-endpoint shorter-window limits (prevent burst abuse / brute force)
# { endpoint_name: (max_requests, window_seconds) }
ENDPOINT_LIMITS = {
    "generate":      (RATE_LIMIT_HOURLY, 3600),
    "add_name":      (RATE_LIMIT_HOURLY, 3600),
    "mockups":       (20, 600),     # 20 per 10 min
    "status":        (300, 600),    # 300 polls per 10 min (≈1 every 2s)
    "preview":       (200, 600),
    "download":      (30, 600),
    "debug":         (5, 900),      # 5 per 15 min for admin endpoints
    "webhook":       (30, 60),      # Shopify webhook — 30/min
}

_rate_buckets: dict[str, deque] = defaultdict(deque)
_rate_lock = Lock()


def _client_ip() -> str:
    """Extract client IP, respecting common proxy headers."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


def check_rate_limit(ip: str, endpoint: str = "generate") -> tuple[bool, str]:
    """Sliding window rate check per IP + endpoint.
    Returns (allowed, reason_if_blocked)."""
    max_req, window = ENDPOINT_LIMITS.get(endpoint, (RATE_LIMIT_HOURLY, 3600))
    now = time.time()
    cutoff = now - window
    # Daily cap for expensive endpoints (generate, add_name)
    day_cutoff = now - 86400
    key = f"{ip}:{endpoint}"

    with _rate_lock:
        bucket = _rate_buckets[key]
        # Prune old entries
        while bucket and bucket[0] < min(cutoff, day_cutoff):
            bucket.popleft()

        recent = sum(1 for t in bucket if t >= cutoff)
        daily_count = len(bucket)

        if recent >= max_req:
            mins = max(1, window // 60)
            return False, f"Too many requests. Please try again in about {mins} minute(s)."
        if endpoint in ("generate", "add_name") and daily_count >= RATE_LIMIT_DAILY:
            return False, f"You've reached the daily limit of {RATE_LIMIT_DAILY} portraits. Please try again tomorrow."

        bucket.append(now)
        return True, ""


# ─────────────────────────────────────────────────────────────
# Input sanitization helpers
# ─────────────────────────────────────────────────────────────
import re as _re

# Allow letters, numbers, spaces, apostrophes, hyphens, periods.
# Max 30 chars. Rejects quotes, braces, brackets, backslashes, backticks,
# newlines, and anything else that could break out of a prompt string.
_PET_NAME_PATTERN = _re.compile(r"^[A-Za-z0-9\s\-\u2019'.]{1,30}$", _re.UNICODE)

def sanitize_pet_name(raw: str) -> tuple[bool, str]:
    """Returns (is_valid, clean_value). Strips + validates against whitelist."""
    if not raw:
        return False, ""
    name = raw.strip()
    if not name:
        return False, ""
    if len(name) > 30:
        return False, ""
    if not _PET_NAME_PATTERN.match(name):
        return False, ""
    return True, name


# MIME type whitelist for photo uploads
_ALLOWED_MIME_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_MIME_MAGIC_BYTES = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp",  # needs second check for "WEBP" at offset 8
}

def validate_image_file(file_storage) -> tuple[bool, str]:
    """Validate uploaded file by reading magic bytes (not just extension).
    Returns (is_valid, reason_if_invalid)."""
    # Read first 16 bytes
    head = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    if not head:
        return False, "Empty file."

    detected = None
    if head[:3] == b"\xff\xd8\xff":
        detected = "image/jpeg"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        detected = "image/png"
    elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        detected = "image/webp"

    if detected not in _ALLOWED_MIME_TYPES:
        return False, "File must be a real JPG, PNG, or WebP image."

    # Also check declared MIME matches detected (defense in depth)
    declared = (file_storage.mimetype or "").lower()
    if declared and declared not in _ALLOWED_MIME_TYPES:
        return False, f"Content-Type {declared} not allowed."

    return True, ""


def validate_url(url: str, allowed_hosts: Optional[list] = None) -> bool:
    """Validate a URL is https and from an allowed host (prevents SSRF)."""
    if not url:
        return False
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("https",):
        return False
    host = parsed.hostname or ""
    # Block local/private ranges
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    # If an explicit allowlist is provided, enforce it
    if allowed_hosts is not None:
        if not any(host == h or host.endswith("." + h) for h in allowed_hosts):
            return False
    return True


# Hosts our app is allowed to fetch from in /add-name
_SAFE_IMAGE_HOSTS = [h.strip() for h in os.environ.get(
    "SAFE_IMAGE_HOSTS",
    "r2.dev,r2.cloudflarestorage.com,railway.app,up.railway.app"
).split(",") if h.strip()]


# ─────────────────────────────────────────────────────────────
# Cloudflare Turnstile bot protection (free, privacy-friendly)
# ─────────────────────────────────────────────────────────────
TURNSTILE_SECRET = os.environ.get("TURNSTILE_SECRET_KEY", "")
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(token: str, ip: str) -> bool:
    """Verify a Cloudflare Turnstile token. Returns True if valid human.

    If TURNSTILE_SECRET_KEY isn't configured, verification is skipped
    (dev mode). In production, set the env var to enforce bot protection.
    """
    if not TURNSTILE_SECRET:
        return True  # not configured — skip in dev
    if not token:
        return False
    try:
        resp = _req.post(TURNSTILE_VERIFY_URL, data={
            "secret": TURNSTILE_SECRET,
            "response": token,
            "remoteip": ip,
        }, timeout=10)
        data = resp.json()
        return bool(data.get("success"))
    except Exception as exc:
        log.warning("Turnstile verification failed: %s", exc)
        return False  # fail closed — reject on error

app = Flask(__name__)
# Hard cap on request size — prevents oversized uploads
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB (allows 20MB photo + overhead)

# ── Request queue tracking ────────────────────────────────────────────────────
import threading as _thr
_active_generations = 0
_active_lock = _thr.Lock()
_peak_generations = 0

# ─────────────────────────────────────────────────────────────
# CORS — restricted to trusted origins only
# ─────────────────────────────────────────────────────────────
_ALLOWED_ORIGINS = set(
    (os.environ.get("ALLOWED_ORIGINS")
     or "https://petprintables.ca,https://www.petprintables.ca,https://petprintables.myshopify.com"
    ).split(",")
)

@app.after_request
def _add_cors(response):
    origin = request.headers.get("Origin", "")
    # Exact-match whitelist (no wildcard)
    if origin in _ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Token"
        response.headers["Access-Control-Max-Age"] = "3600"
    # Basic security headers on every response
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


# Handle 413 Request Entity Too Large cleanly
@app.errorhandler(413)
def _request_too_large(e):
    return jsonify(error="File too large. Max upload size is 20 MB."), 413

@app.route("/generate", methods=["OPTIONS"])
@app.route("/status/<job_id>", methods=["OPTIONS"])
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

def _require_debug_token() -> bool:
    """Gate debug/admin endpoints behind a secret token from env.
    Returns True if the request is authorized."""
    token = os.environ.get("DEBUG_ADMIN_TOKEN", "")
    if not token:
        return False  # not configured = locked down
    provided = request.headers.get("X-Admin-Token", "") or request.args.get("admin_token", "")
    return provided == token


@app.route("/health")
def health():
    """Public health check — returns minimal status only.
    Sensitive details require X-Admin-Token header."""
    # Basic public response — no secret info
    status = {"status": "ok"}
    # Admin-only fields
    if _require_debug_token():
        from generate import MAX_CONCURRENT_GENERATIONS
        status.update({
            "gemini_key_configured": "GEMINI_API_KEY" in os.environ,
            "active_generations": _active_generations,
            "peak_generations": _peak_generations,
            "max_concurrent": MAX_CONCURRENT_GENERATIONS,
            "queued_jobs": queue_depth(),
        })
    return jsonify(status)


@app.route("/debug/catalog/<int:product_id>")
def debug_catalog(product_id):
    """Debug endpoint — requires X-Admin-Token header."""
    if not _require_debug_token():
        return jsonify(error="Forbidden"), 403
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
    except Exception:
        log.exception("debug_catalog failed")
        return jsonify(error="Debug call failed"), 500


@app.route("/generate", methods=["POST"])
def generate_route():
    """Accept a portrait request, enqueue it, and return a job ID immediately."""
    # ── Bot protection: Cloudflare Turnstile ─────────────────────────────────
    ip = _client_ip()
    turnstile_token = request.form.get("turnstile_token", "").strip()
    if not verify_turnstile(turnstile_token, ip):
        return jsonify(
            error="Please complete the verification challenge and try again.",
            code="turnstile_failed",
        ), 403

    # ── Rate limiting ───────────────────────────────────────────────────────
    allowed, reason = check_rate_limit(ip, "generate")
    if not allowed:
        return jsonify(error=reason, code="rate_limited"), 429

    # ── Validate inputs ──────────────────────────────────────────────────────
    if "photo" not in request.files:
        return jsonify(error="No photo file received."), 400

    file = request.files["photo"]

    # Sanitize pet_name — whitelist chars + length
    ok_name, pet_name = sanitize_pet_name(request.form.get("pet_name", ""))
    if not ok_name:
        return jsonify(
            error="Pet name must be 1–30 characters using only letters, numbers, spaces, hyphens, periods, or apostrophes."
        ), 400

    # Whitelist style + map React IDs (soft-watercolour → watercolor)
    style_raw = request.form.get("style", "classic")
    if not isinstance(style_raw, str) or len(style_raw) > 40:
        return jsonify(error="Invalid style."), 400
    from fulfillment import _map_style_id
    style = _map_style_id(style_raw)
    if style not in PROMPTS:
        return jsonify(error="Invalid style."), 400

    # Validate file by magic bytes (not just extension)
    ok_file, file_reason = validate_image_file(file)
    if not ok_file:
        return jsonify(error=file_reason), 400

    # Also double-check extension whitelist
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        return jsonify(error="Unsupported file type. Use JPG, PNG, or WebP."), 400

    # ── Save upload (persists until worker processes it) ──────────────────────
    upload_path = UPLOAD_DIR / f"{uuid.uuid4()}{suffix}"
    file.save(upload_path)

    # ── Verify the image is actually a pet (cheap classifier call) ───────────
    # Rejects: humans, logos, cartoons, screenshots, NSFW, scenery, objects.
    # Costs ~$0.0001 per call vs $0.04 for wasted generation.
    try:
        is_pet, detail = verify_image_is_pet(upload_path)
        if not is_pet:
            # Clean up the upload since we won't use it
            try: upload_path.unlink(missing_ok=True)
            except: pass
            return jsonify(
                error="We can only create portraits of pets. "
                      "Please upload a clear photo of your dog, cat, or other pet.",
                detail=detail,
            ), 400
        log.info("Pet verified: %s (%s)", detail, upload_path.name)
    except Exception as exc:
        log.warning("Pet verification errored, rejecting: %s", exc)
        try: upload_path.unlink(missing_ok=True)
        except: pass
        return jsonify(
            error="Could not verify your photo. Please try a different image of your pet."
        ), 400

    # ── Enqueue job and return immediately ────────────────────────────────────
    job = create_job(pet_name=pet_name, style=style, upload_path=str(upload_path))
    depth = queue_depth()

    return jsonify(
        job_id=job["job_id"],
        status="queued",
        position=depth,
    ), 202


@app.route("/status/<job_id>")
def job_status(job_id):
    """Poll endpoint — returns job status, queue position, or result URLs."""
    # Rate limit status polls — prevents job ID enumeration brute force
    ip = _client_ip()
    allowed, reason = check_rate_limit(ip, "status")
    if not allowed:
        return jsonify(error=reason, code="rate_limited"), 429

    # Validate job_id format (hex, max 64 chars)
    if not _re.match(r"^[a-zA-Z0-9_\-]{1,64}$", job_id or ""):
        return jsonify(error="Invalid job id"), 400

    job = get_job(job_id)
    if not job:
        return jsonify(error="Job not found"), 404

    status = job.get("status", "queued")

    if status == "queued":
        return jsonify(
            job_id=job_id,
            status="queued",
            position=int(job.get("position", 0)),
            queue_depth=queue_depth(),
        )
    elif status == "processing":
        return jsonify(
            job_id=job_id,
            status="processing",
        )
    elif status == "complete":
        return jsonify(
            job_id=job_id,
            status="complete",
            raw=job.get("raw", ""),
            composited=job.get("composited", ""),
            composited_png_cdn=job.get("composited_png_cdn", ""),
            download=job.get("download", ""),
            filename=job.get("filename", ""),
            cdn=job.get("cdn", False),
        )
    else:  # failed
        return jsonify(
            job_id=job_id,
            status="failed",
            error=job.get("error", "Generation failed"),
        )


@app.route("/preview/<filename>")
def preview(filename):
    """Serve images from output/ for in-browser display."""
    ip = _client_ip()
    allowed, _ = check_rate_limit(ip, "preview")
    if not allowed:
        return "Too many requests", 429

    # Path traversal guard — only allow simple filenames
    if not _re.match(r"^[A-Za-z0-9_\-]+\.(png|webp|jpg|jpeg)$", filename or ""):
        return "Bad filename", 400

    safe_name = Path(filename).name
    path = OUTPUT_DIR / safe_name
    mime = "image/webp" if safe_name.endswith(".webp") else "image/png"
    try:
        return send_file(path, mimetype=mime)
    except FileNotFoundError:
        return "Not found", 404


@app.route("/download/<filename>")
def download(filename):
    """Serve images as attachment (triggers browser download)."""
    ip = _client_ip()
    allowed, _ = check_rate_limit(ip, "download")
    if not allowed:
        return "Too many requests", 429

    if not _re.match(r"^[A-Za-z0-9_\-]+\.(png|webp|jpg|jpeg)$", filename or ""):
        return "Bad filename", 400

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
    """Debug: poll a mockup task. Requires X-Admin-Token."""
    if not _require_debug_token():
        return jsonify(error="Forbidden"), 403
    import requests as req
    from mockups import _headers, PRINTFUL_API
    resp = req.get(f"{PRINTFUL_API}/mockup-tasks", params={"id": task_id}, headers=_headers(), timeout=15)
    return jsonify(status_code=resp.status_code, raw=resp.json() if resp.ok else resp.text[:1000])


@app.route("/debug/mockup-raw", methods=["POST"])
def debug_mockup_raw():
    """Debug: create a mockup task. Requires X-Admin-Token."""
    if not _require_debug_token():
        return jsonify(error="Forbidden"), 403
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


@app.route("/add-name", methods=["POST", "OPTIONS"])
def add_name():
    """Generate the with-name version of an existing portrait on-demand.
    Called at add-to-cart time to save Gemini cost during preview.

    Body: { image_url: str, pet_name: str, style: str }
    Returns: { composited_url, composited_png_cdn, filename }
    """
    if request.method == "OPTIONS":
        return "", 204

    ip = _client_ip()
    allowed, reason = check_rate_limit(ip, "add_name")
    if not allowed:
        return jsonify(error=reason, code="rate_limited"), 429

    # Cap JSON body size explicitly (defense in depth vs MAX_CONTENT_LENGTH)
    if request.content_length and request.content_length > 4096:
        return jsonify(error="Payload too large."), 413

    data = request.get_json(silent=True) or {}
    image_url = (data.get("image_url") or "").strip()
    style_raw = (data.get("style") or "watercolor").strip()

    # Validate pet_name
    ok_name, pet_name = sanitize_pet_name(data.get("pet_name", ""))
    if not ok_name:
        return jsonify(error="Invalid pet name."), 400

    # Map React style ID (soft-watercolour) → PROMPTS key (watercolor)
    from fulfillment import _map_style_id
    style = _map_style_id(style_raw) if len(style_raw) <= 40 else None
    if not style or style not in PROMPTS:
        return jsonify(error="Invalid style."), 400

    # Validate image_url — must be HTTPS, from allowed hosts (prevents SSRF)
    if not validate_url(image_url, allowed_hosts=_SAFE_IMAGE_HOSTS):
        return jsonify(error="Invalid image URL."), 400

    try:
        import requests as _req
        # Stream + size cap (prevent memory exhaustion from huge remote files)
        resp = _req.get(image_url, timeout=30, stream=True)
        if not resp.ok:
            return jsonify(error=f"Could not fetch image: {resp.status_code}"), 400
        MAX_SIZE = 10 * 1024 * 1024  # 10 MB
        chunks = []; size = 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > MAX_SIZE:
                return jsonify(error="Image too large."), 413
            chunks.append(chunk)
        no_name_bytes = b"".join(chunks)

        comp_path, web_path = generate_with_name_on_demand(
            no_name_image_bytes=no_name_bytes,
            pet_name=pet_name,
            style=style,
        )

        # Upload both to R2 for fast CDN delivery
        comp_cdn = upload_portrait(comp_path)
        web_cdn = upload_portrait(web_path)

        return jsonify(
            composited=web_cdn or f"/preview/{web_path.name}",
            composited_png_cdn=comp_cdn or f"/preview/{comp_path.name}",
            filename=comp_path.name,
        )
    except RuntimeError as e:
        if str(e) == "BUSY":
            return jsonify(error="Servers are busy, please try again"), 503
        log.exception("add-name failed")
        return jsonify(error="Something went wrong. Please try again."), 500
    except Exception:
        log.exception("add-name failed")
        return jsonify(error="Something went wrong. Please try again."), 500


@app.route("/mockups", methods=["POST", "OPTIONS"])
def mockups():
    """Generate Printful product mockups with the user's portrait."""
    if request.method == "OPTIONS":
        return "", 204

    ip = _client_ip()
    allowed, reason = check_rate_limit(ip, "mockups")
    if not allowed:
        return jsonify(error=reason, code="rate_limited"), 429

    if request.content_length and request.content_length > 8192:
        return jsonify(error="Payload too large."), 413

    data = request.get_json(silent=True) or {}
    image_filename = (data.get("image_filename") or "").strip()
    image_url = (data.get("image_url") or "").strip()
    product_type = (data.get("product_type") or "canvas").strip()
    variants = data.get("variants")

    # Validate product_type is a simple handle
    if product_type not in ("canvas", "poster", "mug"):
        return jsonify(error="Invalid product type."), 400

    # Path traversal guard on filename
    if image_filename and ("/" in image_filename or "\\" in image_filename or ".." in image_filename):
        return jsonify(error="Invalid filename."), 400
    if len(image_filename) > 200:
        return jsonify(error="Filename too long."), 400

    # SSRF guard on image_url
    if image_url and not validate_url(image_url, allowed_hosts=_SAFE_IMAGE_HOSTS):
        return jsonify(error="Invalid image URL."), 400

    # Variants list must be small + strings
    if variants is not None:
        if not isinstance(variants, list) or len(variants) > 20:
            return jsonify(error="Invalid variants."), 400
        if any(not isinstance(v, str) or len(v) > 30 for v in variants):
            return jsonify(error="Invalid variants."), 400

    if not image_filename and not image_url:
        return jsonify(error="image_filename or image_url required"), 400

    # If no CDN URL provided, verify local file exists
    if not image_url:
        path = OUTPUT_DIR / Path(image_filename).name
        if not path.exists():
            return jsonify(error="Image not found"), 404

    try:
        results = generate_mockups(
            image_filename=image_filename,
            product_type=product_type,
            image_url=image_url or None,
            variants=variants,
        )
        return jsonify(mockups=results)
    except Exception:
        log.exception("Mockup generation failed")
        return jsonify(error="Mockup generation failed. Please try again."), 500


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
    ip = _client_ip()
    allowed, reason = check_rate_limit(ip, "webhook")
    if not allowed:
        return jsonify(error="Rate limited"), 429

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

        # Extract R2 key from portrait URL for upscale-based fulfillment
        composited_r2_key = None
        portrait_url = item.get("preview_url", "")
        r2_public = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
        if r2_public and portrait_url.startswith(r2_public):
            composited_r2_key = portrait_url[len(r2_public) + 1:]  # strip base + "/"

        result = fulfill_order_item(
            photo_path=photo_path,
            pet_name=item["pet_name"],
            style=item["style"],
            product_type=item["product_type"],
            size=item["size"],
            shopify_order_id=order_id,
            recipient=recipient,
            composited_r2_key=composited_r2_key,
        )

        log.info(
            "Order #%s — fulfillment complete: Printful order %s",
            order_id,
            result.get("result", {}).get("id", "?"),
        )

    except Exception:
        log.exception("Order #%s — fulfillment failed for item %s", order_id, item)


# ---------------------------------------------------------------------------
# Background job worker — processes queued portrait generation jobs
# ---------------------------------------------------------------------------

import time as _worker_time

# How many jobs to process concurrently (matches the Gemini semaphore)
_WORKER_THREADS = int(os.environ.get("MAX_CONCURRENT_GENERATIONS", 20))
_worker_pool = ThreadPoolExecutor(max_workers=_WORKER_THREADS, thread_name_prefix="gen-worker")


def _process_job(job: dict):
    """Process a single generation job — called by the worker pool."""
    job_id = job["job_id"]
    upload_path = Path(job["upload_path"])

    global _active_generations, _peak_generations
    with _active_lock:
        _active_generations += 1
        if _active_generations > _peak_generations:
            _peak_generations = _active_generations

    try:
        raw_path, comp_path, web_path = generate(str(upload_path), job["pet_name"], job["style"])

        # Upload original photo to R2 for future fulfillment (avoids re-generating via Gemini)
        original_cdn = upload_portrait(upload_path, key=f"originals/{job_id}{upload_path.suffix}")

        # Upload generated images to R2 for permanent CDN URLs
        raw_cdn = upload_portrait(raw_path)
        comp_cdn = upload_portrait(comp_path)
        web_cdn = upload_portrait(web_path)

        update_job(
            job_id,
            status="complete",
            raw=raw_cdn or f"/preview/{raw_path.name}",
            composited=web_cdn or f"/preview/{web_path.name}",  # frontend gets fast WebP
            composited_png_cdn=comp_cdn or f"/preview/{comp_path.name}",  # full-res PNG for Printful
            download=f"/download/{comp_path.name}",  # full-res PNG for download
            filename=comp_path.name,
            cdn="1" if comp_cdn else "0",
            original_cdn=original_cdn or "",
        )
        log.info("Job %s complete: %s", job_id, comp_path.name)

    except Exception as exc:
        log.exception("Job %s failed", job_id)
        update_job(job_id, status="failed", error=str(exc))
    finally:
        with _active_lock:
            _active_generations -= 1
        upload_path.unlink(missing_ok=True)


def _worker_loop():
    """
    Continuously poll the job queue and dispatch jobs to the worker pool.
    Runs in a daemon thread so it dies with the main process.
    """
    log.info("Job worker started (%d threads)", _WORKER_THREADS)
    while True:
        try:
            job = dequeue_job()
            if job:
                _worker_pool.submit(_process_job, job)
            else:
                _worker_time.sleep(0.5)  # no jobs — sleep briefly
        except Exception:
            log.exception("Worker loop error")
            _worker_time.sleep(1)


_worker_thread = _threading.Thread(target=_worker_loop, daemon=True)
_worker_thread.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, port=port)
