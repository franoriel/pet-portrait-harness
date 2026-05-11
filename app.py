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
    PRINTFUL_BASE,
    SHOPIFY_ADMIN_API_VERSION,
    _printful_headers,
    _get_printful_variant_id,
    PRINT_SIZES,
    PRODUCT_RATIOS,
    build_product_key,
    create_printful_order,
    generate_print_file,
    upload_print_file,
    parse_order_items,
    set_order_metafield,
    tag_shopify_order,
    tags_from_order_items,
    verify_shopify_webhook,
)
from mockups import generate_mockups
from storage import upload_portrait
from jobs import (
    create_job, get_job, dequeue_job, update_job, queue_depth,
    set_order_record, get_order_record,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger(__name__)

# Background executor for async fulfillment (webhook responds immediately).
# Wrapped in a class that auto-recreates the pool if a request hits a
# worker mid-shutdown (Railway redeploy race) — without this, customers
# see "cannot schedule new futures after shutdown" leak through to the UI.
class _ResilientPool:
    def __init__(self, max_workers, thread_name_prefix):
        self._max_workers = max_workers
        self._thread_name_prefix = thread_name_prefix
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=thread_name_prefix,
        )

    def submit(self, fn, *args, **kwargs):
        try:
            return self._pool.submit(fn, *args, **kwargs)
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "shutdown" not in msg and "broken" not in msg:
                raise
            # Pool is dead (atexit fired or broken). Recreate once and retry.
            with self._lock:
                # Double-check inside the lock — another thread may have
                # already recreated the pool.
                try:
                    return self._pool.submit(fn, *args, **kwargs)
                except RuntimeError:
                    log.warning(
                        "Thread pool %s was shut down — recreating",
                        self._thread_name_prefix,
                    )
                    self._pool = ThreadPoolExecutor(
                        max_workers=self._max_workers,
                        thread_name_prefix=self._thread_name_prefix,
                    )
                    return self._pool.submit(fn, *args, **kwargs)


_fulfillment_pool = _ResilientPool(max_workers=4, thread_name_prefix="fulfill")

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


# Backend exception messages can contain raw model output, stack traces, or
# other internals. These end up in job.error, which is sent to the client and
# rendered in the UI — anything we don't whitelist here is a customer-facing
# leak. Always log the full exception via log.exception; only a curated message
# may travel to the customer.
_SAFE_ERROR_LEAK_MARKERS = (
    "gemini returned",
    "model response:",
    "anthropic returned",
    "openai returned",
    "traceback",
    "**",            # markdown bold from a model dump
    "i have created",
    # Python runtime / infrastructure jargon that leaks during graceful
    # restarts and confuses non-technical customers.
    "cannot schedule new futures",
    "after shutdown",
    "executor",
    "concurrent.futures",
    "broken pool",
    "thread",
)


def _safe_customer_error(exc: BaseException) -> str:
    """Reduce an exception to a customer-safe error string.

    Pass-through is allowed only for short, plain messages that don't look like
    a model dump. Anything suspicious collapses to a generic message; the full
    detail is preserved in server logs (caller is expected to log.exception)."""
    msg = (str(exc) or "").strip()
    if not msg:
        return "Generation failed"
    low = msg.lower()
    for marker in _SAFE_ERROR_LEAK_MARKERS:
        if marker in low:
            return "Generation failed"
    if len(msg) > 160 or "\n" in msg:
        return "Generation failed"
    return msg


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
# Max 20 chars — keeps the name on a single line and prevents the AI from
# cramming a tiny font that gets cut off by a square crop. Rejects quotes,
# braces, brackets, backslashes, backticks, newlines, and anything else that
# could break out of a prompt string.
PET_NAME_MAX = 20
_PET_NAME_PATTERN = _re.compile(
    r"^[A-Za-z0-9\s\-\u2019'.]{1," + str(PET_NAME_MAX) + r"}$",
    _re.UNICODE,
)

def sanitize_pet_name(raw: str) -> tuple[bool, str]:
    """Returns (is_valid, clean_value). Strips + validates against whitelist."""
    if not raw:
        return False, ""
    name = raw.strip()
    if not name:
        return False, ""
    if len(name) > PET_NAME_MAX:
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
# CORS — restricted to trusted origins (supports wildcard suffixes)
# ─────────────────────────────────────────────────────────────
_raw_origins = (os.environ.get("ALLOWED_ORIGINS")
    or "https://petprintables.ca,https://www.petprintables.ca,https://petprintables.myshopify.com")
_ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]
_ALLOW_ANY_ORIGIN = "*" in _ALLOWED_ORIGINS


def _origin_allowed(origin: str) -> bool:
    """Check if an origin is in the whitelist. Supports:
     - "*" matches any origin
     - exact match "https://petprintables.ca"
     - wildcard subdomain "*.myshopify.com" matches "foo.myshopify.com"
     - trusted suffix .shopifypreview.com / .myshopify.com for admin previews
    """
    if _ALLOW_ANY_ORIGIN:
        return True
    if not origin:
        return False
    for allowed in _ALLOWED_ORIGINS:
        if allowed == origin:
            return True
        if allowed.startswith("*.") and origin.endswith(allowed[1:]):
            return True
    # Allow any Shopify preview/admin subdomain so testing works
    try:
        from urllib.parse import urlparse
        host = (urlparse(origin).hostname or "").lower()
        if host.endswith(".myshopify.com") or host.endswith(".shopifypreview.com"):
            return True
    except Exception:
        pass
    return False


@app.after_request
def _add_cors(response):
    origin = request.headers.get("Origin", "")
    if _origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin or "*"
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Token"
        response.headers["Access-Control-Max-Age"] = "3600"
    # Security headers on every response
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
@app.route("/contest/entry", methods=["OPTIONS"])
@app.route("/contest/earn", methods=["OPTIONS"])
@app.route("/contest/skill-test", methods=["OPTIONS"])
def _options_preflight(**_):
    return "", 204


# ---------------------------------------------------------------------------
# Contest entry — captures email + sends to Klaviyo + Meta CAPI Lead.
# Powers the Share Funnel's email gate. Designed to be tolerant: missing
# env vars (Klaviyo key, Meta CAPI token) make the relevant leg a no-op
# rather than failing the entire entry.
# ---------------------------------------------------------------------------

_EMAIL_RE = _re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

@app.route("/contest/skill-test", methods=["GET"])
def contest_skill_test():
    """Returns the day's skill-testing question (Canadian compliance)."""
    from contest import todays_skill_test
    question, _expected = todays_skill_test()
    return jsonify(question=question)


@app.route("/contest/entry", methods=["POST"])
def contest_entry():
    from contest import (
        referral_code, push_klaviyo_entry, send_meta_capi_lead,
        validate_skill_test, credit_referrer, BONUS_VALUES,
    )

    ip = _client_ip()
    allowed, reason = check_rate_limit(ip, "generate")
    if not allowed:
        return jsonify(error=reason, code="rate_limited"), 429

    # Inputs — accept JSON or form-encoded.
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    pet_name = (data.get("pet_name") or "").strip()[:40]
    style_id = (data.get("style_id") or "").strip()[:40]
    preview_url = (data.get("preview_url") or "").strip()[:500]
    referrer = (data.get("referrer_code") or "").strip().upper()[:20]
    sms_consent = str(data.get("sms_consent") or "").lower() in ("true", "1", "yes", "on")
    skill_answer = (data.get("skill_answer") or "").strip()
    utm = {
        "source":   (data.get("utm_source")   or "").strip()[:60],
        "medium":   (data.get("utm_medium")   or "").strip()[:60],
        "campaign": (data.get("utm_campaign") or "").strip()[:60],
        "content":  (data.get("utm_content")  or "").strip()[:60],
    }

    if not _EMAIL_RE.match(email):
        return jsonify(error="Please enter a valid email address.", code="email_invalid"), 400
    if not validate_skill_test(skill_answer):
        return jsonify(error="Skill-testing answer is incorrect. Please try again.",
                       code="skill_test_failed"), 400

    code = referral_code(pet_name, email)
    fbc = request.cookies.get("_fbc", "")
    fbp = request.cookies.get("_fbp", "")
    user_agent = request.headers.get("User-Agent", "")
    source_url = request.headers.get("Referer", "")

    klaviyo_ok = push_klaviyo_entry(
        email=email, pet_name=pet_name, style_id=style_id,
        preview_url=preview_url, code=code, referrer_code=referrer or None,
        sms_consent=sms_consent, utm=utm,
    )
    capi_ok = send_meta_capi_lead(
        email=email, pet_name=pet_name, style_id=style_id,
        client_ip=ip, user_agent=user_agent, event_source_url=source_url,
        fbc=fbc, fbp=fbp,
    )
    # Credit the referrer with +3 entries (fires a Klaviyo event on their
    # profile if we can resolve their email from their referral code).
    referrer_credited = False
    if referrer:
        referrer_credited = credit_referrer(referrer, email, pet_name)

    log.info(
        "[contest] entry email=%s pet=%s code=%s ref=%s referrer_credited=%s klaviyo=%s capi=%s sms=%s",
        email, pet_name, code, referrer or "-",
        referrer_credited, klaviyo_ok, capi_ok, sms_consent,
    )

    # Base 1 entry + 2 SMS bonus (if consented). Bonus claims happen on
    # /contest/earn separately. The "total" returned here is what the UI
    # initially shows — it's optimistic; the source of truth for the draw
    # is the sum of all Klaviyo events on the profile.
    entries_initial = 1 + (BONUS_VALUES["sms_consent"] if sms_consent else 0)

    return jsonify(
        ok=True,
        referral_code=code,
        share_url_suffix=f"?ref={code}",
        entries=entries_initial,
        klaviyo=klaviyo_ok,
        capi=capi_ok,
    )


# ---------------------------------------------------------------------------
# Bonus claim — share-screen "earn more entries" actions.
# ---------------------------------------------------------------------------

# Simple in-process dedup so the same profile can't spam-click the same
# bonus button. Resets on process restart; not a security boundary (the
# draw audit is the real one) but kills the obvious abuse vector.
_BONUS_DEDUP: dict = {}
_BONUS_DEDUP_TTL = 60 * 60 * 24 * 30  # 30 days

def _dedup_seen(email: str, bonus_type: str) -> bool:
    key = f"{email}:{bonus_type}"
    now = _time.time()
    # Trim expired entries occasionally
    if len(_BONUS_DEDUP) > 5000:
        for k, ts in list(_BONUS_DEDUP.items()):
            if now - ts > _BONUS_DEDUP_TTL:
                _BONUS_DEDUP.pop(k, None)
    if key in _BONUS_DEDUP:
        return True
    _BONUS_DEDUP[key] = now
    return False


