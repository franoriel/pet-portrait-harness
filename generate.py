#!/usr/bin/env python3
"""
Pet portrait generator — Gemini API + Pillow name compositing.

Usage:
    python generate.py <photo_path> <pet_name> [--style classic|minimal|naturalist|watercolor]

Outputs to ./output/:
    <stem>_<style>_raw.png          — raw Gemini image
    <stem>_<style>_<name>.png       — composited with pet name
"""

from __future__ import annotations

import argparse
import base64
import functools
import logging
import os
import re
import sys
import tempfile
import threading
import time
import urllib.request
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

# Concurrency limiter — prevents OOM when many requests arrive at once.
# Requests beyond this limit get a 503 from app.py instead of queuing.
# Set via env var for easy scaling on Railway/Render (default 20 for production).
MAX_CONCURRENT_GENERATIONS = int(os.environ.get("MAX_CONCURRENT_GENERATIONS", 20))
_generation_semaphore = threading.Semaphore(MAX_CONCURRENT_GENERATIONS)

OUTPUT_DIR = Path("output")
FONTS_DIR  = Path("fonts")

# ALLOWED_SUFFIXES derived from MIME_MAP — single source of truth
MIME_MAP: dict[str, str] = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
}
ALLOWED_SUFFIXES = frozenset(MIME_MAP)

PORTRAIT_RATIO    = (4, 5)
PORTRAIT_MIN_SIZE = (1200, 1500)

# Legacy aliases (used by watercolor; kept for backwards compatibility)
WATERCOLOR_RATIO    = PORTRAIT_RATIO
WATERCOLOR_MIN_SIZE = PORTRAIT_MIN_SIZE


# ---------------------------------------------------------------------------
# Prompt strings
# ---------------------------------------------------------------------------

_CLASSIC_PROMPT = """\
Transform this photo into a refined single-line ink portrait illustration.

STRICT RULES:
- Pure white background (#FFFFFF), no texture, no gradient, no paper grain
- Black ink lines ONLY — no gray, no shading, no fill, no wash, no halftone
- No crosshatching, no stippling, no hatching of any kind
- ZERO text, letters, words, signatures, watermarks, or symbols anywhere
- The bottom 25% of the image must be COMPLETELY EMPTY white space

STYLE:
- Elegant continuous contour line drawing, as if drawn by a skilled illustrator \
with a fine-tip pen in a single sitting
- Primarily uniform 1.5px stroke, subtle variation at eyes/nose/ears
- Capture the specific character of THIS individual animal
- Suggest fur with sparse flowing lines, not individual strands

COMPOSITION:
- Chest up, centered horizontally, three-quarter angle, direct gaze
- Head occupies 40-50% of image height
- Bottom 25% is empty white space for text — do NOT generate any text\
"""

_MINIMAL_PROMPT = """\
Reduce the animal to its most essential contour — roughly 30 confident pen strokes. \
Think luxury brand logo of this specific pet.

STRICT RULES:
- Pure white background (#FFFFFF), no texture, no gradient, no paper grain
- Black ink lines ONLY — no gray, no shading, no fill, no wash, no halftone
- ZERO text, letters, words, signatures, watermarks, or symbols anywhere
- The bottom 25% of the image must be COMPLETELY EMPTY white space

STYLE:
- Minimal, confident strokes — each line counts
- Luxury brand aesthetic: Hermès or Cartier monogram quality
- Capture the specific character of THIS individual animal

COMPOSITION:
- Chest up, centered horizontally
- Head occupies 40-50% of image height
- Bottom 25% is empty white space for text — do NOT generate any text\
"""

_NATURALIST_PROMPT = """\
Victorian field-guide illustration style. Fine parallel hatching permitted on the \
body for volume, but NOT on the face.

STRICT RULES:
- Pure white background (#FFFFFF), no texture, no gradient, no paper grain
- Steel nib dip pen quality: precise, fine lines
- Fine parallel hatching on body for volume — face must remain clean contour only
- ZERO text, letters, words, signatures, watermarks, or symbols anywhere
- The bottom 25% of the image must be COMPLETELY EMPTY white space

STYLE:
- Victorian natural history illustration quality
- Detailed, scientific accuracy to the specific animal's features
- Confident, controlled hatching technique

COMPOSITION:
- Chest up, centered horizontally, three-quarter angle, direct gaze
- Head occupies 40-50% of image height
- Bottom 25% is empty white space for text — do NOT generate any text\
"""

