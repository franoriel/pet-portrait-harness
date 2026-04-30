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

# Print-ready minimum — 300 DPI at 10×12.5" for canvas variants
PORTRAIT_RATIO    = (4, 5)
PORTRAIT_MIN_SIZE = (3000, 3750)

# Legacy aliases (used by watercolor; kept for backwards compatibility)
WATERCOLOR_RATIO    = PORTRAIT_RATIO
WATERCOLOR_MIN_SIZE = PORTRAIT_MIN_SIZE


# ---------------------------------------------------------------------------
# Per-style "name integration" instructions — tells Gemini how to render the
# pet's name as a NATIVE part of the artwork (not a sticker on top).
# ---------------------------------------------------------------------------

def _name_integration(
    style_id: str,
    pet_name: str,
    background_mode: Optional[str] = "auto",
) -> str:
    """Returns a prompt fragment instructing Gemini how to integrate the
    pet's name into the artwork in the style's native medium.

    `background_mode` matters only for the three styles that expose a dark
    inversion (minimal-line-art, watercolor, bold-graphic-poster). When dark
    is picked for one of them, we swap the hardcoded dark name color for
    a light ivory/cream — otherwise the name renders invisible on the
    inverted background.
    """
    if not pet_name or not pet_name.strip():
        return "- Do NOT include any text, words, or letters anywhere in the image."

    # Defense in depth: strip any chars that could break out of prompt strings
    # (quotes, braces, brackets, backslashes, backticks, newlines). Only allow
    # letters, numbers, spaces, hyphens, apostrophes, periods. Max 20 chars so
    # it fits comfortably on a single line without needing a microscopic font
    # or wrapping off the canvas edge.
    safe = re.sub(r"[^A-Za-z0-9\s\-'\u2019.]", "", pet_name.strip())[:20].strip()
    if not safe:
        return "- Do NOT include any text, words, or letters anywhere in the image."

    name = safe.title()
    name_upper = safe.upper()

    # POSITION & SAFETY ENVELOPE
    # The source image is 4:5. Customers can order 1:1 (square canvas), 3:4,
    # or 4:5. A center-crop from 4:5 → 1:1 removes the top 10% and bottom 10%
    # of source height. To guarantee the name stays visible on EVERY aspect
    # ratio AND leaves a proper top margin above it on the visible face, the
    # name's vertical center must sit between 18% and 24% of source height:
    #   - On 4:5 orders: name at 18-24% of print → visible with generous top margin.
    #   - On 1:1 orders (center crop): name ends up at 10-17.5% of the square
    #     crop — still with clear top margin, not touching the edge.
    #   - On 3:4 orders (horizontal crop only): name position unchanged.
    # The pet's face stays at 50% of source so it lands at the visual center
    # of the visible face on every variant.
    safe_zone = (
        "- POSITION — CRITICAL: Place the name in the most aesthetically "
        "pleasing area of NEGATIVE SPACE in the artwork — the calm, "
        "unoccupied region of the background where the eye naturally rests. "
        "The default is the upper portion above the pet, but you may shift "
        "to a side or corner of negative space if that area is visibly "
        "calmer and lets the name breathe. Choose ONE pocket of negative "
        "space and commit; never split the name across regions.\n"
        "- The name is rendered as part of the artwork's own background or "
        "atmosphere (NOT a separate white strip or solid panel).\n"
        "- PADDING — CRITICAL: Treat the name as a single block and surround "
        "it on ALL FOUR sides with generous, even padding of clean negative "
        "space. The minimum clear padding around every edge of the name "
        "block is 8% of image height — visually obvious breathing room. "
        "Nothing (canvas edge, pet, foliage, props, frame, decorative "
        "flourishes) may touch or crowd the name within this padding.\n"
        "- SAFE PRINT MARGIN — CRITICAL: NEVER render any letter, glyph, "
        "stroke, serif, or letter shadow inside the outer 12% margin of the "
        "image on ANY side (top, bottom, left, right). The full name, with "
        "its padding, must sit comfortably within the inner 76% of the "
        "canvas so it cannot be cropped, clipped, or wrapped on a gallery-"
        "wrap canvas at any product aspect ratio (square, 3:4, 4:5).\n"
        "- If the upper area is the chosen negative-space pocket, the name's "
        "vertical CENTER should sit between 18% and 24% of the image height "
        "from the TOP edge so the name lands cleanly above the pet on every "
        "crop. NEVER place the name at the very bottom of the image, near "
        "the pet's paws, or directly overlapping the pet.\n"
        "- SINGLE LINE ONLY — never wrap, break, or stack the name across "
        "two lines. The complete name must read on one continuous baseline.\n"
        "- WIDTH CONSTRAINT — CRITICAL: the entire name (including any "
        "letter spacing) must fit within the CENTER 60% of the image width. "
        "BEFORE drawing, measure the name at the requested font size — if "
        "the letters would exceed this 60% width envelope, REDUCE the font "
        "size further until the whole name fits comfortably with the full "
        "padding still intact. It is better to render the name SMALLER than "
        "to bleed past the safe zone, clip a letter, or shrink the padding.\n"
        "- SIZE — CRITICAL: Render the name SMALL and refined — a delicate, "
        "editorial accent, never a banner or a headline. The name should "
        "feel like a quiet caption that complements the pet, not competes "
        "with it. When in doubt, make it smaller.\n"
        "- The full name must be 100% visible — no letter, accent, or "
        "descender may be cropped, faded into the canvas edge, or run off "
        "the image. If you cannot fit the name with full padding intact, "
        "shrink the font size — DO NOT push the name closer to the edge.\n"
        "- Name must NEVER overlap the pet, its fur, paws, eyes, ears, or "
        "any anatomical feature.\n"
        "- CONTRAST: Use HIGH-CONTRAST color so the name is clearly legible. "
        "For light backgrounds use deep saturated dark colors (near-black, "
        "deep navy, rich brown, deep sepia). For DARK backgrounds (moody "
        "drapery, oil paint shadow, dark graphic poster) use LIGHT colors "
        "(warm ivory, antique gold, pale cream) so the name reads clearly "
        "against the dark area. Do NOT use washed-out or low-contrast "
        "colors.\n"
    )

    # AESTHETIC PRINCIPLE: The name is a SMALL, REFINED accent — never
    # competing with the pet portrait. Minimalist, editorial sensibility.
    # Target sizes: 3-5% of image height for most styles. Bold styles go
    # slightly larger (5-6%) but never oversized.

    # When the user picked Dark on one of the three supported styles, the
    # background is inverted — so the name's hardcoded dark ink color would
    # disappear. Swap in an ivory/cream ink for legibility.
    _dark_inverted = (
        (background_mode or "auto").lower() == "dark"
        and style_id in {"minimal-line-art", "watercolor", "bold-graphic-poster"}
    )

    watercolor_ink = (
        "warm ivory #F3EFE4 or pale cream" if _dark_inverted
        else "deep sepia #4a2c14 or rich umber"
    )
    minimal_ink = (
        "warm ivory #F3EFE4 (matching the inverted linework)" if _dark_inverted
        else "Solid black (#000000)"
    )
    poster_ink = (
        "warm ivory #F3EFE4 or a bright palette accent color" if _dark_inverted
        else "jet black #000000 OR a palette accent color"
    )

    integrations = {
        "watercolor": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" as DELICATE hand-written calligraphy (thin brush, "
            f"flowing script). Use {watercolor_ink} for contrast but with "
            f"a LIGHT, refined stroke — not bold. Slight natural bleed at letter edges is okay. "
            f"Size: SMALL and refined, 2-3% of image height. Centered. Minimalist, editorial feel."
        ),
        "minimal-line-art": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in clean geometric sans-serif capitals "
            f"(Futura/Avenir feel). Wide letter-spacing (~0.15em). Thin-to-medium weight, "
            f"NOT bold. {minimal_ink}. Size: 2-2.5% of image height. Centered."
        ),
        "modern-oil-paint": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" as an elegant italic serif (Playfair Display feel), "
            f"thin weight, painted subtly into the canvas. Use warm dark brown #3a2818 for "
            f"contrast but keep the stroke REFINED — not chunky. "
            f"Size: 2-3% of image height. Centered."
        ),
        "neon-pop-art": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as a small accent in a bold sans-serif with "
            f"thin black outline and a single solid pop color (hot pink, electric blue, or yellow). "
            f"Understated — not a huge banner. Size: 3-3.5% of image height. Centered."
        ),
        "renaissance-royalty": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in fine Trajan-style classical Roman capitals. "
            f"Thin serifs, refined proportions. Use antique gold #8B7355 OR deep burnt umber "
            f"#3a2414 for strong contrast. Size: 2-3% of image height. Centered. Elegant, not loud."
        ),
        "cozy-film-grain": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" as a small personal handwritten cursive (ballpoint "
            f"pen). Deep warm sepia #2e1a0a or deep faded black. Slightly imperfect but "
            f"delicate. Size: 2-3% of image height. Centered. Intimate, not shouty."
        ),
        "rainbow-bridge": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" as a delicate flowing cursive script. Warm gold "
            f"#B8860B or rich dark rose #9A3B4B for solid contrast. Thin strokes, refined. "
            f"Size: 2-3% of image height. Centered. Soft and restrained."
        ),
        "bold-graphic-poster": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in a bold geometric sans-serif, but kept at a "
            f"SMALL size — a refined design accent, never a billboard. Use {poster_ink}. "
            f"Size: 3.5-4% of image height. Centered."
        ),
        "aura-gradient": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" in a delicate rounded sans-serif (Quicksand feel), "
            f"thin weight. Deep purple #4a2c5a with optional subtle glow. "
            f"Size: 2-3% of image height. Centered. Airy, minimal."
        ),
        # Legacy ink-only styles (classic/minimal/naturalist)
        "classic": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as fine single-stroke pen capitals with wide "
            f"letter-spacing. Solid black #000000. Size: 2-2.5% of image height. Centered. Refined."
        ),
        "minimal": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as tiny sans-serif capitals with extreme "
            f"letter-spacing. Solid jet black #000000. Size: 1.5-2% of image height. Centered."
        ),
        "naturalist": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as Victorian field-guide lettering with fine "
            f"hairlines. Solid black ink (#000000) only. Size: 4-5% of image height. Centered."
        ),
    }

    return integrations.get(style_id, (
        f"NAME INTEGRATION — CRITICAL:\n"
        f"{safe_zone}"
        f"- Render the name \"{name_upper}\" in high-contrast lettering matching the artwork style."
    ))


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
- NO solid color blocks, bars, panels, or rectangles anywhere in the image
- Background must be uniform white from edge to edge — no separate bottom panel