@app.route("/contest/earn", methods=["POST"])
def contest_earn():
    """Record a self-attested bonus claim. Fires `Contest Entries Earned`
    on the entrant's Klaviyo profile.

    Frontend POSTs after the user clicks Claim on a share-step action.
    Server records once per (email, bonus_type) pair.
    """
    from contest import record_bonus_claim, BONUS_VALUES

    ip = _client_ip()
    allowed, reason = check_rate_limit(ip, "status")
    if not allowed:
        return jsonify(error=reason, code="rate_limited"), 429

    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    bonus_type = (data.get("bonus_type") or "").strip().lower()
    proof_url = (data.get("proof_url") or "").strip()[:500]

    if not _EMAIL_RE.match(email):
        return jsonify(error="Email required.", code="email_required"), 400
    if bonus_type not in BONUS_VALUES or bonus_type == "referral":
        return jsonify(error="Unknown bonus type.", code="bad_bonus_type"), 400

    if _dedup_seen(email, bonus_type):
        return jsonify(ok=True, deduped=True, bonus_value=0,
                       message="Already claimed."), 200

    ok, bonus = record_bonus_claim(email=email, bonus_type=bonus_type, proof_url=proof_url)
    return jsonify(ok=ok, bonus_value=bonus)

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
            error="Pet name must be 1–20 characters using only letters, numbers, spaces, hyphens, periods, or apostrophes."
        ), 400

    # Whitelist style + map React IDs (soft-watercolour → watercolor)
    style_raw = request.form.get("style", "classic")
    if not isinstance(style_raw, str) or len(style_raw) > 40:
        return jsonify(error="Invalid style."), 400
    from fulfillment import _map_style_id
    style = _map_style_id(style_raw)
    if style not in PROMPTS:
        return jsonify(error="Invalid style."), 400

    # Background mode: 'auto' / 'light' / 'dark' for most styles, plus the 8
    # MODERN_BG_COLORS for modern-shape-art (cream/clay/sage/terracotta/mauve/
    # mustard/navy/charcoal) and the 8 POSTER_PALETTES for bold-graphic-poster
    # (teal/cobalt/rose/citrus/forest/rust/violet/ember). Anything else falls
    # back to 'auto'.
    from generate import MODERN_BG_COLORS, POSTER_PALETTES
    _BG_VALID = (
        "auto", "light", "dark",
        *MODERN_BG_COLORS.keys(),
        *POSTER_PALETTES.keys(),
    )
    background_mode_raw = (request.form.get("background_mode") or "auto").strip().lower()
    background_mode = background_mode_raw if background_mode_raw in _BG_VALID else "auto"
    # Modern locks to a colour palette — if the request comes in with
    # auto/light/dark while the chosen style is modern, default to 'clay'.
    if style == "modern-shape-art" and background_mode not in MODERN_BG_COLORS:
        background_mode = "clay"
    # Bold Graphic Poster locks to a paired-tone palette — same idea, but
    # default 'teal' when the request didn't carry a valid palette id.
    if style == "bold-graphic-poster" and background_mode not in POSTER_PALETTES:
        background_mode = "teal"
    # Optional variation seed — when the customer regenerates the same
    # photo + style they used recently, the client passes a non-zero
    # seed and the server picks one of N variation hints to nudge Gemini
    # away from converging on a near-identical composition. Cleanly
    # parsed; if absent or non-numeric, no variation hint is used.
    variation_seed_raw = (request.form.get("variation_seed") or "").strip()
    variation_seed: Optional[int] = None
    if variation_seed_raw:
        try:
            variation_seed = int(variation_seed_raw) % 10_000
        except (TypeError, ValueError):
            variation_seed = None
    log.info(
        "[/generate] style=%s bg_mode=%s variation_seed=%s (raw=%r)",
        style, background_mode, variation_seed, background_mode_raw,
    )

    # ── Photo-license consent (audit trail) ──────────────────────────────────
    # Frontend checkbox sends an ISO-8601 timestamp when the customer ticks
    # "I own rights to this photo and grant a licence to reproduce/modify/print".
    # We require it to be present, within a plausible window, and log it with
    # the IP so we have a defensible record if a dispute arises.
    terms_accepted_at = (request.form.get("terms_accepted_at") or "").strip()[:40]
    if not terms_accepted_at:
        return jsonify(
            error="You must accept the photo upload terms before generating.",
            code="terms_required",
        ), 400
    try:
        from datetime import datetime, timezone, timedelta
        # Normalise trailing Z → +00:00 for fromisoformat
        _ts_iso = terms_accepted_at.replace("Z", "+00:00")
        _accepted = datetime.fromisoformat(_ts_iso)
        if _accepted.tzinfo is None:
            _accepted = _accepted.replace(tzinfo=timezone.utc)
        _now = datetime.now(timezone.utc)
        # Accept only timestamps within the last 24h and not from the future
        # (beyond a 2-minute clock-skew tolerance).
        if _accepted > _now + timedelta(minutes=2) or _accepted < _now - timedelta(hours=24):
            return jsonify(error="Photo terms acceptance is stale. Please re-check the box.",
                           code="terms_stale"), 400
    except Exception:
        return jsonify(error="Invalid terms acceptance timestamp.",
                       code="terms_invalid"), 400
    log.info("PHOTO_LICENCE_ACCEPTED ip=%s ts=%s style=%s", ip, terms_accepted_at, style)

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
    job = create_job(
        pet_name=pet_name,
        style=style,
        upload_path=str(upload_path),
        terms_accepted_at=terms_accepted_at,
        client_ip=ip,
        background_mode=background_mode,
        variation_seed=variation_seed,
    )
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
        # Field semantics:
        #   composited / raw_preview → watermarked WebPs the customer sees
        #   raw / composited_png_cdn → un-watermarked PNGs for fulfillment.
        #   The frontend should prefer raw_preview over raw for any UI
        #   surface; raw is kept for backward-compat with older clients.
        return jsonify(
            job_id=job_id,
            status="complete",
            raw=job.get("raw", ""),
            raw_preview=job.get("raw_preview", "") or job.get("raw", ""),
            composited=job.get("composited", ""),
            composited_png_cdn=job.get("composited_png_cdn", ""),
            download=job.get("download", ""),
            filename=job.get("filename", ""),
            cdn=job.get("cdn", False),
            original_cdn=job.get("original_cdn", ""),
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
# Signed download URL — used by the Klaviyo "A Gift For You" email so the
# customer can pull a one-time, time-limited high-res copy of their portrait.
# ---------------------------------------------------------------------------

def _download_secret() -> bytes:
    """HMAC secret for signing/verifying download tokens. Falls back to the
    Shopify webhook secret if a dedicated DOWNLOAD_TOKEN_SECRET isn't set —
    same trust boundary, no operational overhead."""
    return (os.environ.get("DOWNLOAD_TOKEN_SECRET")
            or os.environ.get("SHOPIFY_WEBHOOK_SECRET")
            or "dev-secret-do-not-use-in-prod").encode("utf-8")


def make_download_token(r2_key: str, ttl_seconds: int = 86400) -> str:
    """Sign an R2 object key with HMAC + expiry. Returns a urlsafe-base64
    token of the form base64(payload).hexsignature.
    payload = "{r2_key}|{expires_unix}"
    """
    import base64, hmac, hashlib, time
    expires = int(time.time()) + ttl_seconds
    payload = f"{r2_key}|{expires}".encode("utf-8")
    sig = hmac.new(_download_secret(), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + "." + sig


def verify_download_token(token: str) -> Optional[str]:
    """Verify a token returned by make_download_token. Returns the r2_key on
    success, None on signature failure or expiry."""
    import base64, hmac, hashlib, time
    try:
        b64_payload, sig = token.rsplit(".", 1)
        # Re-pad for base64 decoding
        padding = 4 - (len(b64_payload) % 4)
        if padding != 4:
            b64_payload += "=" * padding
        payload = base64.urlsafe_b64decode(b64_payload)
        expected = hmac.new(_download_secret(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        r2_key, expires_str = payload.decode("utf-8").rsplit("|", 1)
        if int(expires_str) < int(time.time()):
            return None
        return r2_key
    except Exception:
        return None


def make_order_token(order_id: str, ttl_seconds: int = 90 * 86400) -> str:
    """Sign an order_id with HMAC + expiry. Mirrors make_download_token's
    format. Used for the /portraits/<token> digital-gift page link in
    the post-purchase email — no Shopify account login required."""
    import base64, hmac, hashlib, time
    expires = int(time.time()) + ttl_seconds
    payload = f"order:{order_id}|{expires}".encode("utf-8")
    sig = hmac.new(_download_secret(), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + "." + sig


def verify_order_token(token: str) -> Optional[str]:
    """Verify an order token. Returns the order_id on success, None on
    signature failure or expiry."""
    import base64, hmac, hashlib, time
    try:
        b64_payload, sig = token.rsplit(".", 1)
        padding = 4 - (len(b64_payload) % 4)
        if padding != 4:
            b64_payload += "=" * padding
        payload = base64.urlsafe_b64decode(b64_payload)
        expected = hmac.new(_download_secret(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        decoded = payload.decode("utf-8")
        if not decoded.startswith("order:"):
            return None
        body, expires_str = decoded.rsplit("|", 1)
        if int(expires_str) < int(time.time()):
            return None
        return body[len("order:"):]
    except Exception:
        return None


# Self-contained HTML for the digital-gift page. Single CTA above the
# fold, per-portrait card with downloads + share + gallery submit, plus
# a soft review nudge. Inline CSS so it renders standalone — no theme
# Liquid dependency. Brand-aligned with the email template (cream bg,
# Cormorant Garamond serif italic, charcoal ink, no em-dashes).
_PORTRAITS_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Your portraits — Pet Printables</title>
<link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,500;1,400;1,500&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 0; background: #FAF8F5; color: #1C1C1C; font-family: 'Inter', -apple-system, sans-serif; }
.wrap { max-width: 720px; margin: 0 auto; padding: 32px 24px 80px; }
.header { text-align: center; padding: 16px 0 32px; }
.brand-logo { display: block; height: 48px; width: auto; max-width: 240px; margin: 0 auto; }
@media (max-width: 600px) { .brand-logo { height: 40px; max-width: 200px; } }
.eyebrow { font-size: 11px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: #8B7D6B; margin: 0 0 8px; }
.h1 { font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; font-size: 38px; line-height: 1.1; margin: 0 0 12px; color: #1C1C1C; }
.h1-sub { font-size: 14px; color: #6B6B63; line-height: 1.55; margin: 0 0 32px; }
.expiry { font-size: 12px; color: #8B7D6B; margin: 0 0 24px; font-style: italic; }
.portrait-card { background: #FFFFFF; border: 1px solid #E4DDD4; border-radius: 12px; padding: 24px; margin-bottom: 24px; }
.portrait-card__head { display: grid; grid-template-columns: 140px 1fr; gap: 20px; align-items: start; margin-bottom: 16px; }
.portrait-card__img { width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: 8px; display: block; background: #F3EDE6; }
.portrait-card__name { font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; font-size: 22px; color: #1C1C1C; margin: 0 0 4px; }
.portrait-card__style { font-size: 12px; color: #8B7D6B; margin: 0 0 14px; }
.dl-section { margin-top: 14px; }
.dl-eyebrow { font-size: 11px; font-weight: 600; letter-spacing: 0.14em; text-transform: uppercase; color: #8B7D6B; margin: 0 0 10px; }
.dl-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 12px; }
.dl-btn { display: flex; flex-direction: column; align-items: flex-start; padding: 12px 14px; border: 1px solid #1C1C1C; border-radius: 6px; background: #FFFFFF; color: #1C1C1C; text-decoration: none; cursor: pointer; font-family: inherit; font-size: 13px; transition: all 0.15s; }
.dl-btn:hover { background: #F3EDE6; }
.dl-btn strong { font-size: 14px; font-weight: 600; }
.dl-btn span { font-size: 11px; color: #6B6B63; margin-top: 2px; }
.dl-btn--solid { background: #2F2F2A; color: #fff; border-color: #2F2F2A; }
.dl-btn--solid:hover { background: #1C1C1C; color: #fff; }
.dl-btn--ghost { background: transparent; }
.actions-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.review-cta { background: #F3EDE6; border-radius: 12px; padding: 28px; text-align: center; margin-top: 32px; }
.review-cta h2 { font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; font-size: 24px; margin: 0 0 8px; color: #1C1C1C; }
.review-cta p { font-size: 14px; color: #6B6B63; margin: 0 0 16px; line-height: 1.55; }
.btn-primary { display: inline-block; background: #2F2F2A; color: #FFFFFF; padding: 14px 28px; border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; }
.btn-secondary { display: inline-block; background: #FFFFFF; color: #1C1C1C; padding: 13px 26px; border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: 600; letter-spacing: 0.06em; border: 1px solid #1C1C1C; }
.howto { background: #FFFFFF; border: 1px solid #E4DDD4; border-radius: 12px; padding: 28px; margin-top: 32px; }
.howto h2 { font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; font-size: 24px; margin: 0 0 8px; color: #1C1C1C; }
.howto > p.howto-sub { font-size: 14px; color: #6B6B63; margin: 0 0 20px; line-height: 1.55; }
.howto-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px 24px; margin: 0; }
.howto-step { display: flex; gap: 12px; align-items: flex-start; }
.howto-step__num { display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; width: 24px; height: 24px; border-radius: 50%; background: #2F2F2A; color: #fff; font-size: 11px; font-weight: 600; }
.howto-step h3 { font-size: 13px; font-weight: 600; margin: 0 0 4px; color: #1C1C1C; }
.howto-step p { font-size: 12px; color: #6B6B63; margin: 0; line-height: 1.5; }
.reorder-cta { background: #FFFFFF; border: 1px solid #E4DDD4; border-radius: 12px; padding: 28px; text-align: center; margin-top: 24px; }
.reorder-cta h2 { font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; font-size: 22px; margin: 0 0 6px; color: #1C1C1C; }
.reorder-cta p { font-size: 13px; color: #6B6B63; margin: 0 0 16px; line-height: 1.55; }
.tracking { background: #FFFFFF; border: 1px solid #E4DDD4; border-radius: 12px; padding: 20px 24px; margin: 0 0 24px; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
.tracking__icon { display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: 50%; background: #F3EDE6; flex-shrink: 0; font-family: 'Cormorant Garamond', serif; font-style: italic; font-weight: 500; color: #1C1C1C; font-size: 18px; }
.tracking__copy { flex: 1; min-width: 200px; }
.tracking__status { font-size: 11px; font-weight: 600; letter-spacing: 0.16em; text-transform: uppercase; color: #8B7D6B; margin: 0 0 4px; }
.tracking__line { font-size: 14px; color: #1C1C1C; margin: 0; line-height: 1.45; }
.tracking__cta { font-size: 13px; font-weight: 600; color: #1C1C1C; text-decoration: none; padding: 10px 18px; border: 1px solid #1C1C1C; border-radius: 4px; letter-spacing: 0.04em; background: #FFFFFF; }
.tracking__cta:hover { background: #1C1C1C; color: #FFFFFF; }
.footer { text-align: center; margin-top: 48px; padding-top: 24px; border-top: 1px solid #E4DDD4; }
.footer p { font-size: 11px; color: #8B7D6B; letter-spacing: 0.04em; margin: 4px 0; }
.footer a { color: #8B7D6B; text-decoration: underline; }
@media (max-width: 600px) {
  .h1 { font-size: 30px; }
  .portrait-card__head { grid-template-columns: 1fr; }
  .portrait-card__img { max-width: 220px; margin: 0 auto; }
  .dl-grid { grid-template-columns: 1fr; }
  .howto-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="wrap">
  <header class="header">
    <img class="brand-logo" src="/static/pet-printables-wordmark.svg" alt="Pet Printables">
  </header>
  __EYEBROW__
  __HEADLINE__
  <p class="expiry">Links are good until __EXPIRES__. Save the originals — they expire.</p>
  __TRACKING__
  __PORTRAITS__

  <section class="howto">
    <h2>How to use them</h2>
    <p class="howto-sub">Each format is sized for one specific surface. Tap the buttons above to download, then:</p>
    <div class="howto-grid">
      <div class="howto-step">
        <span class="howto-step__num">1</span>
        <div>
          <h3>Set as wallpaper</h3>
          <p>Open the Wallpaper file in Photos. Share &rarr; Use as Wallpaper.</p>
        </div>
      </div>
      <div class="howto-step">
        <span class="howto-step__num">2</span>
        <div>
          <h3>Share to your Story</h3>
          <p>Open the Story file in Instagram. Tap the photo on the Story camera to add as background.</p>
        </div>
      </div>
      <div class="howto-step">
        <span class="howto-step__num">3</span>
        <div>
          <h3>Post to your feed</h3>
          <p>Use the Square file for a feed post or the Portrait file for a 4:5 crop &mdash; both fit Instagram's preferred sizes.</p>
        </div>
      </div>
      <div class="howto-step">
        <span class="howto-step__num">4</span>
        <div>
          <h3>Tag us</h3>
          <p>If you share, tag <a href="https://instagram.com/mypetprintables" style="color:#1C1C1C;text-decoration:underline;">@mypetprintables</a> &mdash; we love seeing them out in the world.</p>
        </div>
      </div>
    </div>
  </section>

  __REORDER_CTA__

  __REVIEW_CTA__
  <footer class="footer">
    <p style="font-family:'Cormorant Garamond',serif;font-style:italic;font-size:16px;color:#1C1C1C;">Pet Printables</p>
    <p>A modern tribute to unconditional love. Vancouver, BC.</p>
    <p><a href="https://petprintables.ca">petprintables.ca</a> · <a href="https://instagram.com/mypetprintables">@mypetprintables</a></p>
  </footer>
</div>
<script>
document.querySelectorAll('[data-share-url]').forEach(function (btn) {
  btn.addEventListener('click', async function () {
    var url = btn.getAttribute('data-share-url');
    var name = btn.getAttribute('data-share-name') || 'pet portrait';
    var data = { title: name + ' portrait', text: 'Look at ' + name + ' \\ud83d\\udc3e', url: url };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(url);
      var orig = btn.textContent;
      btn.textContent = 'Link copied';
      setTimeout(function () { btn.textContent = orig; }, 2000);
    } catch (e) { /* user cancelled or unsupported */ }
  });
});
</script>
</body>
</html>"""


def _render_portraits_page(record: dict) -> str:
    """Render the /portraits/<token> page from a digital_files record.

    Branches the headline, eyebrow, tracking strip, reorder CTA, and review
    CTA based on the live order context the route attaches at render time:
    - customer_orders_count > 1 → repeat-buyer welcome
    - fulfillment_status / tracking_url → live tracking strip
    """
    import html as _html
    portraits = record.get("portraits") or []
    expires_at = record.get("expires_at") or ""
    ctx = record.get("context") or {}
    is_repeat = (ctx.get("customer_orders_count") or 0) > 1
    first_name = (ctx.get("customer_first_name") or "").strip()
    # Format expiry as "May 8, 2026" if it's an ISO timestamp
    try:
        from datetime import datetime as _dt
        expiry_dt = _dt.strptime(expires_at[:10], "%Y-%m-%d") if expires_at else None
        expires_str = expiry_dt.strftime("%B %-d, %Y") if expiry_dt else "soon"
    except Exception:
        expires_str = "soon"

    # Eyebrow + headline. Repeat-buyer welcome wins over first-time copy
    # when the customer has placed more than one order.
    if is_repeat:
        eyebrow = (
            f'<p class="eyebrow">Welcome back{", " + _html.escape(first_name) if first_name else ""}</p>'
        )
        if len(portraits) == 1:
            name = _html.escape(portraits[0].get("pet_name") or "your pet")
            headline = (
                f'<h1 class="h1">A digital copy of <em>{name}</em>, on us.</h1>'
                '<p class="h1-sub">Thanks for coming back. Your canvas is in production. '
                'Until it lands at your door, here is a digital copy in every format you '
                'would want to share &mdash; feed, story, profile pic, even a phone wallpaper.</p>'
            )
        else:
            headline = (
                f'<h1 class="h1">Your {len(portraits)} new portraits, ready to share.</h1>'
                '<p class="h1-sub">Thanks for coming back. Your order is in production. '
                'Until it lands at your door, here is a digital copy of each portrait in '
                'every format you would want to share &mdash; feed, story, profile pic, '
                'even a phone wallpaper.</p>'
            )
    else:
        eyebrow = '<p class="eyebrow">Your digital gift</p>'
        if len(portraits) == 1:
            name = _html.escape(portraits[0].get("pet_name") or "your pet")
            headline = (
                f'<h1 class="h1">A digital copy of <em>{name}</em>, on us.</h1>'
                '<p class="h1-sub">Your order is in production. Until it lands at your door, '
                'here is a digital copy in every format you would want to share &mdash; feed, '
                'story, profile pic, even a phone wallpaper.</p>'
            )
        else:
            headline = (
                f'<h1 class="h1">Your {len(portraits)} portraits, ready to share.</h1>'
                '<p class="h1-sub">Your order is in production. Until it lands at your door, '
                'here is a digital copy of each portrait in every format you would want '
                'to share &mdash; feed, story, profile pic, even a phone wallpaper.</p>'
            )

    cards = []
    for p in portraits:
        pet_name = _html.escape(p.get("pet_name") or "Pet")
        style = _html.escape(p.get("style") or "")
        preview = _html.escape(p.get("preview_url") or "")
        original = _html.escape(p.get("original_url") or preview)
        gallery = _html.escape(p.get("gallery_submit_url") or "")
        downloads = p.get("downloads") or {}

        dl_buttons = []
        formats = [
            ("square", "Square", "Instagram post"),
            ("story", "Story", "IG &amp; FB story"),
            ("portrait", "Portrait", "IG feed (4:5)"),
            ("wallpaper", "Wallpaper", "Phone lock screen"),
        ]
        for key, label, desc in formats:
            url = downloads.get(key)
            if url:
                dl_buttons.append(
                    f'<a href="{_html.escape(url)}" class="dl-btn" download>'
                    f'<strong>{label}</strong><span>{desc}</span></a>'
                )

        actions = [
            f'<a href="{original}" class="dl-btn dl-btn--solid" download>Download original</a>',
            f'<button type="button" class="dl-btn dl-btn--ghost" data-share-url="{original}" data-share-name="{pet_name}">Share</button>',
        ]
        if gallery:
            actions.append(
                f'<a href="{gallery}" class="dl-btn dl-btn--ghost">Submit to gallery</a>'
            )

        cards.append(f"""
<article class="portrait-card">
  <div class="portrait-card__head">
    <a href="{original}" download><img class="portrait-card__img" src="{preview}" alt="{pet_name} portrait"/></a>
    <div>
      <p class="portrait-card__name">{pet_name}</p>
      <p class="portrait-card__style">{style}</p>
      <div class="actions-row">{"".join(actions)}</div>
    </div>
  </div>
  <div class="dl-section">
    <p class="dl-eyebrow">Made for sharing</p>
    <div class="dl-grid">{"".join(dl_buttons)}</div>
  </div>
</article>""")

    # Tracking strip — render only when we have something useful to say.
    # Status normalisation keeps the copy readable regardless of which
    # Shopify enum value the order is currently sitting at.
    fulfillment_status = (ctx.get("fulfillment_status") or "").lower()
    tracking_url = ctx.get("tracking_url") or ""
    tracking_company = ctx.get("tracking_company") or ""
    tracking_number = ctx.get("tracking_number") or ""

    if fulfillment_status in ("fulfilled", "success", "shipped"):
        status_label = "On the way"
        status_line = (
            f'Your canvas shipped'
            + (f' with {_html.escape(tracking_company)}' if tracking_company else '')
            + (f' &middot; {_html.escape(tracking_number)}' if tracking_number else '')
            + '.'
        )
    elif fulfillment_status in ("partial",):
        status_label = "Partially shipped"
        status_line = "Part of your order is on the way; the rest is still being made."
    elif fulfillment_status in ("delivered",):
        status_label = "Delivered"
        status_line = "Your canvas should be at your door."
    else:
        status_label = "In production"
        status_line = "Your canvas is being made. We&rsquo;ll email you the moment it ships."

    if tracking_url:
        tracking_html = (
            '<section class="tracking">'
            '<span class="tracking__icon" aria-hidden="true">→</span>'
            '<div class="tracking__copy">'
            f'<p class="tracking__status">{status_label}</p>'
            f'<p class="tracking__line">{status_line}</p>'
            '</div>'
            f'<a class="tracking__cta" href="{_html.escape(tracking_url)}" target="_blank" rel="noopener noreferrer">TRACK ORDER</a>'
            '</section>'
        )
    elif ctx:
        # We have order context but no tracking URL yet — still surface the
        # status so the customer knows what state their canvas is in.
        tracking_html = (
            '<section class="tracking">'
            '<span class="tracking__icon" aria-hidden="true">✦</span>'
            '<div class="tracking__copy">'
            f'<p class="tracking__status">{status_label}</p>'
            f'<p class="tracking__line">{status_line}</p>'
            '</div>'
            '</section>'
        )
    else:
        tracking_html = ""

    # Reorder CTA: soften copy on repeat. Otherwise default to the "make
    # another" prompt.
    if is_repeat:
        reorder_html = (
            '<div class="reorder-cta">'
            '<h2>Another pet you want to celebrate?</h2>'
            '<p>You know the drill. Upload a photo, pick a style, send the canvas.</p>'
            '<a href="https://petprintables.ca/pages/create" class="btn-secondary">MAKE ANOTHER PORTRAIT</a>'
            '</div>'
        )
    else:
        reorder_html = (
            '<div class="reorder-cta">'
            '<h2>Have another one to celebrate?</h2>'
            '<p>Make a portrait for a second pet or a friend&rsquo;s dog.</p>'
            '<a href="https://petprintables.ca/pages/create" class="btn-secondary">MAKE ANOTHER PORTRAIT</a>'
            '</div>'
        )

    review_html = (
        '<div class="review-cta">'
        '<h2>How was your order?</h2>'
        '<p>Your canvas is on its way. Once it lands, we&rsquo;d love to hear what you think.</p>'
        '<a href="https://petprintables.ca/pages/reviews" class="btn-primary">LEAVE A REVIEW</a>'
        '</div>'
    )

    return (_PORTRAITS_PAGE_TEMPLATE
            .replace("__EYEBROW__", eyebrow)
            .replace("__HEADLINE__", headline)
            .replace("__EXPIRES__", _html.escape(expires_str))
            .replace("__TRACKING__", tracking_html)
            .replace("__PORTRAITS__", "".join(cards))
            .replace("__REORDER_CTA__", reorder_html)
            .replace("__REVIEW_CTA__", review_html))


def _fetch_order_context(order_id: str) -> dict:
    """Pull live fulfillment + customer data from Shopify Admin API for the
    Flask gift page. Used to render the tracking strip and branch the
    headline for repeat customers. Fails open — returns an empty dict on
    any error so the page still renders without the enrichments."""
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()
    if not domain or not token:
        return {}

    headers = {"X-Shopify-Access-Token": token, "Accept": "application/json"}
    out: dict = {}

    try:
        r = _req.get(
            f"https://{domain}/admin/api/{SHOPIFY_ADMIN_API_VERSION}/orders/{order_id}.json",
            headers=headers,
            params={"fields": "id,fulfillment_status,fulfillments,customer,note_attributes,line_items"},
            timeout=8,
        )
        if r.status_code != 200:
            return {}
        order = (r.json() or {}).get("order") or {}
    except Exception:
        log.warning("[portraits/page] order=%s context fetch failed", order_id, exc_info=True)
        return {}

    # Fulfillment + tracking from the most recent fulfillment record.
    fulfillments = order.get("fulfillments") or []
    if fulfillments:
        latest = fulfillments[-1]
        out["fulfillment_status"] = latest.get("status") or order.get("fulfillment_status")
        out["tracking_url"] = (
            (latest.get("tracking_urls") or [None])[0]
            or latest.get("tracking_url")
        )
        out["tracking_company"] = latest.get("tracking_company")
        out["tracking_number"] = (
            (latest.get("tracking_numbers") or [None])[0]
            or latest.get("tracking_number")
        )
        out["shipped_at"] = latest.get("created_at")
    else:
        out["fulfillment_status"] = order.get("fulfillment_status") or "unfulfilled"

    # Customer order count — drives the repeat-buyer welcome.
    customer = order.get("customer") or {}
    out["customer_first_name"] = (customer.get("first_name") or "").strip()
    try:
        out["customer_orders_count"] = int(customer.get("orders_count") or 0)
    except (TypeError, ValueError):
        out["customer_orders_count"] = 0

    return out


@app.route("/portraits/<token>")
def portraits_page(token):
    """Render the digital-gift page for a signed order token. The Klaviyo
    post-purchase email links here. No Shopify account login required —
    HMAC-signed token is the access control. 90-day TTL."""
    ip = _client_ip()
    allowed, _ = check_rate_limit(ip, "download")
    if not allowed:
        return "Too many requests", 429

    order_id = verify_order_token(token)
    if not order_id:
        return ("This portrait link has expired. The digital gift was available for "
                "90 days from when your order email was sent. Reply to that email and "
                "we will send a fresh one."), 410

    record = get_order_record(order_id)
    if not record:
        log.warning("[portraits/page] order=%s record not found in store", order_id)
        return ("We could not find your portraits. If you just placed an order, "
                "it may take a few minutes for the digital files to be ready — "
                "try again in 5 minutes."), 404

    # Enrich the cached record with live order/customer context (tracking,
    # repeat-buyer count). Fails open — page still renders if the Shopify
    # call returns nothing.
    record = dict(record)
    record["context"] = _fetch_order_context(order_id)

    log.info("[portraits/page] order=%s rendered (%d portrait(s))",
             order_id, len(record.get("portraits") or []))
    return _render_portraits_page(record)


@app.route("/portrait/download/<token>")
def portrait_download(token):
    """Resolve a signed token to an R2 key, then 302 to the public R2 URL
    so the browser triggers a download. Tokens expire 24h after issuance.

    Email CTA hits this endpoint; we never expose the raw R2 key in the
    email itself (so a copied URL can't be re-shared after the window)."""
    ip = _client_ip()
    allowed, _ = check_rate_limit(ip, "download")
    if not allowed:
        return "Too many requests", 429

    r2_key = verify_download_token(token)
    if not r2_key:
        return ("This download link has expired. The high-res copy was "
                "available for 24 hours from when your order email was sent. "
                "Reply to that email and we'll send a fresh one."), 410

    # Build the public R2 URL and redirect. Browser handles the actual
    # download with the file's stored Content-Disposition (we set
    # ContentDisposition='attachment' at upload time in storage.py).
    r2_public = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if not r2_public:
        log.error("R2_PUBLIC_URL not configured — cannot redirect download")
        return "Storage not configured", 500
    target = f"{r2_public}/{r2_key}"
    log.info("[download/token] %s → %s", token[:16] + "...", r2_key)
    from flask import redirect
    return redirect(target, code=302)


# ---------------------------------------------------------------------------
# Gallery submission — one-click "submit my portrait to the public gallery"
# from the Klaviyo digital-gift email. The link is HMAC-signed at order time
# (encoding r2_key + pet_name + order_id) so the customer doesn't see a form;
# they just click and their portrait lands on petprintables.ca/pages/gallery.
#
# Idempotent: the metaobject handle is `gallery-{order_id}`, so repeat clicks
# (multi-device, forwarded to themselves, etc.) update in place rather than
# creating duplicates. Auto-publishes — every metaobject created here is
# `active` immediately. Trust boundary: the token is unforgeable, and the only
# image that can be submitted is the one we generated for that customer's
# paid order, so there's no moderation queue.
# ---------------------------------------------------------------------------

def _gallery_secret() -> bytes:
    """HMAC secret for signing/verifying gallery-submit tokens. Falls back to
    the download-token secret (same trust boundary). Separated so a future
    rotation of one doesn't invalidate the other."""
    return (os.environ.get("GALLERY_TOKEN_SECRET")
            or os.environ.get("DOWNLOAD_TOKEN_SECRET")
            or os.environ.get("SHOPIFY_WEBHOOK_SECRET")
            or "dev-secret-do-not-use-in-prod").encode("utf-8")


def make_gallery_token(r2_key: str, pet_name: str, order_id: str,
                       ttl_seconds: int = 30 * 86400) -> str:
    """Sign (r2_key, pet_name, order_id) with HMAC + expiry. URL-quotes each
    field so '|' can't appear inside any of them. 30-day default TTL matches
    the social-variant tokens issued at the same time."""
    import base64, hmac, hashlib, time
    from urllib.parse import quote
    expires = int(time.time()) + ttl_seconds
    parts = [quote(s or "", safe="") for s in (r2_key, pet_name, order_id, str(expires))]
    payload = "|".join(parts).encode("utf-8")
    sig = hmac.new(_gallery_secret(), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=") + "." + sig


def verify_gallery_token(token: str) -> Optional[dict]:
    """Verify a token returned by make_gallery_token. Returns
    {r2_key, pet_name, order_id} on success, None on signature failure or
    expiry."""
    import base64, hmac, hashlib, time
    from urllib.parse import unquote
    try:
        b64_payload, sig = token.rsplit(".", 1)
        padding = 4 - (len(b64_payload) % 4)
        if padding != 4:
            b64_payload += "=" * padding
        payload = base64.urlsafe_b64decode(b64_payload)
        expected = hmac.new(_gallery_secret(), payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        parts = payload.decode("utf-8").split("|")
        if len(parts) != 4:
            return None
        r2_key, pet_name, order_id, expires_str = (unquote(p) for p in parts)
        if int(expires_str) < int(time.time()):
            return None
        return {"r2_key": r2_key, "pet_name": pet_name, "order_id": order_id}
    except Exception:
        return None


def _shopify_admin_graphql(query: str, variables: dict) -> dict:
    """POST a GraphQL query to the Shopify Admin API. Raises on transport
    error; returns the parsed JSON body otherwise (caller checks userErrors).
    """
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    admin_token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()
    if not domain or not admin_token:
        raise RuntimeError("SHOPIFY_SHOP_DOMAIN or SHOPIFY_ADMIN_API_TOKEN not set")
    url = f"https://{domain}/admin/api/{SHOPIFY_ADMIN_API_VERSION}/graphql.json"
    resp = _req.post(
        url,
        headers={
            "X-Shopify-Access-Token": admin_token,
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _submit_to_gallery(r2_key: str, pet_name: str, order_id: str) -> tuple[bool, str]:
    """Upload the R2 preview to Shopify Files and upsert a `gallery_submission`
    metaobject keyed on order_id. Returns (ok, detail). The metaobject handle
    is `gallery-{order_id}` so repeat clicks are idempotent."""
    r2_public = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if not r2_public:
        return False, "R2_PUBLIC_URL not configured"
    image_url = f"{r2_public}/{r2_key}"
    handle = f"gallery-{order_id}"

    # 1. Idempotency — if a metaobject with this handle already exists, skip
    #    file upload + creation entirely. Avoids stacking new file uploads
    #    every time the customer re-clicks.
    existing_q = """
      query ExistingSubmission($handle: MetaobjectHandleInput!) {
        metaobjectByHandle(handle: $handle) { id handle }
      }
    """
    try:
        existing = _shopify_admin_graphql(existing_q, {
            "handle": {"type": "gallery_submission", "handle": handle},
        })
        if (existing.get("data") or {}).get("metaobjectByHandle"):
            return True, "already-submitted"
    except Exception as exc:
        log.warning("[gallery] order=%s existing-check failed: %s", order_id, exc)

    # 2. Upload the R2 image to Shopify Files. Shopify fetches by URL
    #    server-side; the file appears in the shop's Files collection and
    #    a permanent CDN URL is assigned.
    file_create_q = """
      mutation FileCreate($files: [FileCreateInput!]!) {
        fileCreate(files: $files) {
          files { id alt fileStatus }
          userErrors { field message }
        }
      }
    """
    alt = (pet_name or "Pet portrait").strip()[:80]
    try:
        file_resp = _shopify_admin_graphql(file_create_q, {
            "files": [{
                "alt": alt,
                "contentType": "IMAGE",
                "originalSource": image_url,
            }],
        })
    except Exception as exc:
        log.warning("[gallery] order=%s fileCreate transport failed: %s", order_id, exc)
        return False, f"fileCreate transport failed: {exc}"

    file_data = ((file_resp.get("data") or {}).get("fileCreate") or {})
    user_errors = file_data.get("userErrors") or []
    if user_errors:
        return False, f"fileCreate userErrors: {user_errors}"
    files = file_data.get("files") or []
    if not files:
        return False, "fileCreate returned no files"
    file_id = files[0].get("id")
    if not file_id:
        return False, "fileCreate returned no file id"

    # 3. Upsert the metaobject. metaobjectUpsert handles the
    #    create-or-update-by-handle in one call, which is what we want for
    #    idempotency without a separate read-then-write.
    upsert_q = """
      mutation MetaobjectUpsert(
        $handle: MetaobjectHandleInput!,
        $metaobject: MetaobjectUpsertInput!
      ) {
        metaobjectUpsert(handle: $handle, metaobject: $metaobject) {
          metaobject { id handle type }
          userErrors { field message }
        }
      }
    """
    from datetime import datetime as _dt, timezone as _tz
    submitted_at = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        upsert_resp = _shopify_admin_graphql(upsert_q, {
            "handle": {"type": "gallery_submission", "handle": handle},
            "metaobject": {
                "capabilities": {"publishable": {"status": "ACTIVE"}},
                "fields": [
                    {"key": "image", "value": file_id},
                    {"key": "pet_name", "value": (pet_name or "")[:60]},
                    {"key": "category", "value": "dogs"},
                    {"key": "order_id", "value": str(order_id)},
                    {"key": "submitted_at", "value": submitted_at},
                ],
            },
        })
    except Exception as exc:
        return False, f"metaobjectUpsert transport failed: {exc}"

    upsert_data = ((upsert_resp.get("data") or {}).get("metaobjectUpsert") or {})
    user_errors = upsert_data.get("userErrors") or []
    if user_errors:
        return False, f"metaobjectUpsert userErrors: {user_errors}"
    if not upsert_data.get("metaobject"):
        return False, "metaobjectUpsert returned no metaobject"
    return True, "ok"


@app.route("/gallery/submit/<token>")
def gallery_submit(token):
    """Land the customer in the public gallery in one click from the email.
    Verifies the HMAC token, uploads the preview image to Shopify Files,
    upserts a gallery_submission metaobject, then 302s to the thank-you page.

    On any failure, still redirects to the thank-you page with ?status=error
    so the customer doesn't hit a stack trace — and we log the detail. The
    gallery section reads `shop.metaobjects.gallery_submission.values` and
    renders new submissions automatically."""
    from flask import redirect

    storefront = os.environ.get("STOREFRONT_BASE_URL", "https://petprintables.ca").rstrip("/")
    thanks_url = f"{storefront}/pages/gallery-thanks"

    ip = _client_ip()
    allowed, _ = check_rate_limit(ip, "gallery")
    if not allowed:
        return "Too many requests", 429

    payload = verify_gallery_token(token)
    if not payload:
        log.info("[gallery] token rejected (expired or invalid) ip=%s", ip)
        return redirect(f"{thanks_url}?status=expired", code=302)

    ok, detail = _submit_to_gallery(
        payload["r2_key"], payload["pet_name"], payload["order_id"],
    )
    if ok:
        log.info(
            "[gallery] order=%s pet=%s ✓ %s",
            payload["order_id"], payload["pet_name"] or "(unknown)", detail,
        )
        return redirect(thanks_url, code=302)
    log.warning(
        "[gallery] order=%s pet=%s ✗ %s",
        payload["order_id"], payload["pet_name"] or "(unknown)", detail,
    )
    return redirect(f"{thanks_url}?status=error", code=302)


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
    background_mode = (data.get("background_mode") or "auto").strip().lower()
    from generate import MODERN_BG_COLORS, POSTER_PALETTES
    if background_mode not in ("auto", "light", "dark",
                               *MODERN_BG_COLORS.keys(),
                               *POSTER_PALETTES.keys()):
        background_mode = "auto"

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

    # STRUCTURAL DOUBLE-NAME GUARD — every named file we save has
    # `_named` in its filename (see generate_with_name_on_demand:
    # f"{uid}_{style}_{safe_name}_named.png"). If a client sends a URL
    # whose path contains _named, they're trying to use an already-
    # composited file as the "no-name source", which would double the
    # name and produce JEWILDER / ROOGEER ghosts. Refuse with a clear
    # error rather than silently doubling.
    if "_named" in image_url.lower():
        log.warning(
            "[/add-name] rejected request with already-named source URL: %s",
            image_url[:120],
        )
        return jsonify(
            error="Source image already contains a name. Please regenerate the portrait first.",
            code="named_source_rejected",
        ), 400

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

        (
            comp_path, web_path,
            comp_path_3x4, web_path_3x4,
            comp_path_1x1, web_path_1x1,
        ) = generate_with_name_on_demand(
            no_name_image_bytes=no_name_bytes,
            pet_name=pet_name,
            style=style,
            background_mode=background_mode,
        )

        # Upload all six (4:5 / 3:4 / 1:1 named PNG + watermarked WebP
        # preview each) to R2 in parallel. The PNGs are the per-aspect
        # print files Printful fetches at fulfillment time; the WebPs
        # are the diagonal-watermarked previews shown on PDP.
        comp_fut = _fulfillment_pool.submit(upload_portrait, comp_path)
        web_fut = _fulfillment_pool.submit(upload_portrait, web_path)
        comp_3x4_fut = _fulfillment_pool.submit(upload_portrait, comp_path_3x4)
        web_3x4_fut = _fulfillment_pool.submit(upload_portrait, web_path_3x4)
        comp_1x1_fut = _fulfillment_pool.submit(upload_portrait, comp_path_1x1)
        web_1x1_fut = _fulfillment_pool.submit(upload_portrait, web_path_1x1)
        comp_cdn = comp_fut.result()
        web_cdn = web_fut.result()
        comp_3x4_cdn = comp_3x4_fut.result()
        web_3x4_cdn = web_3x4_fut.result()
        comp_1x1_cdn = comp_1x1_fut.result()
        web_1x1_cdn = web_1x1_fut.result()

        return jsonify(
            composited=web_cdn or f"/preview/{web_path.name}",
            composited_png_cdn=comp_cdn or f"/preview/{comp_path.name}",
            composited_png_3x4_cdn=comp_3x4_cdn or f"/preview/{comp_path_3x4.name}",
            composited_3x4_preview=web_3x4_cdn or f"/preview/{web_path_3x4.name}",
            composited_png_1x1_cdn=comp_1x1_cdn or f"/preview/{comp_path_1x1.name}",
            composited_1x1_preview=web_1x1_cdn or f"/preview/{web_path_1x1.name}",
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
    image_urls_by_aspect_raw = data.get("image_urls_by_aspect")

    # Shopify product-handle aliases → backend catalog keys. The PDP
    # injection script reads productHandle straight from the URL path
    # (e.g. /products/framed-canvas → "framed-canvas"), but mockups.py's
    # CATALOG_PRODUCTS keys use a different word order ("canvas-framed")
    # so the fulfillment-side product_key naming stays consistent
    # ("canvas-12x16-framed", etc.). Without this normalisation the
    # /mockups call from a framed PDP 400s — silently breaking framed-
    # canvas mockups while gallery-wrap continues to work.
    _PRODUCT_TYPE_ALIASES = {
        "framed-canvas": "canvas-framed",
    }
    product_type = _PRODUCT_TYPE_ALIASES.get(product_type, product_type)

    # Validate product_type is a simple handle. Allowlist mirrors
    # CATALOG_PRODUCTS keys in mockups.py + legacy "poster" / "mug"
    # values that still flow through this endpoint from older theme
    # builds (generate_mockups silently no-ops on unknown types).
    if product_type not in ("canvas", "canvas-framed", "poster", "mug"):
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

    # Per-aspect URLs: each value must pass the same SSRF guard as
    # image_url. Keys are the aspect strings ('1:1', '3:4', '4:5'); we
    # don't enforce the key set strictly — generate_mockups silently
    # ignores aspects it doesn't recognise.
    image_urls_by_aspect: dict[str, str] = {}
    if image_urls_by_aspect_raw is not None:
        if not isinstance(image_urls_by_aspect_raw, dict) or len(image_urls_by_aspect_raw) > 6:
            return jsonify(error="Invalid image_urls_by_aspect."), 400
        for k, v in image_urls_by_aspect_raw.items():
            if not isinstance(k, str) or len(k) > 8:
                return jsonify(error="Invalid image_urls_by_aspect key."), 400
            if not isinstance(v, str) or len(v) > 1000:
                return jsonify(error="Invalid image_urls_by_aspect value."), 400
            if not validate_url(v, allowed_hosts=_SAFE_IMAGE_HOSTS):
                return jsonify(error="Invalid image_urls_by_aspect URL."), 400
            image_urls_by_aspect[k] = v

    if not image_filename and not image_url and not image_urls_by_aspect:
        return jsonify(error="image_filename, image_url, or image_urls_by_aspect required"), 400

    # If no CDN URL provided, verify local file exists
    if not image_url and not image_urls_by_aspect:
        path = OUTPUT_DIR / Path(image_filename).name
        if not path.exists():
            return jsonify(error="Image not found"), 404

    try:
        results = generate_mockups(
            image_filename=image_filename,
            product_type=product_type,
            image_url=image_url or None,
            variants=variants,
            image_urls_by_aspect=image_urls_by_aspect or None,
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

    # Tag the order in Shopify with the chosen styles + product types.
    # Runs in the background so webhook stays well inside the 5s budget.
    # Fulfillment continues whether or not tagging succeeds.
    order_tags = tags_from_order_items(items)
    if order_tags:
        _fulfillment_pool.submit(tag_shopify_order, order_id, order_tags)

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

    if items:
        # ONE fulfillment task per order — bundles all line items into a
        # single Printful order. Per-item dispatch caused duplicate
        # external_id collisions where only one item survived.
        _fulfillment_pool.submit(
            _process_fulfillment, order_id, items, recipient,
        )

    # Push order metadata to Klaviyo as profile properties so the
    # transactional emails (Order Confirmation, Gift Download) can
    # personalise without parsing line-item-property arrays in Liquid.
    # Also signs a 24h download token for the gift email's CTA.
    customer_email = order.get("email") or recipient.get("email")
    if customer_email and items:
        _fulfillment_pool.submit(
            _push_klaviyo_order_event, customer_email, order_id, items, order,
        )

    return jsonify(status="accepted", items=len(items)), 200


def _join_names(names: list[str]) -> str:
    """Format a list of pet names as 'Luna', 'Luna and Milo', or
    'Luna, Milo, and Buddy' for use in email copy."""
    cleaned = [n for n in (s.strip() for s in names) if n]
    if not cleaned:
        return "your pets"
    if len(cleaned) == 1:
        return cleaned[0]
    if len(cleaned) == 2:
        return f"{cleaned[0]} and {cleaned[1]}"
    return ", ".join(cleaned[:-1]) + f", and {cleaned[-1]}"


def _push_klaviyo_order_event(
    customer_email: str,
    order_id: str,
    items: list,
    order: dict,
) -> None:
    """POST to Klaviyo's Profile API to set download_url + portrait properties
    on the customer's profile, then track an 'Order Portrait Ready' event the
    flows can trigger off if you want a fully decoupled trigger.

    Fails open — if Klaviyo is unreachable or the API key isn't configured,
    fulfillment continues regardless (this is a notification enhancement,
    not a blocker)."""
    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    if not api_key:
        # Bump to warning so it shows up clearly in Railway logs — this is
        # the most common cause of the "gift email shows the placeholder
        # image instead of the customer's pet" symptom, since no profile
        # properties get pushed without the API key.
        log.warning(
            "[klaviyo] order=%s ✗ no KLAVIYO_API_KEY set — skipping profile push "
            "(this is why preview emails will show the fallback image)",
            order_id,
        )
        return
    try:
        from datetime import datetime, timezone, timedelta
        from social_variants import generate_social_variants
        import hashlib as _hashlib

        SOCIAL_TTL_S = 30 * 86400  # 30 days
        backend_base = os.environ.get("PUBLIC_BASE_URL", "https://api.petprintables.ca").rstrip("/")
        shop_base = os.environ.get(
            "SHOPIFY_STOREFRONT_URL", "https://petprintables.ca",
        ).rstrip("/")
        order_token = (order.get("token") or "").strip()
        order_number = str(order.get("order_number") or order.get("name") or order_id)

        # Dedupe items into unique portraits keyed by (pet, style, preview_url,
        # show_name). Same portrait on two products (canvas + magnet) collapses
        # into one set of downloads.
        unique: dict[tuple, dict] = {}
        order_unique = []
        for it in items:
            preview_url = (it.get("preview_url") or "").strip()
            if not preview_url:
                continue
            pet_name = (it.get("pet_name") or "").strip() or "your pet"
            style = (it.get("style") or "").strip()
            show_name_lc = ((it.get("show_name") or "Yes").strip().lower())
            key = (pet_name.lower(), style.lower(), preview_url, show_name_lc)
            if key in unique:
                continue
            portrait_id = _hashlib.sha1(
                f"{pet_name}|{style}|{preview_url}|{show_name_lc}".encode()
            ).hexdigest()[:12]
            unique[key] = {
                "id": portrait_id,
                "pet_name": pet_name,
                "style": style,
                "show_name": "No" if show_name_lc == "no" else "Yes",
                "preview_url": preview_url,
                "_print_4x5": (
                    it.get("no_name_url") if show_name_lc == "no" and it.get("no_name_url")
                    else (it.get("print_file_url") or "")
                ),
                "_print_1x1": (
                    it.get("no_name_url_1x1") if show_name_lc == "no" and it.get("no_name_url_1x1")
                    else (it.get("print_file_url_1x1") or "")
                ),
            }
            order_unique.append(unique[key])

        if not order_unique:
            log.warning("[klaviyo] order=%s no unique portraits to publish", order_id)
            return

        # For each unique portrait, sign download tokens and generate social
        # variants under a per-portrait subdir so multi-portrait orders don't
        # overwrite each other in R2.
        portraits_payload = []
        for p in order_unique:
            preview_url = p["preview_url"]
            preview_r2_key = _r2_key_from_url(preview_url) or preview_url.split("/")[-1]
            preview_token = make_download_token(preview_r2_key, ttl_seconds=SOCIAL_TTL_S)
            original_url = f"{backend_base}/portrait/download/{preview_token}"

            gallery_token = make_gallery_token(
                preview_r2_key, p["pet_name"], str(order_id),
                ttl_seconds=SOCIAL_TTL_S,
            )
            gallery_submit_url = f"{backend_base}/gallery/submit/{gallery_token}"

            downloads: dict[str, str] = {}
            try:
                social_keys = generate_social_variants(
                    p["_print_4x5"], p["_print_1x1"], order_id,
                    subdir=p["id"],
                )
                for variant_name, r2_key in social_keys.items():
                    tok = make_download_token(r2_key, ttl_seconds=SOCIAL_TTL_S)
                    downloads[variant_name] = f"{backend_base}/portrait/download/{tok}"
            except Exception as exc:
                log.warning(
                    "[klaviyo] order=%s portrait=%s social variant gen failed: %s",
                    order_id, p["id"], exc,
                )

            portraits_payload.append({
                "id": p["id"],
                "pet_name": p["pet_name"],
                "style": p["style"],
                "show_name": p["show_name"],
                "preview_url": preview_url,
                "original_url": original_url,
                "gallery_submit_url": gallery_submit_url,
                "downloads": downloads,
            })

        social_expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        digital_files_record = {
            "version": 1,
            "order_id": str(order_id),
            "order_number": order_number,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expires_at": social_expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "portraits": portraits_payload,
        }

        # Persist canonical record in TWO places:
        # 1. Local Redis store (keyed by order_id) — read by the Flask
        #    /portraits/<token> route to render the digital-gift page
        #    without requiring Shopify customer-account login. This is
        #    the PRIMARY surface customers hit from the email.
        # 2. Shopify order metafield — for customers who DO log into
        #    a (legacy) customer account and look at the order detail
        #    page. Optional/redundant given the Flask page works for
        #    everyone, but harmless to keep.
        try:
            set_order_record(str(order_id), digital_files_record)
        except Exception as exc:
            log.warning(
                "[klaviyo] order=%s order-record write failed: %s",
                order_id, exc,
            )
        try:
            set_order_metafield(
                str(order_id), "petprintables", "digital_files",
                digital_files_record, value_type="json",
            )
        except Exception as exc:
            log.warning(
                "[klaviyo] order=%s metafield write failed: %s",
                order_id, exc,
            )

        # Klaviyo payload — minimal. The email's single CTA points at the
        # Flask /portraits/<token> page (NOT the Shopify customer account)
        # because Shopify's New Customer Accounts UI doesn't load theme
        # Liquid, and we can't render our digital files block there. The
        # token is HMAC-signed with a 90-day TTL so the link works for the
        # life of the social variants.
        first_p = portraits_payload[0]
        order_token_signed = make_order_token(str(order_id), ttl_seconds=SOCIAL_TTL_S)
        order_url = f"{backend_base}/portraits/{order_token_signed}"
        properties = {
            "pet_name": first_p["pet_name"],
            "pet_preview_url": first_p["preview_url"],
            "portrait_count": len(portraits_payload),
            "order_url": order_url,
            "last_order_id": str(order_id),
            "last_order_number": order_number,
        }
        if len(portraits_payload) > 1:
            properties["all_preview_urls"] = [p["preview_url"] for p in portraits_payload]
            properties["pet_names_text"] = _join_names(
                [p["pet_name"] for p in portraits_payload]
            )

        body = {
            "data": {
                "type": "profile",
                "attributes": {
                    "email": customer_email,
                    "properties": properties,
                },
                "meta": {"patch_properties": {"append": {}}},
            }
        }
        resp = _req.post(
            "https://a.klaviyo.com/api/profile-import",
            headers={
                "Authorization": f"Klaviyo-API-Key {api_key}",
                "revision": "2024-10-15",
                "Content-Type": "application/json",
                "accept": "application/json",
            },
            json=body,
            timeout=8,
        )
        if resp.status_code >= 400:
            log.warning(
                "[klaviyo] order=%s ✗ profile push FAILED status=%s body=%s",
                order_id, resp.status_code, resp.text[:300],
            )
        else:
            # Single-line success log with the full property set so you can
            # grep "[klaviyo] order=12345 ✓" and verify pet_preview_url +
            # the social variant tokens all made it onto the profile.
            preview_short = (
                preview_url[:64] + "…" if len(preview_url) > 64 else preview_url
            )
            log.info(
                "[klaviyo] order=%s ✓ profile push OK email=%s pet=%s "
                "preview=%s | %d properties: %s",
                order_id, customer_email, pet_name or "(unknown)",
                preview_short or "(none)",
                len(properties), ", ".join(properties.keys()),
            )
    except Exception as exc:
        log.warning(
            "[klaviyo] order=%s ✗ profile push EXCEPTION: %s",
            order_id, exc,
        )


# ---------------------------------------------------------------------------
# Admin — verify a Shopify order's print files reached Printful intact
# ---------------------------------------------------------------------------

def _head_ok(url: str) -> bool:
    """HEAD the URL with a tight timeout. Used to confirm Printful can fetch
    the print file we handed it. Returns False on any non-2xx or network error."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        r = _req.head(url, timeout=5, allow_redirects=True)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def _r2_key_from_url(url: str) -> Optional[str]:
    """Strip the R2_PUBLIC_URL prefix to recover the object key, mirroring
    the logic in _process_fulfillment."""
    if not url:
        return None
    r2_public = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if r2_public and url.startswith(r2_public + "/"):
        return url[len(r2_public) + 1:]
    return None


@app.route("/admin/verify-order/<shopify_id>")
def admin_verify_order(shopify_id):
    """Cross-check a Shopify order against Printful: returns the cart's
    print-file URL, the file URL Printful actually received, and reachability
    flags so you can confirm the customer's generated portrait — not a
    placeholder — was sent for printing.

    Auth: X-Admin-Token header or ?admin_token=… query param.
    """
    if not _require_debug_token():
        return jsonify(error="Forbidden"), 403

    warnings: list[str] = []

    # ── 1. Pull the Shopify order so we can re-parse line items ──────────
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()
    if not domain or not token:
        return jsonify(error="SHOPIFY_SHOP_DOMAIN or SHOPIFY_ADMIN_API_TOKEN not set"), 500

    shopify_url = f"https://{domain}/admin/api/{SHOPIFY_ADMIN_API_VERSION}/orders/{shopify_id}.json"
    try:
        sr = _req.get(
            shopify_url,
            headers={"X-Shopify-Access-Token": token, "Accept": "application/json"},
            timeout=10,
        )
    except Exception:
        log.exception("verify-order: Shopify fetch failed for %s", shopify_id)
        return jsonify(error="Shopify request failed"), 502

    if sr.status_code == 404:
        return jsonify(error="Shopify order not found", shopify_order_id=shopify_id), 404
    if sr.status_code != 200:
        return jsonify(
            error="Shopify lookup failed",
            status=sr.status_code,
            body=sr.text[:300],
        ), 502

    order = sr.json().get("order") or {}
    parsed_items = parse_order_items(order)

    cart_items = []
    for it in parsed_items:
        cart_url = it.get("print_file_url") or it.get("preview_url") or ""
        cart_items.append({
            "pet_name": it.get("pet_name"),
            "style": it.get("style"),
            "product_type": it.get("product_type"),
            "size": it.get("size"),
            "job_id": it.get("job_id"),
            "cart_print_file_url": cart_url,
            "cart_print_file_reachable": _head_ok(cart_url),
            "source_r2_key": _r2_key_from_url(cart_url),
        })

    if not cart_items:
        warnings.append("Shopify order has no portrait line items (nothing was dispatched to Printful).")

    # ── 2. Pull the matching Printful order by external_id ───────────────
    printful_block: dict = {"found": False}
    try:
        pr = _req.get(
            f"{PRINTFUL_BASE}/orders/@{shopify_id}",
            headers=_printful_headers(),
            timeout=15,
        )
    except RuntimeError as e:
        # Missing PRINTFUL_API_KEY — _printful_headers raises this.
        return jsonify(error=str(e)), 500
    except Exception:
        log.exception("verify-order: Printful fetch failed for %s", shopify_id)
        return jsonify(error="Printful request failed"), 502

    if pr.status_code == 404:
        warnings.append(
            "Printful has no order with this external_id yet. "
            "If checkout just happened, fulfillment may still be running."
        )
    elif pr.status_code != 200:
        warnings.append(f"Printful lookup returned HTTP {pr.status_code}: {pr.text[:200]}")
    else:
        pf = pr.json().get("result") or {}
        pf_items = []
        for pi in pf.get("items", []):
            files_out = []
            for f in pi.get("files", []):
                furl = f.get("url") or ""
                files_out.append({
                    "type": f.get("type"),
                    "url": furl,
                    "preview_url": f.get("preview_url"),
                    "filename": f.get("filename"),
                    "url_reachable": _head_ok(furl),
                    "is_our_r2": bool(_r2_key_from_url(furl)),
                })
            pf_items.append({
                "variant_id": pi.get("variant_id"),
                "name": pi.get("name"),
                "quantity": pi.get("quantity"),
                "files": files_out,
            })

        printful_block = {
            "found": True,
            "order_id": pf.get("id"),
            "external_id": pf.get("external_id"),
            "status": pf.get("status"),
            "items": pf_items,
        }

    # ── 3. Roll-up checks ────────────────────────────────────────────────
    pf_files = [
        f
        for it in printful_block.get("items", [])
        for f in it.get("files", [])
    ]
    all_reachable = bool(pf_files) and all(f["url_reachable"] for f in pf_files)
    all_our_r2 = bool(pf_files) and all(f["is_our_r2"] for f in pf_files)
    no_local_fallback = not any("/preview/" in (f.get("url") or "") for f in pf_files)

    if printful_block["found"] and not all_our_r2:
        warnings.append(
            "One or more Printful files are NOT served from our R2 bucket — "
            "this is the fallback path and may indicate the upload failed."
        )
    if printful_block["found"] and not all_reachable:
        warnings.append(
            "One or more Printful files returned non-2xx on HEAD — "
            "Printful may not be able to fetch them for printing."
        )
    if printful_block["found"] and not no_local_fallback:
        warnings.append(
            "A file URL contains '/preview/' — that is the local-Flask fallback "
            "from upload_print_file() and is unreachable from Printful."
        )

    return jsonify(
        shopify_order_id=shopify_id,
        shopify={
            "name": order.get("name"),
            "email": order.get("email"),
            "items": cart_items,
        },
        printful=printful_block,
        checks={
            "shopify_lookup_ok": True,
            "printful_lookup_ok": printful_block["found"],
            "all_printful_files_reachable": all_reachable,
            "all_printful_files_from_our_r2": all_our_r2,
            "no_local_fallback_urls": no_local_fallback,
        },
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Admin — per-style smoke test (no Printful submission)
# ---------------------------------------------------------------------------

@app.route("/admin/style-smoke-test/<style_id>")
def admin_style_smoke_test(style_id: str):
    """Run the full pre-Printful pipeline for a single style and return a
    JSON report. Validates that:
      - the prompt template + Gemini integration produce a valid image
      - the print-file pipeline produces the right pixel dimensions and
        300 DPI metadata for each requested product (canvas + magnet)
      - the SAME composited source is reused across multiple variants —
        the magnet-upsell single-source invariant
      - all uploaded URLs are reachable from the public web (R2-served)

    Does NOT submit to Printful. Hits Gemini once per call (~$0.04) plus
    one upscale per product key.

    Auth: X-Admin-Token header or ?admin_token=… query param.

    Path:
      /admin/style-smoke-test/<style_id>     React-side ID, e.g. 'modern-shape-art'

    Query params:
      products    comma-separated product keys (default: canvas-16x20,magnet-4x4)
      photo       filename in test_photos/    (default: buddy.png)
      pet_name    name to composite           (default: Buddy)
    """
    if not _require_debug_token():
        return jsonify(error="Forbidden"), 403

    import time
    from PIL import Image as _Image
    from generate import PROMPTS, generate as generate_portrait
    from fulfillment import (
        PRINT_SIZES, PRODUCT_RATIOS,
        generate_print_file, upload_print_file, _map_style_id,
    )
    from storage import upload_portrait

    # Validate style — accept React-side ID (preferred) or harness key.
    harness_style = _map_style_id(style_id)
    if harness_style == "classic" and style_id != "classic":
        return jsonify(
            error=f"Unknown style: {style_id}",
            valid_react_styles=[
                "soft-watercolour", "minimal-line-art", "modern-shape-art",
                "neon-pop-art", "renaissance-royalty",
                "bold-graphic-poster", "aura-gradient", "charcoal",
            ],
        ), 400

    # Parse + validate product keys.
    requested = (request.args.get("products") or "canvas-16x20,magnet-4x4").split(",")
    requested = [p.strip() for p in requested if p.strip()]
    invalid = [p for p in requested if p not in PRINT_SIZES]
    if invalid:
        return jsonify(
            error=f"Unknown product keys: {invalid}",
            valid_products=sorted(PRINT_SIZES.keys()),
        ), 400

    # Resolve test photo.
    photo_name = request.args.get("photo", "buddy.png")
    photo_path = Path("test_photos") / photo_name
    if not photo_path.exists():
        return jsonify(error=f"Test photo not found: test_photos/{photo_name}"), 400

    pet_name = (request.args.get("pet_name") or "Buddy").strip()[:20] or "Buddy"

    started = time.time()
    warnings_out: list[str] = []

    # ── Step 1: generate the no-name composited via the production path ──
    try:
        _gen_paths = generate_portrait(
            photo_path, pet_name, style=harness_style,
            style_vars=None, background_mode="auto",
        )
        _raw_path, comp_path, web_path = _gen_paths[0], _gen_paths[1], _gen_paths[2]
    except Exception as e:
        log.exception("smoke-test: portrait generation failed for style=%s", style_id)
        return jsonify(error=f"Portrait generation failed: {e}"), 500

    # Upload composited PNG to R2 — this is the source the print-file
    # pipeline reuses for every product key.
    ts = int(time.time())
    composited_key = f"smoke-test/{ts}/{harness_style}/composited.png"
    composited_url = upload_portrait(comp_path, key=composited_key)
    if not composited_url:
        warnings_out.append(
            "R2 upload of composited image failed — print files will fall back "
            "to re-generating via Gemini, which is not what production does."
        )
        composited_key = None  # don't pass an unreachable key to print-file gen

    # Web preview URL (the WebP shown in cart / order confirmation).
    preview_key = f"smoke-test/{ts}/{harness_style}/preview.webp"
    preview_url = upload_portrait(web_path, key=preview_key)

    # ── Step 2: build a print file per product key, all from one source ──
    print_results: list[dict] = []
    sources_used: set[str] = set()

    for pk in requested:
        try:
            print_path = generate_print_file(
                photo_path=photo_path,
                pet_name=pet_name,
                style=style_id,  # React-side ID; fulfillment maps it internally
                product_key=pk,
                style_vars=None,
                composited_r2_key=composited_key,
                font_size="small",
                show_name="Yes",
            )
            sources_used.add(composited_key or "<no-r2>")

            with _Image.open(print_path) as pim:
                actual_w, actual_h = pim.size
                actual_dpi = pim.info.get("dpi", (None, None))
                actual_format = pim.format

            print_url = upload_print_file(print_path)

            expected_w, expected_h = PRINT_SIZES[pk]
            expected_ratio_w, expected_ratio_h = PRODUCT_RATIOS[pk]

            print_results.append({
                "product_key": pk,
                "expected_px": [expected_w, expected_h],
                "actual_px": [actual_w, actual_h],
                "expected_ratio": f"{expected_ratio_w}:{expected_ratio_h}",
                "expected_dpi": 300,
                "actual_dpi": list(actual_dpi) if actual_dpi[0] else None,
                "format": actual_format,
                "size_bytes": print_path.stat().st_size,
                "url": print_url,
                "url_reachable": _head_ok(print_url) if print_url else False,
                "served_from_r2": bool(_r2_key_from_url(print_url)) if print_url else False,
                "dimensions_match": (actual_w, actual_h) == (expected_w, expected_h),
                # PIL stores DPI as float (300 may read back as 299.9994).
                # Round before comparing to avoid spurious failures.
                "dpi_match": bool(
                    actual_dpi[0]
                    and round(actual_dpi[0]) == 300
                    and round(actual_dpi[1]) == 300
                ),
            })
        except Exception as e:
            log.exception("smoke-test: print-file failed for style=%s product=%s", style_id, pk)
            print_results.append({"product_key": pk, "error": str(e)})

    # ── Step 3: roll-up checks ──
    successful = [r for r in print_results if "error" not in r]
    all_dim = bool(successful) and all(r["dimensions_match"] for r in successful)
    all_dpi = bool(successful) and all(r["dpi_match"] for r in successful)
    all_reachable = bool(successful) and all(r["url_reachable"] for r in successful)
    all_r2 = bool(successful) and all(r["served_from_r2"] for r in successful)
    single_source = len(sources_used) == 1 and "<no-r2>" not in sources_used

    if successful and not all_dim:
        warnings_out.append("One or more print files have wrong pixel dimensions vs PRINT_SIZES")
    if successful and not all_dpi:
        warnings_out.append("One or more print files lack 300 DPI metadata — Printful may scale wrong")
    if successful and not all_reachable:
        warnings_out.append("One or more print file URLs are not reachable on HEAD")
    if successful and not all_r2:
        warnings_out.append("One or more print files are not served from our R2 bucket")
    if len(requested) > 1 and not single_source:
        warnings_out.append(
            "Print files used different source images — magnet-upsell single-source "
            "invariant broken (canvas + magnet should reuse one composited image)"
        )
    if any("error" in r for r in print_results):
        warnings_out.append("One or more product keys failed during print-file generation")

    return jsonify(
        style=style_id,
        harness_style=harness_style,
        test_photo=photo_name,
        pet_name=pet_name,
        preview_url=preview_url,
        preview_url_reachable=_head_ok(preview_url) if preview_url else False,
        composited_source_r2_key=composited_key,
        print_files=print_results,
        checks={
            "all_dimensions_match": all_dim,
            "all_dpi_match": all_dpi,
            "all_print_files_reachable": all_reachable,
            "all_print_files_from_our_r2": all_r2,
            "single_source_used_for_all_products": single_source,
        },
        warnings=warnings_out,
        elapsed_seconds=round(time.time() - started, 2),
    )


def _process_fulfillment(order_id: str, items: list, recipient: dict):
    """
    Background task: process all line items in a Shopify order as a SINGLE
    Printful order.

    Items sharing the same creative (pet, style, show-name choice, source
    image, aspect ratio) reuse a single hi-res print file — so a canvas
    12x12 + magnet 4x4 of the same portrait generates one upscaled file
    instead of two redundant Gemini round-trips.
    """
    if not items:
        return

    try:
        r2_public = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
        photo_path_cache: dict[str, Path] = {}  # preview_url -> downloaded path
        creative_print_url: dict[tuple, str] = {}  # creative_key -> R2 print URL

        def _resolve_photo(preview_url: str) -> Optional[Path]:
            """Cached: download preview to local path (Gemini fallback source)."""
            if not preview_url:
                return None
            if preview_url in photo_path_cache:
                return photo_path_cache[preview_url]
            if preview_url.startswith("/"):
                p = OUTPUT_DIR / Path(preview_url).name
            else:
                import tempfile
                import requests as req
                resp = req.get(preview_url, timeout=30)
                resp.raise_for_status()
                tmp = Path(tempfile.mktemp(suffix=".png", dir="uploads"))
                tmp.write_bytes(resp.content)
                p = tmp
            if not p.exists():
                return None
            photo_path_cache[preview_url] = p
            return p

        # Pass 1 — validate and group items by creative key.
        # creative_key = (pet_name, style, show_name, source_url, aspect_ratio).
        # Items in the same group share a print file generated at the
        # largest item's size.
        from collections import defaultdict
        groups: dict[tuple, list[tuple[dict, str]]] = defaultdict(list)

        def _pick_source_url(item: dict, aspect: tuple, show_name_lc: str) -> str:
            """Pick the pre-rendered print URL whose aspect matches the
            target product front face. 1:1 variants prefer the 1×1
            derivative, 3:4 variants prefer the 3×4 derivative, 4:5
            (and any other) variants use the 4:5 master. Falls back to
            the 4:5 fields if the requested per-aspect derivative isn't
            present (orders placed before per-aspect generation
            existed)."""
            no_name = show_name_lc == "no"
            if aspect == (1, 1):
                primary = (
                    item.get("no_name_url_1x1") if no_name else item.get("print_file_url_1x1")
                ) or ""
                if primary:
                    return primary
            if aspect == (3, 4):
                primary = (
                    item.get("no_name_url_3x4") if no_name else item.get("print_file_url_3x4")
                ) or ""
                if primary:
                    return primary
            return (
                item.get("no_name_url") if no_name and item.get("no_name_url")
                else item.get("preview_url", "")
            )

        for item in items:
            try:
                product_key = build_product_key(item["product_type"], item["size"])
            except Exception:
                log.exception("Order #%s — could not build product_key for item %s", order_id, item)
                continue
            if product_key not in PRINT_SIZES:
                log.error("Order #%s — unknown product_key %s, skipping", order_id, product_key)
                continue

            aspect = PRODUCT_RATIOS[product_key]
            show_name = (item.get("show_name") or "Yes").strip()
            source_url = _pick_source_url(item, aspect, show_name.lower())
            creative_key = (
                item["pet_name"],
                item["style"],
                show_name.lower(),
                source_url,
                aspect,
            )
            groups[creative_key].append((item, product_key))

        if not groups:
            log.error("Order #%s — no fulfillable items after parsing", order_id)
            return

        # Pass 2 — for each creative group, generate ONE print file at the
        # largest size needed in the group, then reuse that URL for every
        # item in the group.
        for creative_key, group in groups.items():
            pet_name, style, show_name_lc, source_url, _aspect = creative_key

            # Pick the largest product_key in this group (max pixel area).
            largest_item, largest_product_key = max(
                group,
                key=lambda gi: PRINT_SIZES[gi[1]][0] * PRINT_SIZES[gi[1]][1],
            )

            photo_path = _resolve_photo(largest_item.get("preview_url", ""))
            if not photo_path:
                log.error("Order #%s — photo unavailable for creative %s", order_id, creative_key[:3])
                continue

            composited_r2_key = None
            if r2_public and source_url.startswith(r2_public):
                composited_r2_key = source_url[len(r2_public) + 1:]

            print_path = generate_print_file(
                photo_path=photo_path,
                pet_name=pet_name,
                style=style,
                product_key=largest_product_key,
                composited_r2_key=composited_r2_key,
                show_name="No" if show_name_lc == "no" else "Yes",
            )
            print_url = upload_print_file(print_path)
            creative_print_url[creative_key] = print_url
            log.info(
                "Order #%s — print file ready for creative %s/%s: %s",
                order_id, pet_name, style, print_url,
            )

        # Pass 3 — assemble Printful items in original Shopify order order.
        pf_items = []
        for item in items:
            try:
                product_key = build_product_key(item["product_type"], item["size"])
            except Exception:
                continue
            if product_key not in PRINT_SIZES:
                continue
            show_name = (item.get("show_name") or "Yes").strip()
            aspect = PRODUCT_RATIOS[product_key]
            source_url = _pick_source_url(item, aspect, show_name.lower())
            creative_key = (
                item["pet_name"],
                item["style"],
                show_name.lower(),
                source_url,
                aspect,
            )
            print_url = creative_print_url.get(creative_key)
            if not print_url:
                # The creative's print file generation failed earlier — skip
                # this item rather than ship an empty Printful entry.
                log.warning("Order #%s — no print file for item %s, skipping", order_id, item)
                continue
            try:
                variant_id = _get_printful_variant_id(product_key)
            except Exception:
                log.exception("Order #%s — variant lookup failed for %s", order_id, product_key)
                continue
            pf_items.append({
                "variant_id": variant_id,
                "quantity": int(item.get("quantity", 1) or 1),
                "print_file_url": print_url,
            })

        if not pf_items:
            log.error("Order #%s — no Printful items to send", order_id)
            return

        result = create_printful_order(
            shopify_order_id=order_id,
            recipient=recipient,
            items=pf_items,
        )
        log.info(
            "Order #%s — Printful order %s created (%d item%s, %d unique print files)",
            order_id,
            result.get("result", {}).get("id", "?"),
            len(pf_items), "" if len(pf_items) == 1 else "s",
            len(creative_print_url),
        )

        tags = _printful_file_tags(result)
        if tags:
            tag_shopify_order(order_id, tags)

    except Exception:
        log.exception("Order #%s — fulfillment failed", order_id)


def _printful_file_tags(printful_response: dict) -> list[str]:
    """Build `printful-file:<hash>` tags from a create_printful_order response.

    Hash priority: the 8-char uuid embedded in our generated filename
    (`print_<product>_<pet>_<uuid8>.png`); if that pattern isn't matched,
    fall back to the first 8 chars of md5(url) so the tag is still stable
    and unique per file."""
    import hashlib as _hashlib
    import re as _re_local

    tags: list[str] = []
    seen: set[str] = set()
    items = (printful_response.get("result") or {}).get("items") or []
    for it in items:
        for f in it.get("files") or []:
            url = f.get("url") or ""
            if not url:
                continue
            stem = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            m = _re_local.search(r"_([0-9a-f]{8})$", stem)
            short = m.group(1) if m else _hashlib.md5(url.encode()).hexdigest()[:8]
            tag = f"printful-file:{short}"
            if tag not in seen:
                seen.add(tag)
                tags.append(tag)
    return tags


# ---------------------------------------------------------------------------
# Background job worker — processes queued portrait generation jobs
# ---------------------------------------------------------------------------

import time as _worker_time

# How many jobs to process concurrently (matches the Gemini semaphore)
_WORKER_THREADS = int(os.environ.get("MAX_CONCURRENT_GENERATIONS", 20))
_worker_pool = _ResilientPool(max_workers=_WORKER_THREADS, thread_name_prefix="gen-worker")


def _process_job(job: dict):
    """Process a single generation job — called by the worker pool."""
    job_id = job["job_id"]
    upload_path = Path(job["upload_path"])

    global _active_generations, _peak_generations
    with _active_lock:
        _active_generations += 1
        if _active_generations > _peak_generations:
            _peak_generations = _active_generations

    # We own the local upload file and must clean it up — unless we hand it
    # off to the deferred-original task below, which takes over cleanup.
    file_owned = True
    try:
        worker_bg = job.get("background_mode") or "auto"
        # variation_seed is stored as "" in the job record when absent
        # (Redis HSET coerces None → ""). Coerce back to None here so
        # generate()'s `if variation_seed is not None` check behaves
        # correctly — passing "" through caused "" % 8 inside generate
        # and surfaced "not all arguments converted during string
        # formatting" to the customer on every first-attempt generate.
        _seed_raw = job.get("variation_seed")
        try:
            worker_seed = int(_seed_raw) if _seed_raw not in (None, "") else None
        except (TypeError, ValueError):
            worker_seed = None
        log.info(
            "[worker] job=%s style=%s bg_mode=%s variation_seed=%s",
            job_id, job["style"], worker_bg, worker_seed,
        )
        (
            raw_path, comp_path, web_path, raw_web_path,
            raw_path_1x1, comp_path_1x1,
            raw_path_3x4, raw_web_path_3x4, raw_web_path_1x1, comp_path_3x4,
        ) = generate(
            str(upload_path),
            job["pet_name"],
            job["style"],
            background_mode=worker_bg,
            variation_seed=worker_seed,
        )

        # Mark the job complete with LOCAL /preview/ URLs the moment
        # the watermarked WebP is on disk — the customer sees their
        # preview ~5–15s sooner than if we waited on CDN. CDN uploads
        # then run in a background task and re-update the job record
        # with R2 URLs (cdn="1") for any consumer that needs them
        # (fulfillment writes happen at add-to-cart, by which time the
        # backfill has typically finished).
        # Field semantics:
        #   raw_preview / composited / *_3x4_preview / *_1x1_preview
        #     → watermarked WebPs for customer UI
        #   raw / composited_png_cdn → un-watermarked 4:5 PNGs
        #   raw_3x4 / composited_png_3x4_cdn → un-watermarked 3:4 PNGs
        #   raw_1x1 / composited_png_1x1_cdn → un-watermarked 1:1 PNGs
        # The composited_* fields are no-name-aliased placeholders until
        # /add-name overwrites them with real with-name files.
        update_job(
            job_id,
            status="complete",
            raw=f"/preview/{raw_path.name}",
            raw_preview=f"/preview/{raw_web_path.name}",
            composited=f"/preview/{web_path.name}",
            composited_png_cdn=f"/preview/{comp_path.name}",
            raw_3x4=f"/preview/{raw_path_3x4.name}",
            composited_3x4_preview=f"/preview/{raw_web_path_3x4.name}",
            composited_png_3x4_cdn=f"/preview/{comp_path_3x4.name}",
            raw_1x1=f"/preview/{raw_path_1x1.name}",
            composited_1x1_preview=f"/preview/{raw_web_path_1x1.name}",
            composited_png_1x1_cdn=f"/preview/{comp_path_1x1.name}",
            download=f"/download/{comp_path.name}",
            filename=comp_path.name,
            cdn="0",
        )
        log.info("Job %s complete (local URLs): %s", job_id, comp_path.name)

        # Background CDN backfill — uniques set collapses with-name
        # placeholders onto their no-name source files (comp_path is
        # raw_path's alias, etc.), so each unique file uploads once.
        unique_paths = {
            raw_path, comp_path, web_path, raw_web_path,
            raw_path_3x4, raw_web_path_3x4, comp_path_3x4,
            raw_path_1x1, raw_web_path_1x1, comp_path_1x1,
        }
        def _backfill_cdn():
            try:
                upload_futures = {
                    p: _fulfillment_pool.submit(upload_portrait, p)
                    for p in unique_paths
                }
                upload_cdn = {p: f.result() for p, f in upload_futures.items()}
                raw_cdn = upload_cdn[raw_path]
                comp_cdn = upload_cdn[comp_path]
                web_cdn = upload_cdn[web_path]
                raw_web_cdn = upload_cdn[raw_web_path]
                raw_3x4_cdn = upload_cdn[raw_path_3x4]
                raw_web_3x4_cdn = upload_cdn[raw_web_path_3x4]
                comp_3x4_cdn = upload_cdn[comp_path_3x4]
                raw_1x1_cdn = upload_cdn[raw_path_1x1]
                raw_web_1x1_cdn = upload_cdn[raw_web_path_1x1]
                comp_1x1_cdn = upload_cdn[comp_path_1x1]
                if not comp_cdn:
                    log.warning("CDN backfill incomplete for job %s — comp_cdn empty", job_id)
                update_job(
                    job_id,
                    raw=raw_cdn or f"/preview/{raw_path.name}",
                    raw_preview=raw_web_cdn or f"/preview/{raw_web_path.name}",
                    composited=web_cdn or f"/preview/{web_path.name}",
                    composited_png_cdn=comp_cdn or f"/preview/{comp_path.name}",
                    raw_3x4=raw_3x4_cdn or f"/preview/{raw_path_3x4.name}",
                    composited_3x4_preview=raw_web_3x4_cdn or f"/preview/{raw_web_path_3x4.name}",
                    composited_png_3x4_cdn=comp_3x4_cdn or f"/preview/{comp_path_3x4.name}",
                    raw_1x1=raw_1x1_cdn or f"/preview/{raw_path_1x1.name}",
                    composited_1x1_preview=raw_web_1x1_cdn or f"/preview/{raw_web_path_1x1.name}",
                    composited_png_1x1_cdn=comp_1x1_cdn or f"/preview/{comp_path_1x1.name}",
                    cdn="1" if comp_cdn else "0",
                )
                log.info("Job %s CDN backfill done", job_id)
            except Exception:
                log.exception("CDN backfill failed for job %s", job_id)
        _fulfillment_pool.submit(_backfill_cdn)

        # Hand off the upload file to a background task: upload to R2 for
        # fulfillment, then delete. Fulfillment reads original_cdn from the
        # job record; if a Printful order arrives before upload finishes,
        # the order handler will see an empty original_cdn and retry later.
        _orig_path = upload_path
        _orig_suffix = upload_path.suffix
        def _defer_original():
            try:
                cdn = upload_portrait(_orig_path, key=f"originals/{job_id}{_orig_suffix}")
                if cdn:
                    update_job(job_id, original_cdn=cdn)
            except Exception:
                log.exception("Deferred original upload failed for job %s", job_id)
            finally:
                _orig_path.unlink(missing_ok=True)
        _fulfillment_pool.submit(_defer_original)
        file_owned = False  # deferred task will clean up

    except Exception as exc:
        log.exception("Job %s failed", job_id)
        update_job(job_id, status="failed", error=_safe_customer_error(exc))
    finally:
        with _active_lock:
            _active_generations -= 1
        if file_owned:
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