_MINIMAL_LINE_ART_TEMPLATE = """\
Transform this photo into a minimal line art pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. A black dog must stay black. A brown dog \
must stay brown. A white cat must stay white. Preserve the original coloring faithfully.
- Match the animal's actual eye color from the photo.

STYLE:
- Clean, confident single-weight ink lines on a warm off-white (#FAF8F5) background
- High contrast — bold black linework against the light background
- Minimal detail: capture the essence of the pet in as few strokes as possible
- No shading, no fills, no gradients — pure linework only
- Suggest fur direction with sparse, deliberate strokes
- Fine art illustration quality, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, direct or three-quarter gaze
- The bottom 20%% of the image must be left as clean off-white space — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, cartoon, anime, 3D render, gray shading, \
crosshatching, stippling, color fills, text, watermark, border.\
"""

_MODERN_OIL_PAINT_TEMPLATE = """\
Transform this photo into a modern oil painting pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. A black dog must stay black. A brown dog \
must stay brown. A white cat must stay white. Preserve the original coloring faithfully.
- Match the animal's actual eye color from the photo.
- The color palette of the painting should complement the pet's real coat color, \
not override it.

STYLE:
- Rich, visible impasto brush strokes with thick paint texture
- Warm studio lighting — soft golden directional light from one side
- Deep, saturated colors with luminous highlights
- Painterly fur texture with bold confident strokes following fur direction
- Slightly dark, moody background with warm amber and sienna tones that vignette softly
- Classical oil portrait aesthetic with a contemporary looseness
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, noble three-quarter angle, direct gaze
- The bottom 20%% of the image must be a softly darkened area — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, flat digital art, cartoon, anime, 3D render, \
harsh shadows, neon colors, pixelation, blurry, text, watermark, border.\
"""

_NEON_POP_ART_TEMPLATE = """\
Transform this photo into a neon pop art pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Use the animal's fur/coat pattern and markings as the structural guide. \
Reinterpret the coat in bold saturated pop art colors — but preserve all \
distinguishing markings, patches, and patterns from the original photo.
- The overall palette should be electric and vibrant: hot pink, electric blue, \
neon green, bright orange, vivid yellow.
- Match the animal's actual eye shape and expression from the photo.

STYLE:
- Bold thick black outlines (comic book / screen print weight)
- Flat saturated color fills — no gradients within sections, hard color boundaries
- Bright contrasting background — single bold color or geometric color blocks
- Andy Warhol meets Keith Haring aesthetic — playful, graphic, punchy
- Halftone dot texture in select areas for retro pop feel
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, facing forward with personality and attitude
- The bottom 20%% of the image must be a bold solid color block — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, soft edges, muted colors, watercolor, \
oil paint, 3D render, blurry, low resolution, text, watermark, border.\
"""

_RENAISSANCE_ROYALTY_TEMPLATE = """\
Transform this photo into a Renaissance-style royal pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. A black dog must stay black. A brown dog \
must stay brown. A white cat must stay white. Preserve the original coloring faithfully.
- Match the animal's actual eye color from the photo.
- The surrounding palette should use rich muted Renaissance tones — deep burgundy, \
gold, forest green, midnight blue — while keeping the pet's natural coloring intact.

STYLE:
- Classical Renaissance oil portrait (Rembrandt / Titian lighting)
- The pet is depicted wearing ornate royal attire — velvet robes, gold embroidery, \
jeweled collar, or regal military sash appropriate to the animal
- Rich chiaroscuro lighting with a dark dramatic background
- Muted, aged color palette as if the painting is centuries old
- Subtle craquelure texture for authenticity
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and upper body in regal pose, noble and dignified
- Dark moody background with subtle drapery or classical column
- The bottom 20%% of the image must be a dark toned area — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, bright neon colors, cartoon, anime, 3D render, \
modern clothing, contemporary objects, text, watermark, border.\
"""