STYLE:
- Elegant continuous contour line drawing, as if drawn by a skilled illustrator \
with a fine-tip pen in a single sitting
- Primarily uniform 1.5px stroke, subtle variation at eyes/nose/ears
- Capture the specific character of THIS individual animal
- Suggest fur with sparse flowing lines, not individual strands

COMPOSITION:
- Chest up, centered horizontally, three-quarter angle, direct gaze
- Head occupies 40-50% of image height
- Pure white background fills the entire canvas edge-to-edge\
"""

_MINIMAL_PROMPT = """\
Reduce the animal to its most essential contour — roughly 30 confident pen strokes. \
Think luxury brand logo of this specific pet.

STRICT RULES:
- Pure white background (#FFFFFF), no texture, no gradient, no paper grain
- Black ink lines ONLY — no gray, no shading, no fill, no wash, no halftone
- ZERO text, letters, words, signatures, watermarks, or symbols anywhere
- NO solid color blocks, bars, panels, or rectangles anywhere in the image
- Background must be uniform white from edge to edge — no separate bottom panel

STYLE:
- Minimal, confident strokes — each line counts
- Luxury brand aesthetic: Hermès or Cartier monogram quality
- Capture the specific character of THIS individual animal

COMPOSITION:
- Chest up, centered horizontally
- Head occupies 40-50% of image height
- Pure white background fills the entire canvas edge-to-edge\
"""

_NATURALIST_PROMPT = """\
Victorian field-guide illustration style. Fine parallel hatching permitted on the \
body for volume, but NOT on the face.

STRICT RULES:
- Pure white background (#FFFFFF), no texture, no gradient, no paper grain
- Steel nib dip pen quality: precise, fine lines
- Fine parallel hatching on body for volume — face must remain clean contour only
- ZERO text, letters, words, signatures, watermarks, or symbols anywhere
- NO solid color blocks, bars, panels, or rectangles anywhere in the image
- Background must be uniform white from edge to edge — no separate bottom panel

STYLE:
- Victorian natural history illustration quality
- Detailed, scientific accuracy to the specific animal's features
- Confident, controlled hatching technique

COMPOSITION:
- Chest up, centered horizontally, three-quarter angle, direct gaze
- Head occupies 40-50% of image height
- Pure white background fills the entire canvas edge-to-edge\
"""

_MINIMAL_LINE_ART_TEMPLATE = """\
Transform this photo into a minimal line art pet portrait.

MONOCHROME — THIS IS CRITICAL:
- The artwork MUST be strictly BLACK AND WHITE ONLY. Pure black ink (#000000) \
lines on a warm off-white (#FAF8F5) background. Absolutely NO color anywhere \
in the image — not in the pet, not in the eyes, not in the background, not \
anywhere. Ignore the original coat color; render everything as black linework.
- Use the pet's coat markings/patches/patterns as a guide for WHERE lines go, \
but DO NOT color-fill them. All markings are expressed purely through line \
placement and density, not tonal or color variation.

STYLE:
- Clean, confident single-weight ink lines on the warm off-white background
- High contrast — bold pure-black linework against the light background
- Minimal detail: capture the essence of the pet in as few strokes as possible
- No shading, no fills, no gradients, no gray tones — pure linework only
- Suggest fur direction with sparse, deliberate strokes
- Fine art illustration quality, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, direct or three-quarter gaze
- The artwork must fill the entire canvas edge-to-edge — no reserved panels, \
bars, color blocks, or empty bands at the top or bottom
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: ANY color whatsoever, colored ink, colored background, colored eyes, \
colored fur, sepia, tinted, duotone, photography, photorealism, cartoon, \
anime, 3D render, gray shading, crosshatching, stippling, color fills, text, \
watermark, border, solid color bars or panels at image edges.\
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
- The artwork must fill the entire canvas edge-to-edge — no reserved panels, \
bars, color blocks, or empty bands at the top or bottom
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, flat digital art, cartoon, anime, 3D render, \
harsh shadows, neon colors, pixelation, blurry, text, watermark, border, \
solid color bars or panels at image edges.\
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
- The artwork must fill the entire canvas edge-to-edge — no reserved panels, \
bars, color blocks, or empty bands at the top or bottom
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, soft edges, muted colors, watercolor, \
oil paint, 3D render, blurry, low resolution, text, watermark, border, \
solid color bars or panels at image edges.\
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
- The artwork must fill the entire canvas edge-to-edge — the moody background and \
classical drapery/column extend naturally all the way to the bottom and top \
edges. No reserved panels, bars, color blocks, or empty bands anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, bright neon colors, cartoon, anime, 3D render, \
modern clothing, contemporary objects, text, watermark, border, \
solid color bars or panels at image edges.\
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
- The artwork must fill the entire canvas edge-to-edge — the warm blurred \
background extends naturally to every edge. No reserved panels, bars, \
color blocks, or empty bands
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: harsh digital sharpness, oversaturation, cold blue tones, high contrast, \
cartoon, anime, 3D render, pixelation, text, watermark, border, \
solid color bars or panels at image edges.\
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
- The artwork must fill the entire canvas edge-to-edge — soft clouds and \
ethereal mist extend naturally to every edge. No reserved panels, bars, \
color blocks, or empty bands
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, dark/morbid imagery, tears, sadness, harsh shadows, \
cartoon, anime, 3D render, pixelation, text, watermark, border, \
solid color bars or panels at image edges.\
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
- The background is a single solid flat color that fills the entire canvas \
edge-to-edge behind the pet — one continuous color, NOT split into panels \
or bands. No reserved color blocks, bars, or rectangles anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, soft edges, gradients, watercolor, painterly strokes, \
3D render, blurry, detailed fur texture, text, watermark, border, \
solid color bars or panels at image edges, horizontal color-band splits.\
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
- The aura gradient fills the entire canvas edge-to-edge as one continuous \
smooth wash. No reserved panels, bars, color blocks, or empty bands anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, harsh edges, flat colors, cartoon, anime, 3D render, \
dark/moody atmosphere, pixelation, blurry, text, watermark, border, \
solid color bars or panels at image edges.\
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
- The watercolor wash and natural bleed edges extend to every edge of the \
canvas. No reserved panels, bars, color blocks, or empty bands anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, harsh shadows, dark background, pixelation, \
blurry, low resolution, cartoon, anime, 3D render, clipping, text, watermark, border.\
"""

def build_watercolor_prompt(_style_vars: Optional[dict] = None) -> str:
    return _WATERCOLOR_TEMPLATE


# ---------------------------------------------------------------------------
# DARK-mode templates — dedicated prompts for the three styles where a
# dark inversion reads on-brand. These REPLACE the base template rather than
# patching it with an override, because the base templates lock in very
# specific light-background/ink-color rules (e.g. minimal line art's
# MONOCHROME block hardcodes "pure black ink on #FAF8F5"). A post-hoc
# BACKGROUND MODE — DARK override can't reliably countermand those — Gemini
# defers to the earlier, more specific instruction. Dedicated templates
# avoid the conflict entirely.
# ---------------------------------------------------------------------------

_MINIMAL_LINE_ART_DARK_TEMPLATE = """\
Transform this photo into a minimal WHITE-INK pet portrait drawn on black paper \
— a single-weight brush or pen illustration rendered in warm ivory ink on a \
solid deep background.

TWO-TONE WHITE-INK ILLUSTRATION — THIS IS CRITICAL:
- The linework is warm ivory / cream (#F3EFE4). The surface it sits on is a \
SOLID DEEP DARK field — pick ONE and hold it across the entire image: deep \
charcoal (#1A1A1A), midnight navy (#0E1424), or rich forest (#0F1F14).
- Reference styles to lean into: fine white-ink brush drawing on black paper, \
editorial minimalist ink-on-dark illustration. The pet is drawn IN ivory \
strokes, directly on top of the dark surface.
- Pure two-tone only: ivory strokes + one dark background color. Pet markings \
are expressed through line placement and density, never as filled shapes.

STYLE:
- Clean, confident single-weight ivory strokes on the solid dark field
- High contrast — the warm-ivory linework reads cleanly against the dark surface
- Minimal detail: capture the essence of the pet in as few strokes as possible
- Suggest fur direction with sparse, deliberate strokes
- Fine art illustration quality, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, direct or three-quarter gaze
- The dark background fills the entire canvas edge-to-edge — no reserved \
panels, bars, color blocks, or empty bands at the top or bottom
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: white or off-white backgrounds, black ink on light paper, color fills, \
chalkboard / blackboard texture (this is ink on paper, not chalk on a board), \
photography, photorealism, cartoon, anime, 3D render, gray shading, \
crosshatching, stippling, text, watermark, border, solid color bars or panels \
at image edges.\
"""

_WATERCOLOR_DARK_TEMPLATE = """\
Transform this photo into a nocturnal watercolor pet portrait — painted on \
paper that has been pre-flooded edge-to-edge with a deep, richly pigmented \
dark wash. Think moody night-study watercolor, indigo-dye ink-wash painting, \
or a Winslow Homer nocturne.

COLOR ACCURACY — THIS IS CRITICAL:
- Match the animal's EXACT fur/coat color from the uploaded photo. Do NOT shift, \
lighten, darken, or alter the coat color. Preserve the original coloring faithfully.
- Match the animal's actual eye color from the photo.
- The pet stays at its natural coloring against the dark wash — the pet must \
remain clearly visible and well-lit, not lost in shadow.

STYLE:
- Loose expressive brushwork, soft wet-on-wet watercolor technique
- The BACKGROUND is a deep pigmented watercolor wash — pick ONE: deep indigo, \
midnight navy, rich aubergine, warm charcoal, or deep burgundy. The wash \
carries natural organic bleed edges, but the paper is saturated with dark \
pigment all the way through — no light paper shows.
- Painterly fur texture with subtle fine ink linework on facial features
- Warm focused lighting on the pet so it reads clearly against the dark wash
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Slight natural vignette
- The dark watercolor wash extends to every edge of the canvas. No reserved \
panels, bars, color blocks, or empty bands anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: white or cream paper showing through, bright washed-out backgrounds, \
photography, photorealism, harsh shadows, pixelation, blurry, low resolution, \
cartoon, anime, 3D render, clipping, text, watermark, border.\
"""

_BOLD_GRAPHIC_POSTER_DARK_TEMPLATE = """\
Transform this photo into a bold graphic poster pet portrait on a pure \
black-or-dark-gray background — a high-contrast screen print where the pet \
pops off the black field in crisp white and one or two bold accent colors. \
Think Saul Bass after-dark title card, Shepard Fairey "Obey" on black, or a \
mid-century night-poster screen print.

BACKGROUND — THIS IS CRITICAL:
- The BACKGROUND is a single flat NEUTRAL dark — pick ONE and hold it across \
the whole canvas: pure black (#0A0A0A), charcoal (#151515), or dark graphite \
gray (#202020).
- It must read as black or near-black — NEVER navy, crimson, forest, \
aubergine, or any chromatic dark. No color cast. No gradient. No split panels.

PET COLOR TREATMENT — THIS IS CRITICAL:
- The pet is rendered primarily in CRISP WHITE / IVORY (#F5F2EB) as the \
dominant value, so it reads instantly against the black field.
- Add 1-2 BOLD accent colors (hot red #E63946, electric yellow #FFD60A, \
bright cyan #1BA5D4, neon orange #FF6B1A, or electric pink #FF3E8F) as \
punchy spot highlights — muzzle, tongue, collar zone, a single eye \
highlight, a stripe of fur. Accents are sparing, not dominant.
- Use the pet's fur pattern and markings as a guide for WHERE shapes go, \
but reinterpret into 3-5 flat zones of white + accent + deep shadow. \
Shadows can read as pure black (merging into the background) for a classic \
screen-print silhouette effect.
- Do NOT try to match the pet's realistic fur colors — this is a stylized \
poster, not a portrait transcription.

STYLE:
- Flat vector illustration — clean geometric shapes with hard edges
- Strong color blocking, no gradients, no soft shading
- Thick confident black outlines where white zones meet accent colors
- Mid-century modern poster / screen print aesthetic
- Shepard Fairey / Aaron Draplin inspired graphic boldness
- Fine art illustration style, high resolution 300dpi, print-ready

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, strong forward-facing pose, graphic impact
- Clean negative space around the subject
- The black/dark-gray background is one continuous flat color filling the \
entire canvas edge-to-edge behind the pet — NOT split into panels or bands. \
No reserved color blocks, bars, or rectangles anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: colored dark backgrounds (navy, burgundy, forest, aubergine), light \
or pastel backgrounds, realistic fur coloring, photography, photorealism, \
soft edges, gradients, watercolor, painterly strokes, 3D render, blurry, \
detailed fur texture, text, watermark, border, solid color bars or panels \
at image edges, horizontal color-band splits.\
"""

# (style_id, mode) → dedicated template. Missing keys fall back to the base
# PROMPTS template plus the generic _BACKGROUND_MODE_RULES override.
_ALT_PROMPTS: dict[tuple[str, str], str] = {
    ("minimal-line-art", "dark"):    _MINIMAL_LINE_ART_DARK_TEMPLATE,
    ("watercolor", "dark"):          _WATERCOLOR_DARK_TEMPLATE,
    ("bold-graphic-poster", "dark"): _BOLD_GRAPHIC_POSTER_DARK_TEMPLATE,
}


def _static(text: str) -> Callable[[Optional[dict]], str]:
    """Wrap a fixed prompt string as a style-vars-aware callable."""
    return lambda _vars: text


# Regex patterns to strip "no text" restrictions from legacy prompts,
# since we now WANT Gemini to render the pet's name as part of the art.
_NO_TEXT_PATTERNS = [
    re.compile(r"- *ZERO text[^\n]*\n?", re.IGNORECASE),
    re.compile(r"- *Do NOT include any text[^\n]*\n?", re.IGNORECASE),
    re.compile(r"- *The bottom [0-9]+%% of the image must be[^\n]+\n?", re.IGNORECASE),
    re.compile(r"- *The bottom [0-9]+% of the image must be[^\n]+\n?", re.IGNORECASE),
    re.compile(r"- *Bottom [0-9]+% is empty[^\n]+\n?", re.IGNORECASE),
    re.compile(r"- *Bottom [0-9]+%% is empty[^\n]+\n?", re.IGNORECASE),
    re.compile(r",?\s*text,?\s*watermark,?\s*border", re.IGNORECASE),
]


def _strip_no_text_rules(prompt: str) -> str:
    """Remove 'no text' and 'empty bottom zone' rules from legacy prompts."""
    for pat in _NO_TEXT_PATTERNS:
        prompt = pat.sub("", prompt)
    return prompt


_NO_BORDER_RULE = (
    "\n\nCRITICAL: The image must have NO paper edges, NO torn paper effect, "
    "NO deckled edges, NO frames, NO borders of any kind. The artwork should "
    "fill the canvas cleanly with no visible paper boundaries."
)

_COMPOSITION_RULE = (
    "\n\nCOMPOSITION — CRITICAL (read twice, follow exactly):\n"
    "- FULL-BLEED CANVAS FILL: The artwork MUST extend edge-to-edge on all four "
    "sides (top, bottom, left, right). Zero margins. Zero borders. Zero letterbox "
    "bars. Zero frames. Zero paper edges. Zero vignette cutoffs. The background, "
    "palette, and atmosphere fill 100% of the canvas pixel grid.\n"
    "- 4:5 PORTRAIT ASPECT RATIO: The image canvas is taller than it is wide. "
    "Compose for that — do NOT output a square or wide image.\n"
    "- CENTERED FACE — FOCAL POINT: The pet's FACE is the single focal subject. "
    "Position the pet's EYES at approximately 42-48% down from the top (roughly "
    "horizontal center of the frame) and perfectly centered left-to-right. The "
    "nose/muzzle sits just below center. The head occupies 40-55% of the image "
    "height so it reads as the clear focal point from across a room.\n"
    "- NO CROPPED FEATURES: Ears must be fully visible with breathing room above "
    "them. Chin/chest must have breathing room below. Nothing important (eyes, "
    "ears, whiskers, nose, chin) is clipped by any edge.\n"
    "- HEADROOM FOR NAME: Leave roughly the top 10-18% of the image as the style's "
    "natural background (continuous scenery/wash/tone — NOT a reserved solid "
    "panel) so a small name label can sit above the pet's head cleanly.\n"
    "- NO RESERVED BANDS: Never output a solid color bar, empty rectangle, or "
    "panel at the top or bottom. The artwork's native scenery extends uniformly "
    "to every edge, just more open above the head than around the body.\n"
    "- If the style has a dark moody background (e.g. Renaissance, oil paint), "
    "the dark atmosphere still extends uniformly to all four edges — drapery, "
    "shadow, or wall continues into the top area above the pet, just slightly "
    "more open so a name reads clearly.\n"
    "- The pet should NOT be pushed to the bottom edge. Natural portrait framing.\n"
)


# Background-mode overrides — injected after the style prompt so the customer's
# choice trumps the style default without otherwise changing the art direction.
# 'auto' → no override (style keeps its native palette).
_BACKGROUND_MODE_RULES: dict[str, str] = {
    "light": (
        "\n\nBACKGROUND MODE — LIGHT (customer choice, OVERRIDES any dark "
        "background instructions above):\n"
        "- Render the background in SOFT, LIGHT tones that complement the pet's "
        "coat colors — cream, warm off-white, pale sand, soft peach, gentle "
        "blush, muted sky, or a delicate pastel wash tuned to the pet's palette.\n"
        "- The light background must still match the style's medium (watercolor "
        "wash, oil paint glow, poster flat color, etc.) — just in a light key.\n"
        "- Keep the pet's natural coat color unchanged — only the surrounding "
        "atmosphere is lightened.\n"
        "- The light tone fills the ENTIRE canvas edge-to-edge with no borders.\n"
    ),
    "dark": (
        "\n\nBACKGROUND MODE — DARK (customer choice, OVERRIDES any light "
        "background instructions above):\n"
        "- Render the background in RICH, DEEP tones that complement the pet's "
        "coat colors — midnight navy, deep forest, warm charcoal, aubergine, "
        "deep burgundy, or a moody dark wash tuned to the pet's palette.\n"
        "- The dark background must still match the style's medium (watercolor "
        "wash, oil paint shadow, poster flat color, etc.) — just in a dark key.\n"
        "- Keep the pet's natural coat color unchanged and well-lit — only the "
        "surrounding atmosphere goes dark. The pet must remain clearly visible "
        "against the darker tones, not lost in shadow.\n"
        "- The dark tone fills the ENTIRE canvas edge-to-edge with no borders.\n"
    ),
}


# Per-style background-mode allowlist — mirrors the `backgrounds` field on
# STYLES in portrait-flow.js. Kept in sync by hand (both files are the source
# of truth for their tier). A frontend filter hides unsupported modes; this
# server-side guard prevents a crafted request from injecting an override
# that clashes with the style's hardcoded palette (e.g. minimal line art is
# black ink on white — 'dark' background makes lines disappear).
_STYLE_BACKGROUND_SUPPORT: dict[str, set[str]] = {
    # Ink-only legacy styles — white paper only.
    "classic":             {"auto"},
    "minimal":             {"auto"},
    "naturalist":          {"auto"},
    # Only three current styles expose light/dark — the rest keep their
    # baked-in look. Matches portrait-flow.js STYLES[].backgrounds.
    "watercolor":          {"auto", "light", "dark"},
    "minimal-line-art":    {"auto", "light", "dark"},
    "modern-oil-paint":    {"auto"},
    "neon-pop-art":        {"auto"},
    "renaissance-royalty": {"auto"},
    "cozy-film-grain":     {"auto"},
    "rainbow-bridge":      {"auto"},
    "bold-graphic-poster": {"auto", "light", "dark"},
    "aura-gradient":       {"auto"},
}


def _background_rule(mode: Optional[str], style_id: Optional[str] = None) -> str:
    """Return the background-mode instruction block for 'light'/'dark',
    or '' for 'auto' (and for any mode the style doesn't support —
    we silently fall back to auto rather than fight the style)."""
    resolved = (mode or "auto").lower()
    if style_id is not None:
        allowed = _STYLE_BACKGROUND_SUPPORT.get(style_id, {"auto"})
        if resolved not in allowed:
            resolved = "auto"
    return _BACKGROUND_MODE_RULES.get(resolved, "")


def _resolve_prompt_body(
    style_id: str,
    style_vars: Optional[dict],
    background_mode: Optional[str],
) -> str:
    """Return the base prompt body (style template + any background handling)
    with the server-side background-mode guard applied.

    If a dedicated alt template exists for (style, mode) (see _ALT_PROMPTS),
    it's used verbatim — no generic background override is appended, since
    the alt template already bakes the inversion in. Otherwise we fall back
    to the base PROMPTS template plus the generic _background_rule block.

    Callers still append _COMPOSITION_RULE / name block / _NO_BORDER_RULE.
    """
    requested = (background_mode or "auto").lower()
    resolved = requested
    if resolved not in _STYLE_BACKGROUND_SUPPORT.get(style_id, {"auto"}):
        resolved = "auto"

    alt = _ALT_PROMPTS.get((style_id, resolved))
    if alt is not None:
        log.info(
            "[prompt] style=%s bg_requested=%s bg_resolved=%s template=alt(%s,%s)",
            style_id, requested, resolved, style_id, resolved,
        )
        return alt
    log.info(
        "[prompt] style=%s bg_requested=%s bg_resolved=%s template=base%s",
        style_id, requested, resolved,
        f"+override({resolved})" if resolved in _BACKGROUND_MODE_RULES else "",
    )
    return PROMPTS[style_id](style_vars) + _BACKGROUND_MODE_RULES.get(resolved, "")



def build_prompt_with_name(
    style_id: str,
    pet_name: str,
    style_vars: Optional[dict] = None,
    background_mode: Optional[str] = "auto",
) -> str:
    """Build the full prompt for a style with the pet's name integrated
    into the artwork as a native design element."""
    base = _resolve_prompt_body(style_id, style_vars, background_mode)
    base = _strip_no_text_rules(base)
    name_block = _name_integration(style_id, pet_name, background_mode)
    return (
        base.rstrip()
        + _COMPOSITION_RULE
        + "\n\n" + name_block
        + _NO_BORDER_RULE
    )


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

def _detect_text_color(image: Image.Image) -> tuple:
    """
    Sample the bottom 20% of the image to determine if text should be
    light or dark for good contrast.
    Returns (text_rgb, line_rgba) tuple.
    """
    w, h = image.size
    zone_top = int(h * 0.80)
    bottom = image.crop((0, zone_top, w, h))
    # Average the pixel values
    pixels = list(bottom.getdata())
    if not pixels:
        return (0, 0, 0), (0, 0, 0, 80)
    avg_r = sum(p[0] for p in pixels) / len(pixels)
    avg_g = sum(p[1] for p in pixels) / len(pixels)
    avg_b = sum(p[2] for p in pixels) / len(pixels)
    # Perceived luminance (ITU-R BT.709)
    luminance = 0.2126 * avg_r + 0.7152 * avg_g + 0.0722 * avg_b
    if luminance < 128:
        # Dark background → white text
        return (255, 255, 255), (255, 255, 255, 100)
    else:
        # Light background → dark text
        return (0, 0, 0), (0, 0, 0, 80)


# Per-style text rendering config — controls how the name looks on each style
STYLE_TEXT_CONFIG: dict[str, dict] = {
    "watercolor": {
        "size_ratio": 0.05,     # font size as fraction of image width
        "transform": "title",   # title case
        "zone_top": 0.82,       # where the text zone starts (fraction of height)
        "letter_spacing": 0,    # extra spacing between chars (0 = natural)
        "opacity": 0.85,        # text opacity (for softer styles)
    },
    "minimal-line-art": {
        "size_ratio": 0.035,
        "transform": "upper",
        "zone_top": 0.84,
        "letter_spacing": 6,
        "opacity": 1.0,
    },
    "modern-oil-paint": {
        "size_ratio": 0.045,
        "transform": "title",
        "zone_top": 0.82,
        "letter_spacing": 1,
        "opacity": 0.9,
    },
    "neon-pop-art": {
        "size_ratio": 0.06,
        "transform": "upper",
        "zone_top": 0.80,
        "letter_spacing": 4,
        "opacity": 1.0,
    },
    "renaissance-royalty": {
        "size_ratio": 0.04,
        "transform": "upper",
        "zone_top": 0.83,
        "letter_spacing": 8,
        "opacity": 0.9,
    },
    "cozy-film-grain": {
        "size_ratio": 0.04,
        "transform": "title",
        "zone_top": 0.84,
        "letter_spacing": 1,
        "opacity": 0.8,
    },
    "rainbow-bridge": {
        "size_ratio": 0.06,
        "transform": "title",
        "zone_top": 0.82,
        "letter_spacing": 0,
        "opacity": 0.85,
    },
    "bold-graphic-poster": {
        "size_ratio": 0.07,
        "transform": "upper",
        "zone_top": 0.78,
        "letter_spacing": 5,
        "opacity": 1.0,
    },
    "aura-gradient": {
        "size_ratio": 0.045,
        "transform": "title",
        "zone_top": 0.83,
        "letter_spacing": 2,
        "opacity": 0.85,
    },
}

# Default config for styles not in the map
_DEFAULT_TEXT_CONFIG = {
    "size_ratio": 0.045,
    "transform": "title",
    "zone_top": 0.82,
    "letter_spacing": 0,
    "opacity": 1.0,
}


def composite_name(
    image: Image.Image,
    pet_name: str,
    style: Optional[str] = None,
    font_size_key: str = "small",
) -> Image.Image:
    """
    Composite the pet name onto the bottom of the image.
    Uses per-style config for font size, casing, positioning, and opacity
    so the text feels integrated with each artistic style.
    """
    img = image.copy() if image.mode == "RGB" else image.convert("RGB")
    w, h = img.size

    # Get style-specific config
    cfg = STYLE_TEXT_CONFIG.get(style, _DEFAULT_TEXT_CONFIG)

    # Auto-detect text color based on bottom region brightness
    text_color, _ = _detect_text_color(img)

    # Apply opacity to text color
    opacity = cfg["opacity"]
    if opacity < 1.0:
        text_color = tuple(int(c * opacity + (255 - 255 * opacity) * (1 - text_color[0] / 255)) for c in text_color)

    # Format the name
    name = pet_name.strip()
    if cfg["transform"] == "upper":
        name = name.upper()
    else:
        name = name.title()

    # Add letter spacing if configured
    spacing = cfg["letter_spacing"]
    if spacing > 0:
        name = (" " * spacing).join(name)

    # Calculate font size
    scale = FONT_SIZE_SCALE.get(font_size_key, 1.0)
    base_size = max(20, int(w * cfg["size_ratio"]))
    font_size = max(16, int(base_size * scale))
    font = get_font(font_size, style=style)

    # Position text
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), name, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    zone_top = int(h * cfg["zone_top"])
    zone_center = zone_top + (h - zone_top) // 2
    text_x = (w - text_w) // 2
    text_y = zone_center - text_h // 2

    draw.text((text_x, text_y), name, fill=text_color, font=font)

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

def verify_image_is_pet(photo_path: Path) -> tuple[bool, str]:
    """Use Gemini's text-only classification to verify the uploaded image
    contains a real pet (dog, cat, small animal) and nothing offensive.

    Returns:
        (is_pet, reason) — if False, reason contains user-friendly message.

    Costs ~$0.0001 per call (text-only Gemini Flash) vs $0.04 for image gen.
    """
    try:
        client = _get_client()
        image_bytes = photo_path.read_bytes()
        mime_type = MIME_MAP.get(photo_path.suffix.lower(), "image/jpeg")

        classification_prompt = (
            "Look at this image and answer with ONLY a JSON object (no markdown, no code fence). "
            "Is the primary subject of this image a real pet animal (dog, cat, bird, rabbit, "
            "guinea pig, hamster, reptile, or similar domesticated pet)?\n\n"
            "Return JSON like this:\n"
            '{"is_pet": true, "animal": "dog"}\n'
            "or\n"
            '{"is_pet": false, "reason": "brief description of what the image is"}\n\n'
            "Rules:\n"
            "- Humans are NOT pets. Return false if the primary subject is a person.\n"
            "- Cartoons, drawings, stuffed animals, or AI-generated fake pets are NOT pets. "
            "Return false for non-real/non-photographic pets.\n"
            "- Wild animals (lions, bears, dolphins) are NOT pets unless clearly domesticated.\n"
            "- Logos, text, memes, screenshots, objects, scenery — NOT pets.\n"
            "- Any NSFW, violent, or inappropriate content — return false with reason.\n"
            "- If the image is blank, solid color, or unidentifiable — return false."
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",  # cheap text-only model
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        types.Part.from_text(text=classification_prompt),
                    ],
                )
            ],
        )

        text = ""
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

        # Extract JSON (may be wrapped in markdown despite instructions)
        import json as _json
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        result = _json.loads(text)
        is_pet = bool(result.get("is_pet", False))

        if is_pet:
            return True, result.get("animal", "pet")
        else:
            reason = result.get("reason", "not a pet photo")
            return False, reason

    except Exception as exc:
        # If classification fails, fail SAFE (reject) to prevent abuse
        log.warning("Pet verification failed: %s", exc)
        return False, "Could not verify the uploaded image. Please try a different photo of your pet."


def add_name_to_image(
    image_bytes: bytes,
    style: str,
    pet_name: str,
    max_retries: int = 2,
    background_mode: Optional[str] = "auto",
) -> bytes:
    """Take an already-generated portrait and ask Gemini to add the pet's name
    into the existing artwork — preserving every detail of the original image.

    This avoids the problem of two separate Gemini calls producing two different
    artworks when we want "same image with/without name".
    """
    client = _get_client()
    name_block = _name_integration(style, pet_name, background_mode)
    prompt = (
        "Take this existing artwork and add the pet's name integrated into it. "
        "KEEP THE ORIGINAL ARTWORK EXACTLY AS IT IS — do NOT redraw, reimagine, "
        "or change any part of the existing image. Only ADD the pet's name text "
        "as a native part of the art. The original composition, pose, colors, "
        "brushstrokes, and details must remain 100% identical.\n\n"
        + name_block +
        "\n\nIMPORTANT: No paper edges, no deckled borders, no torn-paper effects. "
        "The image should have a clean edge, not look like a piece of paper."
    )

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.1-flash-image-preview",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
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
            raise RuntimeError("Gemini returned no image when adding name")
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = any(k in err_str for k in ("429", "500", "503", "overloaded", "timeout"))
            if is_transient and attempt < max_retries:
                time.sleep((attempt + 1) * 3)
                continue
            raise
    raise last_exc  # type: ignore[misc]


def call_gemini(
    photo_path: Path,
    style: str,
    style_vars: Optional[dict] = None,
    max_retries: int = 2,
    pet_name: str = "",
    background_mode: Optional[str] = "auto",
) -> bytes:
    """Send photo + prompt to Gemini; return raw PNG/JPEG bytes of the generated image.

    When pet_name is provided, the name is integrated into the artwork natively
    (hand-painted into watercolor, engraved into renaissance, etc.) rather than
    composited as a flat text overlay afterward.

    Retries transient failures with exponential backoff.
    """
    client      = _get_client()
    image_bytes = photo_path.read_bytes()
    mime_type   = MIME_MAP.get(photo_path.suffix.lower(), "image/jpeg")
    if pet_name:
        prompt = build_prompt_with_name(style, pet_name, style_vars, background_mode)
    else:
        prompt = (
            _resolve_prompt_body(style, style_vars, background_mode)
            + _COMPOSITION_RULE
            + _NO_BORDER_RULE
        )

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
    background_mode: Optional[str] = "auto",
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
        return _generate_inner(photo_path, pet_name, style, output_dir, style_vars, background_mode)  # type: ignore[return-value]
    finally:
        _generation_semaphore.release()


def _generate_inner(
    photo_path: "str | Path",
    pet_name: str,
    style: str,
    output_dir: Optional[Path],
    style_vars: Optional[dict],
    background_mode: Optional[str] = "auto",
) -> tuple[Path, Path]:
    import uuid as _uuid
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    photo = Path(photo_path)
    uid   = _uuid.uuid4().hex[:10]  # unique per request — no file collisions

    log.info("[generate] %s  '%s'  ←  %s", style, pet_name, photo.name)

    # Preview generation: ONE Gemini call — no-name version only.
    # The with-name version is generated lazily by add_name_endpoint
    # when the user adds to cart (halves per-portrait Gemini cost).
    raw_bytes = call_gemini(photo, style, style_vars, pet_name="", background_mode=background_mode)

    ai_image_no_name = Image.open(BytesIO(raw_bytes))
    ai_image_no_name.load()

    # Per-style post-processing (crop + upscale to print size)
    processed_no_name = POST_PROCESS.get(style, lambda img: img)(ai_image_no_name)
    if processed_no_name is not ai_image_no_name:
        ai_image_no_name.close()
    ai_image_no_name = processed_no_name

    # Save the hi-res no-name version (same file used for both paths)
    raw_path = out / f"{uid}_{style}_raw.png"
    ai_image_no_name.save(raw_path, "PNG", dpi=(300, 300))
    log.info("           raw (no name) → %s (%dx%d @ 300 DPI)",
             raw_path, ai_image_no_name.width, ai_image_no_name.height)

    # For preview purposes, the "composited" (with-name) version is
    # initially the SAME as the no-name version. It'll be upgraded
    # by generate_with_name_on_demand() when user adds to cart.
    composited = ai_image_no_name

    safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
    comp_path = out / f"{uid}_{style}_{safe_name}.png"
    # Save with 300 DPI metadata so Printful reads the correct print quality
    composited.save(comp_path, "PNG", dpi=(300, 300))
    log.info("           comp (with name) → %s (%dx%d @ 300 DPI)",
             comp_path, composited.width, composited.height)

    # Optimized web preview (small WebP for fast frontend display)
    web_path = save_web_preview(composited, comp_path)
    composited.close()

    return raw_path, comp_path, web_path


# ---------------------------------------------------------------------------
# On-demand: add name to an already-generated portrait
# ---------------------------------------------------------------------------

def generate_with_name_on_demand(
    no_name_image_bytes: bytes,
    pet_name: str,
    style: str,
    output_dir: Optional[Path] = None,
    background_mode: Optional[str] = "auto",
) -> tuple[Path, Path]:
    """Add the pet's name to an already-generated no-name portrait.
    Called at add-to-cart time to halve the up-front Gemini cost.

    Returns: (comp_path, web_preview_path)
    """
    if not _generation_semaphore.acquire(timeout=2):
        raise RuntimeError("BUSY")

    try:
        out = output_dir or OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)

        import uuid as _uuid
        uid = _uuid.uuid4().hex[:10]

        log.info("[generate_with_name] %s '%s' (on-demand)", style, pet_name)

        composited_bytes = add_name_to_image(
            no_name_image_bytes, style, pet_name, background_mode=background_mode
        )

        ai_image = Image.open(BytesIO(composited_bytes))
        ai_image.load()
        processed = POST_PROCESS.get(style, lambda img: img)(ai_image)
        if processed is not ai_image:
            ai_image.close()
        composited = processed

        safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
        comp_path = out / f"{uid}_{style}_{safe_name}_named.png"
        composited.save(comp_path, "PNG", dpi=(300, 300))
        log.info("           comp (with name) → %s (%dx%d @ 300 DPI)",
                 comp_path, composited.width, composited.height)

        web_path = save_web_preview(composited, comp_path)
        composited.close()
        return comp_path, web_path
    finally:
        _generation_semaphore.release()


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
