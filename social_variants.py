"""Social-media-optimised variants of customer pet portraits.

Generated at order-completion time and signed with 30-day download
tokens, then surfaced via the "A gift for you" Klaviyo email so the
customer can grab their pet portrait sized for the platform they want
to share on without signing in or fiddling with crops themselves.

Generated formats:
  square    1080×1080  Instagram post / profile pic / FB post
  story     1080×1920  Instagram + FB stories, Reels covers (9:16)
  portrait  1080×1350  Instagram feed (4:5 — the most-engaged IG aspect)
  wallpaper 1170×2532  iPhone lock screen / home screen (~9:19.5)

Source files:
  square      → print_file_url_1x1 (already 1:1, just downscale)
  others      → print_file_url     (4:5 master, downscale then pad
                                    top/bottom with corner-sampled bg
                                    colour for taller aspects)

All variants are JPEG q90 — small enough to download fast on mobile,
high quality enough that social platforms' re-encoding still looks
clean. Files end up at ~150-400 KB each.

Outputs are uploaded to R2 under social/{order_id}/{variant}.jpg.
Pair with a Cloudflare R2 Object Lifecycle Rule with prefix
"social/" set to delete after 30 days to match the token TTL — keeps
storage bounded without breaking customer download links during the
window we promise them.

Failure handling: every variant generation is independently wrapped
in try/except. A failed download or upload skips just that variant
and emits a warning; the other variants still ship. Customer ends
up with however many download buttons rendered successfully — the
Klaviyo template uses {{ person.download_url_*|default:'' }} so a
missing property hides the button rather than shows a broken link.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import Optional

import requests

log = logging.getLogger(__name__)


# Each spec: target dimensions + which print-file aspect to source from.
# source_aspect: "1:1" prefers the per-aspect square derivative; "4:5"
# prefers the master and lets _fit_into_canvas pad top/bottom for the
# story/wallpaper formats which are taller than the source.
SOCIAL_VARIANT_SPECS: dict[str, dict] = {
    "square":    {"size": (1080, 1080), "source": "1:1"},
    "story":     {"size": (1080, 1920), "source": "4:5"},
    "portrait":  {"size": (1080, 1350), "source": "4:5"},
    "wallpaper": {"size": (1170, 2532), "source": "4:5"},
}

JPEG_QUALITY = 90
DOWNLOAD_TIMEOUT_S = 30


def _sample_corner_bg(img) -> tuple[int, int, int]:
    """Average RGB of the four corner regions — same logic used for the
    canvas wrap bleed, so the bg padding band on story/wallpaper variants
    matches the visual tone the customer chose for their portrait.
    """
    rgb = img.convert("RGB") if img.mode != "RGB" else img
    w, h = rgb.size
    corner_size = max(8, min(w, h) // 50)
    pixels: list = []
    for x0, y0 in (
        (0, 0), (w - corner_size, 0),
        (0, h - corner_size), (w - corner_size, h - corner_size),
    ):
        pixels.extend(rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata())
    if not pixels:
        return (255, 255, 255)
    r = sum(p[0] for p in pixels) // len(pixels)
    g = sum(p[1] for p in pixels) // len(pixels)
    b = sum(p[2] for p in pixels) // len(pixels)
    return (r, g, b)


def _fit_into_canvas(img, target_w: int, target_h: int):
    """Resize the source so it fills the target area without cropping
    pet content, then pad any leftover space with sampled corner colour.

    For taller-than-source aspects (story, wallpaper from a 4:5 source),
    the pet ends up centred vertically with bg-colour bands above + below
    — gives stories a calm framing and lets phone wallpapers leave room
    for clock/widget overlays without the pet being cropped.

    For matched aspects (square-from-1:1, portrait-from-4:5) the
    function is just a resize — no padding step kicks in.
    """
    from PIL import Image
    src_w, src_h = img.size
    # Scale so the source fits ENTIRELY inside the target with no cropping
    # of pet content. This is "fit", not "cover" — leftover space gets
    # padded with the sampled bg colour rather than cropped away.
    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)

    if (new_w, new_h) == (target_w, target_h):
        return resized

    bg = _sample_corner_bg(resized)
    canvas = Image.new("RGB", (target_w, target_h), bg)
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    canvas.paste(resized, (paste_x, paste_y))
    return canvas


def _download_print_file(url: str):
    """Fetch a print file URL and return a PIL Image, or None on failure."""
    from PIL import Image
    try:
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        img.load()
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception as exc:
        log.warning("[social-variants] download failed for %s: %s", url[:80], exc)
        return None


def generate_social_variants(
    print_file_url_4x5: str,
    print_file_url_1x1: str,
    order_id: str,
    subdir: str = "",
) -> dict[str, str]:
    """Generate, upload, and return R2 keys for each social variant.

    Args:
        print_file_url_4x5: Public R2 URL of the 4:5 print master.
        print_file_url_1x1: Public R2 URL of the 1:1 print derivative.
                            Optional — falls back to the 4:5 master if
                            absent (older orders pre-per-aspect pipeline).
        order_id: Shopify order id — used to namespace R2 keys under
                  social/{order_id}/{variant}.jpg so an R2 lifecycle rule
                  can clean them up after 30 days without complex matching.
        subdir: Optional sub-namespace for multi-portrait orders. When
                non-empty, keys become social/{order_id}/{subdir}/{variant}.jpg
                so portrait B's variants don't overwrite portrait A's.

    Returns:
        dict mapping variant name (e.g. "square") to R2 key (e.g.
        "social/12345/square.jpg") for variants that uploaded
        successfully. Variants that fail are silently omitted — the
        Klaviyo template's |default:'' on each property hides the
        corresponding button when a variant is missing.
    """
    from storage import upload_bytes

    if not order_id:
        log.warning("[social-variants] no order_id supplied — skipping")
        return {}

    # Load source images once each. Store under aspect key so multiple
    # variants pulling from the same source don't trigger redundant
    # downloads.
    sources: dict[str, object] = {}
    if print_file_url_4x5:
        img_4x5 = _download_print_file(print_file_url_4x5)
        if img_4x5 is not None:
            sources["4:5"] = img_4x5
    if print_file_url_1x1:
        img_1x1 = _download_print_file(print_file_url_1x1)
        if img_1x1 is not None:
            sources["1:1"] = img_1x1

    if not sources:
        log.warning("[social-variants] order=%s no source files available", order_id)
        return {}

    out: dict[str, str] = {}
    for variant_name, spec in SOCIAL_VARIANT_SPECS.items():
        try:
            target_w, target_h = spec["size"]
            source_aspect = spec["source"]

            # Pick the matching source aspect; fall back to 4:5 master if
            # the requested aspect isn't available. Square fed from 4:5
            # works visually because _fit_into_canvas pads sides rather
            # than cropping.
            src = sources.get(source_aspect) or sources.get("4:5") or sources.get("1:1")
            if src is None:
                log.warning(
                    "[social-variants] order=%s variant=%s: no usable source",
                    order_id, variant_name,
                )
                continue

            canvas = _fit_into_canvas(src, target_w, target_h)

            buf = BytesIO()
            canvas.save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
            buf.seek(0)

            if subdir:
                key = f"social/{order_id}/{subdir}/{variant_name}.jpg"
            else:
                key = f"social/{order_id}/{variant_name}.jpg"
            public_url = upload_bytes(
                buf.getvalue(), key, content_type="image/jpeg",
            )
            if not public_url:
                log.warning(
                    "[social-variants] order=%s variant=%s: R2 upload returned None",
                    order_id, variant_name,
                )
                continue

            out[variant_name] = key
            log.info(
                "[social-variants] order=%s %s → %dx%d (%d KB) at %s",
                order_id, variant_name, target_w, target_h,
                len(buf.getvalue()) // 1024, key,
            )
        except Exception as exc:
            log.warning(
                "[social-variants] order=%s variant=%s failed: %s",
                order_id, variant_name, exc,
            )
            continue

    return out