_COZY_FILM_GRAIN_TEMPLATE = """\
Transform this photo into a cozy vintage film-style pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift \
the hue — only apply a subtle warm vintage color grade over the entire image. \
A black dog must stay dark. A brown dog must stay brown. A white cat must stay \
cream-white. Preserve the original coloring through the vintage filter.
- Match the animal's actual eye color from the photo, with a warm tint.

STYLE:
- Soft warm vintage color grade — slightly faded, lifted blacks, warm highlights
- Kodak Portra 400 / Fuji Pro 400H film emulation aesthetic
- Subtle organic film grain texture across the entire image
- Gentle vignette darkening the edges
- Warm golden hour lighting — soft, diffused, wrapping around the subject
- Slightly desaturated but warm overall — autumn/honey tones
- Fine art photography feel, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, natural relaxed pose, soft eye contact
- Shallow depth of field feel — soft blurred warm background
- The bottom 20%% of the image must be a softly blurred warm-toned area — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: harsh digital sharpness, oversaturation, cold blue tones, high contrast, \
cartoon, anime, 3D render, pixelation, text, watermark, border.\
"""

_RAINBOW_BRIDGE_TEMPLATE = """\
Transform this photo into a serene Rainbow Bridge memorial pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. Preserve the original coloring faithfully \
so the pet is instantly recognizable.
- Match the animal's actual eye color from the photo.
- The surrounding environment uses soft ethereal pastel tones — but the pet itself \
must retain its true colors.

STYLE:
- Soft, luminous, ethereal atmosphere — the pet bathed in warm golden light
- Gentle clouds or soft mist in the background, pastel sky with warm sunset hues
- Subtle rainbow arc or prismatic light in the distant background (not overpowering)
- Warm angelic glow surrounding the pet — peaceful, comforting, serene mood
- Soft painterly rendering — between watercolor and digital painting
- Respectful memorial tone — beautiful but not sad
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, peaceful expression, gentle eye contact
- Soft light emanating from behind/around the pet
- The bottom 20%% of the image must be soft clouds or gentle mist — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, dark/morbid imagery, tears, sadness, harsh shadows, \
cartoon, anime, 3D render, pixelation, text, watermark, border.\
"""

_BOLD_GRAPHIC_POSTER_TEMPLATE = """\
Transform this photo into a bold graphic poster-style pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Use the animal's fur/coat pattern and markings as the structural guide. \
Simplify into 4-6 flat color zones that respect the original coloring. \
A black dog uses deep charcoal/black shapes. A brown dog uses warm earth tones. \
Preserve the recognizable pattern of the specific animal.
- Match the animal's actual eye shape from the photo.

STYLE:
- Flat vector illustration — clean geometric shapes with hard edges
- Strong color blocking with 4-6 bold flat colors, no gradients
- Thick confident outlines where color zones meet
- Mid-century modern poster / screen print aesthetic
- Clean solid background — single bold contrasting color
- Shepard Fairey / Aaron Draplin inspired graphic boldness
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, strong forward-facing pose, graphic impact
- Clean negative space around the subject
- The bottom 20%% of the image must be a solid color block — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, soft edges, gradients, watercolor, painterly strokes, \
3D render, blurry, detailed fur texture, text, watermark, border.\
"""

_AURA_GRADIENT_TEMPLATE = """\
Transform this photo into a dreamy aura gradient pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. A black dog must stay black. A brown dog \
must stay brown. A white cat must stay white. Preserve the original coloring faithfully.
- Match the animal's actual eye color from the photo.
- The glowing aura colors should complement the pet's natural coloring — \
not compete with it.

STYLE:
- Soft, luminous color gradient halos radiating outward from the pet
- Smooth ethereal aura in 2-3 complementary colors (lavender, soft teal, warm peach)
- The pet rendered in soft realistic detail, slightly dreamy and glowing
- Background is a smooth gradient blend of the aura colors — no hard edges
- Subtle light bloom / lens flare effect around the pet's outline
- Mystical, spiritual, new-age aesthetic — calming and beautiful
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, serene expression, gentle eye contact
- Aura radiates symmetrically from the pet outward to the edges
- The bottom 20%% of the image must be a smooth gradient wash — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, harsh edges, flat colors, cartoon, anime, 3D render, \
dark/moody atmosphere, pixelation, blurry, text, watermark, border.\
"""

_WATERCOLOR_TEMPLATE = """\
Transform this photo into a watercolor pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. A black dog must stay black. A brown dog \
must stay brown. A white cat must stay white. Preserve the original coloring faithfully.
- Match the animal's actual eye color from the photo.
- The color palette of the painting should complement the pet's real coat color, \
not override it.

STYLE:
- Loose expressive brushwork, soft wet-on-wet color washes
- White paper background with natural watercolor bleed edges
- Painterly fur texture with subtle fine ink linework on facial features
- Warm soft lighting, no harsh shadows
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Slight natural vignette
- The bottom 20%% of the image must be left as lightly tinted white wash — \
completely free of the animal — reserved for a name label
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, harsh shadows, dark background, pixelation, \
blurry, low resolution, cartoon, anime, 3D render, clipping, text, watermark, border.\
"""

def build_watercolor_prompt(_style_vars: Optional[dict] = None) -> str:
    return _WATERCOLOR_TEMPLATE


def _static(text: str) -> Callable[[Optional[dict]], str]:
    """Wrap a fixed prompt string as a style-vars-aware callable."""
    return lambda _vars: text


# Master registry: style → prompt builder callable
# All values share the same signature: (style_vars: Optional[dict]) -> str
PROMPTS: dict[str, Callable[[Optional[dict]], str]] = {
    "classic":            _static(_CLASSIC_PROMPT),
    "minimal":            _static(_MINIMAL_PROMPT),
    "naturalist":         _static(_NATURALIST_PROMPT),
    "watercolor":         build_watercolor_prompt,
    "minimal-line-art":   _static(_MINIMAL_LINE_ART_TEMPLATE),
    "modern-oil-paint":   _static(_MODERN_OIL_PAINT_TEMPLATE),
    "neon-pop-art":       _static(_NEON_POP_ART_TEMPLATE),
    "renaissance-royalty": _static(_RENAISSANCE_ROYALTY_TEMPLATE),
    "cozy-film-grain":    _static(_COZY_FILM_GRAIN_TEMPLATE),
    "rainbow-bridge":     _static(_RAINBOW_BRIDGE_TEMPLATE),
    "bold-graphic-poster": _static(_BOLD_GRAPHIC_POSTER_TEMPLATE),
    "aura-gradient":      _static(_AURA_GRADIENT_TEMPLATE),
}


# ---------------------------------------------------------------------------
# Per-style post-processing hooks
# ---------------------------------------------------------------------------

def _portrait_post_process(img: Image.Image) -> Image.Image:
    """Standard post-process for all portrait styles: 4:5 crop + minimum size."""
    img = crop_to_ratio(img, PORTRAIT_RATIO)
    min_w, min_h = PORTRAIT_MIN_SIZE
    if img.width < min_w or img.height < min_h:
        scale = max(min_w / img.width, min_h / img.height)
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
        )
    return img


# All colour/painterly styles share the same 4:5 crop + min-size pipeline.
# Ink-only styles (classic, minimal, naturalist) pass through unchanged.
_PORTRAIT_STYLES = [
    "watercolor",
    "minimal-line-art",
    "modern-oil-paint",
    "neon-pop-art",
    "renaissance-royalty",
    "cozy-film-grain",
    "rainbow-bridge",
    "bold-graphic-poster",
    "aura-gradient",
]

POST_PROCESS: dict[str, Callable[[Image.Image], Image.Image]] = {
    style: _portrait_post_process for style in _PORTRAIT_STYLES
}


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_font_cache: dict[str, ImageFont.FreeTypeFont] = {}

# Style → Google Font mapping (must match frontend STYLE_FONTS)
STYLE_FONT_MAP: dict[str, dict] = {
    "watercolor":           {"family": "Dancing Script",     "google": "Dancing+Script:wght@700",                  "file": "DancingScript-Bold.ttf"},
    "minimal-line-art":     {"family": "Raleway",            "google": "Raleway:wght@600",                         "file": "Raleway-SemiBold.ttf"},
    "modern-oil-paint":     {"family": "Playfair Display",   "google": "Playfair+Display:wght@700",                "file": "PlayfairDisplay-Bold.ttf"},
    "neon-pop-art":         {"family": "Bungee",             "google": "Bungee",                                   "file": "Bungee-Regular.ttf"},
    "renaissance-royalty":  {"family": "Cinzel",             "google": "Cinzel:wght@700",                          "file": "Cinzel-Bold.ttf"},
    "cozy-film-grain":      {"family": "Libre Baskerville",  "google": "Libre+Baskerville:wght@400",               "file": "LibreBaskerville-Regular.ttf"},
    "rainbow-bridge":       {"family": "Sacramento",         "google": "Sacramento",                               "file": "Sacramento-Regular.ttf"},
    "bold-graphic-poster":  {"family": "Oswald",             "google": "Oswald:wght@700",                          "file": "Oswald-Bold.ttf"},
    "aura-gradient":        {"family": "Quicksand",          "google": "Quicksand:wght@700",                       "file": "Quicksand-Bold.ttf"},
    # Ink-only legacy styles use Libre Baskerville Bold
    "classic":              {"family": "Libre Baskerville",  "google": "Libre+Baskerville:wght@700",               "file": "LibreBaskerville-Bold.ttf"},
    "minimal":              {"family": "Libre Baskerville",  "google": "Libre+Baskerville:wght@700",               "file": "LibreBaskerville-Bold.ttf"},
    "naturalist":           {"family": "Libre Baskerville",  "google": "Libre+Baskerville:wght@700",               "file": "LibreBaskerville-Bold.ttf"},
}

# Font size multipliers (matches frontend FONT_SIZES)
FONT_SIZE_SCALE: dict[str, float] = {
    "small":  0.7,
    "medium": 1.0,
    "large":  1.35,
}


def _download_google_font(google_spec: str, filename: str) -> Optional[Path]:
    """Download a Google Font TTF file. Cached in fonts/ directory."""
    font_path = FONTS_DIR / filename
    if font_path.exists():
        return font_path

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading Google Font: {google_spec}…", file=sys.stderr)
    try:
        req = urllib.request.Request(
            f"https://fonts.googleapis.com/css2?family={google_spec}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; PetPortraitBot/1.0)"},
        )
        css = urllib.request.urlopen(req, timeout=10).read().decode()
        matches = re.findall(r"src:\s*url\(([^)]+)\)", css)
        if not matches:
            raise ValueError("Could not parse font URL from Google Fonts response")
        font_url = matches[-1].strip("'\"")
        tmp = Path(tempfile.mktemp(dir=FONTS_DIR, suffix=".ttf.tmp"))
        with urllib.request.urlopen(font_url, timeout=15) as resp:
            tmp.write_bytes(resp.read())
        tmp.replace(font_path)
        print(f"  Font saved → {font_path}", file=sys.stderr)
        return font_path
    except Exception as exc:
        print(f"  Font download failed ({exc}). Falling back to built-in font.", file=sys.stderr)
        return None


@functools.lru_cache(maxsize=1)
def _get_font_path() -> Optional[Path]:
    """Download Libre Baskerville Bold once (legacy default)."""
    return _download_google_font("Libre+Baskerville:wght@700", "LibreBaskerville-Bold.ttf")


def get_font(size: int, style: Optional[str] = None) -> ImageFont.FreeTypeFont:
    """Return a cached FreeTypeFont at the requested size, optionally style-specific."""
    cache_key = f"{style or 'default'}:{size}"
    if cache_key in _font_cache:
        return _font_cache[cache_key]

    font_path = None
    if style and style in STYLE_FONT_MAP:
        spec = STYLE_FONT_MAP[style]
        font_path = _download_google_font(spec["google"], spec["file"])

    if not font_path:
        font_path = _get_font_path()

    if font_path:
        try:
            font = ImageFont.truetype(str(font_path), size)
            _font_cache[cache_key] = font
            return font
        except OSError:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Text compositing
# ---------------------------------------------------------------------------

def composite_name(
    image: Image.Image,
    pet_name: str,
    style: Optional[str] = None,
    font_size_key: str = "medium",
) -> Image.Image:
    """
    Composite the pet name onto the bottom 20% of the image.
    Uses a style-specific Google Font and respects the user's font size choice.
    Adds a thin separator line above the name.
    """
    img = image.copy() if image.mode == "RGB" else image.convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size

    scale = FONT_SIZE_SCALE.get(font_size_key, 1.0)
    spaced   = "  ".join(pet_name.upper())
    base_size = max(20, int(w * 0.045))
    font_size = max(16, int(base_size * scale))
    font      = get_font(font_size, style=style)

    bbox   = draw.textbbox((0, 0), spaced, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    zone_top    = int(h * 0.80)
    zone_center = zone_top + (h - zone_top) // 2
    text_x      = (w - text_w) // 2
    text_y      = zone_center - text_h // 2

    line_y = text_y - 16
    margin = (w - int(w * 0.30)) // 2
    draw.line([(margin, line_y), (w - margin, line_y)], fill=(0, 0, 0), width=1)
    draw.text((text_x, text_y), spaced, fill=(0, 0, 0), font=font)

    return img


# ---------------------------------------------------------------------------
# Image utilities
# ---------------------------------------------------------------------------

def save_web_preview(image: Image.Image, out_path: Path, max_width: int = 800) -> Path:
    """
    Save a fast-loading web preview: resize to max_width, convert to WebP at q80.

    Typical output: ~60-120 KB vs 2-5 MB for the full PNG.
    Returns the path to the saved .webp file.
    """
    preview_path = out_path.with_suffix(".webp")
    img = image if image.mode == "RGB" else image.convert("RGB")
    w, h = img.size
    if w > max_width:
        scale = max_width / w
        img = img.resize((max_width, int(h * scale)), Image.LANCZOS)
    img.save(preview_path, "WEBP", quality=80)
    log.info("           web  → %s (%dx%d, %d KB)",
             preview_path.name, img.width, img.height,
             preview_path.stat().st_size // 1024)
    return preview_path


def crop_to_ratio(image: Image.Image, ratio: tuple, gravity: str = "center") -> Image.Image:
    """Crop image to the given (width, height) ratio.

    gravity:
        "center" — classic centre crop (default)
        "top"    — anchor to top edge, crop from bottom (preserves pet face)
        "bottom" — anchor to bottom edge, crop from top (preserves name text)
    """
    w, h = image.size
    target_w, target_h = ratio
    if w / h > target_w / target_h:
        # Image is wider than target — crop sides (center horizontally)
        new_w = int(h * target_w / target_h)
        left  = (w - new_w) // 2
        return image.crop((left, 0, left + new_w, h))
    else:
        # Image is taller than target — crop top/bottom based on gravity
        new_h = int(w * target_h / target_w)
        if gravity == "top":
            return image.crop((0, 0, w, new_h))
        elif gravity == "bottom":
            return image.crop((0, h - new_h, w, h))
        else:
            top = (h - new_h) // 2
            return image.crop((0, top, w, top + new_h))


# ---------------------------------------------------------------------------
# Gemini client singleton
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _get_client() -> genai.Client:
    return genai.Client(api_key=os.environ.get("GEMINI_API_KEY", ""))


# ---------------------------------------------------------------------------
# Gemini API call
# ---------------------------------------------------------------------------

def call_gemini(
    photo_path: Path,
    style: str,
    style_vars: Optional[dict] = None,
    max_retries: int = 2,
) -> bytes:
    """Send photo + prompt to Gemini; return raw PNG/JPEG bytes of the generated image.

    Retries transient failures with exponential backoff.
    """
    client      = _get_client()
    image_bytes = photo_path.read_bytes()
    mime_type   = MIME_MAP.get(photo_path.suffix.lower(), "image/jpeg")
    prompt      = PROMPTS[style](style_vars)   # unified — no per-style branching

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-image-preview",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                            types.Part.from_text(text=prompt),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )

            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if part.inline_data is not None:
                        data = part.inline_data.data
                        if isinstance(data, str):
                            data = base64.b64decode(data)
                        return data

            text_parts = [
                p.text for c in response.candidates
                for p in c.content.parts if hasattr(p, "text") and p.text
            ]
            raise RuntimeError(
                f"Gemini returned no image. Model response: {' | '.join(text_parts) or 'no details'}"
            )

        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = any(k in err_str for k in ("429", "500", "503", "overloaded", "timeout", "deadline"))
            if is_transient and attempt < max_retries:
                wait = (attempt + 1) * 3  # 3s, 6s
                log.warning("Gemini transient error (attempt %d/%d), retrying in %ds: %s",
                            attempt + 1, max_retries + 1, wait, exc)
                time.sleep(wait)
                continue
            raise

    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Main generate function (called by app.py and batch.py too)
# ---------------------------------------------------------------------------

def generate(
    photo_path: "str | Path",
    pet_name: str,
    style: str = "classic",
    output_dir: Optional[Path] = None,
    style_vars: Optional[dict] = None,
) -> tuple[Path, Path, Path]:
    """
    Generate a portrait and composite the pet name onto it.

    Uses a semaphore to limit concurrent Gemini calls and prevent OOM.
    Raises RuntimeError('BUSY') if the semaphore cannot be acquired within 2s,
    which app.py maps to a 503 response so the frontend can retry.

    Returns:
        (raw_path, composited_path, web_preview_path)
    """
    if not _generation_semaphore.acquire(timeout=2):
        raise RuntimeError("BUSY")

    try:
        return _generate_inner(photo_path, pet_name, style, output_dir, style_vars)  # type: ignore[return-value]
    finally:
        _generation_semaphore.release()


def _generate_inner(
    photo_path: "str | Path",
    pet_name: str,
    style: str,
    output_dir: Optional[Path],
    style_vars: Optional[dict],
) -> tuple[Path, Path]:
    import uuid as _uuid
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    photo = Path(photo_path)
    uid   = _uuid.uuid4().hex[:10]  # unique per request — no file collisions

    log.info("[generate] %s  '%s'  ←  %s", style, pet_name, photo.name)

    raw_bytes = call_gemini(photo, style, style_vars)

    # Validate image fully before writing to disk
    ai_image = Image.open(BytesIO(raw_bytes))
    ai_image.load()

    raw_path = out / f"{uid}_{style}_raw.png"
    raw_path.write_bytes(raw_bytes)
    log.info("           raw  → %s", raw_path)

    # Per-style post-processing (crop, resize, colour-grade, …)
    processed = POST_PROCESS.get(style, lambda img: img)(ai_image)
    if processed is not ai_image:
        ai_image.close()
    ai_image = processed

    # Composite name
    composited = composite_name(ai_image, pet_name)
    ai_image.close()

    safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
    comp_path = out / f"{uid}_{style}_{safe_name}.png"
    composited.save(comp_path, "PNG")
    log.info("           comp → %s", comp_path)

    # Optimized web preview (small WebP for fast frontend display)
    web_path = save_web_preview(composited, comp_path)
    composited.close()

    return raw_path, comp_path, web_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate AI pet portrait via Gemini + Pillow"
    )
    parser.add_argument("photo_path", help="Path to the pet photo")
    parser.add_argument("pet_name",   help="Pet's name (will be composited onto the print)")
    parser.add_argument(
        "--style",
        choices=list(PROMPTS),
        default="classic",
        help="Style variant (default: classic)",
    )
    # Watercolor-specific vars
    parser.add_argument("--breed",         default=None, help="e.g. 'golden retriever'")
    parser.add_argument("--eye-color",     default=None, help="e.g. 'warm brown'")
    parser.add_argument("--fur-color",     default=None, help="e.g. 'golden'")
    parser.add_argument("--color-palette", default=None, help="e.g. 'warm amber and honey'")
    args = parser.parse_args()

    style_vars = None
    if args.style == "watercolor":
        style_vars = {k: v for k, v in {
            "BREED":         args.breed,
            "EYE_COLOR":     args.eye_color,
            "FUR_COLOR":     args.fur_color,
            "COLOR_PALETTE": args.color_palette,
        }.items() if v is not None}

    raw_path, comp_path, web_path = generate(args.photo_path, args.pet_name, args.style,
                                             style_vars=style_vars)
    print(f"\nRaw output:   {raw_path}")
    print(f"Composited:   {comp_path}")
    print(f"Web preview:  {web_path}")


if __name__ == "__main__":
    main()
