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
from PIL import Image, ImageDraw, ImageFilter, ImageFont

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
    style_vars: Optional[dict] = None,
) -> str:
    """Returns a prompt fragment instructing Gemini how to integrate the
    pet's name into the artwork in the style's native medium.

    `background_mode` matters for the two styles that still expose a dark
    inversion (minimal-line-art, watercolor) — when dark is picked we swap
    the hardcoded dark name colour for a light ivory/cream so it stays
    legible.
    `style_vars` carries the palette id for bold-graphic-poster — its asymmetric
    2-tone bg means the right name ink depends on which paired-tone palette the
    customer picked (cream-on-saturated for most, deep aubergine for the
    light/dark Rose pair, etc.).
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
    # of source height — i.e. source y=0.10 maps to the TOP edge of the
    # square print. Anything above source y=0.10 is gone; anything between
    # source y=0.10 and y=0.22 lands inside the canvas's printer-safe top
    # margin (12% of the cropped face) and risks clipping.
    #
    # We anchor positioning by the TOP of the tallest rendered glyph (not
    # by the center) so font choice and ascender height can't push letters
    # off the canvas. The rule below tells Gemini:
    #   - TOP of the tallest letter must be ≥22% of source height from the
    #     top edge → after 1:1 crop, letter top sits at ≥15% of the print,
    #     comfortably inside the 12% safe margin even at the largest text
    #     style (Bold Poster, ~4% letter height + ~2% ascent buffer).
    #   - Vertical CENTER of the name lands between 26-32% of source →
    #     post 1:1 crop = 20-27.5% of the print → a calm upper-third
    #     placement on every aspect ratio (1:1, 3:4, 4:5).
    # Past failure mode: a center range of 10-13% of source put the letter
    # tops at or above the 1:1 crop edge and shipped clipped names.
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
        "- TOP-EDGE CLEARANCE — ABSOLUTE, NON-NEGOTIABLE: measure the TOP "
        "of the tallest rendered glyph (cap height + any ascender, accent, "
        "serif, brush flick, or letter shadow — NOT the baseline, NOT the "
        "center). That top edge MUST sit at least 22% of the image height "
        "below the top of the source canvas. If you are at all unsure how "
        "tall your chosen font's ascenders are, place the name LOWER, never "
        "higher. This rule is what keeps the name from getting clipped on "
        "the 1:1 square print, which crops 10% off the top of the source.\n"
        "- If the upper area is the chosen negative-space pocket, the name's "
        "vertical CENTER must sit between 26% and 32% of the image height "
        "from the TOP edge — far enough below the canvas top that a 1:1 "
        "square center-crop cannot touch the letters, while still reading "
        "as a calm caption in the upper third of the visible print. NEVER "
        "place the name at the very top of the source (above 22% from top), "
        "at the very bottom, near the pet's paws, or directly overlapping "
        "the pet's ears, eyes, or fur. If the pet's head crowds the 26-32% "
        "band, render the pet a touch smaller — do NOT push the name up "
        "into the unsafe top zone.\n"
        "- SINGLE LINE ONLY — never wrap, break, or stack the name across "
        "two lines. The complete name must read on one continuous baseline.\n"
        "- WIDTH CONSTRAINT — CRITICAL: the entire name (including any "
        "letter spacing) must fit within the CENTER 60% of the image width. "
        "BEFORE drawing, measure the name at the requested font size — if "
        "the letters would exceed this 60% width envelope, REDUCE the font "
        "size further until the whole name fits comfortably with the full "
        "padding still intact. It is better to render the name SMALLER than "
        "to bleed past the safe zone, clip a letter, or shrink the padding.\n"
        "- SIZE — ABSOLUTE MAXIMUM: The TOTAL rendered height of the name "
        "block — including ALL ascenders, descenders, calligraphic "
        "flourishes, swashes, shadows, and ornamentation — must NEVER "
        "exceed 3% of image height. At 1024px tall, that is ≤30px for the "
        "entire name block top-to-bottom. Cap-height of a single uppercase "
        "letter must be ≤20px. The pet's head must appear at least 15× "
        "taller than the name's cap-height — the name is a quiet footnote, "
        "not a title. If it looks like a headline, a chapter heading, or a "
        "poster element, it is already far too large. Make it smaller.\n"
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

    # When the user picked Dark on one of the two supported styles, the
    # background is inverted — so the name's hardcoded dark ink color would
    # disappear. Swap in an ivory/cream ink for legibility.
    _dark_inverted = (
        (background_mode or "auto").lower() == "dark"
        and style_id in {"minimal-line-art", "watercolor"}
    )

    watercolor_ink = (
        "warm ivory #F3EFE4 or pale cream" if _dark_inverted
        else "deep sepia #4a2c14 or rich umber"
    )
    minimal_ink = (
        "warm ivory #F3EFE4 (matching the inverted linework)" if _dark_inverted
        else "Solid black (#000000)"
    )
    # Bold Graphic Poster ink follows the chosen palette — each POSTER_PALETTES
    # entry hand-picks an ink colour that reads cleanly across BOTH panels of
    # its asymmetric 2-tone bg split. Falls back to the 'teal' pair's cream ink
    # if no palette has been selected yet.
    if style_id == "bold-graphic-poster":
        _palette_id = (style_vars or {}).get("poster_palette") or "teal"
        if _palette_id not in POSTER_PALETTES:
            _palette_id = "teal"
        poster_ink = POSTER_PALETTES[_palette_id]["name_ink"]
    else:
        poster_ink = "jet black #000000"

    integrations = {
        "watercolor": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" as DAINTY, FANCY HAND-PAINTED "
            f"WATERCOLOR CALLIGRAPHY — a delicate copperplate / Spencerian / "
            f"flourished script feel: very fine hairline upstrokes, slightly "
            f"thicker downstrokes (the natural pressure variation of a pointed "
            f"watercolor brush), graceful tapered entry and exit strokes, "
            f"tasteful curling flourishes on the first letter and the final "
            f"letter only — never overwrought. Title case (first letter "
            f"capitalised, rest lowercase). The lettering must clearly read as "
            f"painted in the SAME watercolor medium as the rest of the artwork "
            f"— same brush, same paper, same hand. Slight natural watercolor "
            f"bleed and a faint hairline ink halo at letter edges is welcome.\n"
            f"- MONOCHROME — use a SINGLE ink tone only ({watercolor_ink}); no "
            f"second colour, no gradient, no multi-hue lettering. The whole "
            f"name reads as one continuous watercolor mark in one ink.\n"
            f"- TINY AND REFINED — the name is an engraver's mark on the "
            f"back of a fine print, not a headline above the painting. "
            f"Cap-height: 1.5-2% of image height (≤20px at 1024px). The "
            f"ENTIRE name block including all flourishes, ascenders, and "
            f"descenders must stay within 2.5% of image height total — "
            f"NEVER larger than 3%. Flourishes are decorative micro-details "
            f"only; they do NOT justify making the name taller or wider. "
            f"The dog/cat/pet is the star — the name is a whisper beside it. "
            f"Centered. Editorial, intimate, hand-painted."
        ),
        "minimal-line-art": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in clean geometric sans-serif capitals "
            f"(Futura/Avenir feel). Wide letter-spacing (~0.15em). Thin-to-medium weight, "
            f"NOT bold. {minimal_ink}. Size: 1.6-2% of image height — tiny refined "
            f"caption, never larger than 2.2%. Centered."
        ),
        "modern-shape-art": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in ALL CAPS using a TALL, NARROW, "
            f"CONDENSED display sans-serif — Humane / Bebas Neue / Anton feel: "
            f"tightly compressed letterforms, very high cap-height-to-width ratio, "
            f"single uniform stroke weight, no italics, no contrast strokes. Modern "
            f"editorial-magazine-cover aesthetic. Comfortable letter-spacing "
            f"(~0.06em) so the tall narrow caps still breathe without feeling "
            f"crammed. Treat the name as a confident typographic anchor placed in "
            f"clean negative space — never overlapping any shape. Use a single "
            f"deep neutral ink (charcoal #1f1f1f, deep navy #1d2a44, or warm black "
            f"#181614) sampled to read clearly against the calmest patch of "
            f"background. Size: 2.4-3% of image height — refined, never larger "
            f"than 3.2%. Centered. Quietly bold, modern, editorial."
        ),
        "neon-pop-art": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as a small accent in a bold sans-serif with "
            f"thin black outline and a single solid pop color (hot pink, electric blue, or yellow). "
            f"Understated — not a huge banner. Size: 2.4-3% of image height — never "
            f"larger than 3.2%. Centered."
        ),
        "renaissance-royalty": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in fine Trajan-style classical Roman capitals. "
            f"Thin serifs, refined proportions. Use antique gold #8B7355 OR deep burnt umber "
            f"#3a2414 for strong contrast. Size: 1.8-2.4% of image height — never "
            f"larger than 2.6%. Centered. Elegant, not loud."
        ),
        "bold-graphic-poster": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in a bold geometric sans-serif, but kept at a "
            f"SMALL size — a refined design accent, never a billboard. Use {poster_ink}. "
            f"Size: 2.6-3.2% of image height — never larger than 3.4%. Centered."
        ),
        "charcoal": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" in fine hand-drawn charcoal lettering "
            f"matching the sketch medium. Same charcoal grey as the darkest pet shading. "
            f"Slightly imperfect strokes, like a personal signature. Size: 2-2.5% of "
            f"image height — never larger than 2.8%. Centered. Quiet, sketched, "
            f"never printed-looking."
        ),
        "aura-gradient": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name}\" in a delicate rounded sans-serif (Quicksand feel), "
            f"thin weight. Deep purple #4a2c5a with optional subtle glow. "
            f"Size: 1.8-2.4% of image height — never larger than 2.6%. Centered. "
            f"Airy, minimal."
        ),
        # Legacy ink-only styles (classic/minimal/naturalist)
        "classic": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as fine single-stroke pen capitals with wide "
            f"letter-spacing. Solid black #000000. Size: 1.6-2% of image height — never "
            f"larger than 2.2%. Centered. Refined."
        ),
        "minimal": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as tiny sans-serif capitals with extreme "
            f"letter-spacing. Solid jet black #000000. Size: 1.2-1.6% of image height — "
            f"never larger than 1.8%. Centered."
        ),
        "naturalist": (
            f"NAME INTEGRATION — CRITICAL:\n"
            f"{safe_zone}"
            f"- Render the name \"{name_upper}\" as Victorian field-guide lettering with fine "
            f"hairlines. Solid black ink (#000000) only. Size: 2.4-3% of image height — "
            f"never larger than 3.4%. Centered."
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
- Head occupies 70-78% of image height
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
- Head occupies 70-78% of image height
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
- Head occupies 70-78% of image height
- Pure white background fills the entire canvas edge-to-edge\
"""

_MINIMAL_LINE_ART_TEMPLATE = """\
Transform this photo into a SINGLE-LINE continuous-line pet portrait — \
the kind of minimalist one-stroke ink drawing where the entire animal is \
captured in ONE unbroken, flowing line, as if the pen never lifted off \
the page from start to finish.

ONE CONTINUOUS LINE — THIS IS CRITICAL:
- The whole portrait reads as ONE UNINTERRUPTED LINE. There are no \
separate strokes, no detached marks, no "floating" features — every \
element of the pet (each ear, each eye, the snout, the chest contour, \
fur indications) is reached by continuing that same single line.
- The line may LOOP, curve back on itself, double-back, and cross over \
itself — but it must remain CONNECTED throughout the entire drawing. \
Think Picasso's iconic continuous-line animal sketches, or the modern \
"one-line drawing" / "monoline" aesthetic seen in editorial line-art \
illustration: elegant, gestural, confident, one fluid motion.
- Eyes, nostrils, and mouth are NOT drawn as separate dots or marks — \
they are formed by the line briefly looping around to imply the feature, \
then continuing onward. Same for whiskers and fur direction.
- Use the pet's actual coat markings only as a SUGGESTION for where the \
line might curve or loop to hint at pattern. Never render markings as \
filled shapes or as a second separate line.

LINE QUALITY:
- Single, even, uniform line weight from start to finish — neither \
tapering nor varying. Smooth controlled pen pressure throughout.
- Pure black ink (#000000) on a warm off-white (#FAF8F5) background.
- Strictly two-tone: one black line + the light background. ABSOLUTELY \
NO color anywhere, NO grey shading, NO fills, NO crosshatching, NO \
stippling, NO sketchy multi-pass strokes.

DETAIL ECONOMY — line activity must be UNIFORM across the figure:
- Do NOT pack one region (e.g. a single paw) with dense busy curls while \
leaving another region (e.g. the opposite limb, the back, or the chest) \
as a bare outline. Each anatomical area receives roughly the same line \
density. If one paw gets toe definition, the other paw gets toe definition; \
if the chest gets fur indication, the back does too. Asymmetric detail \
reads as the algorithm giving up — it is the #1 thing that ruins this style.
- Treat the WHOLE figure with the same calm, gestural pace from first \
mark to last. No scribbled "panic regions". No bare regions. Confident \
even rhythm throughout.

WHERE THE LINE ENDS — CRITICAL:
- The line tapers to a CLEAN stopping point ON the figure's silhouette \
itself — at the back of the chest, along the underside of the body, or \
where a second ear meets the head. The line MUST NOT extend past the \
body's outline into the empty background.
- Below the lowest body element (paws, chest line, seated bottom) the \
canvas is COMPLETELY EMPTY background — no lines, no marks, no straight \
or curved extensions, no "phantom" vertical or horizontal strokes \
dropping into negative space. The single most common failure mode is a \
straight vertical line falling below the figure into the white space — \
this MUST NOT happen.
- Body contours CLOSE back to the silhouette. Chest, belly, and limb \
outlines never trail off into the background — they always loop back to \
meet another part of the contour. Open contours that bleed into negative \
space are forbidden.

UPPER BAND — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the script:
- The pet's head, ears, ANY part of the line, and any stray mark MUST \
stay BELOW y=22% of the canvas. Top of the tallest ear sits at y≈25-28% \
— never closer to the canvas top.
- Within the top ~22%, the warm off-white paper stays completely empty \
— no line work, no stray strokes, no marks of any kind. This rule is \
non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation).
- Head and chest, direct or three-quarter gaze.
- The PET (formed by the single line) occupies 76-78% of image height — \
top of ears at ~22% from top (right at the bottom edge of the name safe \
zone), bottom of chest at ~96-99% from top, centered horizontally. The \
line should fill the canvas confidently — a small line in a sea of paper \
reads as timid, not minimal. Use the full available width too: the \
line's widest point (ear-to-ear or shoulder-to-shoulder) reaches \
~85-92% of canvas width.
- BACKGROUND (warm off-white) extends edge-to-edge — no reserved panels, \
bars, color blocks, or empty bands.
- Do NOT include any text, words, letters, watermarks, or signatures.

Avoid: multiple separate strokes, broken or interrupted lines, sketchy \
hatched marks, detached features (floating eyes, separate whisker dots), \
phantom vertical or horizontal stray lines hanging below the figure, \
line endings that trail off into empty background, asymmetric detail \
(one paw busy with toes, opposite limb left bare), open contours that \
never close back to the silhouette, filled shapes, varying line weight, \
ANY color, sepia, tinted, duotone, gray shading, crosshatching, \
stippling, photography, photorealism, cartoon, anime, 3D render, text, \
watermark, border, solid color bars or panels at image edges, pet \
pushed to canvas edges.\
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
- ONE bright, contrasting background colour — a single saturated pop tone \
(hot pink, electric blue, neon green, vivid yellow, or similar). NO multi-zone \
backgrounds, NO geometric color blocks behind the pet, NO checker or stripe \
panels — just one flat field of saturated colour edge-to-edge.
- FULL-BLEED BACKGROUND — CRITICAL: the saturated colour MUST extend \
absolutely edge-to-edge on all four sides of the canvas. The four \
corners are pure saturated bg colour, identical to each other. NO \
white border, NO off-colour fade at the edges, NO anti-aliased \
softening at the canvas perimeter, NO unprinted margin, NO let-the- \
white-paper-show artifact. If the bg colour does not reach the very \
last pixel column on the left, right, top, and bottom, the result is \
unusable. Treat it as a screen-print: ink covers the entire sheet.
- COMPOSITION OPTIONS — pick ONE and commit to it (do not produce a \
hybrid):
  (a) FULL-BLEED PORTRAIT: the pet's body extends past the canvas \
  edges where appropriate (ears can crop at the upper boundary, \
  shoulders can crop at the side edges, chest cuts off at \
  the bottom edge) so the artwork reads as a confident edge-to-edge \
  composition with no visible bg margin around the pet.
  (b) CLEAN BUST CROP: the pet is composed as a tight head + neck + \
  upper-shoulder bust with crisp clean line terminations — the bust \
  silhouette ends in a confident horizontal cut or smooth taper, \
  surrounded by the saturated bg with NO ragged fur fly-aways, NO \
  random splatters, NO pet body parts that don't fully resolve.
  Either way, the bg is fully solid and edge-to-edge.
- Andy Warhol meets Keith Haring aesthetic — playful, graphic, punchy
- Halftone dot texture in select areas of the PET ONLY for retro pop feel \
(never on the background)
- Fine art illustration style, high resolution 300dpi, print-ready

UPPER BAND — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the type:
- The pet's head, ears, fur fly-aways, and ANY graphic accents MUST \
stay BELOW y=22% of the canvas. Top of the tallest ear sits at y≈25-28% \
— never closer to the canvas top.
- Within the top ~22%, the saturated background continues edge-to-edge \
but stays calm and uniform — no halftone dots, no accent bursts, no \
splatters, no extra graphic flourishes inside this band.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, facing forward with personality and attitude
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved upper band (top ~22%) is what creates the breathing room \
above the ears.
- The BACKGROUND is ONE single saturated colour, perfectly uniform from \
corner to corner, extending edge-to-edge with NO internal rectangles, \
panels, bars, checker zones, or empty bands. The same colour you see in \
one corner you see in every other corner.
- Do NOT include any text, words, letters, watermarks, or signatures anywhere. \
IMPORTANT: these are layout instructions for YOU — do NOT paint the words \
"safe zone", "upper band", "type band", "zone", or any composition guide \
labels as visible text or shapes in the artwork.

Avoid: photography, photorealism, soft edges, muted colors, watercolor, \
oil paint, 3D render, blurry, low resolution, text, watermark, border, \
composition guide words or labels rendered as visible artwork, \
solid color bars or panels at image edges, white margin around the \
artwork, off-colour fade at the canvas perimeter, anti-aliased \
softening that leaves any pixel less than fully saturated at the \
extreme edges, ragged fur fly-aways with no defined termination, \
floating pet parts that don't resolve.\
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

UPPER BAND — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the engraved-style type:
- The pet's head, crown, ears, drapery folds, and ANY decorative detail \
MUST stay BELOW y=22% of the canvas. Top of the head/crown sits at \
y≈25-28% — never closer to the canvas top.
- Within the top ~22%, the moody dark background continues uniformly \
edge-to-edge — no light highlights, no drapery folds, no architectural \
detail, no craquelure flourish inside this band.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and upper body in regal pose, noble and dignified
- The PET itself (head + upper body) occupies 70-75% of image height — \
top of head/crown at ~25-28% from top, bottom of body at ~96-99% from top, \
centered horizontally. The reserved upper band (top ~22%) is what \
creates the breathing room above the head.
- The BACKGROUND (dark moody drapery/column) extends edge-to-edge naturally \
to all four sides. No reserved panels, bars, color blocks, or empty bands
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, bright neon colors, cartoon, anime, 3D render, \
modern clothing, contemporary objects, text, watermark, border, \
solid color bars or panels at image edges.\
"""

# 8 print-safe colours offered to customers via the Modern style background
# picker. All sit comfortably inside CMYK gamut (no neon, no pure RGB
# primaries) so what they pick on-screen is what arrives on the wall.
# id → (hex, descriptive name used in prompt)
MODERN_BG_COLORS: dict[str, tuple[str, str]] = {
    "cream":      ("#F4EFE7", "warm off-white cream"),
    "clay":       ("#E2C7A8", "soft warm clay"),
    "sage":       ("#B6C2A3", "muted sage green"),
    "terracotta": ("#D9A28D", "soft dusty terracotta"),
    "mauve":      ("#C9A4A4", "soft dusty mauve"),
    "mustard":    ("#D4B884", "soft warm wheat"),
    "navy":       ("#1D2A44", "deep navy ink"),
    "charcoal":   ("#2E2A26", "warm charcoal"),
}

# 8 wash-tint colours offered to customers via the Soft Watercolour background
# picker. Each colour sets the dominant tint of the watercolor wash and paper
# tone — from classic white paper to soft lifestyle hues that pair with
# real home decor palettes. All sit inside CMYK gamut for print fidelity.
# id → (hex, descriptive name used in prompt)
# These are WASH MARK colours — the painted halos and bleeds around the pet.
# The paper surface always stays clean white; only the marks carry this tint.
WATERCOLOR_BG_COLORS: dict[str, tuple[str, str]] = {
    "violet":  ("#9B7DB8", "soft violet purple"),
    "blue":    ("#7499BC", "dusty powder blue"),
    "teal":    ("#5EA8A0", "soft teal green"),
    "blush":   ("#C87878", "dusty rose blush"),
    "sage":    ("#7AAF82", "soft sage green"),
    "peach":   ("#C8906A", "warm peach"),
    "slate":   ("#6E8FAA", "blue-gray slate"),
    "gold":    ("#C8A050", "warm golden ochre"),
}

# Bold Graphic Poster background seam position, expressed as a fraction of
# canvas width measured from the left edge. 0.38 (golden-ratio adjacent)
# puts the seam off-centre — narrow LEFT panel + wide RIGHT panel — so a
# centred face-forward subject no longer sits axis-on-axis with the seam.
# The seam falls ~12% inside the pet's left silhouette and disappears
# behind the pet for most of the canvas height, killing the prior
# bisects-the-subject failure mode without altering the 2-colour palette
# logic. Used by the prompt template AND the post-processing pipeline
# (_flatten_poster_bg / _pad_split_bg / _remove_poster_halos) so prompt
# geometry and snap geometry stay in lockstep.
_BGP_SEAM_RATIO = 0.38

# 8 paired-colour palettes offered to customers via the Bold Graphic Poster
# style background picker. Each palette drives an asymmetric 2-tone background
# split (narrow LEFT panel + wide RIGHT panel, seam at _BGP_SEAM_RATIO) AND
# seeds the saturated accent colours used for the pet's cubist faceting. All
# values sit inside CMYK gamut so screen preview matches the printed canvas.
#
# Schema for each palette:
#   bg_left_hex / bg_right_hex  → exact hex codes for the left + right bg panels
#   bg_left_name / bg_right_name → descriptive labels Gemini can latch onto
#   accents              → human-readable list of accent colours for the pet
#   name_ink             → ink colour for the composited pet name (chosen so
#                          it reads cleanly against BOTH bg panels at once)
POSTER_PALETTES: dict[str, dict[str, str]] = {
    "teal": {
        "label":         "Teal",
        "bg_left_hex":   "#2DA39F", "bg_left_name":  "vivid teal",
        "bg_right_hex":  "#1B6B6E", "bg_right_name": "deep teal",
        "accents":       "warm orange (#F4A340), golden mustard (#F2CB52), warm ivory (#F4EFE7), and charcoal black (#1B1B1B)",
        "name_ink":      "warm ivory #F4EFE7",
        # Cool bg overlaps natural shadow tones in stylised pet rendering.
        # Tighten interior snap so a midtone teal-grey shadow on the pet
        # doesn't get snapped to canonical bg → silhouette breach.
        "interior_tol":  50,
    },
    "cobalt": {
        "label":         "Cobalt",
        "bg_left_hex":   "#3D6FAA", "bg_left_name":  "bright cobalt blue",
        "bg_right_hex":  "#1B2E58", "bg_right_name": "deep navy ink",
        "accents":       "warm yellow (#F4D641), ivory (#F4EFE7), hot red (#E63946), and charcoal (#1B1B1B)",
        "name_ink":      "warm ivory #F4EFE7",
        "interior_tol":  50,
    },
    "rose": {
        "label":         "Rose",
        "bg_left_hex":   "#F2BAC2", "bg_left_name":  "soft dusty pink",
        "bg_right_hex":  "#9F4A6F", "bg_right_name": "deep magenta plum",
        "accents":       "ivory (#F4EFE7), deep aubergine (#3B1F36), peachy blush (#F2C9A4), and dusty mauve (#C9A4A4)",
        "name_ink":      "deep aubergine #3B1F36",
    },
    "citrus": {
        "label":         "Citrus",
        "bg_left_hex":   "#F2C14A", "bg_left_name":  "golden yellow",
        "bg_right_hex":  "#C76A1F", "bg_right_name": "burnt orange",
        "accents":       "deep teal (#234A4A), ivory (#F4EFE7), warm charcoal (#1B1B1B), and soft brick red (#A04030)",
        "name_ink":      "deep teal #234A4A",
    },
    "forest": {
        "label":         "Forest",
        "bg_left_hex":   "#3F8559", "bg_left_name":  "vivid emerald green",
        "bg_right_hex":  "#1F4A30", "bg_right_name": "deep forest green",
        "accents":       "warm mustard (#C9A352), ivory (#F4EFE7), terracotta (#C77B58), and charcoal (#1B1B1B)",
        "name_ink":      "warm ivory #F4EFE7",
        "interior_tol":  50,
    },
    "rust": {
        "label":         "Rust",
        "bg_left_hex":   "#C75D3F", "bg_left_name":  "warm rust orange",
        "bg_right_hex":  "#7A2C1F", "bg_right_name": "deep maroon",
        "accents":       "warm ochre (#E8C547), ivory (#F4EFE7), deep navy (#1B2E58), and charcoal (#1B1B1B)",
        "name_ink":      "warm ivory #F4EFE7",
    },
    "violet": {
        "label":         "Violet",
        "bg_left_hex":   "#8C5FA8", "bg_left_name":  "vivid violet",
        "bg_right_hex":  "#3F1F58", "bg_right_name": "deep aubergine purple",
        "accents":       "warm yellow (#F2CB52), ivory (#F4EFE7), hot pink (#E68FB5), and charcoal (#1B1B1B)",
        "name_ink":      "warm ivory #F4EFE7",
        # Deep aubergine sits close to charcoal/dark-shadow tones — same
        # over-snap risk as the other cool palettes, slightly less acute.
        "interior_tol":  60,
    },
    "ember": {
        "label":         "Ember",
        "bg_left_hex":   "#E63946", "bg_left_name":  "vivid coral red",
        "bg_right_hex":  "#7A1F2A", "bg_right_name": "deep wine",
        "accents":       "warm yellow (#F2CB52), deep teal (#234A4A), ivory (#F4EFE7), and charcoal (#1B1B1B)",
        "name_ink":      "warm ivory #F4EFE7",
    },
}
POSTER_PALETTE_IDS = tuple(POSTER_PALETTES.keys())

def _modern_shape_art_prompt(style_vars: Optional[dict] = None) -> str:
    """Build the modern-shape-art prompt with the customer-chosen background
    colour interpolated into the COMPOSITION block. Falls back to clay if
    no colour is supplied or the id is unknown."""
    color_id = (style_vars or {}).get("modern_bg_color") or "clay"
    if color_id not in MODERN_BG_COLORS:
        color_id = "clay"
    hex_code, name = MODERN_BG_COLORS[color_id]
    return _MODERN_SHAPE_ART_TEMPLATE.replace(
        "{{MODERN_BG_HEX}}", hex_code,
    ).replace(
        "{{MODERN_BG_NAME}}", name,
    )

_MODERN_SHAPE_ART_TEMPLATE = """\
Transform this photo into a bold mid-century modern poster-style pet portrait \
on a single solid background of {{MODERN_BG_NAME}} ({{MODERN_BG_HEX}}).

COLOR ACCURACY — THIS IS CRITICAL:
- Use the animal's fur/coat pattern and markings as the structural guide. \
Simplify into 4-6 flat color zones that respect the original coloring. A \
black dog uses deep charcoal/black shapes. A brown dog uses warm earth \
tones. A white cat uses cool ivory + pale grey shadow shapes. Preserve the \
recognisable pattern of the specific animal — chest blaze, tan points, \
calico patches, tabby markings — rendered as bold simple flat shapes, not \
realistic detail.
- EYES — FLAT GRAPHIC ICONS, NOT REALISTIC ANATOMY (CRITICAL): \
Match the animal's actual eye SHAPE from the photo (round, almond, \
etc.) but render gaze as ALERT and ENGAGED — both eyes looking \
forward toward the viewer (or to the same off-camera direction by \
the same amount). NEVER downcast, NEVER looking down at the floor, \
NEVER half-closed, NEVER drooped. If the source photo catches the \
pet looking down, away, sleepy, or with a tired/melancholy gaze, \
REINTERPRET the eyes as wide-open and alert — the customer wants \
their pet to look ALIVE in the portrait, not match a low-energy \
source. Render the eye itself as a stylised vector ICON: TWO flat \
solid colour blocks total — one for the iris (a single uniform \
shade, no inner gradient, no outer ring of a second colour, no \
rim lighting, no shading toward the pupil) and one for the pupil \
(a single solid dark shape, no highlight inside it, no reflective \
sheen). NO catchlights, \
NO white glints, NO sparkles, NO reflective dots, NO crescent \
highlights anywhere on or around the eyes. NO secondary iris ring \
of a different colour. NO gradient WITHIN the iris from light to dark. \
NO visible sclera (white of the eye) — the iris fills the entire \
visible eye opening. NO drop shadow under the upper eyelid onto the \
eye. The eye must read as something a vector designer would draw with \
two filled paths in Illustrator — graphic, iconic, deliberately \
flat — NOT as an anatomically rendered eye with depth, moisture, or \
light interaction. RECURRING FAILURE MODE TO AVOID: a small white dot \
catchlight in the upper portion of the iris combined with a darker \
ring around the pupil — this single detail collapses the entire \
flat-vector aesthetic into uncanny-valley realism. If you find \
yourself adding ANY second tone, glint, or shading inside the eye \
shape, stop and render it as one flat iris colour + one flat pupil \
shape only.
- EXPRESSION — RENDER THE PET ALIVE AND ENGAGED, NEVER SAD (CRITICAL): \
the pet's facial expression in the rendered portrait must read as \
CONFIDENT, ALERT, and PRESENT — alive, looking out at the viewer, \
present in the moment. NEVER sad, droopy, melancholy, sleepy, \
forlorn, mournful, or low-energy. Mouth should be neutral or \
gently relaxed (closed/soft for cats and most dogs at rest, gentle \
open smile for dogs caught panting in the source) — NEVER turned \
DOWN at the corners, NEVER frowning, NEVER pinched. Brow should \
be relaxed and open (no furrowed/worried brow). If the source \
photo catches the pet at a tired/sad/dejected moment (head low, \
eyes downcast, mouth pulled down), do NOT replicate that mood — \
REINTERPRET the expression as the pet at their alert/happy best. \
The customer's portrait should be a celebration of their pet, not \
a screenshot of one bad-mood second from a phone roll.
- EAR POSTURE — CRITICAL: render ears in the breed's natural \
ALERT position. Erect-eared breeds (French Bulldog / Boston \
Terrier with bat ears, German Shepherd / Husky / Corgi with \
pricked ears, all cats unless they're folded breeds) get ears \
pointing UP and forward. Floppy-eared breeds (Beagle, Basset, \
Cocker, Goldendoodle, Cavalier, Spaniels) get ears in their \
natural relaxed-but-alert hang — never pulled all the way back \
flat against the head (which reads as fearful or aggressive). \
Even if the source photo catches the pet with ears slightly back \
or relaxed, render the ears in the breed's typical alert position. \
A Frenchie with drooped ears reads as "tired/sick" — keep the \
bat-ear silhouette upright.
- COLOR HARMONY — CRITICAL: the pet's coat palette and the \
{{MODERN_BG_NAME}} background must read as ONE intentional, harmonious \
palette. Pick coat shades that contrast comfortably with \
{{MODERN_BG_HEX}} (no disappearing pet, no value-clash vibration), and \
let one secondary coat tone subtly echo the background hue so the eye \
reads continuity across the image.

STYLE:
- MODERN EDITORIAL VECTOR ILLUSTRATION — refined, smooth, premium. The \
aesthetic reference is contemporary product illustration / Spotify \
Wrapped portrait art / Malika Favre / Olimpia Zagnoli / Apple TV \
character art / modern Behance "vector portrait" trend. NOT mid-\
century screen-print, NOT Charley Harper / Shepard Fairey graphic \
poster, NOT Disney-style cartoon mascot, NOT children's book \
illustration, NOT sticker art.
- Form is defined ENTIRELY by color separation between adjacent zones. \
ABSOLUTELY NO OUTLINES of any kind. NO black outlines around the pet \
silhouette. NO charcoal contour strokes between colour zones. NO \
inked edges, no stroke borders, no line work. The shape reads through \
tonal contrast and clean colour boundaries — never through drawn \
lines.
- 5-7 SUBTLY GRADUATED FLAT COLOURS in a tight, harmonious palette: \
adjacent shades of the pet's primary hue family that step through \
light → mid → shadow, plus 1-2 small accents for the nose and eyes. \
Each colour zone is one flat tone (no gradient WITHIN a zone), but \
the zones step through close, related shades so the pet reads as a \
soft, refined form — never as a hard-edged poster block.
- Smooth, organic zone boundaries that follow the pet's anatomy \
(cheek meets brow with a confident curve, snout meets cheek along \
the muzzle's natural plane). Not aggressively geometric, not \
faceted, not paper-cut angular.
- INTERNAL SHAPE LANGUAGE — CRITICAL: every colour zone inside the pet's \
silhouette must read as a broad anatomical mass (a cheek, a brow, a \
shoulder plane, a chest blaze, a leg). Shapes are wide and stable, not \
narrow or elongated. NEVER render long curved tubular bands, tongue-shaped \
wedges, swooping ribbon highlights, sausage forms, paisley curls, S-curves, \
teardrops, or any narrow-elongated-with-rounded-ends form. A chest blaze \
reads as a single broad bib/triangle, never as a tall curving tongue. Leg \
shading is one or two flat blocks following the leg axis, never curved \
bands that snake across the body. If a shape would read as suggestive or \
phallic in isolation, redraw it as an angular plane or merge it into the \
adjacent block.
- BODY RESOLUTION — CRITICAL: the pet's lower body must read as a clear, \
readable sitting posture with FRONT PAWS visible at the bottom. The chest \
blaze and the front legs are DISTINCT shapes that meet at the chest's \
bottom edge, not a single fused heart-shaped or shield-shaped blob. The \
silhouette from neck to floor must show: (1) a defined chest area in the \
middle, (2) two front legs/forepaws as broad parallel masses on either \
side of the chest, (3) two visible front paws at the very bottom edge \
sitting flush at the canvas floor. NEVER let the legs disappear into a \
fluffy chest mass; NEVER end the body in a single pointy V-shape; NEVER \
merge the chest blaze with the front legs into one shield-like silhouette. \
The pet should read as anatomically articulated — a viewer should be \
able to trace a finger along the silhouette and identify where chest ends \
and legs begin. RECURRING FAILURE MODE TO AVOID: a fluffy long-coated dog \
(Goldendoodle, Maltipoo, Bichon, Cavoodle) rendered with all the lower-\
body fur fused into a single drooping heart/shield blob with no visible \
legs or paws — this reads as a children's book illustration, not the \
editorial vector portrait we ship.
- AESTHETIC PUSH — CRITICAL: bias the entire rendering toward EDITORIAL \
SOPHISTICATION. The reference is a magazine illustration, a Spotify \
Wrapped portrait, an Apple TV+ character key art card. NOT a Disney \
mascot, NOT a children's storybook page, NOT a pet birthday card, NOT a \
greeting-card-aisle "fur baby" sticker. Specific tells that this rule \
is being violated: oversized doe-eyes, exaggerated cute proportions, \
soft pastel-rainbow palettes, smiling mouth-opening animation, "kawaii" \
roundness. If the result feels like it could go on a child's bedroom \
wall to make them smile, you have over-cartooned it; pull back toward \
"this is a refined art print someone in their 30s would hang in a calm \
adult living room."
- THE PET IS THE ONLY SUBJECT. Do NOT add decorative elements, abstract \
shapes, arcs, circles, dots, foliage, halos, frames, or any other graphic \
ornaments around the pet. The composition is just the pet on a single \
solid background — nothing else.
- Fine art illustration style, high resolution 300dpi, print-ready.

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation).
- FRONT-FACING / FACE-FORWARD POSE — CRITICAL: the pet faces directly \
toward the viewer (camera-on, head straight). Both ears equally visible, \
both shoulders showing in mirror-image symmetry across the vertical \
centre line. NEVER render a profile / side-view / 3⁄4 view / head-tilt — \
those produce asymmetric silhouettes that look truncated when the canvas \
crops to a square (12×12 / 16×16).
- ANATOMICALLY CORRECT TORSO — both shoulders visible at the same \
vertical level, chest centred under the head, body widening naturally \
from the neck down. The body silhouette is mirror-symmetric across the \
vertical centre line — never one shoulder cut off while the other \
extends to the edge.
- CLOSED OUTER SILHOUETTE — CRITICAL: the pet's outer silhouette is a \
SINGLE continuous CLOSED shape. Background colour NEVER cuts INTO the \
pet area from outside the silhouette: NO triangular wedges, NO V-shaped \
notches, NO arrow points, NO peninsulas, NO slots, NO darts of bg colour \
penetrating the body, chest, neck, shoulders, or head. RECURRING FAILURE \
MODE TO AVOID: a triangular wedge of bg colour cutting INWARD between the \
chest blaze and the shoulder/body, leaving the silhouette looking like a \
chunk has been bitten out of the pet. The chest blaze is rendered as an \
INTERNAL lighter colour block sitting INSIDE the closed body silhouette \
— never as bg colour breaking through from the edges. From the neckline \
down to the bottom edge of the canvas, the body silhouette ONLY widens \
or stays constant; it NEVER pinches inward, indents, or admits a wedge \
of bg colour. The only legitimate openings in the silhouette are at the \
very bottom edge between the front paws (a small symmetric V at the \
floor line, if rendered at all) — never along the sides or upper chest.
- Head and chest, strong forward-facing pose, graphic impact.
- The PET itself occupies 78-83% of image height — top of ears at \
~15-18% from top, chest at ~96-99% from top, centred horizontally. The \
pet is grounded at the bottom edge so a square (1:1) centre-crop still \
ships the chest intact.
- The BACKGROUND is ONE single solid colour {{MODERN_BG_NAME}} \
({{MODERN_BG_HEX}}) ONLY, extending edge-to-edge on all four sides. \
Completely uniform — no decoration, no shapes, no gradients, no panels, \
no bars, no colour blocks, no empty bands. Just one flat field of \
{{MODERN_BG_HEX}} behind the pet.

Avoid: photography, photorealism, painterly strokes, watercolor wash, \
3D render, blurry, detailed fur texture, drawn fur strands, OUTLINES \
of any kind around the pet or between colour zones, black contour \
strokes, ink lines, inked edges, charcoal outlines, coloring-book \
look, Disney-style cartoon mascot, children's book illustration, \
sticker art, animated character look, mid-century screen-print poster \
aesthetic, Shepard Fairey "Obey" / Charley Harper / Aaron Draplin \
graphic-block boldness, hard paper-cut faceting, multiple background \
colours, vertical or horizontal background splits, decorative shapes, \
foliage, halos, frames, patterns, geometric ornaments, \
profile / side-view / 3⁄4 view poses, head tilts, asymmetric body \
silhouettes, off-centre or tilted-axis compositions, tongue-shaped or \
tube-shaped colour zones, sausage forms, swooping ribbon highlights, \
paisley curls, S-curves, teardrop shapes, narrow elongated rounded-end \
shapes anywhere inside the pet, phallic or suggestive silhouettes, \
bg-colour wedges / notches / V-cuts / triangular gaps cutting INWARD \
into the pet's silhouette from outside, indented or pinched body \
contours below the neckline, chunks-bitten-out-of-the-pet effects, \
text, watermark, border, solid color bars or panels at image edges.

FINAL CHECK BEFORE OUTPUT — TEXT-FREE GUARANTEE (modern-shape-art is \
the WORST style for hallucinated lettering, so re-read this last):
- Scan the WHOLE canvas, especially the upper band above the pet. NO \
letters, NO words, NO glyphs, NO letterforms of ANY alphabet, NO \
character-shaped curves or strokes anywhere in the image.
- Modern editorial pet posters often include the pet's name in real \
products. DO NOT replicate that here. The name will be added later, \
outside this generation, by a separate process. Your job is to render \
ONLY the pet on a uniform {{MODERN_BG_HEX}} field — nothing else.
- If you have rendered ANY shape that could be misread as a letter (a \
curve resembling R, O, G, B, S, etc., or a vertical stroke beside a \
loop), erase it and replace with pure {{MODERN_BG_HEX}} background. The \
upper portion of the canvas above the ears MUST be a clean, unbroken \
field of {{MODERN_BG_HEX}}.

FINAL CHECK BEFORE OUTPUT — EYES ARE FLAT ICONS:
- Look at each eye. It must be exactly TWO flat solid colour blocks: \
iris (one uniform colour) + pupil (one uniform dark shape). Nothing \
more.
- If you see a white catchlight, glint, sparkle, dot, or any small \
brighter shape on or near the eye, ERASE it. The flat-vector \
aesthetic does not survive the addition of a single highlight dot.
- If the iris has any gradient, shading, inner ring of a second \
colour, or transition from light to dark, FLATTEN it to one solid \
hue.
- The eye should read like an icon a designer drew with two filled \
paths — not like a photo of an eye, not like a Pixar character's eye.

FINAL CHECK BEFORE OUTPUT — PET LOOKS HAPPY AND ALIVE, NOT SAD:
- Look at the overall expression. Does the pet read as alert, \
present, looking at the viewer with engaged eyes and a relaxed or \
gently-smiling mouth? If yes, output. If no — if the pet looks \
sad, droopy, sleepy, melancholy, dejected, mournful, or low-\
energy — REDRAW with: eyes wide-open and forward-engaged, mouth \
neutral/relaxed (NOT pulled down at corners), ears in the breed's \
alert position (UP for bat-eared / pricked-ear / cat breeds), brow \
relaxed not furrowed.
- Specifically check: are the eyes looking DOWN at the floor? Are \
the mouth corners turned DOWN? Are the ears drooped FLAT against \
the head when they shouldn't be? Any of these = sad-pet failure \
mode = REDRAW.
- The customer's portrait is a celebration of their pet — render \
the pet at their best, not at their tiredest.\
"""

_BOLD_GRAPHIC_POSTER_TEMPLATE = """\
Transform this photo into a CUBIST flat-graphic pet portrait in the style \
of a vintage screen-print poster — the pet rendered as a mosaic of bold \
angular polygonal colour blocks meeting at sharp clean edges, framed as a \
BUST PORTRAIT (head + neck + full shoulders + visible chest extending \
down to the canvas bottom edge — the lower THIRD of the canvas is occupied \
by NECK and SHOULDER and CHEST faceting, NOT by background. NEVER a face- \
only / head-only WPAP closeup — the head alone occupies AT MOST the upper \
two-thirds; the lower third is ALWAYS body), centred on the canvas, set \
against an ASYMMETRIC TWO-PANEL BACKGROUND with a vertical seam at 38% \
from the left edge (narrow LEFT panel + wide RIGHT panel — NEVER a \
centred 50/50 split).

COLOR ACCURACY — THIS IS CRITICAL:
- Use the animal's actual fur/coat PATTERN from the photo as the \
structural guide for which colour block goes where (light areas → \
lighter palette colours, shadow areas → darker palette colours, \
distinctive markings preserved). DO NOT match the literal coat hue. \
The pet's faceting uses ONLY the {{POSTER_ACCENTS}} palette below — \
the customer's chosen palette is the law, the animal's natural coat \
hue is just a guide for light/dark distribution within those palette \
colours. A golden dog on a teal palette reads through ochre + cream \
+ navy blocks (NOT warm orange). A black dog on a rose palette reads \
through plum + ivory + deep aubergine blocks (NOT charcoal). Preserve \
the recognisable identity of the specific pet — the breed shape, the \
head angle, any major chest blaze or markings — but always through \
the chosen palette's accent colours.
- Eye placement, gaze direction, and head angle stay TRUE to the \
source photo. The eyes themselves are stylised as bold OPEN graphic \
shapes — flat polygonal iris + flat polygonal pupil — that engage \
the viewer (see STYLE). The pet must look ALIVE and ALERT, never \
asleep, never squinting.
- BG / PET CONTRAST IS NON-NEGOTIABLE: the pet's silhouette MUST read \
distinctly against BOTH bg halves — left and right. NEVER use a pet \
facet colour that shares a hue or lightness with either bg colour. \
If the customer's pet is naturally a colour close to the bg (e.g. a \
golden retriever on the rust palette where the bg is warm rust + \
deep maroon), substitute the warm-orange / russet tones on the pet \
with the palette's CONTRASTING accents (ochre is fine, ivory is fine, \
navy is fine, charcoal is fine — but warm-orange or maroon-toned \
blocks on the pet are FORBIDDEN because they merge with the bg). \
RECURRING FAILURE MODE TO AVOID: a golden / red / brown pet rendered \
in warm orange blocks that visually merge with a warm-toned bg, \
making the pet's silhouette ambiguous. The pet must always pop off \
the bg with high contrast at every silhouette edge.

STYLE — CUBIST FLAT GRAPHIC:
- The pet's face and chest are reinterpreted as a CUBIST FACETED MOSAIC: \
12-20 bold polygonal flat colour blocks arranged like cut paper or \
stained glass.
- Each block is a flat, sharp-edged polygon (triangle, parallelogram, \
irregular quadrilateral, kite). Edges WITHIN the pet are STRAIGHT and \
GEOMETRIC, not curved. The OUTER silhouette of the pet (top of head, \
ear tips, jawline, chest line) may follow the natural curve of the \
breed; everything internal is faceted.
- NO outlines or strokes around the colour blocks — the colour change \
at each edge IS the edge. Blocks meet at razor-clean precise lines.
- 4-6 saturated flat colours total used across the pet's faceting, \
drawn from this palette only: {{POSTER_ACCENTS}}. Repeat colours \
across multiple blocks to create rhythm.
- NO gradients, NO airbrush, NO soft shading, NO photographic detail, \
NO drawn fur strands. Each block is uniformly flat colour.

PET FILL — CRITICAL (anti-stencil rule):
- The pet's silhouette is FULLY OPAQUE. Every single pixel inside the \
pet's outline is filled with one of the {{POSTER_ACCENTS}} palette \
colours (the ONE exception is the tongue — see TONGUE EXCEPTION \
below). The background colour NEVER shows through the pet — there \
are NO transparent gaps, NO holes, NO slivers of bg colour visible \
inside the silhouette. If you traced the pet's outline and flood- \
filled the inside, every pixel in the fill region would be a palette \
accent or the tongue carve-out colour (no pixel inside the silhouette \
is the bg's left-half hex or right-half hex — those colours live \
ONLY outside the pet).
- TONGUE EXCEPTION — the ONE pixel-level override of the palette-only \
rule: if the source photo shows the pet's tongue (open-mouth panting \
smile, lolling tongue, visible inside-of-mouth), render the tongue \
as a single flat warm CORAL-PINK polygon (#E07A6B). This is the \
ONLY area inside the pet's silhouette that may sit outside the \
{{POSTER_ACCENTS}} palette. Reason: a tongue rendered in any palette \
accent (teal, charcoal, ochre, ivory, mustard, etc.) reads as a sick \
/ poisoned / dead animal — pink is universally read as "alive and \
happy," and a non-pink tongue collapses the joyful pet-portrait vibe \
into uncanny-valley creep. The tongue polygon gets the same flat- \
faceted treatment as everything else (one flat block, sharp clean \
edges, no gradient, no shading). Mouth interior shadow behind the \
tongue, if shown, uses the palette's darkest accent. RECURRING \
FAILURE MODE TO AVOID: tongue rendered in teal / blue / charcoal / \
ivory / ochre / any non-pink colour — FORBIDDEN. The tongue is \
always a clean flat coral-pink polygon, regardless of which palette \
the customer picked.
- Use AT LEAST 4 distinct palette colours across the pet's body, \
distributed by BODY ZONE — never collapse to a single dark stamp. \
The pet should read like a FULLY-COLOURED cubist mosaic with visible \
light/mid/dark facets across the body, not a dark silhouette on \
coloured paper. Each of the palette's accent colours occupies a \
specific zone: \
\
  LIGHTEST ACCENT (ivory / cream / lightest neutral in the palette) \
  → cheeks (the round of fur outside the muzzle), brow ridge above \
  the eyes, chest blaze (the lighter chest fur), muzzle bridge \
  highlight, top of head highlight. These zones MUST be visibly \
  rendered in the lightest accent — not in the deepest accent, not \
  in the bg-family accent. \
\
  MID-TONE ACCENTS (peachy blush / dusty mauve / warm ochre / soft \
  brick / etc. — the palette's middle-value warm or cool neutrals) \
  → main body fur, neck, shoulder mass, lower jaw, ear front (the \
  side facing the viewer). \
\
  DARKEST ACCENT (charcoal / deep aubergine / deep navy / etc.) \
  → ear interiors only, eye-socket shadow only, nostril shadow, jaw \
  underside, deep chest shadow. The darkest accent is for SHADOW \
  ZONES ONLY — never the dominant colour of the pet's body. \
\
- COLOUR-DISTRIBUTION RULE — HARD: NO single palette accent occupies \
more than ~50% of the pet's silhouette area. The lightest accent \
must take at least 12% of the silhouette (cheeks + brow + chest \
blaze together). The mid-tone accents take 35-50% combined. The \
darkest accent takes only 10-25% (shadow zones only). If you find \
yourself rendering 70%+ of the pet in one dark accent with thin \
slivers of other colours at the edges, you have collapsed the pet \
into a dark stamp — REDRAW with the lightest accent enlarged across \
cheeks/brow/chest. \
\
- BG-FAMILY-ACCENT RULE — HARD (anti-merge): if the palette includes \
an accent colour that sits in the SAME hue family as either bg \
panel (e.g. the Rose palette's deep aubergine accent vs the deep \
magenta plum bg, or the Citrus palette's brick red accent vs the \
burnt orange bg), that bg-family accent is RESERVED for the smallest \
shadow zones (ear interiors, eye sockets, deep jaw shadow) and MUST \
NOT be used as the dominant body colour. Use the palette's NEUTRAL \
accents (ivory, cream, peachy blush, dusty mauve, warm ochre) as \
the dominant body colours instead. RECURRING FAILURE MODE TO AVOID: \
rendering the pet's main body in the palette's bg-adjacent accent, \
making the pet's silhouette merge into the deeper bg panel. \
FORBIDDEN.
- Each colour block has VISUAL MASS — it's a confident polygonal area, \
not a hairline sliver, not a thin angular stroke. Blocks are SHAPES, \
not LINES. If a "block" is so narrow it reads as a stroke or pen mark, \
it's wrong; widen it into a clear flat-fill polygon.
- RECURRING FAILURE MODE TO AVOID: the pet rendered as a flat dark \
stencil / ink stamp / silhouette made of thin angular dark strokes, \
with the background colour visible through the gaps where the \
"strokes" don't cover. This is FORBIDDEN. The pet is NOT an inked \
overlay on top of the bg; the pet is a fully-painted cubist mosaic \
where every internal pixel is a palette accent colour. If the result \
looks like a coloring-book outline that hasn't been filled in yet, \
or like a dark sticker placed on a flat coloured card, the image is \
wrong. Compare against the WPAP / Tsevis reference: every facet of \
the pet is a confident filled colour block, not a line drawing.
- Eyes are stylised as bold OPEN graphic shapes — vector-icon eyes \
that lock with the viewer (or follow whatever gaze direction the \
source photo shows). Each eye is exactly TWO flat polygonal colour \
blocks: an IRIS (a single uniform palette accent) and a PUPIL (a \
single solid polygon in the palette's DARKEST accent — charcoal / \
aubergine / deep navy / etc.). \
- IRIS COLOUR — HARD RULE (anti-headlight, anti-glow, anti-googly): \
the iris is NEVER the most saturated / brightest palette accent, \
AND the iris is NEVER the same colour as the fur immediately \
surrounding the eye opening. The iris colour MUST visibly contrast \
the cheek/brow/eye-socket fur within ~15px of the iris, so the eye \
reads as a discrete shape — not as one big merged white/light blob \
with a tiny pupil floating in it. Iris-colour priority depends on \
the fur immediately around the eyes: \
\
  CASE A — DARK or COLOURED fur surrounding the eyes (black, brown, \
  ginger, grey, brindle, tabby, etc.): \
  (1) IVORY / CREAM (#F4EFE7-ish), \
  (2) a WARM MID-TONE NEUTRAL (peachy blush, dusty mauve, soft \
  brick, warm ochre at its more muted reading), \
  (3) DEEP TEAL / DEEP NAVY when the bg is a warm-tone palette. \
\
  CASE B — WHITE / CREAM / IVORY fur surrounding the eyes \
  (Maltipoo, Goldendoodle, Bichon, white Cavoodle, white-masked \
  Shih Tzu, white-faced cat, husky with white mask, any pet whose \
  source photo shows white/cream fur immediately framing the eye): \
  IVORY / CREAM IRIS IS FORBIDDEN — it merges with the surrounding \
  white fur into one giant fake-sclera blob with a pinprick pupil, \
  and the result is the single creepiest possible failure mode \
  (possessed-doll / googly-eye look). Iris MUST be the customer's \
  CHOSEN PALETTE'S DEEPEST COOL OR NEUTRAL ACCENT — exactly one of \
  the hex codes listed in {{POSTER_ACCENTS}}, never a colour \
  invented outside that palette. Pick the deepest dark in the \
  palette: charcoal black if present, otherwise the palette's deep \
  aubergine / deep navy / deep teal / deep maroon as listed in \
  the accents. NEVER use a hex code that isn't in the customer's \
  palette accents — Gemini rendering off-palette colours like \
  "deep teal" on a palette without teal produces visible palette \
  violations the post-processing can't snap back. The iris colour \
  must be a literal copy of one of the {{POSTER_ACCENTS}} hexes. \
  Goal: a visually heavy iris that reads as a SOLID DARK EYE \
  against the surrounding white fur, using ONLY palette-native \
  colours. \
\
The brightest saturated palette accent (warm orange, hot red, hot \
pink, vivid yellow, vivid mustard) is FORBIDDEN as an iris fill in \
BOTH cases — those colours reserved for fur faceting, never the \
eye. A saturated iris reads as glowing headlights / taxidermy and \
ruins the portrait. RECURRING FAILURE MODE TO AVOID: a Goldendoodle \
or Maltipoo rendered with ivory irises sitting inside a ring of \
white face fur — the eye opening collapses into one giant white \
blob with a tiny black dot floating in it (looks possessed). \
FORBIDDEN: any white/cream iris on any pet with white fur framing \
the eyes.\
- BETWEEN-EYE COHERENCE — HARD RULE: both pupils point in the SAME \
direction. If the source photo shows the pet looking forward, both \
pupils sit centred in their respective irises. If the source shows \
an off-camera tilt, both pupils shift to the same side by the same \
amount. NEVER render one eye centred and the other off-axis, NEVER \
render diverging gaze (one pupil left, one pupil right), NEVER \
render different gaze depths (one pupil up, one pupil down). \
Diverging or asymmetric gaze breaks the portrait into "wall-eyed" \
or "googly" territory and customers read the result as creepy. \
- Eye SHAPE follows the source: round eyes get round-ish polygons, \
almond eyes get almond polygons, alert eyes are wide, relaxed eyes \
are softer — but the eyes are always clearly OPEN.
- EYE FLATNESS — HARD RULE (preserve the WPAP vector aesthetic): \
NO catchlights, NO white glints, NO sparkle dots, NO sclera (no \
"whites of the eye" visible — the iris fills the entire visible \
eye opening). NO gradient WITHIN the iris from light to dark. NO \
secondary ring of a different colour around the pupil. NO drop \
shadow of the upper eyelid onto the eye. Two flat polygonal blocks \
per eye — iris + pupil — and that's the entire eye. Same vector- \
icon treatment a screen-print designer would draw with two filled \
paths in Illustrator. The eye reads as graphic and iconic, NOT as \
anatomically rendered with depth or moisture.
- EYE FILL — HARD RULE: neither the iris nor the pupil polygon is \
ever filled with {{POSTER_BG_LEFT_HEX}} ({{POSTER_BG_LEFT_NAME}}) \
or {{POSTER_BG_RIGHT_HEX}} ({{POSTER_BG_RIGHT_NAME}}) — the bg \
colours NEVER appear inside the pet, including inside the eye \
shapes. The iris uses a palette accent that visibly contrasts the \
immediately surrounding face blocks, so the eye reads as a discrete \
shape rather than disappearing into the cheek.
- EYE SIZE — HARD RULE (anti-slit, anti-sleeping): each eye iris is \
a substantial polygonal shape whose vertical extent is AT LEAST 6% \
of the head's height — visible from across the room. Iris height-\
to-width ratio sits in the 0.7–1.1 range (round-ish to softly \
almond). Iris height less than 5% of head height = WRONG. Iris \
height-to-width ratio under 0.4 (a thin horizontal sliver) = WRONG. \
Both eyes are clearly OPEN, visibly engaging the viewer.
- PUPIL SIZE RELATIVE TO IRIS — HARD RULE (anti-headlight, anti-\
googly, anti-possessed): the pupil polygon occupies AT LEAST 45% \
of the iris's visible area, with 50–60% being the sweet spot. NOT \
a small dot in the middle of a large light disc. A small pupil \
inside a large light/ivory iris is the SINGLE BIGGEST CONTRIBUTOR \
to the creepy / possessed-doll / googly-eye look — bigger than \
diverging gaze, bigger than asymmetric pupils, bigger than any \
other failure mode. \
\
Concrete sizing test: imagine the iris as a clock face. The pupil \
is so large that it would touch the clock's "centre dot" zone AND \
extend visibly outward — its diameter spans roughly 2/3 of the \
iris's diameter, leaving only a thin ring of iris colour visible \
around it (top, bottom, left, right). If you can see MORE iris \
colour than pupil colour in the eye opening, the pupil is TOO \
SMALL — enlarge it. \
\
Pinprick-pupil / small-dot-pupil / stray-dark-speck-pupil = WRONG. \
A "centre dot" inside an otherwise empty iris disc = WRONG. The \
pupil should feel HEAVY and CENTRAL — like a black olive sitting \
inside a thin coloured ring, not like a poppyseed in the middle of \
a fried egg. RECURRING FAILURE MODE TO AVOID: irises rendered as \
large light discs with a tiny dark dot floating in the centre — \
classic possessed-cartoon-character look. FORBIDDEN. Pupil ≥ 45% \
of iris area, no exceptions, every render.
- FLUFFY-FACED BREED OVERRIDE — CRITICAL: for breeds with long fur \
covering the face (Goldendoodle, Maltipoo, Shih Tzu, Yorkie, Lhasa \
Apso, Old English Sheepdog, Bichon, Cocker Spaniel, Cavoodle, any \
"teddy-bear" or "doodle" mix), the source photo OFTEN shows the \
eyes partially or fully covered by fur strands hanging over the \
brow. In the rendered portrait, the eyes MUST still be drawn as \
FULLY OPEN, FULLY VISIBLE iconic polygonal shapes at the size rule \
above — render the pet AS IF the fur has been gently brushed away \
from the eyes for a clean portrait. Use the breed's normal eye \
shape (round, dark, alert) rather than copying the obscured-by-\
fur appearance from the photo. NEVER render closed slits, hidden \
eyes, fur-only zone where eyes should be, or "looking through fur" \
effects. The customer wants a portrait that captures their pet's \
SPIRIT and GAZE, not a literal redraw of an awkward photo angle.
- CAT OVERRIDE — CRITICAL: cats in source photos OFTEN have \
naturally squinted, half-closed, or near-closed eyes (sunshine \
photos, contented "loaf" pose, slow-blink trust gesture). Render \
cat eyes in the portrait as FULLY OPEN with the iris clearly \
visible — round-to-almond polygons at the EYE SIZE minimum above. \
Cat irises typically show the breed's natural eye colour (gold, \
green, copper, blue, ice) — pick the closest palette accent. Cats \
have vertical slit pupils when alert in bright light; render the \
PUPIL as a vertical narrow shape inside the iris (this is the cat- \
specific iconic detail), but the IRIS itself is wide-open and \
visible — not the whole eye reduced to a horizontal slit. NEVER \
render cat eyes as horizontal slits, sleeping crescents, contented \
half-moons, or zen-meditating squints — those collapse the portrait \
into a "sleepy cat sticker" instead of a confident vector portrait. \
The owner wants their cat to look ALERT and ENGAGING, not asleep.
- RECURRING FAILURE MODE TO AVOID #1: eyes rendered as closed slits, \
narrow wedges, squinted half-moons, sleeping-emoji curves, or thin \
horizontal lines — the pet must look ALIVE and ALERT, never asleep, \
never squinting, never zen-meditating. Closed or near-closed eyes \
on a commercial pet portrait read as sleeping, sick, or dead, which \
is the opposite of the joyful "beloved family pet" vibe we ship. \
RECURRING FAILURE MODE TO AVOID #2: eyes rendered in the bg colour \
so they read as empty sockets / hollow holes / the bg showing \
through, making the pet look eyeless or zombie-like. RECURRING \
FAILURE MODE TO AVOID #3: eyes hidden behind fur strands or omitted \
entirely on long-coated breeds — see FLUFFY-FACED BREED OVERRIDE. \
All three failure modes are FORBIDDEN.
- The nose is a single dark angular polygonal block. The muzzle / mouth \
line is implied by the meeting edge of two adjacent colour blocks, not \
drawn as a stroke.
- Fur direction along the chest and edges of the silhouette is implied \
by ZIG-ZAG triangular block patterns (alternating light/dark wedges \
running along the contour) — never by drawn fur strands.
- WPAP / Tsevis cubist-vector graphic energy: high contrast, bold \
saturated colour, posterised, geometric. The reference is a CLEAN \
DIGITAL VECTOR ILLUSTRATION (Adobe Illustrator output) — NOT a \
screen-print, NOT silk-screened ink, NOT distressed Risograph, NOT \
hand-pulled poster art.
- ABSOLUTELY NO TEXTURE OF ANY KIND inside any colour block. Each \
polygon is a SINGLE flat solid hex value, edge to edge. NO ink \
spatter, NO ink grain, NO halftone dots, NO Risograph misalignment, \
NO paper-fibre texture, NO grunge, NO distressed edges, NO noise, \
NO film grain, NO dust scratches, NO worn-printed-poster effect, \
NO weathered look. The output should look like a vector file, not a \
photographed silk-screen poster. RECURRING FAILURE MODE TO AVOID: \
the model adds a subtle gritty / weathered / inked texture to every \
colour block to "feel like a real print" — this is FORBIDDEN. The \
print produces texture from the canvas weave; the artwork file is a \
clean flat-colour vector composition.
- Fine art illustration style, high resolution 300dpi, print-ready.

BACKGROUND — ASYMMETRIC TWO-PANEL VERTICAL SPLIT (CRITICAL):
- The background is divided VERTICALLY into TWO UNEQUAL panels by a \
RAZOR-SHARP straight seam running floor-to-ceiling at exactly 38% of \
the canvas width measured from the LEFT edge. The seam is OFF-CENTRE — \
NOT at 50%, NOT centred, NOT through the middle. The narrower LEFT \
panel takes the first 38% of canvas width; the wider RIGHT panel takes \
the remaining 62%. This off-centre seam is a deliberate commercial-\
poster choice (golden-ratio composition) and is REQUIRED — a centred \
50/50 seam is FORBIDDEN.
- LEFT panel (0-38% width) = PERFECTLY FLAT {{POSTER_BG_LEFT_NAME}} \
({{POSTER_BG_LEFT_HEX}}) — every pixel within the left panel is the \
exact same hex value, corner to corner, top to bottom.
- RIGHT panel (38-100% width) = PERFECTLY FLAT {{POSTER_BG_RIGHT_NAME}} \
({{POSTER_BG_RIGHT_HEX}}) — every pixel within the right panel is the \
exact same hex value, corner to corner, top to bottom.
- ABSOLUTELY NO BG VARIATION OF ANY KIND inside either panel: NO \
gradient, NO subtle vignette, NO corner darkening, NO ambient \
occlusion, NO darker patch, NO lighter patch, NO faint shadow, NO \
suggestion of light source, NO suggestion of room corners or wall \
joints, NO atmospheric haze, NO depth cue, NO painterly variation, \
NO posterised banding, NO halftone dot pattern. Both panels are \
treated as pure flat colour fields, like a screen-print pull, not a \
photograph of a wall. RECURRING FAILURE MODE TO AVOID: a slightly \
darker rectangular patch in the upper portion of one panel (looks \
like a faint room-corner shadow). DO NOT add this patch.
- NO CORNER PATCHES, EVER: do NOT add a darker rectangular block in \
ANY corner (TOP-LEFT, TOP-RIGHT, BOTTOM-LEFT, BOTTOM-RIGHT). NO \
L-shaped corner shadows, NO triangular corner gradients, NO darker \
rectangle hugging any corner of the canvas at any size (5%, 10%, \
30%, 50% — all forbidden). The pet is NOT in a room, NOT against a \
wall, NOT in a photo studio with a corner shadow. This is a flat 2D \
screen-print, NOT a 3D scene with depth. RECURRING FAILURE MODE TO \
AVOID: a darker rectangular block roughly 25-50% of canvas width and \
10-20% of canvas height, hugging an upper corner — the model \
imagining a window frame, picture-rail moulding, or cropped wall \
panel. FORBIDDEN.
- CRITICAL — NO INSET / NESTED RECTANGLE BEHIND THE PET: do NOT \
render the background as an outer lighter border with a darker \
rectangular block behind the pet (a "poster pinned to a wall" or \
"framed art" look). The two-panel split is the WHOLE canvas — left \
panel goes edge-to-edge top to bottom, right panel goes edge-to-edge \
top to bottom. NO secondary rectangle, NO inner panel, NO darker \
zone surrounding the pet, NO card-on-a-wall layering. The exact \
pixel at the canvas's outer corner is the SAME colour as the pixel \
one inch inward — only the single 38% vertical seam ever changes \
background colour. RECURRING FAILURE MODE TO AVOID: generating four \
background regions (lighter outer border + darker inner rectangle \
around the pet). FORBIDDEN.
- ZERO TOLERANCE FOR SOFT / SUBTLE INSET RECTANGLES: the inset \
rectangle is forbidden at FULL contrast AND at 1% contrast. Even a \
2-pixel-wide ring of slightly lighter colour around the canvas \
perimeter is forbidden. Even a ~5% lightness shift between the area \
immediately behind the pet and the canvas corners is forbidden. If \
when rendering you imagine ANY darker zone, ANY softer halo, ANY \
breathing-room patch, ANY vignette inversion, ANY rectangular bias \
of any kind around the pet — at ANY opacity, in ANY shade — REMOVE \
IT. The four corners of the canvas are EXACTLY the same hex value \
as the pixels directly behind the pet's silhouette within the same \
panel. Imagine using a single paint-bucket fill of \
{{POSTER_BG_LEFT_HEX}} for the entire left panel (every pixel from \
x=0 to x=37.9% width, every pixel from y=0 to y=100% height) and a \
single paint-bucket fill of {{POSTER_BG_RIGHT_HEX}} for the entire \
right panel (every pixel from x=38.1% to x=100% width). Not a brush. \
Not a wash. Not an airbrush. A flat paint-bucket fill, like \
Photoshop's bucket tool with anti-alias and tolerance both off. ANY \
pixel-to-pixel variation within a panel is a PROMPT VIOLATION.
- The seam is PURELY VERTICAL — never horizontal, never diagonal, \
never curved. It runs floor-to-ceiling at the 38% mark.
- EXACTLY ONE SEAM, AT 38% FROM THE LEFT: there is precisely ONE \
colour change in the entire background, and it sits at the 38% \
vertical line — NOT at 50%, NOT centred. NO additional vertical \
seams anywhere. NO secondary lighter or darker vertical strip along \
the LEFT edge of the canvas. NO secondary vertical strip along the \
RIGHT edge of the canvas. NO three-band vertical layout. NO vertical \
pillar, sidebar, gutter, margin strip, or border column of any \
colour. The leftmost pixel column of the canvas is the SAME hex as \
the pixel column at 5%, 10%, 25%, and 37.9% inward — they all read \
as one continuous flat field of {{POSTER_BG_LEFT_NAME}}. Same rule \
for the right panel from 38.1% to 100%. RECURRING FAILURE MODE TO \
AVOID: a vertical strip of slightly different green / rust / cobalt \
/ etc. running down either edge of the canvas, creating a 3-region \
background. FORBIDDEN.
- THE PET STAYS CENTRED ON THE CANVAS, NOT ON THE SEAM — CRITICAL: \
the pet's vertical axis of symmetry sits at 50% of canvas width (the \
canvas centre). The bg seam at 38% sits ~12% to the LEFT of the \
pet's centre, which means the seam crosses behind the pet's LEFT \
shoulder / cheek / ear and is HIDDEN BEHIND the pet's silhouette \
for most of the canvas height. The seam is visible only in the \
strip ABOVE the pet's head (the upper ~15-22% of canvas) and \
possibly a sliver beside the pet's left flank near the bottom. The \
pet's body crosses the seam without altering its faceted block \
colours (the pet's blocks stay the SAME colour whether they sit \
over the left or right bg panel — the seam is pure background, \
behind the pet). RECURRING FAILURE MODE TO AVOID: centring the \
pet on the seam (pet axis at 38% to match the seam) — FORBIDDEN. \
The pet always sits at canvas centre, and the seam falls inside the \
pet's left silhouette where it becomes invisible.
- NO other background detail anywhere: no foliage, no props, no \
shadows, no reflections, no decorative shapes, no halos, no \
gradients, no extra colour blocks. Just the two flat panels.

COMPOSITION — SHOULDERS-UP PORTRAIT WITH HEADROOM (CRITICAL):
- 4:5 portrait aspect ratio (portrait orientation).
- FRAMING IS A SHOULDERS-UP PORTRAIT, NOT A TIGHT FACE CROP. The \
visible body parts are: FULL head (entire top of skull, both ears \
COMPLETE from base to tip with no clipping at the canvas edge), neck, \
both shoulders, and upper chest. NEVER a tight face closeup where \
the ears are clipped, NEVER a "muzzle + eyes only" render, NEVER a \
head-fills-the-frame composition.
- BODY-PART Y-POSITIONS — HARD RULE (this is the most important \
framing constraint, every Y-position must be inside the visible \
canvas): \
  - Top of highest ear / topmost head fur: y = 18-24% from top. \
  - Eye line (centre of irises): y = 32-40% from top. \
  - Tip of nose: y = 50-58% from top. \
  - Bottom of jaw / chin: y = 60-67% from top. \
  - Top of shoulders (where the neck meets the shoulder mass): y = \
  65-72% from top. \
  - Bottom of visible chest: y = 98-100% from top (CHEST RUNS TO \
  THE CANVAS BOTTOM EDGE — the chest is the lowest visible body \
  element and it sits flush against the bottom of the canvas, with \
  NO bg sliver below it). \
  All of these Y-positions MUST sit inside the canvas (between 0% \
  and 100%). If the chin is below 70% from top (i.e. the head fills \
  most of the canvas with only a thin band of body below), the \
  framing has collapsed to a face-zoom — REDRAW with the head \
  smaller and the chest larger. If the top-of-ears is above 0% \
  (i.e. ears clipped at the top edge), the framing is WRONG — REDRAW.
- THE LOWER THIRD IS ALWAYS BODY, NEVER BG — HARD RULE: the lower \
33% of the canvas (y = 67-100%) is filled with NECK + SHOULDERS + \
CHEST faceting. The chest extends to the canvas bottom edge — flush, \
no bg below the chest. The head + ears together occupy AT MOST the \
upper 67% of the canvas (y = 0-67%, with the empty bg headroom \
above the ears). If the dog's head fills 75%+ of the canvas height \
with shoulders/chest as a thin sliver at the bottom, the framing \
has collapsed to a face-zoom — FORBIDDEN. The head occupies the \
upper two-thirds, the body occupies the lower third in substantive \
faceting (multiple visible polygons of fur, not a thin compressed \
band).
- HEADROOM — HARD RULE: the TOP 22-26% of the canvas (from the top \
edge down to the ear tips) is empty flat bg colour — NO fur, NO \
ears, NO head silhouette in that upper band. This headroom is also \
where the typography pipeline composites the pet's name later, so \
it MUST stay clean.
- HORIZONTAL: pet centred horizontally — pet's vertical axis of \
symmetry at 50% of canvas width.
- FRONT-FACING / FACE-FORWARD POSE — CRITICAL: the pet faces directly \
toward the viewer (camera-on, head straight). Both ears equally \
visible, both shoulders showing in mirror-image symmetry across the \
pet's own vertical centre line. The pet's vertical axis of symmetry \
sits at the CANVAS CENTRE (50% width), NOT on the bg seam (which is \
at 38%) — the seam falls behind the pet's left shoulder / cheek and \
disappears under the pet's silhouette. NEVER profile / 3⁄4 / head- \
tilt poses.
- IGNORE SOURCE-PHOTO FRAMING — CRITICAL: customers often upload a \
tight face-zoom phone photo where the dog's face fills the frame and \
the neck/body is cropped off. The RENDERED OUTPUT must NOT mirror \
the source's tight framing — ZOOM OUT. If the source photo shows \
only the head (no shoulders, no chest), RECONSTRUCT the neck, \
shoulders, and chest from breed knowledge: a golden retriever / \
Goldendoodle has a thick golden / cream-coloured shoulder and chest, \
a Maltipoo has a fluffier white-cream shoulder and chest, a husky \
has a white-blazed chest, a black lab has a sleek black chest, a \
tabby cat has its breed's chest pattern continuing down from the \
neck. The output is ALWAYS a designed shoulders-up portrait, never \
a stylised re-render of whatever tight crop the customer's phone \
happened to capture.
- RECURRING FAILURE MODE TO AVOID: a face-zoom render where the \
dog's eyes and muzzle dominate the frame, ears CLIPPED at the top \
canvas edge (only the lower half of the ear visible, tip cut off), \
no shoulders or chest visible, jaw / chin at the bottom edge, often \
with a sliver of tongue or collar at the canvas bottom. FORBIDDEN. \
The output is ALWAYS a shoulders-up portrait with FULL head + \
COMPLETE ears + neck + both shoulders + upper chest all visible \
inside the canvas, with 22-26% clean headroom above.

Avoid: photography, photorealism, soft or curved edges WITHIN the pet, \
gradients, watercolor, painterly strokes, 3D render, blurry, detailed \
fur texture, drawn fur strands, hatching, eye whites / sclera, gradient- \
filled iris, secondary iris ring, catchlights, white glints in the \
eye, sparkle dots, eyelashes, closed slit eyes, half-closed eyes, \
squinted eyes, sleeping-emoji eye curves, asleep / zen / meditating \
expression, teal tongue, blue tongue, charcoal tongue, ivory tongue, \
any non-pink tongue, stencil look, ink-stamp look, sticker look, \
silhouette with bg showing through, dark angular strokes overlaid on \
flat bg, coloring-book outline, hollow pet figure, single-colour pet \
stamp, monochrome pet on coloured field, thin sliver "blocks", \
hairline angular strokes inside the pet, decorative shapes, foliage, \
props, halos, frames, framed-art look, poster-pinned-to-wall look, \
inset rectangle behind the pet, nested background rectangles, darker \
rectangular block surrounding the pet, four-quadrant background, \
vertical edge strip, vertical pillar or sidebar at left or right edge, \
three-region vertical background, additional vertical seams beyond \
the single 38% one, darker corner patch, top-left corner rectangle, \
top-right corner rectangle, bottom-left corner rectangle, \
bottom-right corner rectangle, L-shaped corner shadows, triangular \
corner gradients, window-frame look, picture-rail moulding, cropped \
wall panel, photographed-corner-of-room look, more than two \
background colours, horizontal background splits, diagonal background \
splits, curved background seams, gradient backgrounds, centred 50/50 \
seam, pet axis aligned with the seam, background patterns, drop \
shadows, text, watermark, border, solid color bars or panels at image \
edges.

FINAL CHECK BEFORE OUTPUT — PET IS A FULL-COLOUR MOSAIC, NOT A DARK \
STAMP (CRITICAL):
- DOMINANT-COLOUR CHECK: scan the pet's silhouette and ask "is one \
single accent colour covering more than ~50% of the pet's body?" If \
yes, the pet has collapsed to a near-monochrome silhouette. REDRAW \
with: cheeks + brow + chest blaze in the LIGHTEST palette accent \
(ivory / cream / lightest neutral); main body fur in MID-TONE \
accents (peachy blush / dusty mauve / warm ochre / etc.); darkest \
accent confined to SHADOW ZONES ONLY (ear interiors, eye sockets, \
nostril shadow, deep jaw shadow). The pet should show at least \
THREE clearly different accent colours occupying meaningful area, \
not one dark colour with thin edge slivers of others.
- LIGHT-ACCENT CHECK: can you see the lightest palette accent \
(ivory / cream / lightest neutral) clearly on the pet's cheeks, \
brow, and chest blaze? If those zones are rendered in a darker \
mid-tone or in the deepest accent, the pet has lost its 3D form \
and reads flat. REDRAW with the lightest accent placed on those \
specific zones.
- BG-MERGE CHECK: cover the lighter bg panel with your hand and \
look only at the pet against the deeper bg panel. Does the pet's \
silhouette read DISTINCTLY against the deeper panel, with clear \
contrast at every edge? If the pet blends into the deeper panel \
because its body colour is too close to the bg colour, the pet's \
dominant body colour is wrong. REDRAW the body in a NEUTRAL accent \
(ivory / cream / peachy blush / mid-tone) that contrasts BOTH bg \
panels, and confine the bg-family accent to small shadow zones \
only.

FINAL CHECK BEFORE OUTPUT — FRAMING IS SHOULDERS-UP, NOT FACE-ZOOM \
(CRITICAL):
- HEADROOM CHECK: measure from the top of the canvas down to the tip \
of the highest ear / topmost head fur. Is that distance 22-26% of \
canvas height? If the head touches the top edge, if the ears are \
clipped, if you can't see the COMPLETE ear including the very tip, \
ZOOM OUT and re-render with the head pushed down so the top 22-26% \
of the canvas is clean flat bg colour.
- TORSO CHECK — MEASURABLE: imagine a horizontal line at y = 75% \
from the top of the canvas (so the bottom 25% of the canvas). What \
do you see in that bottom band? \
  - If you see SHOULDERS and CHEST → framing is CORRECT, output. \
  - If you see only the lower jaw / chin / collar / neck-thin and \
  the bottom edge cuts off there → framing is WRONG. The pet is a \
  head-only closeup. ZOOM OUT and re-render so the bottom 25% of \
  the canvas contains visible SHOULDER MASS + CHEST FUR (not just \
  jaw or neck). \
  - If the bottom band is empty flat bg → the pet is too small / \
  positioned too high. ZOOM IN slightly so the chest fills the \
  bottom 18-22%. \
The output is a shoulders-up portrait. The bottom 25% of the canvas \
showing only the jaw with nothing below = FORBIDDEN.
- HEAD-ONLY-IS-WRONG CHECK: does the pet's head occupy more than \
~70% of the visible pet (i.e. the head is most of what you see, \
with shoulders just barely visible or not at all)? If yes, the WPAP \
convention has overridden the shoulders-up framing rule — REDRAW \
with the pet smaller in frame so the head is roughly the upper half \
and the shoulders + chest are the lower portion. The WPAP-style \
faceting is the LOOK; the framing is shoulders-up, not WPAP-style \
head-only.
- IF UNSURE: zoom OUT, not in. A pet rendered slightly smaller with \
generous headroom AND visible shoulders/chest is ALWAYS better than \
a pet zoomed in tight with clipped ears or a missing torso.

FINAL CHECK BEFORE OUTPUT — EYES ARE NOT HEADLIGHTS, NOT POSSESSED \
(CRITICAL — this is the #1 source of creepy renders):

- WHITE-FUR-AROUND-EYES CHECK (RUN THIS FIRST): Look at the fur \
immediately surrounding each eye opening, within ~15px of the iris. \
Is that fur WHITE / CREAM / IVORY (typical for Goldendoodles, \
Maltipoos, Bichons, white-faced Cavoodles, white-masked Shih Tzus, \
white-masked huskies, white-faced cats)? If YES, the iris MUST be \
the DEEPEST DARK accent in the customer's chosen palette — \
exactly one of the hex codes listed in {{POSTER_ACCENTS}}, NEVER \
a hex code invented outside the palette. NEVER ivory or cream — \
an ivory iris inside a ring of white face fur creates one giant \
fake-sclera blob with a tiny pupil floating in it, which looks \
possessed. NEVER an off-palette colour like "deep teal" rendered \
when the palette doesn't include teal — that produces visible \
palette violations. If the iris is currently ivory/cream OR an \
off-palette colour, REDRAW it as a literal copy of the deepest \
dark hex listed in {{POSTER_ACCENTS}}.

- HEADLIGHT CHECK: Is the iris one of the most-saturated palette \
accents (warm orange, hot red, hot pink, vivid yellow, vivid \
mustard)? If yes, REDRAW in a muted neutral (deep teal, deep navy, \
deep aubergine, charcoal, or — only if the surrounding fur is dark \
— ivory).

- PUPIL SIZE CHECK (HARDEST RULE — DO NOT SKIP): Look at the pupil \
inside each iris. Measure: does the pupil's diameter span at least \
2/3 of the iris's diameter? Does the pupil cover at least 45% of \
the iris area? If the pupil looks like a small dot, a centre dot, \
a poppyseed, or a pinprick inside a much larger iris disc, the \
pupil is TOO SMALL — ENLARGE it until it fills 50–60% of the iris \
area. The eye should read as "mostly dark pupil with a thin ring \
of iris colour around it," not "mostly iris colour with a dark \
speck in the middle." A small pupil inside a large light iris is \
the #1 cause of creepy/possessed renders — fix it before output.

- GAZE COHERENCE CHECK: Do both pupils point in the SAME direction \
relative to their respective irises? If one pupil is centred and \
the other is off-axis, OR if the pupils diverge, OR if they sit at \
different vertical heights inside their irises, REDRAW so both \
pupils sit in the same relative position. Diverging gaze = creepy.

- FLATNESS CHECK: Eye is still exactly TWO flat solid colour blocks: \
iris (one uniform colour) + pupil (one uniform dark shape). NO \
catchlight, NO white glint, NO inner ring, NO gradient, NO sclera, \
NO white whites of the eye visible around the iris (the iris fills \
the entire eye opening).

FINAL CHECK BEFORE OUTPUT — TEXT-FREE GUARANTEE (CRITICAL — read this LAST):
- Scan the WHOLE canvas, especially the empty bg band above the pet's \
head. The image contains ZERO letters, ZERO words, ZERO glyphs, ZERO \
numerals, ZERO letterforms of ANY alphabet (Latin, Cyrillic, CJK, \
Arabic — none), ZERO character-shaped curves, ZERO inscriptions, \
ZERO captions, ZERO signatures, ZERO date stamps, ZERO logos.
- NO pet name anywhere on the canvas. NO "JIM", NO "MAX", NO any \
name. The pet's name is composited onto the image LATER by a separate \
typography pipeline at a precise position with a chosen typeface — \
your job is to deliver a CLEAN portrait with a LETTER-FREE band of \
clean bg colour above the pet's head where the typography pipeline \
will place the name.
- RECURRING FAILURE MODE TO AVOID: rendering 2-4 stylized cubist \
glyphs at the top of the canvas to "feel like a poster." This is \
FORBIDDEN — when the typography pipeline composites the customer's \
real pet name on top, the result is overlapping illegible text \
("JJ I'M" / "JEWILDER" / "MAXMAXX" effects) that looks like a bug \
to the customer. The TOP 22% of the canvas must be uninterrupted \
flat bg colour from the canvas edge down to the pet's ear tips, with \
no decoration of any kind in that band.
- If ANY shape inside the image resembles a letter, redraw it as a \
non-letter polygonal shape or remove it entirely.\
"""

def _bold_graphic_poster_prompt(style_vars: Optional[dict] = None) -> str:
    """Build the bold-graphic-poster prompt with the customer-chosen palette
    interpolated: asymmetric 2-tone bg split colours (seam at _BGP_SEAM_RATIO)
    + saturated accent palette for the pet's cubist faceting. Falls back to
    'teal' if nothing is supplied
    or the id is unknown."""
    palette_id = (style_vars or {}).get("poster_palette") or "teal"
    if palette_id not in POSTER_PALETTES:
        palette_id = "teal"
    p = POSTER_PALETTES[palette_id]
    return (
        _BOLD_GRAPHIC_POSTER_TEMPLATE
        .replace("{{POSTER_BG_LEFT_HEX}}",   p["bg_left_hex"])
        .replace("{{POSTER_BG_LEFT_NAME}}",  p["bg_left_name"])
        .replace("{{POSTER_BG_RIGHT_HEX}}",  p["bg_right_hex"])
        .replace("{{POSTER_BG_RIGHT_NAME}}", p["bg_right_name"])
        .replace("{{POSTER_ACCENTS}}",       p["accents"])
    )


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int]:
    """Parse '#RRGGBB' → (r, g, b) tuple of 0-255 ints."""
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _flatten_poster_bg(
    img: Image.Image,
    palette: dict[str, str],
    perimeter_tol: int = 90,
    interior_tol: int = 90,
) -> Image.Image:
    """Snap near-bg pixels to the exact palette hex on each panel of the
    bold-graphic-poster image (split at _BGP_SEAM_RATIO), killing Gemini's
    recurring "soft room-shadow / inset rectangle / corner patch /
    letterbox bands" hallucinations.

    FULL-CANVAS SNAP — perimeter and interior both default to tol=90:

      Previously the interior of the canvas (where the pet lives) was
      left untouched on the theory that wide-tolerance snap might chip
      pet edges. In practice the inset-rectangle / corner-patch failure
      modes recur in exactly that interior region, and prompt-tightening
      hit a diminishing-returns wall. All 8 Bold Graphic Poster palettes
      are designed so accent pet colours (saturated orange / charcoal /
      ivory / mustard / etc.) sit >190 RGB distance from either bg hex —
      so tol=90 (~52 per channel) cannot accidentally snap a pet
      faceting block.

      Anti-aliased pet/bg edge pixels at >60% bg may get snapped to
      canonical, which sharpens the silhouette by 1-2 px — visually
      cleaner against an already-faceted poster aesthetic, not chipped.
      Pet-side edge pixels (50/50 mix or less bg) sit at ~95+ RGB
      distance from bg, safely outside tol=90.

      Both panels are gated by x position (pixels left of the
      _BGP_SEAM_RATIO mark snap to bg_left only, pixels right of it
      snap to bg_right only).

      perimeter_tol / interior_tol kept as separate kwargs so a future
      palette with a softer bg/accent contrast can dial the interior
      back without losing perimeter aggression.

    NumPy vectorised path used when available (~50× faster on a 1024-tall
    portrait) with a pure-PIL fallback for envs without numpy.
    """
    img = img.convert("RGB")
    w, h = img.size
    bg_left  = _hex_to_rgb(palette["bg_left_hex"])
    bg_right = _hex_to_rgb(palette["bg_right_hex"])
    mid = int(w * _BGP_SEAM_RATIO)
    perimeter_tol_sq = perimeter_tol * perimeter_tol
    interior_tol_sq  = interior_tol  * interior_tol

    # Perimeter safe-zone bands: top band + outer side margins.
    top_band_h    = int(h * 0.18)
    side_margin_w = int(w * 0.10)

    try:
        import numpy as np
        # int32 (not int16) — squared per-channel diffs reach 65025, and
        # summed across 3 channels reach ~195k. Both overflow int16 and
        # wrap to negative values that falsely pass a "< tol_sq" check.
        arr = np.asarray(img, dtype=np.int32).copy()  # h × w × 3, mutable
        bg_left_arr  = np.array(bg_left,  dtype=np.int32)
        bg_right_arr = np.array(bg_right, dtype=np.int32)
        d2_left  = ((arr - bg_left_arr ) ** 2).sum(axis=2)   # h × w
        d2_right = ((arr - bg_right_arr) ** 2).sum(axis=2)   # h × w

        # Build perimeter mask (top + side margins).
        in_top          = np.zeros((h, w), dtype=bool)
        in_left_margin  = np.zeros((h, w), dtype=bool)
        in_right_margin = np.zeros((h, w), dtype=bool)
        in_top[:top_band_h, :]                           = True
        in_left_margin[top_band_h:, :side_margin_w]      = True
        in_right_margin[top_band_h:, w - side_margin_w:] = True
        in_perimeter = in_top | in_left_margin | in_right_margin

        left_half  = np.zeros((h, w), dtype=bool)
        right_half = np.zeros((h, w), dtype=bool)
        left_half[:, :mid]  = True
        right_half[:, mid:] = True

        # Pass 1: perimeter snap (wide tol).
        peri_left  = (d2_left  < perimeter_tol_sq) & in_perimeter & left_half
        peri_right = (d2_right < perimeter_tol_sq) & in_perimeter & right_half
        # Pass 2: interior snap (narrow tol). ~in_perimeter = the central
        # region where the pet lives.
        interior   = ~in_perimeter
        int_left   = (d2_left  < interior_tol_sq)  & interior & left_half
        int_right  = (d2_right < interior_tol_sq)  & interior & right_half

        # Pass 3: cross-half snap. Gemini's "wrong-half rectangle"
        # failure mode drops a patch of the OPPOSITE half's bg colour
        # inside the current half (e.g. a deep-aubergine block in the
        # vivid-violet light half). Same-half snap can never catch it
        # because the patch's RGB distance from the local bg exceeds
        # the tol that protects pet edges. Cross-half snap targets
        # pixels close to the OPPOSITE half's bg and re-paints them
        # with their OWN half's bg so the seam stays clean.
        #
        # Safety: pet accent colours are >190 RGB from both bg hexes
        # by palette design, so the OPPOSITE-bg distance check cannot
        # accidentally repaint a pet faceting block.
        cross_left  = (d2_right < interior_tol_sq) & left_half  & ~peri_right
        cross_right = (d2_left  < interior_tol_sq) & right_half & ~peri_left

        arr[peri_left | int_left | cross_left]    = bg_left_arr
        arr[peri_right | int_right | cross_right] = bg_right_arr
        return Image.fromarray(arr.astype(np.uint8), mode="RGB")
    except ImportError:
        # Pure-PIL fallback. Slow but correct — mirrors the same-half +
        # cross-half logic from the numpy path so the "wrong-half
        # rectangle" failure mode is caught regardless of which path
        # runs at request time.
        px = img.load()
        for y in range(h):
            in_top = y < top_band_h
            for x in range(w):
                in_left_margin  = (not in_top) and x < side_margin_w
                in_right_margin = (not in_top) and x >= w - side_margin_w
                in_perimeter    = in_top or in_left_margin or in_right_margin
                tol_sq = perimeter_tol_sq if in_perimeter else interior_tol_sq
                r, g, b = px[x, y]
                if x < mid:
                    own_r, own_g, own_b   = bg_left
                    other_r, other_g, other_b = bg_right
                else:
                    own_r, own_g, own_b   = bg_right
                    other_r, other_g, other_b = bg_left
                d2_own   = (r - own_r) ** 2 + (g - own_g) ** 2 + (b - own_b) ** 2
                d2_other = (r - other_r) ** 2 + (g - other_g) ** 2 + (b - other_b) ** 2
                # Same-half snap (own bg) OR cross-half snap (other bg's
                # interior tol) → repaint with OWN-half bg so the seam
                # stays clean.
                if d2_own < tol_sq or d2_other < interior_tol_sq:
                    px[x, y] = (own_r, own_g, own_b)
        return img

_CHARCOAL_TEMPLATE = """\
Transform this photo into a hand-drawn fine-art charcoal pet portrait on warm cream paper.

COLOR ACCURACY — THIS IS CRITICAL:
- Charcoal is monochromatic — render the pet in soft graphite/charcoal greys, \
with confident dark accents on the nose, eyes, mouth, and shadow areas, and \
lighter strokes on bright fur (cheek, brow, chest). The pet's true coat \
markings still come through as charcoal density variation: a black dog reads \
as deep saturated charcoal; a white cat reads as soft pencil hatching with \
mostly cream paper showing through; tabby/spotted/patched coats keep their \
distinguishing pattern in charcoal density.
- Eyes are alive — keep small white catchlights and the natural eye colour \
suggestion through subtle warmth in the iris.

STYLE:
- Hand-drawn fine-art charcoal sketch on textured warm cream paper, \
expressive but disciplined.
- Loose hatching for fur direction; richer charcoal density on the nose, \
eyes, and shadow areas; lighter strokes on the brighter cheek and chest fur.
- Slightly rough edges where charcoal strokes end. A few stray strokes near \
the body suggesting hand-drawn movement and life — never geometric.
- Loose chest line that organically dissolves into the paper rather than \
ending in a hard cut.
- High-end pet portrait artist's piece. Premium, intimate, museum-quality.
- Fine art illustration style, high resolution 300dpi, print-ready.

UPPER BAND — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for hand-drawn type:
- The pet's head, ears, charcoal strokes, stray marks, and ANY hatching \
MUST stay BELOW y=22% of the canvas. Top of the tallest ear sits at \
y≈25-28% — never closer to the canvas top.
- Within the top ~22%, only the bare warm cream paper (#F4EFE7) shows — \
no charcoal strokes, no hatching, no stray marks, no smudges. The same \
calm paper continues edge-to-edge through this band.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation).
- Head and chest, calm pose, direct gentle gaze.
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved upper band (top ~22%) is what creates the breathing room \
above the ears.
- BOTTOM SILHOUETTE — CRITICAL: the chest must dissolve organically into the \
paper texture with looser strokes, never end in a flat horizontal cut.
- BACKGROUND: warm cream paper texture (#F4EFE7 base) with subtle organic \
paper-fibre grain extending uniformly to all four edges. The cream hue is \
the EXACT SAME RGB tone in every region of the canvas — top edge, bottom \
edge, all four corners, behind the pet, above the pet, in the upper safe \
zone. The paper does NOT shift warmer near the top, cooler near the bottom, \
or pinker / rosier / peachier in any region. NO rectangles, NO frames, NO \
inner panel of a different shade, NO mat, NO border, NO letterbox bar, NO \
geometric splits, NO tea-stain wash, NO aged-paper vignette, NO subtle \
pink / rose / peach cast at the edges or corners, NO colour gradient \
across the paper. Pet and paper are drawn in the same medium in the same \
pass.
- RECURRING FAILURE MODE TO AVOID: a faint rosy / warm-pink / peach cast \
across the top portion of the canvas (especially in the upper band \
or above the pet's head) — Gemini sometimes mimics aged-paper / \
tea-stained-paper / vintage-photograph effects that introduce a localised \
warm tint. The paper is FLAT uniform #F4EFE7 cream — never a rosy wash, \
never a warm vignette, never a coloured halo around the pet. If any pixel \
in the bg is more saturated than the base cream (e.g. a pink-tinted band \
along the top or a peachy glow behind the pet), the image is wrong.
- Do NOT include any text, words, letters, watermarks, or signatures anywhere.

Avoid: photography, photorealism, oil paint, watercolor, ink wash, neon, \
saturated colour, gradients, drop shadows, 3D render, cartoon, anime, \
text, watermark, border, decorative shapes, geometric ornaments, halos, \
frames, anything other than a charcoal-on-cream sketch of the pet.\
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

UPPER BAND — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the type:
- The pet's head, ears, fur fly-aways, and the most intense portion of \
the aura halo MUST stay BELOW y=22% of the canvas. Top of the tallest \
ear sits at y≈25-28% — never closer to the canvas top.
- Within the top ~22%, the gradient continues edge-to-edge but stays \
calm and uniform — soft outer aura tones only, no light bloom peaks, \
no lens flare, no high-contrast accents inside this band.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, serene expression, gentle eye contact
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved upper band (top ~22%) is what creates the breathing room \
above the ears; the aura still radiates BELOW the chest.
- The BACKGROUND (aura gradient) radiates symmetrically and fills every edge \
as one continuous smooth wash. No reserved panels, bars, color blocks, or \
empty bands anywhere
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
- White paper background with natural watercolor bleed edges in {{WATERCOLOR_WASH_NAME}} tones
- Painterly fur texture with subtle fine ink linework on facial features
- Warm soft lighting, no harsh shadows
- Fine art illustration style, high resolution 300dpi, print-ready

PAPER SURFACE — CRITICAL:
- The background is PLAIN WATERCOLOR PAPER. Nothing else. No wall, no \
shelf, no table, no floor, no surface the pet is "sitting on" or \
"placed against." The pet floats on paper.
- ABSOLUTELY NO HORIZONTAL LINES, STREAKS, BANDS, OR STRIPES anywhere \
in the background or behind the pet — at any opacity, in any colour. \
This includes (forbidden, all of them): wood grain, wood panelling, \
shiplap, plank lines, parquet, beadboard, slat walls, fence boards, \
floorboards, table edges, shelf lines, wainscoting, horizon lines, \
counter lines, picture-rail mouldings, baseboard mouldings, ruled \
notebook lines, washi-tape edges, banding from a printing pass, \
posterised stripes, scanner lines, any horizontal mark that suggests \
a built environment behind or beneath the subject.
- NO vertical lines either: no plank seams, no wall corners, no door \
edges, no curtain folds, no window frames.
- NO surface texture: no canvas weave, no linen, no rough plaster, no \
concrete, no marble, no granite, no fabric, no leather, no metal. \
Just clean white watercolor paper with the watercolor wash painted \
on it. RECURRING FAILURE MODE TO AVOID: faint horizontal pencil-like \
streaks running across the lower third of the canvas, OR drifting \
across the RIGHT or LEFT third at chest/leg height (the model \
imagining a wooden floor, shelf, table edge, baseboard, or counter \
the pet is on or near). DO NOT add these streaks at any opacity, \
in any colour, at any length, anywhere on the canvas — including \
short fragments that only span 10-30% of the width. If a viewer can \
trace a straight or near-straight horizontal segment on the canvas, \
the image is wrong.

WATERCOLOR MARK COLOUR — CRITICAL:
- The painted watercolor wash marks, color halo, wet bleeds, and \
atmospheric washes surrounding the pet are rendered in \
{{WATERCOLOR_WASH_NAME}} ({{WATERCOLOR_WASH_HEX}}) tones. This is the \
dominant tint for ALL painted marks and atmospheric color in the artwork. \
The paper surface underneath remains plain clean white — NOT tinted, NOT \
coloured, NOT off-white. The paper is white; only the painted marks carry \
the selected colour.
- The pet's coat color is faithfully preserved from the uploaded photo — \
the mark colour tints the background washes, not the animal.

WASH SHAPE — CRITICAL:
- The wash is a SOFT, ROUNDED, ORGANIC HALO around the pet. Edges of \
the wash are irregular petal shapes, lobed and curved, with feathered \
wet-on-wet bleeds. The wash CAN and SHOULD bleed and wrap around the \
sides of the canvas — that is encouraged — but ONLY as soft rounded \
bleeds, never as straight bands or horizontal striations.
- The wash NEVER has a straight edge. Not at the top, not at the \
bottom, not at either side. No flat horizon, no flat shoreline, no \
flat baseline, no ruled-line termination. If the wash thins out or \
fades, it does so in soft scalloped curves, not in straight cuts.
- Splatters and dots are fine. Lines are not. A "splash" is allowed; \
a "stripe" is not.

WATERCOLOR WASH COVERAGE — CRITICAL:
- The painted watercolor wash (the soft wet bleeds, washes, and color halo \
that surround the pet) MUST extend organically toward the LEFT and RIGHT \
edges of the canvas, reaching at least ~85-90% of the image width with \
visible painterly color, bleed, and atmospheric tint. Never confine the \
wash to a narrow column directly behind the pet — never leave large strips \
of bare untouched white paper at the left or right margins. The wash \
breathes outward from the pet in soft, irregular, organic petals; the \
edges of the canvas can fade back to clean paper, but the painted area is \
clearly wider than the pet itself.
- The wash extends BELOW the chest where applicable, and may breathe \
sideways into the upper corners — but the TOP of the canvas above the \
pet is treated as a calm clean-paper area (see UPPER BAND).

NAME CLEARANCE — CRITICAL: A handwritten pet name will be composited \
into the upper portion of the finished image. The pet must be positioned \
so the upper area is free of subject detail:
- The pet's head, ears, fur fly-aways, watercolor splatters, dark wash \
strokes, and ink linework MUST stay BELOW y=22% of the canvas. The top \
of the tallest ear sits at y≈25-28% — never closer to the canvas top.
- The paper / wash above the pet is the SAME continuous medium as the \
rest of the image — same paper colour, same ambient wash tone, same \
texture. There is NO separate top zone, NO tinted band, NO panel, NO \
strip, NO horizontal seam, NO gradient transition, NO change of colour \
or value where the wash thins out above the head. The wash simply \
breathes more lightly toward the top because the pet isn't there — but \
the paper underneath it is one uninterrupted sheet, edge to edge.
- ABSOLUTELY NO horizontal edge or colour shift across the canvas at \
any height — no band of warmer cream above a cooler white, no rectangle \
of tinted paper sitting over the rest, no visible transition line. If a \
viewer can point to a horizontal y-coordinate and say "the colour \
changes here", the image is wrong.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Slight natural vignette
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved upper band (top ~22%) is what creates the breathing \
room above the ears; the pet still feels confidently present below it.
- The BACKGROUND (watercolor wash and natural bleed edges) extends to every \
edge of the canvas. No reserved panels, bars, color blocks, or empty bands
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, harsh shadows, dark background, pixelation, \
blurry, low resolution, cartoon, anime, 3D render, clipping, text, watermark, border, \
narrow watercolor wash column with bare white paper at the sides, \
horizontal streaks, horizontal lines, horizontal bands, horizontal stripes, \
horizontal pencil marks, faint horizontal scuffs, ruled-out floor lines, \
right-side floor streaks, left-side floor streaks, partial horizontal \
fragments behind the pet's legs or chest, straight wash edges, ruled \
wash terminations, flat-bottomed wash, flat-topped wash, \
top tint band, top colour band, top warm-cream band over cooler paper, \
horizontal seam between a tinted top zone and the rest of the canvas, \
visible y-axis colour transition at any height, two-tone paper split, \
wood grain, wood panelling, shiplap, plank lines, floorboards, shelf, \
table, table edge, counter line, baseboard, wall texture, surface \
texture behind the pet, ruled lines, banding, scanner lines.\
"""

def _trim_off_palette_margin(
    img: Image.Image,
    palette: dict[str, str],
    bg_match_tol: int = 70,
    row_threshold: float = 0.55,
) -> Image.Image:
    """Crop perimeter rows/columns that don't match the canonical bg.

    Generalizes the old "_trim_cream_margin" helper. Any margin row/col
    where fewer than `row_threshold` of pixels are within `bg_match_tol`
    RGB distance of EITHER canonical bg colour gets trimmed.

    Catches both directions of Gemini's edge-to-edge failures:
      - cream / near-white sliver above the colored split (old bug)
      - subtle perimeter darkening / vignette (new bug — visible as a
        darker frame between the bleed and the actual artwork in the
        printed canvas mockup)

    Bails if the trim would crop more than 50% of the image (safety
    against false positives — e.g. a very dark portrait that legitimately
    fills the perimeter).
    """
    rgb = img.convert("RGB") if img.mode != "RGB" else img
    w, h = rgb.size
    bg_left = _hex_to_rgb(palette["bg_left_hex"])
    bg_right = _hex_to_rgb(palette["bg_right_hex"])
    tol_sq = bg_match_tol * bg_match_tol

    try:
        import numpy as np
        arr = np.asarray(rgb, dtype=np.int32)
        # Distance² to each canonical bg colour; "matches" if within tol of either.
        dl = (arr - np.array(bg_left, dtype=np.int32)) ** 2
        dr = (arr - np.array(bg_right, dtype=np.int32)) ** 2
        match_left = dl.sum(axis=2) <= tol_sq
        match_right = dr.sum(axis=2) <= tol_sq
        match_bg = match_left | match_right
        row_match_frac = match_bg.mean(axis=1)
        col_match_frac = match_bg.mean(axis=0)
    except ImportError:
        # Pure-PIL fallback — slower but correct.
        def _frac_match(strip: Image.Image) -> float:
            n = strip.width * strip.height
            if n == 0:
                return 0.0
            count = 0
            for px in strip.getdata():
                dl_v = (px[0] - bg_left[0])**2 + (px[1] - bg_left[1])**2 + (px[2] - bg_left[2])**2
                dr_v = (px[0] - bg_right[0])**2 + (px[1] - bg_right[1])**2 + (px[2] - bg_right[2])**2
                if dl_v <= tol_sq or dr_v <= tol_sq:
                    count += 1
            return count / n
        row_match_frac = [_frac_match(rgb.crop((0, y, w, y + 1))) for y in range(h)]
        col_match_frac = [_frac_match(rgb.crop((x, 0, x + 1, h))) for x in range(w)]

    # Walk INWARD from each edge, trimming rows/columns where fewer than
    # `row_threshold` of pixels match either canonical bg. Stop as soon as
    # we hit a row/col that's clearly content (matches threshold).
    top = 0
    while top < h and row_match_frac[top] < row_threshold:
        top += 1
    bottom = h
    while bottom > top and row_match_frac[bottom - 1] < row_threshold:
        bottom -= 1
    left = 0
    while left < w and col_match_frac[left] < row_threshold:
        left += 1
    right = w
    while right > left and col_match_frac[right - 1] < row_threshold:
        right -= 1

    if (top, left, bottom, right) == (0, 0, h, w):
        return rgb
    # Safety: bail if we'd crop more than 50% of either dimension. That
    # would indicate a darker portrait whose perimeter is genuinely off-
    # palette but still legitimate — better to leave it than gouge the
    # artwork.
    if (bottom - top) < int(h * 0.5) or (right - left) < int(w * 0.5):
        log.info(
            "bold-graphic-poster: skipped trim (would crop >50%%): top=%d left=%d bottom=%d right=%d",
            top, left, h - bottom, w - right,
        )
        return rgb
    log.info(
        "bold-graphic-poster: trimmed off-palette margin top=%d left=%d bottom=%d right=%d (was %dx%d)",
        top, left, h - bottom, w - right, w, h,
    )
    return rgb.crop((left, top, right, bottom))


def _pad_split_bg(
    img: Image.Image,
    palette: dict[str, str],
    padding_ratio: float = 0.12,
    pad_bottom_ratio: float = 0.0,
) -> Image.Image:
    """Pad a bold-graphic-poster front face onto a larger canvas whose
    bleed band is filled with the canonical 2-panel split — left panel
    bg_left, right panel bg_right — extending the seam vertically.

    Replaces edge-replication padding for bold-graphic-poster: when
    Gemini leaves a sliver of cream/white outside the colored split
    (a recurring failure mode where it doesn't honour edge-to-edge),
    edge-replication propagates the cream into the bleed band and the
    customer sees a visible cream border around the artwork. Drawing
    the canonical split first and pasting the AI image centered on
    top eliminates that whole class of error.

    The AI image is pasted centered, and the bleed-band seam is placed
    at the embedded image's seam x-position (pad_w + w*_BGP_SEAM_RATIO),
    NOT at the new canvas's _BGP_SEAM_RATIO mark — those differ once
    horizontal padding is added, and any mismatch would show as a
    visible vertical step at the bleed boundary.
    """
    rgb = img.convert("RGB") if img.mode != "RGB" else img
    w, h = rgb.size
    pad_w = int(w * padding_ratio)
    pad_h = int(h * padding_ratio)
    pad_b = int(h * pad_bottom_ratio)

    bg_left = _hex_to_rgb(palette["bg_left_hex"])
    bg_right = _hex_to_rgb(palette["bg_right_hex"])

    target_w = w + 2 * pad_w
    target_h = h + pad_h + pad_b

    out = Image.new("RGB", (target_w, target_h), bg_left)
    mid = pad_w + int(w * _BGP_SEAM_RATIO)
    right_half = Image.new("RGB", (target_w - mid, target_h), bg_right)
    out.paste(right_half, (mid, 0))
    out.paste(rgb, (pad_w, pad_h))
    return out


def _remove_poster_halos(
    img: Image.Image,
    palette: dict[str, str],
    white_tol: int = 20,
    halo_thickness_px: int = 3,
) -> Image.Image:
    """Erase the thin near-white halo Gemini sometimes leaves between dark
    line work and the colored background in bold-graphic-poster output.

    Distinguishes halos from intentional white fur by THICKNESS — halos
    are thin (1–3 px) strips at dark/bg boundaries; fur is a wider
    connected region. Erodes the near-white mask by `halo_thickness_px`
    to keep only "fat" white (fur), then recolors the eroded-off thin
    strips to the local bg hex (left panel → bg_left, right panel →
    bg_right; seam at _BGP_SEAM_RATIO).

    `white_tol=20` keeps the threshold (235 per channel) above palette
    ivory accents like #F4EFE7 (b=231), so genuine ivory stays untouched.

    Pure-PIL implementation so no scipy/cv2 dependency is needed.
    """
    from PIL import ImageChops, ImageFilter

    img = img.convert("RGB")
    w, h = img.size
    bg_left = _hex_to_rgb(palette["bg_left_hex"])
    bg_right = _hex_to_rgb(palette["bg_right_hex"])
    mid = int(w * _BGP_SEAM_RATIO)

    # Per-channel threshold mask: pixel is "near-white" only if r,g,b all
    # exceed (255 - white_tol).
    threshold = 255 - white_tol
    r, g, b = img.split()
    mask_r = r.point(lambda v: 255 if v > threshold else 0, "L")
    mask_g = g.point(lambda v: 255 if v > threshold else 0, "L")
    mask_b = b.point(lambda v: 255 if v > threshold else 0, "L")
    is_white = ImageChops.multiply(ImageChops.multiply(mask_r, mask_g), mask_b)
    is_white = is_white.point(lambda v: 255 if v > 0 else 0, "L")

    # Erode `halo_thickness_px` times — anything that survives is "fat"
    # white (fur). Anything that doesn't is a thin halo strip.
    fat_white = is_white
    for _ in range(halo_thickness_px):
        fat_white = fat_white.filter(ImageFilter.MinFilter(3))
    halo_mask = ImageChops.subtract(is_white, fat_white)

    if halo_mask.getbbox() is None:
        return img  # no halo pixels → fast path

    # Safety gate: only recolour thin whites that sit adjacent to a
    # canonical-bg pixel (because _flatten_poster_bg ran first, "canonical
    # bg" means an exact RGB match with bg_left or bg_right). Thin whites
    # *inside* the pet (e.g. a narrow neck of white fur connecting two
    # wider patches) are surrounded by pet colours, not bg, so they fall
    # outside the dilated bg mask and stay untouched.
    def _exact_match_mask(channel: Image.Image, target: int) -> Image.Image:
        return channel.point(lambda v, t=target: 255 if v == t else 0, "L")

    bg_left_mask = ImageChops.multiply(
        ImageChops.multiply(_exact_match_mask(r, bg_left[0]), _exact_match_mask(g, bg_left[1])),
        _exact_match_mask(b, bg_left[2]),
    )
    bg_right_mask = ImageChops.multiply(
        ImageChops.multiply(_exact_match_mask(r, bg_right[0]), _exact_match_mask(g, bg_right[1])),
        _exact_match_mask(b, bg_right[2]),
    )
    is_bg = ImageChops.add(bg_left_mask, bg_right_mask).point(
        lambda v: 255 if v > 0 else 0, "L"
    )
    # Dilate bg mask by halo_thickness_px + 2 so any halo pixel within
    # that distance of a bg pixel is gated in.
    bg_dilated = is_bg
    for _ in range(halo_thickness_px + 2):
        bg_dilated = bg_dilated.filter(ImageFilter.MaxFilter(3))
    halo_mask = ImageChops.multiply(halo_mask, bg_dilated).point(
        lambda v: 255 if v > 0 else 0, "L"
    )

    if halo_mask.getbbox() is None:
        return img

    # Build a "left bg | right bg" composite that covers the whole canvas,
    # then paste through the halo mask so only halo pixels get recoloured.
    bg_full = Image.new("RGB", (w, h), bg_left)
    right_half = Image.new("RGB", (w - mid, h), bg_right)
    bg_full.paste(right_half, (mid, 0))

    out = img.copy()
    out.paste(bg_full, (0, 0), halo_mask)
    log.info(
        "bold-graphic-poster: halo removed (mask bbox=%s)",
        halo_mask.getbbox(),
    )
    return out


def _bgp_reframe_anchor_bottom(
    img: Image.Image,
    palette: dict[str, str],
    top_room_ratio: float = 0.16,
    bottom_pad_ratio: float = 0.0,
    side_pad_ratio: float = 0.06,
    target_aspect: tuple = (4, 5),
    pet_tol: int = 30,
) -> Image.Image:
    """Reframe a bold-graphic-poster portrait so the pet is anchored at the
    bottom of the canvas, sized to fill confidently, with symmetric L/R
    padding and configurable top room for breathing space (or for a name
    composited later). Mirror of _line_art_reframe_anchor_bottom but for
    the 2-panel poster bg.

    REQUIRES the input to have already been palette-snapped via
    _snap_poster_to_palette (max_distance≈45) so bg pixels are EXACT
    bg_left or bg_right hex. Pet detection is then a pure-equality
    test ("pixel is bg if it matches either canonical bg hex within
    pet_tol RGB, otherwise it's pet"), which is robust against Gemini's
    anti-aliased intermediates that the snap pass already collapsed.

    Builds a fresh canvas at target_aspect filled with the canonical
    2-panel split (seam at _BGP_SEAM_RATIO from the left), then
    composites ONLY the pet silhouette onto it via a mask. Bbox bg
    pixels do NOT get pasted — the new canvas's panel bg fills those
    regions. This keeps the seam pixel-perfect across the full canvas
    and prevents the "old bbox bg colour bleeding into the new wrong
    panel" failure mode that a naive paste would produce.

    `top_room_ratio` is the only knob you turn between no-name (0.16:
    pet dominates with breathing room) and with-name (0.24: dedicated
    band for the name above the pet's head).
    """
    # Defensive palette snap so bbox detection is robust to anti-aliased
    # intermediates left by any upstream LANCZOS resize at the seam.
    # Idempotent if the caller already snapped (the snap function is a
    # no-op when every pixel is already at exact palette).
    rgb = _snap_poster_to_palette(img, palette, max_distance=45).convert('RGB')
    src_w, src_h = rgb.size

    bg_left  = _hex_to_rgb(palette["bg_left_hex"])
    bg_right = _hex_to_rgb(palette["bg_right_hex"])

    try:
        import numpy as np
        arr = np.asarray(rgb, dtype=np.int32)
        bg_l = np.array(bg_left,  dtype=np.int32)
        bg_r = np.array(bg_right, dtype=np.int32)
        d2_l = ((arr - bg_l) ** 2).sum(axis=2)
        d2_r = ((arr - bg_r) ** 2).sum(axis=2)
        tol_sq = pet_tol * pet_tol
        # pet = neither bg_left nor bg_right (within tolerance)
        is_pet = (d2_l > tol_sq) & (d2_r > tol_sq)
        if not is_pet.any():
            return img
        ys, xs = np.where(is_pet)
        fg_min_x = int(xs.min())
        fg_max_x = int(xs.max())
        fg_min_y = int(ys.min())
        fg_max_y = int(ys.max())
    except ImportError:
        # Pure-PIL fallback — slower but correct.
        px = rgb.load()
        fg_min_x, fg_max_x = src_w, 0
        fg_min_y, fg_max_y = src_h, 0
        found = False
        tol_sq = pet_tol * pet_tol
        step = max(1, min(src_w, src_h) // 600)
        for y in range(0, src_h, step):
            for x in range(0, src_w, step):
                p = px[x, y]
                d2_l = (p[0] - bg_left[0]) ** 2 + (p[1] - bg_left[1]) ** 2 + (p[2] - bg_left[2]) ** 2
                d2_r = (p[0] - bg_right[0]) ** 2 + (p[1] - bg_right[1]) ** 2 + (p[2] - bg_right[2]) ** 2
                if d2_l > tol_sq and d2_r > tol_sq:
                    if x < fg_min_x: fg_min_x = x
                    if x > fg_max_x: fg_max_x = x
                    if y < fg_min_y: fg_min_y = y
                    if y > fg_max_y: fg_max_y = y
                    found = True
        if not found:
            return img

    # Inset slightly so anti-aliased pet edges don't get chipped.
    inset = max(int(min(src_w, src_h) // 200), 4)
    fg_min_x = max(0, fg_min_x - inset)
    fg_min_y = max(0, fg_min_y - inset)
    fg_max_x = min(src_w, fg_max_x + inset)
    fg_max_y = min(src_h, fg_max_y + inset)

    pet_w = fg_max_x - fg_min_x
    pet_h = fg_max_y - fg_min_y

    # Compute target canvas size from pet height + top/bottom budget.
    # If the resulting pet_w would exceed the side-pad budget, scale
    # the canvas up so side padding is preserved (top room grows as
    # a side effect — acceptable, beats cropping the pet).
    target_w_ratio, target_h_ratio = target_aspect
    target_aspect_ratio = target_w_ratio / target_h_ratio
    available_pet_h_frac = 1.0 - top_room_ratio - bottom_pad_ratio
    if available_pet_h_frac <= 0.1:
        available_pet_h_frac = 0.5
    canvas_h = int(round(pet_h / available_pet_h_frac))
    canvas_w = int(round(canvas_h * target_aspect_ratio))
    available_pet_w_frac = 1.0 - 2 * side_pad_ratio
    max_pet_w = int(round(canvas_w * available_pet_w_frac))
    if pet_w > max_pet_w:
        canvas_w = int(round(pet_w / available_pet_w_frac))
        canvas_h = int(round(canvas_w / target_aspect_ratio))

    # Crop pet bbox + build a mask of "is pet" pixels inside the crop
    # so we composite ONLY the pet onto the new bg (bbox bg pixels
    # don't get pasted across the new seam).
    cropped = rgb.crop((fg_min_x, fg_min_y, fg_max_x, fg_max_y))
    try:
        import numpy as np
        cropped_arr = np.asarray(cropped, dtype=np.int32)
        d2_l_crop = ((cropped_arr - np.array(bg_left,  dtype=np.int32)) ** 2).sum(axis=2)
        d2_r_crop = ((cropped_arr - np.array(bg_right, dtype=np.int32)) ** 2).sum(axis=2)
        is_pet_crop = (d2_l_crop > tol_sq) & (d2_r_crop > tol_sq)
        mask_arr = (is_pet_crop * 255).astype('uint8')
        mask = Image.fromarray(mask_arr, 'L')
    except ImportError:
        # Pure-PIL fallback for the mask.
        mask = Image.new('L', cropped.size, 0)
        mp = mask.load()
        cp = cropped.load()
        for y in range(cropped.height):
            for x in range(cropped.width):
                p = cp[x, y]
                d2_l = (p[0] - bg_left[0]) ** 2 + (p[1] - bg_left[1]) ** 2 + (p[2] - bg_left[2]) ** 2
                d2_r = (p[0] - bg_right[0]) ** 2 + (p[1] - bg_right[1]) ** 2 + (p[2] - bg_right[2]) ** 2
                if d2_l > tol_sq and d2_r > tol_sq:
                    mp[x, y] = 255

    # Bump the canvas to print resolution BEFORE building the bg, so the
    # 2-panel split is laid down at full resolution with a pixel-perfect
    # seam — never interpolated. The pet crop is scaled separately
    # (with its mask) and composited on top, so seam pixels stay exact.
    min_w, min_h = PORTRAIT_MIN_SIZE
    if canvas_w < min_w or canvas_h < min_h:
        scale = max(min_w / canvas_w, min_h / canvas_h)
        canvas_w_final = int(round(canvas_w * scale))
        canvas_h_final = int(round(canvas_h * scale))
        new_pet_w = int(round(pet_w * scale))
        new_pet_h = int(round(pet_h * scale))
        cropped = cropped.resize((new_pet_w, new_pet_h), Image.LANCZOS)
        mask = mask.resize((new_pet_w, new_pet_h), Image.LANCZOS)
        pet_w, pet_h = new_pet_w, new_pet_h
        canvas_w, canvas_h = canvas_w_final, canvas_h_final

    # Build the fresh 2-panel canvas with the canonical seam at the
    # FINAL resolution — no later resize, so the seam is pixel-sharp.
    out = Image.new('RGB', (canvas_w, canvas_h), bg_left)
    seam_x = int(round(canvas_w * _BGP_SEAM_RATIO))
    right_panel = Image.new(
        'RGB', (canvas_w - seam_x, canvas_h), bg_right,
    )
    out.paste(right_panel, (seam_x, 0))

    # Place the pet centred horizontally, anchored at canvas bottom.
    bottom_pad_px = int(round(canvas_h * bottom_pad_ratio))
    paste_y = canvas_h - bottom_pad_px - pet_h
    paste_x = (canvas_w - pet_w) // 2
    out.paste(cropped, (paste_x, paste_y), mask)
    return out


def _bgp_open_name_band(image: Image.Image, palette: dict[str, str]) -> Image.Image:
    """Re-canvas a bold-graphic-poster post-processed master to open extra
    top room for a composited name. Mirror of _modern_open_name_band /
    _line_art_open_name_band but for the 2-panel poster bg.

    Different ratios for 4:5 (24% top — gives the name a clear band
    above the pet's head while keeping the pet sized to dominate) vs
    1:1 (28% top — square has less vertical room to amortise name +
    pet over).
    """
    is_square = (
        image.height > 0
        and abs((image.width / image.height) - 1.0) < 0.05
    )
    if is_square:
        return _bgp_reframe_anchor_bottom(
            image, palette,
            top_room_ratio=0.28,
            bottom_pad_ratio=0.0,
            side_pad_ratio=0.06,
            target_aspect=(1, 1),
        )
    return _bgp_reframe_anchor_bottom(
        image, palette,
        top_room_ratio=0.24,
        bottom_pad_ratio=0.0,
        side_pad_ratio=0.06,
        target_aspect=(4, 5),
    )


def _snap_poster_to_palette(
    img: Image.Image,
    palette: dict[str, str],
    max_distance: Optional[int] = None,
) -> Image.Image:
    """Snap pixels in the image to the NEAREST canonical palette colour
    (bg_left, bg_right, or one of the accent hexes).

    `max_distance` (RGB Euclidean) gates the snap:

      None  → snap every pixel to its nearest palette colour. Use only
              when you're certain the image already lives entirely
              inside the palette (catastrophic if any pet has off-
              palette fur — collapses the whole pet into bg).

      45-50 → snap only pixels within ~45 RGB of a palette colour. This
              is the SAFE setting for cleaning anti-aliased edge
              halos: anti-aliased intermediates between two palette
              colours sit ~10-30 RGB from the nearest member, so they
              get snapped clean. Genuinely off-palette pet pixels
              (warm-coat dog on a cool palette, etc.) sit >100 RGB
              from any member and stay untouched. Eliminates the
              "transparent-PNG-pasted-on-bg" white-fringe look that
              destroys print quality on canvas.

    Result: silhouette and inter-block edges become razor sharp at the
    pixel level — no white fringing, no light-mauve halos, no soft
    intermediate pixels between accent and bg. Reads as a clean vector
    illustration rather than a low-res screen-print scan.

    Implementation: vectorised numpy distance computation. ~150ms on a
    1024×1024 image with 9 palette colours.
    """
    import re as _re
    rgb = img.convert("RGB") if img.mode != "RGB" else img

    # Parse accent hexes from the palette's "accents" string.
    accents_str = palette.get("accents", "")
    accent_hexes = _re.findall(r'#[0-9A-Fa-f]{6}', accents_str)

    # Build the target palette: bg_left, bg_right, accents, plus the
    # tongue exception (warm coral pink #E07A6B for visible tongues —
    # see the prompt's TONGUE EXCEPTION block).
    palette_colors: list[tuple[int, int, int]] = [
        _hex_to_rgb(palette["bg_left_hex"]),
        _hex_to_rgb(palette["bg_right_hex"]),
    ]
    for hex_str in accent_hexes:
        palette_colors.append(_hex_to_rgb(hex_str))
    palette_colors.append((224, 122, 107))  # tongue coral

    try:
        import numpy as np
        arr = np.asarray(rgb, dtype=np.int32)               # (H, W, 3)
        palette_arr = np.array(palette_colors, dtype=np.int32)  # (N, 3)
        # Compute distance from each pixel to each palette colour.
        # Reshape so we can broadcast: arr (H,W,1,3), palette (1,1,N,3).
        diff = arr[:, :, None, :] - palette_arr[None, None, :, :]
        dist = (diff * diff).sum(axis=-1)                   # (H, W, N)
        nearest_idx = dist.argmin(axis=-1)                  # (H, W)
        snapped = palette_arr[nearest_idx].astype(np.uint8) # (H, W, 3)

        if max_distance is None:
            log.info(
                "bold-graphic-poster: snapped ALL pixels to %d-colour palette",
                len(palette_colors),
            )
            return Image.fromarray(snapped, "RGB")

        # Gated snap: only pixels within max_distance of nearest palette
        # member get snapped. Genuinely off-palette pixels stay as-is.
        max_dist_sq = max_distance * max_distance
        nearest_dist_sq = np.take_along_axis(
            dist, nearest_idx[:, :, None], axis=-1
        ).squeeze(-1)                                       # (H, W)
        snap_mask = nearest_dist_sq <= max_dist_sq          # (H, W)
        out = arr.copy()
        out[snap_mask] = snapped[snap_mask]
        snapped_pct = float(snap_mask.sum()) / snap_mask.size * 100
        log.info(
            "bold-graphic-poster: gated palette-snap (max_distance=%d) "
            "snapped %.1f%% of pixels (anti-aliased edges → exact palette)",
            max_distance, snapped_pct,
        )
        return Image.fromarray(out.astype(np.uint8), "RGB")
    except ImportError:
        log.warning(
            "bold-graphic-poster: numpy not available — skipping palette snap"
        )
        return img
    except Exception as exc:
        log.warning(
            "bold-graphic-poster: palette snap failed (%s) — returning original",
            exc,
        )
        return img


def build_watercolor_prompt(style_vars: Optional[dict] = None) -> str:
    color_id = (style_vars or {}).get("watercolor_bg") or "paper"
    if color_id not in WATERCOLOR_BG_COLORS:
        color_id = "paper"
    hex_code, name = WATERCOLOR_BG_COLORS[color_id]
    return (
        _WATERCOLOR_TEMPLATE
        .replace("{{WATERCOLOR_WASH_HEX}}", hex_code)
        .replace("{{WATERCOLOR_WASH_NAME}}", name)
    )


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
Transform this photo into a SINGLE-LINE continuous-line pet portrait \
rendered in warm ivory ink on a solid deep-dark field — the same \
one-stroke aesthetic as the light version, but inverted: one unbroken \
ivory line laid down on top of black/charcoal/navy paper.

ONE CONTINUOUS LINE — THIS IS CRITICAL:
- The whole portrait reads as ONE UNINTERRUPTED IVORY LINE. There are no \
separate strokes, no detached marks, no "floating" features — every \
element of the pet (each ear, each eye, the snout, the chest contour, \
fur indications) is reached by continuing that same single line.
- The line may LOOP, curve back, double-back, and cross over itself — \
but it must remain CONNECTED throughout. Think Picasso's continuous-line \
animal sketches translated into white-ink-on-black-paper. Elegant, \
gestural, confident — one fluid motion of the pen.
- Eyes, nostrils, and mouth are NOT drawn as separate dots — they are \
formed by the line briefly looping around to imply the feature, then \
continuing onward.
- Use the pet's actual coat markings only as a SUGGESTION for where the \
line might curve or loop. Never render markings as filled shapes or as \
a second separate line.

LINE QUALITY & MEDIUM:
- Single, even, uniform line weight from start to finish.
- Linework is warm ivory / cream (#F3EFE4). Surface is a SOLID DEEP DARK \
field — pick ONE and hold it across the whole image: deep charcoal \
(#1A1A1A), midnight navy (#0E1424), or rich forest (#0F1F14).
- Strictly two-tone: ivory line + one dark background. NO secondary colors, \
NO grey shading, NO fills, NO crosshatching, NO stippling, NO sketchy \
multi-pass strokes.

DETAIL ECONOMY — line activity must be UNIFORM across the figure:
- Do NOT pack one region (e.g. a single paw) with dense busy curls while \
leaving another region (e.g. the opposite limb, the back, or the chest) \
as a bare outline. Each anatomical area receives roughly the same line \
density. If one paw gets toe definition, the other paw gets toe definition. \
Asymmetric detail reads as the algorithm giving up — it is the #1 thing \
that ruins this style.
- Treat the WHOLE figure with the same calm, gestural pace from first \
mark to last. No scribbled "panic regions". No bare regions. Confident \
even rhythm throughout.

WHERE THE LINE ENDS — CRITICAL:
- The line tapers to a CLEAN stopping point ON the figure's silhouette \
itself — at the back of the chest, along the underside of the body, or \
where a second ear meets the head. The line MUST NOT extend past the \
body's outline into the empty dark field.
- Below the lowest body element (paws, chest line, seated bottom) the \
canvas is COMPLETELY EMPTY dark field — no lines, no marks, no straight \
or curved extensions, no "phantom" vertical or horizontal ivory strokes \
dropping into negative space. The single most common failure mode is a \
straight vertical line falling below the figure — this MUST NOT happen.
- Body contours CLOSE back to the silhouette. Chest, belly, and limb \
outlines never trail off into the dark field — they always loop back to \
meet another part of the contour.

UPPER BAND — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the type:
- The pet's head, ears, ANY part of the ivory line, and any stray mark \
MUST stay BELOW y=22% of the canvas. Top of the tallest ear sits at \
y≈25-28% — never closer to the canvas top.
- Within the top ~22%, only the solid dark field shows — no line work, \
no stray strokes, no marks of any kind. This rule is non-negotiable on \
every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation).
- Head and chest, direct or three-quarter gaze.
- The PET (formed by the single ivory line) occupies 76-78% of image \
height — top of ears at ~22% from top (right at the bottom edge of the \
upper band bottom edge), bottom of chest at ~96-99% from top, centered \
horizontally. The line should fill the canvas confidently. The widest \
point of the figure (ear-to-ear or shoulder-to-shoulder) reaches \
~85-92% of canvas width.
- BACKGROUND (solid dark field) extends edge-to-edge — no reserved panels, \
bars, color blocks, or empty bands.
- Do NOT include any text, words, letters, watermarks, or signatures.

Avoid: white or off-white backgrounds, black ink on light paper, multiple \
separate strokes, broken or interrupted lines, sketchy hatched marks, \
detached features (floating eyes, separate whisker dots), phantom \
vertical or horizontal stray lines hanging below the figure, line \
endings that trail off into empty dark field, asymmetric detail (one paw \
busy with toes, opposite limb left bare), open contours that never close \
back to the silhouette, filled shapes, varying line weight, color fills, \
chalkboard / blackboard texture (this is ink on paper, not chalk on a \
board), photography, photorealism, cartoon, anime, 3D render, gray \
shading, crosshatching, stippling, text, watermark, border, solid color \
bars or panels at image edges.\
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

WATERCOLOR WASH COVERAGE — CRITICAL:
- The deep pigmented wash MUST cover the FULL canvas — every edge, every \
corner, including the LEFT and RIGHT margins around the pet. Never leave \
narrow strips of light or untinted paper at the sides. The dark wash is \
unbroken, edge-to-edge, with organic painterly variation in tone but \
never a hard boundary.

WASH SHAPE — CRITICAL:
- ABSOLUTELY NO horizontal lines, streaks, bands, stripes, ruled marks, \
floor lines, shelf lines, table edges, baseboards, horizon lines, or \
any straight horizontal segment anywhere on the canvas — at any opacity, \
at any length. This includes faint pencil-like scuffs across the lower \
or right or left third of the canvas. Recurring failure mode: the model \
imagining a wooden floor, table, or shelf the pet is on; do NOT do this.
- The wash is soft, rounded, organic — variation comes from wet-on-wet \
bleeds and subtle pigment pools, never from straight strokes.

UPPER BAND — CRITICAL: A handwritten pet name will be composited \
into the TOP of the finished image. Reserve the upper ~22% of the canvas \
for legible script:
- The pet's head, ears, fur fly-aways, ink linework, and bright accent \
splatters MUST stay BELOW y=22% of the canvas. Top of the tallest ear \
sits at y≈25-28% — never closer to the canvas top.
- Within the top ~22%, the dark wash continues edge-to-edge but stays \
calm and uniform (no strong tonal variation, no splatter, no light \
catchlights, no painterly detail). Think of a calm wash zone that \
reads as breathing room above the subject.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Slight natural vignette
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved upper band (top ~22%) is what creates the breathing \
room above the ears.
- The BACKGROUND (deep pigmented dark watercolor wash) extends to every edge \
of the canvas. No reserved panels, bars, color blocks, or empty bands
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: white or cream paper showing through, bright washed-out backgrounds, \
photography, photorealism, harsh shadows, pixelation, blurry, low resolution, \
cartoon, anime, 3D render, clipping, text, watermark, border.\
"""

# (style_id, mode) → dedicated template. Missing keys fall back to the base
# PROMPTS template plus the generic _BACKGROUND_MODE_RULES override.
# Bold Graphic Poster used to expose a 'dark' inversion template; the cubist
# WPAP rewrite drops that path entirely — bg colours are now driven by the
# customer's poster_palette pick (see POSTER_PALETTES), so a separate dark
# alt would conflict with the palette-injected bg split.
_ALT_PROMPTS: dict[tuple[str, str], str] = {
    ("minimal-line-art", "dark"):    _MINIMAL_LINE_ART_DARK_TEMPLATE,
    ("watercolor", "dark"):          _WATERCOLOR_DARK_TEMPLATE,
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

_COMPOSITION_RULE_WITH_NAME = (
    "\n\nCOMPOSITION — CRITICAL (read twice, follow exactly):\n"
    "- 4:5 PORTRAIT ASPECT RATIO: The image canvas is taller than it is "
    "wide. Compose for that. Do NOT output a square or wide image.\n"
    "- THE PET IS THE PRIMARY SUBJECT AND MUST DOMINATE THE CANVAS. "
    "The pet's silhouette (top of ears to bottom of chest) occupies "
    "78-83% of image height and roughly 76-84% of image width. The "
    "name is a tiny accent (≤3% of image height); the pet takes up "
    "the vast majority of the image. If the name and the pet look "
    "remotely similar in visual weight, the name is too large — "
    "reduce it. The pet's visual weight rests in the lower two-thirds "
    "of the canvas; the SMALL name sits in the negative space ABOVE.\n"
    "- BOUNDING-BOX CHECK: imagine the pet inscribed in a bounding box. "
    "Top of that box sits ~15-18% from canvas top (generous airspace "
    "above the ears), bottom of the box sits ~96-99% from canvas top "
    "(the chest fills nearly to the bottom edge of the source canvas — "
    "the bottom margin is intentionally tight). Left and right edges "
    "keep ~8-12% clean background on each side. Ears, fur, whiskers, "
    "chin must stay inside the inner frame; the chest may sit close to "
    "the bottom edge so the composition reads grounded, never floating.\n"
    "- CENTERED HORIZONTALLY — EQUAL NEGATIVE SPACE LEFT AND RIGHT "
    "(NON-NEGOTIABLE): the pet's vertical axis of symmetry (the line "
    "through the centre of the nose, between the eyes, down the chest) "
    "sits EXACTLY on the canvas horizontal centre at x=50%. The clean "
    "background gap from the canvas LEFT edge to the leftmost painted "
    "pixel must EQUAL the gap from the canvas RIGHT edge to the "
    "rightmost painted pixel, within ±2% of canvas width. This applies "
    "to both the pet silhouette AND any painterly wash, halo, glow, or "
    "background bleed that surrounds it — the entire painted region "
    "must read as horizontally centered, not shifted left or right. If "
    "you mentally fold the image vertically down the centreline, the "
    "two halves should mirror each other in mass and extent. Eyes land "
    "in the middle of the image, around 38-44% from top — lower than a "
    "traditional portrait so the subject reads grounded with breathing "
    "room above.\n"
    "- BACKGROUND EXTENDS EDGE-TO-EDGE WITHOUT GEOMETRIC ARTIFACTS: the "
    "artwork's background fills the canvas to all four edges. ANY variation "
    "in the background (watercolour bleed, oil-paint atmosphere, soft "
    "radial glow, etc.) must be ORGANIC and intrinsic to the style's "
    "medium — never geometric. STRICTLY forbidden: NO inner rectangle of "
    "one shade inside an outer rectangle of another shade, NO mat, NO "
    "frame, NO border, NO inset panel, NO letterbox bar, NO horizontal or "
    "vertical colour-band splits, NO checker zones, NO sharp-edged colour "
    "blocks behind the pet. If the chosen style calls for a flat solid "
    "background (modern shape art, bold poster, neon pop, minimal line "
    "art), that background is perfectly uniform from corner to corner — "
    "the same colour in every corner, no vignette, no subtle gradient.\n"
    "- NO PASTED-ON LOOK: the pet must read as integral to the artwork — "
    "no halo, no fringe, no hard outline of a different shade around the "
    "pet's silhouette, no visible 'cutout' edge that suggests the pet was "
    "rendered separately and laid on top of a different background. Pet "
    "and background must feel painted in the same pass, in the same medium.\n"
    "- ASPECT-RATIO CROP SURVIVAL: customers order this on 4:5 (e.g. "
    "16x20), 3:4 (e.g. 12x16), AND 1:1 (e.g. 12x12, 16x16) canvases. "
    "  · On 4:5 (no crop) the composition ships exactly as composed: "
    "    ~15-18% empty airspace above the ears, the pet body grounded "
    "    near the bottom edge.\n"
    "  · On 3:4 the source is trimmed slightly on the SIDES only — "
    "    vertical positioning is preserved, so the same 'grounded "
    "    composition with airspace at the top' reads cleanly.\n"
    "  · On 1:1 the source is centre-cropped (~10% off the top AND "
    "    ~10% off the bottom of your 4:5 output). With ears at 15-18% "
    "    of source, the airspace above remains comfortable in the "
    "    cropped square (~6-10% of the printed face) — and with chest "
    "    at 96-99% of source, the chest extends to OR JUST PAST the "
    "    bottom edge of the cropped square, giving the pet a grounded, "
    "    rooted presence in the bottom of the square print rather than "
    "    floating with empty space below. This is INTENTIONAL — the "
    "    visual weight should always rest at the bottom of the visible "
    "    canvas, never at the top.\n"
    "  Never push ears higher than 12% from canvas top, or the 1:1 "
    "  crop ships with the head clipped.\n"
    "- NO CROPPED FEATURES: ears, whiskers, chin, chest must not be "
    "clipped by any edge of the source image. If you can't fit the pet "
    "inside the bounding box above intact, render the pet a touch smaller. "
    "Never push features off any edge.\n"
    "- THE NATURAL BREATHING ROOM SERVES THE NAME: the empty space above "
    "the pet exists so the name has a calm pocket of negative space to sit "
    "in. Do not fill it with extra props, foliage, or decorative motifs.\n"
    "- NO RESERVED BANDS OR PANELS: never output a solid colour bar, empty "
    "rectangle, letterbox stripe, or framed panel at the top or bottom. "
    "The artwork's native scenery extends uniformly to every edge, just "
    "calmer in the breathing room around the pet.\n"
    "- If the style has a dark moody background (e.g. Renaissance, oil "
    "paint), the dark atmosphere still extends uniformly to all four "
    "edges — drapery, shadow, or wall continues into the breathing room "
    "around the pet, just calmer there so the pet stays the focal point.\n"
)


# When no name is being rendered, the pet should grow to fill more of the
# canvas — the breathing room above the ears no longer needs to host a
# name, so it shrinks. The painterly/watercolor wash, oil-paint atmosphere,
# poster background, etc. also extends further toward the edges.
_COMPOSITION_RULE_NO_NAME = (
    "\n\nCOMPOSITION — CRITICAL (read twice, follow exactly):\n"
    "- 4:5 PORTRAIT ASPECT RATIO: The image canvas is taller than it is "
    "wide. Compose for that. Do NOT output a square or wide image.\n"
    "- THE PET FILLS THE FRAME GENEROUSLY. Because there is no name to "
    "host, the pet grows to take up more of the canvas. The pet's "
    "silhouette (top of ears to bottom of chest) occupies 86-92% of image "
    "height and roughly 82-90% of image width. The pet is unambiguously "
    "the dominant subject — the surrounding negative space is a quiet "
    "frame, not a reserved zone.\n"
    "- BOUNDING-BOX CHECK: imagine the pet inscribed in a bounding box. "
    "Top of that box sits ~6-10% from canvas top (a slim, comfortable "
    "airspace above the ears — NOT a tall reserved band), bottom of the "
    "box sits ~96-99% from canvas top (the chest fills nearly to the "
    "bottom edge). Left and right edges keep only ~5-9% clean background "
    "on each side. Ears, fur, whiskers, chin must stay inside the inner "
    "frame; the chest may sit close to the bottom edge so the composition "
    "reads grounded, never floating.\n"
    "- THE STYLE'S NATIVE BACKGROUND ALSO FILLS MORE OF THE CANVAS. "
    "Watercolor wash, oil-paint atmosphere, poster colour field, charcoal "
    "paper, neon flat field — whatever the style's medium is — extends "
    "edge-to-edge organically, with no large empty white margins around "
    "the pet. The painted/printed surface should reach all four edges.\n"
    "- CENTERED HORIZONTALLY — EQUAL NEGATIVE SPACE LEFT AND RIGHT "
    "(NON-NEGOTIABLE): the pet's vertical axis of symmetry (the line "
    "through the centre of the nose, between the eyes, down the chest) "
    "sits EXACTLY on the canvas horizontal centre at x=50%. The clean "
    "background gap from the canvas LEFT edge to the leftmost painted "
    "pixel must EQUAL the gap from the canvas RIGHT edge to the "
    "rightmost painted pixel, within ±2% of canvas width. This applies "
    "to both the pet silhouette AND any painterly wash, halo, glow, or "
    "background bleed that surrounds it — the entire painted region "
    "must read as horizontally centered, not shifted left or right. If "
    "you mentally fold the image vertically down the centreline, the "
    "two halves should mirror each other in mass and extent. Eyes land "
    "around 30-38% from top — a touch higher than the with-name "
    "composition because the pet is larger overall.\n"
    "- BACKGROUND EXTENDS EDGE-TO-EDGE WITHOUT GEOMETRIC ARTIFACTS: the "
    "artwork's background fills the canvas to all four edges. ANY variation "
    "in the background (watercolour bleed, oil-paint atmosphere, soft "
    "radial glow, etc.) must be ORGANIC and intrinsic to the style's "
    "medium — never geometric. STRICTLY forbidden: NO inner rectangle of "
    "one shade inside an outer rectangle of another shade, NO mat, NO "
    "frame, NO border, NO inset panel, NO letterbox bar, NO horizontal or "
    "vertical colour-band splits, NO checker zones, NO sharp-edged colour "
    "blocks behind the pet. If the chosen style calls for a flat solid "
    "background (modern shape art, bold poster, neon pop, minimal line "
    "art), that background is perfectly uniform from corner to corner — "
    "the same colour in every corner, no vignette, no subtle gradient.\n"
    "- NO PASTED-ON LOOK: the pet must read as integral to the artwork — "
    "no halo, no fringe, no hard outline of a different shade around the "
    "pet's silhouette, no visible 'cutout' edge that suggests the pet was "
    "rendered separately and laid on top of a different background. Pet "
    "and background must feel painted in the same pass, in the same medium.\n"
    "- ASPECT-RATIO CROP SURVIVAL: customers order this on 4:5, 3:4, AND "
    "1:1 canvases. On 1:1 the source is centre-cropped (~10% off the top "
    "AND bottom). With ears at 6-10% of source, the airspace above remains "
    "tight but readable in the cropped square (~0-3% of the printed face). "
    "Never push ears higher than 5% from canvas top, or the 1:1 crop ships "
    "with the head clipped.\n"
    "- NO CROPPED FEATURES: ears, whiskers, chin, chest must not be "
    "clipped by any edge of the source image. If you can't fit the pet "
    "inside the bounding box above intact, render the pet a touch smaller. "
    "Never push features off any edge.\n"
    "- NO TEXT ANYWHERE IN THE IMAGE — ZERO tolerance, this is "
    "non-negotiable. NO letters, NO numbers, NO words, NO names, NO "
    "watermarks, NO signatures, NO glyphs, NO letterforms, NO character "
    "shapes, NO calligraphy, NO inscriptions, NO labels, NO monograms, "
    "NO initials. The TOP 25% of the canvas (where the pet's ears live) "
    "must contain ONLY uninterrupted background colour and pet ear-tips — "
    "absolutely nothing that resembles a letter, glyph, or alphabetical "
    "character of ANY alphabet (Latin, Cyrillic, Greek, Arabic, Hebrew, "
    "Han, Hangul, etc.). RECURRING FAILURE MODE TO AVOID: hallucinating "
    "the pet's name as decorative text above the head — DO NOT do this. "
    "If a shape you are drawing resembles the silhouette of an R, O, B, "
    "S, or any other letterform, immediately redraw it as background. "
    "Collars and tags render blank. The breathing room above the pet "
    "stays quiet, unbroken background.\n"
    "- NO RESERVED BANDS OR PANELS: never output a solid colour bar, empty "
    "rectangle, letterbox stripe, or framed panel at the top or bottom. "
    "The artwork's native scenery extends uniformly to every edge.\n"
    "- If the style has a dark moody background (e.g. Renaissance, oil "
    "paint), the dark atmosphere still extends uniformly to all four "
    "edges — drapery, shadow, or wall continues all the way to every "
    "corner.\n"
)


# Backwards-compat alias — prefer _composition_rule(has_name=...) at new
# call sites.
_COMPOSITION_RULE = _COMPOSITION_RULE_WITH_NAME


def _composition_rule(has_name: bool) -> str:
    """Pick the composition rule block based on whether a pet name is being
    rendered. With name: the pet is sized to leave a calm pocket of negative
    space at the top for the name. Without name: the pet grows to fill more
    of the canvas and the style's native background reaches further toward
    the edges."""
    return _COMPOSITION_RULE_WITH_NAME if has_name else _COMPOSITION_RULE_NO_NAME


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
    # Watercolor exposes an 8-swatch wash-tint palette (same pattern as Modern).
    "watercolor":          set(WATERCOLOR_BG_COLORS.keys()),
    "minimal-line-art":    {"auto", "light", "dark"},
    # Modern + Bold Graphic Poster expose dedicated colour palettes instead
    # of auto/light/dark — Modern uses MODERN_BG_COLORS (8 single-tone
    # print-safe options), Bold Graphic Poster uses POSTER_PALETTES (8
    # paired-tone vertical-split options).
    "modern-shape-art":    set(MODERN_BG_COLORS.keys()),
    "neon-pop-art":        {"auto"},
    "renaissance-royalty": {"auto"},
    "bold-graphic-poster": set(POSTER_PALETTE_IDS),
    "aura-gradient":       {"auto"},
    "charcoal":            {"auto", "light"},
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
    name_block = _name_integration(style_id, pet_name, background_mode, style_vars)
    return (
        base.rstrip()
        + _composition_rule(has_name=True)
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
    "modern-shape-art":   _modern_shape_art_prompt,
    "neon-pop-art":       _static(_NEON_POP_ART_TEMPLATE),
    "renaissance-royalty": _static(_RENAISSANCE_ROYALTY_TEMPLATE),
    "bold-graphic-poster": _bold_graphic_poster_prompt,
    "aura-gradient":      _static(_AURA_GRADIENT_TEMPLATE),
    "charcoal":           _static(_CHARCOAL_TEMPLATE),
}


# ---------------------------------------------------------------------------
# Per-style post-processing hooks
# ---------------------------------------------------------------------------

def add_background_padding(
    img: Image.Image,
    padding_ratio: float = 0.12,
    solid_bg_color: Optional[tuple] = None,
    pad_bottom_ratio: Optional[float] = None,
) -> Image.Image:
    """Pad the image by replicating its edge pixels outward.

    Gemini is strongly biased toward dominant-subject framing for pet
    portraits — it routinely renders the pet filling the canvas edge-to-edge
    no matter how aggressively the prompt asks otherwise. After many prompt
    iterations failed to consistently move the framing, we add the breathing
    room programmatically.

    Earlier versions sampled 4 corner regions and filled the padding with a
    solid averaged colour. That worked for white/cream-bg styles but failed
    on saturated-bg styles (neon-pop-art) and gradient-bg styles
    (aura-gradient): tiny edge-gradient/anti-alias drift between the corner
    sample and the actual edge pixels produced a visible "double-frame"
    artefact (outer ring around an inner colour field) on the printed
    canvas — readable as "image inside a frame", not "wrapped canvas".

    This version replicates the actual edge pixels outward via a small set
    of 1-pixel strips per edge. Each padding region inherits the edge's
    real colour at that position, so:
      • solid bg (neon, poster, charcoal, line art) → pad stays solid
      • gradient bg (aura, watercolour wash) → pad extends the gradient
      • white/cream paper bg (watercolour, charcoal) → pad stays white
    No more visible seams between artwork and padding.

    Final canvas grows by 2× pad on each axis; the original image sits in
    the centre untouched.
    """
    from PIL import ImageDraw
    img = img.convert('RGB')
    w, h = img.size
    pad_w = int(w * padding_ratio)
    pad_h = int(h * padding_ratio)
    # Bottom pad can be set independently for the universal flush-bottom
    # rule (pad_bottom_ratio=0 keeps the pet at the canvas bottom edge).
    # Defaults to padding_ratio for backward compat.
    pad_bot_h = int(h * (padding_ratio if pad_bottom_ratio is None else pad_bottom_ratio))

    # Solid-bg shortcut: when the caller knows the style has a uniform
    # edge-to-edge background colour (e.g. neon-pop-art, bold-graphic-
    # poster) it can pass solid_bg_color to skip edge replication
    # entirely. Edge replication produces colour streaks when the AI
    # renders the pet touching an edge — for solid-bg styles, "fill the
    # padding with the known bg colour" is both simpler and visibly
    # cleaner.
    if solid_bg_color is not None:
        out = Image.new('RGB', (w + 2 * pad_w, h + pad_h + pad_bot_h), solid_bg_color)
        out.paste(img, (pad_w, pad_h))
        return out

    out = Image.new('RGB', (w + 2 * pad_w, h + pad_h + pad_bot_h))
    out.paste(img, (pad_w, pad_h))

    # Padding strategy: each band is a blend between two layers.
    #   1. EDGE REPLICATION  — the 1-pixel-wide source edge, stretched
    #      across the band. This carries gradient direction and matches
    #      the artwork's exact colour at the seam, so there's no visible
    #      transition between artwork and padding.
    #   2. SOLID SAMPLE      — the average colour of the source edge,
    #      filled uniformly across the band. This kills colour streaks
    #      that appear when the AI renders the pet touching an edge
    #      (neon-pop-art's "chest at bottom" composition is the classic
    #      offender).
    # The mask blends from 100% replication at the inner seam (where the
    # padding meets the artwork) to 100% solid at the outer rim (where
    # the canvas wrap finishes). The result: smooth seam with the
    # artwork, clean uniform colour where the wrap actually shows.

    def _band_mean(strip):
        # Pick the dominant background colour via 4-bits-per-channel
        # histogram mode. Why not mean or median:
        #   • mean → muddy mid-tone if the pet covers an edge
        #   • median → fails when the pet covers >50% of the edge (the
        #     median picks a pet colour instead of the bg)
        #   • mode → robust because the pet body is split across several
        #     distinct colours (pink, blue, green in neon-pop-art) while
        #     the bg sits in a single concentrated colour bin, so the bg
        #     wins the bin count even when the pet covers the majority
        #     of the strip.
        # Falls back to median on edges where no single bin dominates
        # (smooth gradient like aura-gradient — every pixel is slightly
        # different so no colour bin is large).
        # Down-sample the strip first; we only need a coarse colour
        # signal and the source can be 3000×4000 native print res.
        if max(strip.size) > 200:
            from PIL import Image as _Img
            strip = strip.resize(
                (max(1, strip.size[0] * 200 // max(strip.size)),
                 max(1, strip.size[1] * 200 // max(strip.size))),
                _Img.LANCZOS,
            )
        pixels = list(strip.getdata())
        n = len(pixels)
        counts: dict = {}
        for r, g, b in pixels:
            key = (r >> 4, g >> 4, b >> 4)
            counts[key] = counts.get(key, 0) + 1
        mode_key, mode_count = max(counts.items(), key=lambda kv: kv[1])
        if mode_count >= n * 0.20:
            # Use the bin centre as the representative colour.
            return (
                (mode_key[0] << 4) | 0x08,
                (mode_key[1] << 4) | 0x08,
                (mode_key[2] << 4) | 0x08,
            )
        rs = sorted(p[0] for p in pixels)
        gs = sorted(p[1] for p in pixels)
        bs = sorted(p[2] for p in pixels)
        return (rs[n // 2], gs[n // 2], bs[n // 2])

    def _vertical_blend_mask(width, height, replicate_at_top):
        """White (= solid) at one end, black (= replicate) at the other.
        replicate_at_top=True: replicate edge sits at top (y=0)."""
        m = Image.new('L', (width, height), 0)
        d = ImageDraw.Draw(m)
        for y in range(height):
            t = y / max(1, height - 1)
            alpha = int(255 * t) if replicate_at_top else int(255 * (1 - t))
            d.line([(0, y), (width - 1, y)], fill=alpha)
        return m

    def _horizontal_blend_mask(width, height, replicate_at_left):
        m = Image.new('L', (width, height), 0)
        d = ImageDraw.Draw(m)
        for x in range(width):
            t = x / max(1, width - 1)
            alpha = int(255 * t) if replicate_at_left else int(255 * (1 - t))
            d.line([(x, 0), (x, height - 1)], fill=alpha)
        return m

    # ── Top band (replicate edge sits at the bottom of the band, abutting artwork) ──
    top_strip_1px = img.crop((0, 0, w, 1))
    top_replicated = top_strip_1px.resize((w, pad_h), Image.NEAREST)
    top_solid = Image.new('RGB', (w, pad_h), _band_mean(top_strip_1px))
    top_band = Image.composite(top_solid, top_replicated,
                                _vertical_blend_mask(w, pad_h, replicate_at_top=False))
    out.paste(top_band, (pad_w, 0))

    # ── Bottom band (replicate edge sits at the top of the band) ──
    # Skipped entirely when pad_bot_h == 0 (flush-bottom mode — pet's
    # bottom should sit at the canvas bottom edge, no padding below).
    if pad_bot_h > 0:
        bot_strip_1px = img.crop((0, h - 1, w, h))
        bot_replicated = bot_strip_1px.resize((w, pad_bot_h), Image.NEAREST)
        bot_solid = Image.new('RGB', (w, pad_bot_h), _band_mean(bot_strip_1px))
        bot_band = Image.composite(bot_solid, bot_replicated,
                                    _vertical_blend_mask(w, pad_bot_h, replicate_at_top=True))
        out.paste(bot_band, (pad_w, pad_h + h))

    # ── Left band (replicate edge sits at the right of the band) ──
    left_strip_1px = img.crop((0, 0, 1, h))
    left_replicated = left_strip_1px.resize((pad_w, h), Image.NEAREST)
    left_solid = Image.new('RGB', (pad_w, h), _band_mean(left_strip_1px))
    left_band = Image.composite(left_solid, left_replicated,
                                 _horizontal_blend_mask(pad_w, h, replicate_at_left=False))
    out.paste(left_band, (0, pad_h))

    # ── Right band (replicate edge sits at the left of the band) ──
    right_strip_1px = img.crop((w - 1, 0, w, h))
    right_replicated = right_strip_1px.resize((pad_w, h), Image.NEAREST)
    right_solid = Image.new('RGB', (pad_w, h), _band_mean(right_strip_1px))
    right_band = Image.composite(right_solid, right_replicated,
                                  _horizontal_blend_mask(pad_w, h, replicate_at_left=True))
    out.paste(right_band, (pad_w + w, pad_h))

    # ── Corners: solid sample of the corner pixel ──
    out.paste(img.crop((0, 0, 1, 1)).resize((pad_w, pad_h), Image.NEAREST), (0, 0))
    out.paste(img.crop((w - 1, 0, w, 1)).resize((pad_w, pad_h), Image.NEAREST), (pad_w + w, 0))
    if pad_bot_h > 0:
        out.paste(img.crop((0, h - 1, 1, h)).resize((pad_w, pad_bot_h), Image.NEAREST), (0, pad_h + h))
        out.paste(img.crop((w - 1, h - 1, w, h)).resize((pad_w, pad_bot_h), Image.NEAREST), (pad_w + w, pad_h + h))

    return out


def _modern_shape_art_reframe(
    img: Image.Image,
    pad_top_ratio: float = 0.10,
    pad_side_ratio: float = 0.07,
    pad_bottom_ratio: float = 0.0,
    target_aspect: tuple = PORTRAIT_RATIO,
) -> Image.Image:
    """Reframe a modern-shape-art portrait so the pet fills the canvas
    confidently with exact target margins regardless of how much empty
    background the AI included.

    pad_top_ratio / pad_side_ratio / pad_bottom_ratio are fractions of
    pet height / width. Defaults (0.05 / 0.02 / 0.0) target the no-name
    4:5 master — pet dominates with a thin cream halo and the chest
    cut sits flush at the canvas bottom (where the gallery wrap takes
    only ~6.75% of total height on a 4:5, leaving the chest visible on
    the front face). The 1:1 derivative needs a non-zero
    pad_bottom_ratio because the wrap eats ~8.6% of total height per
    edge on a square — without it the chest cut wraps around to the
    side of the canvas instead of staying on the front.
    """
    rgb = img.convert('RGB')
    w, h = rgb.size
    BG_TOL = 40  # Euclidean color distance → background vs. pet

    # --- 1. Background color from 4 corners ---
    corner_size = max(8, min(w, h) // 40)
    cp: list = []
    for x0, y0 in [(0, 0), (w - corner_size, 0),
                   (0, h - corner_size), (w - corner_size, h - corner_size)]:
        cp.extend(rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata())
    if not cp:
        return add_background_padding(img, padding_ratio=0.15)
    bg_r = sum(p[0] for p in cp) / len(cp)
    bg_g = sum(p[1] for p in cp) / len(cp)
    bg_b = sum(p[2] for p in cp) / len(cp)
    bg = (int(bg_r), int(bg_g), int(bg_b))

    # --- 2. Find pet bounding box ---
    px_load = rgb.load()
    step = 3  # sample every 3rd pixel for speed
    fg_min_x, fg_max_x = w, 0
    fg_min_y, fg_max_y = h, 0
    found = False
    for y in range(0, h, step):
        for x in range(0, w, step):
            p = px_load[x, y]
            d = ((p[0]-bg_r)**2 + (p[1]-bg_g)**2 + (p[2]-bg_b)**2) ** 0.5
            if d > BG_TOL:
                if x < fg_min_x: fg_min_x = x
                if x > fg_max_x: fg_max_x = x
                if y < fg_min_y: fg_min_y = y
                if y > fg_max_y: fg_max_y = y
                found = True

    if not found or fg_max_x <= fg_min_x or fg_max_y <= fg_min_y:
        return add_background_padding(img, padding_ratio=0.15)

    # Sanity check on the detected bbox. Low-contrast pets (a white
    # cat on a cream Modern bg, a cream dog on warm beige, etc.) drop
    # out of the BG_TOL distance check — the body's coat colour is
    # too close to the bg, so only the darker features (ears, eyes,
    # nose, fur shadows) get classified as foreground. Result: bbox
    # is just the face, and the body gets cropped off before the
    # reframe pastes onto the new canvas.
    #
    # When the bbox is implausibly small along an axis we trust the
    # AI's natural composition on that axis instead — Gemini reliably
    # composes pets centred horizontally with the body extending down
    # to the source bottom, so we expand fg_max_y to the source bottom
    # and centre the bbox horizontally on the source midline. The
    # white-body / pale-coat areas of the source are preserved (the
    # bbox crop is a rectangular region; whatever's inside it gets
    # pasted, regardless of whether each pixel passed BG_TOL).
    bbox_w = fg_max_x - fg_min_x
    bbox_h = fg_max_y - fg_min_y
    if bbox_h < h * 0.55:
        fg_max_y = h
        bbox_h = fg_max_y - fg_min_y
    if bbox_w < w * 0.55:
        cx = w // 2
        half = max(bbox_w, int(w * 0.55)) // 2
        fg_min_x = max(0, cx - half)
        fg_max_x = min(w, cx + half)
        bbox_w = fg_max_x - fg_min_x

    # Add inset so we don't clip anti-aliased edges. Antialiased ear
    # tips on a cream background can fall below BG_TOL — without a
    # margin, the bbox cuts into the visible silhouette and re-padding
    # places the ears flush against the canvas edge. 1.5% of bbox
    # dimension gives the soft fade room to be preserved.
    inset_x = max(step * 2, int(bbox_w * 0.015))
    inset_y = max(step * 2, int(bbox_h * 0.015))
    fg_min_x = max(0, fg_min_x - inset_x)
    fg_min_y = max(0, fg_min_y - inset_y)
    fg_max_x = min(w, fg_max_x + inset_x)
    fg_max_y = min(h, fg_max_y + inset_y)

    pet_w = fg_max_x - fg_min_x
    pet_h = fg_max_y - fg_min_y

    # --- 3. Compose new canvas ---
    # pad_bottom defaults to 0 for the 4:5 master — the silhouette
    # (flat chest cut, fluffy fade, tongue/collar dangle) sits flush
    # with the canvas bottom and the gallery wrap on a 4:5 only takes
    # ~6.75% per edge, leaving the chest visible on the front face.
    # The 1:1 derivative passes pad_bottom_ratio>0 because the wrap on
    # a square eats ~8.6% per edge — flush would mean the chest wraps
    # to the side of the canvas instead of staying on the front.
    pad_top    = int(pet_h * pad_top_ratio)
    pad_side   = int(pet_w * pad_side_ratio)
    pad_bottom = int(pet_h * pad_bottom_ratio)

    # Target the requested print ratio exactly so downstream cropping
    # never needs to eat into the pet:
    #   - bbox taller than target: expand sides.
    #   - bbox wider than target: expand top (preserves the flat-bottom rule).
    target_w, target_h = target_aspect
    natural_w = pet_w + 2 * pad_side
    natural_h = pet_h + pad_top + pad_bottom
    if natural_w * target_h < natural_h * target_w:
        needed_w = (natural_h * target_w + target_h - 1) // target_h
        pad_side += (needed_w - natural_w + 1) // 2
    elif natural_w * target_h > natural_h * target_w:
        needed_h = (natural_w * target_h + target_w - 1) // target_w
        pad_top += needed_h - natural_h

    canvas_w = pet_w + 2 * pad_side
    canvas_h = pet_h + pad_top + pad_bottom
    canvas = Image.new('RGB', (canvas_w, canvas_h), bg)
    canvas.paste(rgb.crop((fg_min_x, fg_min_y, fg_max_x, fg_max_y)), (pad_side, pad_top))
    return canvas


def _center_line_art(img: Image.Image) -> Image.Image:
    """Re-centers a high-contrast line-art portrait whose subject drifted
    off-center horizontally OR vertically. Gemini routinely produces
    compositions whose visual weight is shifted left/right of true center
    AND placed too high in the frame, leaving an empty band of negative
    space at the bottom. For solid-bg line-art styles (minimal-line-art
    light and dark variants) the foreground line is reliably detectable,
    so we can find its bounding box and shift the artwork into true
    centre on both axes — keeping the same canvas size and re-filling the
    exposed margin with the original background colour.

    No-op (returns the input unchanged) when the offset is below 1.5% of
    canvas size on each axis (already effectively centered) or when no
    foreground is detected (degenerate frame).
    """
    rgb = img.convert('RGB')
    w, h = rgb.size

    # Sample corners to learn the background colour and decide whether
    # the subject is dark-on-light or light-on-dark.
    corner_size = max(8, min(w, h) // 50)
    corner_pixels = []
    for x0, y0 in [(0, 0), (w - corner_size, 0), (0, h - corner_size), (w - corner_size, h - corner_size)]:
        for px in rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata():
            corner_pixels.append(px)
    if not corner_pixels:
        return img
    avg_r = sum(p[0] for p in corner_pixels) / len(corner_pixels)
    avg_g = sum(p[1] for p in corner_pixels) / len(corner_pixels)
    avg_b = sum(p[2] for p in corner_pixels) / len(corner_pixels)
    bg_color = (int(avg_r), int(avg_g), int(avg_b))
    bg_lum = 0.299 * avg_r + 0.587 * avg_g + 0.114 * avg_b
    light_bg = bg_lum > 128

    # Threshold: pixel is "foreground" if it differs from corner luminance
    # by more than 60 (out of 255). This catches ink lines on either polarity.
    grey = rgb.convert('L')
    px = grey.load()
    fg_min_x, fg_max_x = w, 0
    fg_min_y, fg_max_y = h, 0
    found = False
    # Sample every 2px to keep this fast on hi-res sources.
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            v = px[x, y]
            is_fg = (v < bg_lum - 60) if light_bg else (v > bg_lum + 60)
            if is_fg:
                if x < fg_min_x: fg_min_x = x
                if x > fg_max_x: fg_max_x = x
                if y < fg_min_y: fg_min_y = y
                if y > fg_max_y: fg_max_y = y
                found = True
    if not found or fg_max_x <= fg_min_x or fg_max_y <= fg_min_y:
        return img

    # Compute shift on each axis. Anything below ~1.5% reads as centered.
    subject_cx = (fg_min_x + fg_max_x) / 2
    subject_cy = (fg_min_y + fg_max_y) / 2
    dx = int(round(w / 2 - subject_cx))
    dy = int(round(h / 2 - subject_cy))
    if abs(dx) < int(w * 0.015): dx = 0
    if abs(dy) < int(h * 0.015): dy = 0
    if dx == 0 and dy == 0:
        return img

    shifted = Image.new('RGB', (w, h), bg_color)
    shifted.paste(rgb, (dx, dy))
    return shifted


def _center_horizontal_weight(img: Image.Image) -> Image.Image:
    """Shift image content LEFT or RIGHT so the visual mass is horizontally
    centred. Vertical position is never changed (flush-bottom rule preserved).

    Works by sampling corners for the background colour, then finding the
    horizontal extent of foreground pixels. If the subject's centre-x is
    more than 1.5% away from the canvas mid-point, the whole image is
    translated horizontally and the exposed strip is filled with the
    background colour.

    No-op when:
      • foreground detection fails (degenerate / very low contrast)
      • subject is already within ±1.5% of horizontal centre
    """
    rgb = img.convert('RGB')
    w, h = rgb.size

    corner_size = max(8, min(w, h) // 50)
    corner_pixels: list = []
    for x0, y0 in [(0, 0), (w - corner_size, 0),
                   (0, h - corner_size), (w - corner_size, h - corner_size)]:
        corner_pixels.extend(
            rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata()
        )
    if not corner_pixels:
        return img
    avg_r = sum(p[0] for p in corner_pixels) / len(corner_pixels)
    avg_g = sum(p[1] for p in corner_pixels) / len(corner_pixels)
    avg_b = sum(p[2] for p in corner_pixels) / len(corner_pixels)
    bg_color = (int(avg_r), int(avg_g), int(avg_b))
    bg_lum = 0.299 * avg_r + 0.587 * avg_g + 0.114 * avg_b

    grey = rgb.convert('L')
    pxl = grey.load()
    fg_min_x, fg_max_x = w, 0
    found = False
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            if abs(pxl[x, y] - bg_lum) > 40:
                if x < fg_min_x:
                    fg_min_x = x
                if x > fg_max_x:
                    fg_max_x = x
                found = True

    if not found or fg_max_x <= fg_min_x:
        return img

    subject_cx = (fg_min_x + fg_max_x) / 2
    dx = int(round(w / 2 - subject_cx))
    if abs(dx) < int(w * 0.015):
        return img  # already centred, skip

    shifted = Image.new('RGB', (w, h), bg_color)
    shifted.paste(rgb, (dx, 0))   # dy=0 — never shift vertically
    return shifted


def _tight_crop_to_aspect(
    img: Image.Image,
    target_aspect: tuple = (4, 5),
    bg_tol: int = 35,
) -> Image.Image:
    """Detect the artwork's bounding box, crop to it, then pad
    symmetrically to the target aspect with edge-replicated padding.

    Why: Gemini frequently composes the artwork off-centre within the
    raw frame, leaving a band of background colour on one side. The
    canvas mockup (cover-cropped at any size) then displays the artwork
    visibly shifted, with the bg colour bleeding in on the opposite
    edge. By centring the bbox in the print file itself, the mockup
    always shows the artwork properly positioned and the customer's
    printed canvas matches the on-screen mockup exactly.

    Generating per-aspect derivatives (e.g. a 1:1 alongside the 4:5
    master) means each canvas variant gets a print file composed for
    its exact aspect — no cover-crop loss when the customer picks a
    square size against a 4:5 master.

    No-op when the bbox already covers ≥95% of the frame and the
    current aspect is within 1% of the target (artwork is already
    tight and correctly shaped) or when no foreground is detectable
    (degenerate).

    target_aspect: (width, height) tuple. (4, 5) for tall canvas/poster,
        (1, 1) for square canvas, (3, 4) for some posters.
    bg_tol: Euclidean RGB distance from the corner-sampled background
        colour above which a pixel counts as artwork. Tuned for clear
        contrast between artwork and bg; styles with low-contrast or
        gradient backgrounds (aura-gradient) should not use this.
    """
    rgb = img.convert('RGB')
    w, h = rgb.size

    corner_size = max(8, min(w, h) // 50)
    cp: list = []
    for x0, y0 in [(0, 0), (w - corner_size, 0),
                   (0, h - corner_size), (w - corner_size, h - corner_size)]:
        cp.extend(rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata())
    if not cp:
        return img
    bg_r = sum(p[0] for p in cp) / len(cp)
    bg_g = sum(p[1] for p in cp) / len(cp)
    bg_b = sum(p[2] for p in cp) / len(cp)
    bg_color = (int(bg_r), int(bg_g), int(bg_b))
    tol_sq = bg_tol * bg_tol

    px = rgb.load()
    step = max(1, min(w, h) // 400)
    fg_min_x, fg_max_x = w, 0
    fg_min_y, fg_max_y = h, 0
    found = False
    for y in range(0, h, step):
        for x in range(0, w, step):
            p = px[x, y]
            d = (p[0] - bg_r) ** 2 + (p[1] - bg_g) ** 2 + (p[2] - bg_b) ** 2
            if d > tol_sq:
                if x < fg_min_x: fg_min_x = x
                if x > fg_max_x: fg_max_x = x
                if y < fg_min_y: fg_min_y = y
                if y > fg_max_y: fg_max_y = y
                found = True
    if not found or fg_max_x <= fg_min_x or fg_max_y <= fg_min_y:
        return img

    # Inset preserves anti-aliased edges
    inset = step * 2
    fg_min_x = max(0, fg_min_x - inset)
    fg_min_y = max(0, fg_min_y - inset)
    fg_max_x = min(w, fg_max_x + inset)
    fg_max_y = min(h, fg_max_y + inset)
    bbox_w = fg_max_x - fg_min_x
    bbox_h = fg_max_y - fg_min_y

    target_aspect_ratio = target_aspect[0] / target_aspect[1]
    cur_frame_aspect = w / h
    coverage = (bbox_w * bbox_h) / (w * h)
    aspect_ok = abs(cur_frame_aspect - target_aspect_ratio) / target_aspect_ratio < 0.01
    if coverage >= 0.95 and aspect_ok:
        return img

    cropped = rgb.crop((fg_min_x, fg_min_y, fg_max_x, fg_max_y))
    cw, ch = cropped.size

    # Compute the smallest target-aspect canvas that wraps the bbox
    cur_aspect = cw / ch
    if cur_aspect > target_aspect_ratio:
        target_w = cw
        target_h = int(round(cw / target_aspect_ratio))
    else:
        target_h = ch
        target_w = int(round(ch * target_aspect_ratio))

    pad_x = (target_w - cw) // 2
    pad_y = (target_h - ch) // 2

    out = Image.new('RGB', (target_w, target_h), bg_color)
    out.paste(cropped, (pad_x, pad_y))

    # Edge-replicate strips so any soft fade or texture continues
    # smoothly into the padding rather than abutting a flat bg fill.
    pad_left = pad_x
    pad_right = target_w - pad_x - cw
    pad_top = pad_y
    pad_bot = target_h - pad_y - ch
    if pad_left > 0:
        out.paste(cropped.crop((0, 0, 1, ch)).resize((pad_left, ch), Image.NEAREST), (0, pad_y))
    if pad_right > 0:
        out.paste(cropped.crop((cw - 1, 0, cw, ch)).resize((pad_right, ch), Image.NEAREST), (pad_x + cw, pad_y))
    if pad_top > 0:
        out.paste(cropped.crop((0, 0, cw, 1)).resize((cw, pad_top), Image.NEAREST), (pad_x, 0))
    if pad_bot > 0:
        out.paste(cropped.crop((0, ch - 1, cw, ch)).resize((cw, pad_bot), Image.NEAREST), (pad_x, pad_y + ch))
    # Corners — replicate the corner pixel
    if pad_left > 0 and pad_top > 0:
        out.paste(cropped.crop((0, 0, 1, 1)).resize((pad_left, pad_top), Image.NEAREST), (0, 0))
    if pad_right > 0 and pad_top > 0:
        out.paste(cropped.crop((cw - 1, 0, cw, 1)).resize((pad_right, pad_top), Image.NEAREST), (pad_x + cw, 0))
    if pad_left > 0 and pad_bot > 0:
        out.paste(cropped.crop((0, ch - 1, 1, ch)).resize((pad_left, pad_bot), Image.NEAREST), (0, pad_y + ch))
    if pad_right > 0 and pad_bot > 0:
        out.paste(cropped.crop((cw - 1, ch - 1, cw, ch)).resize((pad_right, pad_bot), Image.NEAREST), (pad_x + cw, pad_y + ch))

    return out


def _portrait_post_process(img: Image.Image) -> Image.Image:
    """Standard post-process for all portrait styles: 4:5 crop + minimum size.
    NOTE: callers should add background padding via add_background_padding()
    BEFORE calling this — the padding step needs the un-cropped image so it
    can sample the corners reliably.

    Uses bottom gravity so any vertical excess is cropped from the top.
    With the universal flush-bottom rule (add_background_padding uses
    pad_bottom_ratio=0), the pet's bottom is at the source bottom edge —
    bottom gravity preserves that flush alignment when cropping to 4:5.
    """
    img = crop_to_ratio(img, PORTRAIT_RATIO, gravity="bottom")
    min_w, min_h = PORTRAIT_MIN_SIZE
    if img.width < min_w or img.height < min_h:
        scale = max(min_w / img.width, min_h / img.height)
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
        )
    return img


def _tight_crop_post_process(img: Image.Image) -> Image.Image:
    """Tight-crop to artwork bbox + re-pad to 4:5 before the standard
    crop pipeline. Used for styles where the AI sometimes leaves
    asymmetric background bands around the artwork (charcoal,
    watercolor, etc.) so the canvas mockup displays the artwork
    centred and edge-to-edge instead of shifted with bg bleed.
    """
    img = _tight_crop_to_aspect(img, target_aspect=(4, 5))
    return _portrait_post_process(img)


def _safe_zone_post_process(img: Image.Image) -> Image.Image:
    """Post-process for any style whose prompt reserves the top ~22%
    of the canvas as a NAME SAFE ZONE. The generic tight-crop helper
    would treat that calm zone as background and crop it away, putting
    the pet's ears at y=0% on the master and forcing the script to
    overlap the head. Skip the tight-crop and just run the standard
    4:5 crop + min-size resize so the safe zone survives intact.
    """
    return _portrait_post_process(img)


# Back-compat alias — older code paths referenced the watercolor name.
_watercolor_post_process = _safe_zone_post_process


def _pad_sides_to_aspect(
    img: Image.Image,
    target_aspect: tuple,
    solid_bg: bool = False,
) -> Image.Image:
    """Pad an image with side strips to reach a wider target aspect
    WITHOUT cropping any height. Used for the 1:1 derivative on
    safe-zone styles so the top NAME SAFE ZONE is preserved vertically
    (a tight-crop centre-pad would lose it).

    Strategies:
      - solid_bg=False (default): edge-replicate the outermost 4px
        column on each side. Right for organic / gradient backgrounds
        (watercolour wash, charcoal paper, aura halo) where natural
        variation extends smoothly outward.
      - solid_bg=True: sample the actual top-left + top-right corner
        pixels of the source and fill the side strips with that solid
        bg colour. Right for SATURATED-FLAT-BG styles (neon, bold-
        graphic-poster) where the AI sometimes leaves a thin imperfect
        edge bleed — replicating those edge pixels would streak the
        imperfection across the whole pad strip and read as a white
        / off-colour vertical band on the printed canvas.

    No-op if the source is already wider-or-equal to the target aspect.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    target_w, target_h = target_aspect
    # Width needed at the source's height to hit the target aspect.
    needed_w = int(round(h * target_w / target_h))
    if needed_w <= w:
        return img
    pad_total = needed_w - w
    pad_left = pad_total // 2
    pad_right = pad_total - pad_left

    if solid_bg:
        # Sample the top corners — for solid-bg styles the corners are
        # always pure background (the pet sits in the lower-middle and
        # the name safe zone keeps the top corners free of detail).
        cs = max(8, min(w, h) // 50)
        corners = (
            img.crop((0, 0, cs, cs)).getdata(),
            img.crop((w - cs, 0, w, cs)).getdata(),
        )
        pixels = list(corners[0]) + list(corners[1])
        n = len(pixels)
        bg = (
            sum(p[0] for p in pixels) // n,
            sum(p[1] for p in pixels) // n,
            sum(p[2] for p in pixels) // n,
        )
        out = Image.new("RGB", (needed_w, h), bg)
        out.paste(img, (pad_left, 0))
        return out

    out = Image.new("RGB", (needed_w, h), (255, 255, 255))
    out.paste(img, (pad_left, 0))
    if pad_left > 0:
        # Replicate a 4-pixel-wide strip rather than 1px so any natural
        # watercolor wash variation reads as continuous wash, not a
        # streaky single-line replication.
        strip_w = min(4, w)
        left_strip = img.crop((0, 0, strip_w, h)).resize((pad_left, h), Image.LANCZOS)
        out.paste(left_strip, (0, 0))
    if pad_right > 0:
        strip_w = min(4, w)
        right_strip = img.crop((w - strip_w, 0, w, h)).resize((pad_right, h), Image.LANCZOS)
        out.paste(right_strip, (pad_left + w, 0))
    return out


# Per-aspect derivative helpers — used to produce a 1:1 (square)
# print file alongside the standard 4:5 master so square canvas
# variants (12×12, 16×16) get a print composed for their exact
# aspect instead of cover-cropping a 4:5 source and losing the
# pet's bottom (or the name area at the top).
PRINT_ASPECT_4_5 = (4, 5)
PRINT_ASPECT_3_4 = (3, 4)
PRINT_ASPECT_1_1 = (1, 1)


def derive_aspect(img: Image.Image, target_aspect: tuple, style_id: str = "") -> Image.Image:
    """Produce a per-aspect derivative of the given (already padded)
    portrait. Modern uses its own bbox-aware reframe; other styles
    use the generic tight-crop. Aura-gradient skips bbox detection
    entirely (gradient bg defeats corner-sample bg detection) and
    falls through to a plain crop_to_ratio.
    """
    if style_id == "modern-shape-art":
        # 1:1 derivative needs more breathing room than 4:5 — a square
        # face has the ears flush against the canvas edge if we use
        # the same tight pad, and the gallery wrap eats ~8.6% per edge
        # on a square so the chest needs explicit bottom padding too.
        # composite_name re-runs the reframe with a larger
        # pad_top_ratio when a name is added to open the band.
        if target_aspect == PRINT_ASPECT_1_1:
            # The 1:1 derivative is mockup-display-only (Printful gets
            # the 4:5 master regardless of canvas variant), so it must
            # render flush at the canvas-face bottom — universal flush-
            # bottom rule. pad_bottom_ratio=0 lands the pet's flat
            # chest cut on the canvas-face bottom edge with no gap.
            #
            # pad_top_ratio bumped from 0.18 → 0.30: at 0.18 the AI's
            # ear tips were landing right at the canvas top edge on
            # broader-headed pets (Lab/Golden silhouettes), reading as
            # a clipped head. 0.30 buys clear breathing room above the
            # ears without shrinking the pet enough to feel small —
            # still well below the 0.45 with-name version that has to
            # host the name band.
            return _modern_shape_art_reframe(
                img,
                pad_top_ratio=0.30,
                pad_side_ratio=0.11,
                pad_bottom_ratio=0,
                target_aspect=PRINT_ASPECT_1_1,
            )
        return _modern_shape_art_reframe(img, target_aspect=target_aspect)
    if style_id in {
        "watercolor", "minimal-line-art", "neon-pop-art",
        "renaissance-royalty", "charcoal", "aura-gradient",
        "bold-graphic-poster",
    }:
        # Pad sides to preserve the top NAME SAFE ZONE — a centred
        # tight-crop would drop the calm-paper / calm-wash / calm-field
        # band and put the pet's ears flush against the new canvas top.
        # Saturated-flat-bg styles fill the side pad with the corner-
        # sampled bg colour (edge replication can streak imperfect AI
        # edges into a visible band); organic-bg styles edge-replicate
        # so wash / halo / paper texture continues smoothly outward.
        # neon-pop-art is the only single-flat-bg style still using the
        # corner-sampled solid fill — bold-graphic-poster's 2-tone vertical
        # split is preserved correctly by edge replication (each side pads
        # with that side's bg colour).
        solid_bg = style_id == "neon-pop-art"
        return _pad_sides_to_aspect(img, target_aspect, solid_bg=solid_bg)
    if style_id == "aura-gradient":
        return crop_to_ratio(img, target_aspect)
    return _tight_crop_to_aspect(img, target_aspect=target_aspect)


def _save_aspect_derivative(
    src_img: Image.Image,
    out_dir: Path,
    filename: str,
    style_id: str,
    target_aspect: tuple,
) -> Path:
    """Derive a per-aspect crop of src_img and save as a 300-DPI PNG.
    Min-size scaling matches the standard portrait pipeline so the
    derivative is print-ready without a separate upscale step.
    """
    derived = derive_aspect(src_img, target_aspect, style_id)
    min_w, min_h = PORTRAIT_MIN_SIZE
    if derived.width < min_w or derived.height < min_h:
        scale = max(min_w / derived.width, min_h / derived.height)
        derived = derived.resize(
            (int(derived.width * scale), int(derived.height * scale)),
            Image.LANCZOS,
        )
    out_path = out_dir / filename
    derived.save(out_path, "PNG", dpi=(300, 300))
    log.info(
        "           %dx%d derivative → %s (%dx%d @ 300 DPI)",
        target_aspect[0], target_aspect[1], out_path.name,
        derived.width, derived.height,
    )
    return out_path


def _modern_shape_art_post_process(img: Image.Image) -> Image.Image:
    """Post-process for modern-shape-art.  Uses bottom gravity for the 4:5
    crop so a flat horizontal chest cut is never clipped off the bottom edge
    (all excess height is removed from the top, where we have breathing room).
    """
    img = crop_to_ratio(img, PORTRAIT_RATIO, gravity="bottom")
    min_w, min_h = PORTRAIT_MIN_SIZE
    if img.width < min_w or img.height < min_h:
        scale = max(min_w / img.width, min_h / img.height)
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
        )
    return img


def _remove_orphan_strokes(img: Image.Image) -> Image.Image:
    """Remove disconnected stray ink fragments from a minimal-line-art result.

    The aesthetic promise is ONE continuous connected line. Gemini sometimes
    leaves orphan fragments — a phantom vertical drop below the figure, a
    detached eye dot, a stray paw — which break the promise. We find the
    largest connected ink component (the line itself) and erase everything
    else by repainting it with the background colour.

    Conservative: if the largest component is less than 70% of total ink
    mass, do nothing — the result is unusual and we'd risk gouging real
    linework. Same logic for orphans below 0.05% of total ink (those are
    antialiasing speckle, not visible artifacts).
    """
    rgb = img.convert('RGB')
    w, h = rgb.size

    corner_size = max(8, min(w, h) // 50)
    corner_pixels = []
    for x0, y0 in [(0, 0), (w - corner_size, 0), (0, h - corner_size), (w - corner_size, h - corner_size)]:
        for px in rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata():
            corner_pixels.append(px)
    if not corner_pixels:
        return img
    avg_r = sum(p[0] for p in corner_pixels) / len(corner_pixels)
    avg_g = sum(p[1] for p in corner_pixels) / len(corner_pixels)
    avg_b = sum(p[2] for p in corner_pixels) / len(corner_pixels)
    bg_color = (int(avg_r), int(avg_g), int(avg_b))
    bg_lum = 0.299 * avg_r + 0.587 * avg_g + 0.114 * avg_b
    light_bg = bg_lum > 128

    # Downscale for component analysis — keeps BFS tractable on hi-res sources.
    target = 1024
    if max(w, h) > target:
        scale = target / max(w, h)
        sw = max(1, int(round(w * scale)))
        sh = max(1, int(round(h * scale)))
        small = rgb.resize((sw, sh), Image.LANCZOS)
    else:
        sw, sh = w, h
        small = rgb

    grey_data = small.convert('L').tobytes()
    n = sw * sh
    fg = bytearray(n)
    fg_total = 0
    if light_bg:
        thr = bg_lum - 60
        for i in range(n):
            if grey_data[i] < thr:
                fg[i] = 1
                fg_total += 1
    else:
        thr = bg_lum + 60
        for i in range(n):
            if grey_data[i] > thr:
                fg[i] = 1
                fg_total += 1

    if fg_total == 0:
        return img

    # 8-connected components via iterative BFS over a flat index buffer.
    visited = bytearray(n)
    components: list[list[int]] = []
    for start in range(n):
        if not fg[start] or visited[start]:
            continue
        comp: list[int] = []
        stack = [start]
        visited[start] = 1
        while stack:
            idx = stack.pop()
            comp.append(idx)
            x = idx % sw
            y = idx // sw
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= sh:
                    continue
                row = ny * sw
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = x + dx
                    if nx < 0 or nx >= sw:
                        continue
                    nidx = row + nx
                    if fg[nidx] and not visited[nidx]:
                        visited[nidx] = 1
                        stack.append(nidx)
        components.append(comp)

    if len(components) <= 1:
        return img

    components.sort(key=len, reverse=True)
    main = components[0]
    if len(main) < fg_total * 0.7:
        return img

    speckle_threshold = max(8, int(fg_total * 0.0005))
    significant = [c for c in components[1:] if len(c) >= speckle_threshold]
    if not significant:
        return img

    del_data = bytearray(n)
    for comp in significant:
        for idx in comp:
            del_data[idx] = 255
    del_small = Image.frombytes('L', (sw, sh), bytes(del_data))
    # Slight dilation catches antialiased fringe around each orphan.
    del_small = del_small.filter(ImageFilter.MaxFilter(3))

    if (sw, sh) != (w, h):
        del_full = del_small.resize((w, h), Image.NEAREST)
    else:
        del_full = del_small

    bg_layer = Image.new('RGB', (w, h), bg_color)
    rgb.paste(bg_layer, (0, 0), del_full)
    log.info(
        "minimal-line-art: removed %d orphan stroke(s) (%d px / %d total ink px)",
        len(significant), sum(len(c) for c in significant), fg_total,
    )
    return rgb


def _line_art_reframe_anchor_bottom(
    img: Image.Image,
    top_room_ratio: float = 0.12,
    bottom_pad_ratio: float = 0.02,
    side_pad_ratio: float = 0.06,
    target_aspect: tuple = PORTRAIT_RATIO,
) -> Image.Image:
    """Reframe a minimal-line-art portrait so the pet is anchored at the
    bottom of the canvas, sized to fill confidently, with symmetric L/R
    padding and configurable top room for breathing space (or for a name
    composited later).

    Anchors the pet's bbox bottom to canvas_height * (1 - bottom_pad_ratio)
    so the bottom of the line work sits just above the canvas edge with
    a small grounding sliver. The pet height is scaled to fill
    (1 - top_room_ratio - bottom_pad_ratio) of canvas height. If that
    would push the pet wider than (1 - 2 * side_pad_ratio) of canvas
    width, the pet is scaled down further so the side padding is
    preserved. Pet centred horizontally — left padding equals right
    padding by construction.

    `top_room_ratio` is the only knob you turn between no-name (0.10-
    0.14: pet dominates) and with-name (0.20-0.24: name has a clear band
    above the pet's head). The same function services both paths.

    Returns a fresh image at a target_aspect canvas filled with the bg
    colour sampled from the input's corners.
    """
    rgb = img.convert('RGB')
    src_w, src_h = rgb.size

    # Sample bg colour from the four corners — same approach as
    # _center_line_art so light + dark variants both work.
    corner_size = max(8, min(src_w, src_h) // 50)
    cp = []
    for x0, y0 in [(0, 0), (src_w - corner_size, 0),
                   (0, src_h - corner_size), (src_w - corner_size, src_h - corner_size)]:
        for px in rgb.crop((x0, y0, x0 + corner_size, y0 + corner_size)).getdata():
            cp.append(px)
    if not cp:
        return img
    bg_lum = sum(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2] for p in cp) / len(cp)
    bg_color = (
        sum(p[0] for p in cp) // len(cp),
        sum(p[1] for p in cp) // len(cp),
        sum(p[2] for p in cp) // len(cp),
    )
    light_bg = bg_lum > 128

    # Detect pet bbox via luminance threshold (60 from corner luminance,
    # same threshold _center_line_art uses).
    grey = rgb.convert('L')
    px = grey.load()
    fg_min_x, fg_max_x = src_w, 0
    fg_min_y, fg_max_y = src_h, 0
    found = False
    step = max(1, min(src_w, src_h) // 600)
    for y in range(0, src_h, step):
        for x in range(0, src_w, step):
            v = px[x, y]
            is_fg = (v < bg_lum - 60) if light_bg else (v > bg_lum + 60)
            if is_fg:
                if x < fg_min_x: fg_min_x = x
                if x > fg_max_x: fg_max_x = x
                if y < fg_min_y: fg_min_y = y
                if y > fg_max_y: fg_max_y = y
                found = True
    if not found or fg_max_x <= fg_min_x or fg_max_y <= fg_min_y:
        # Degenerate input — return centered crop unchanged.
        return _portrait_post_process(img)

    # Inset the bbox slightly so anti-aliased edge pixels don't get
    # clipped off when we crop.
    inset = max(step * 2, 4)
    fg_min_x = max(0, fg_min_x - inset)
    fg_min_y = max(0, fg_min_y - inset)
    fg_max_x = min(src_w, fg_max_x + inset)
    fg_max_y = min(src_h, fg_max_y + inset)

    pet_w = fg_max_x - fg_min_x
    pet_h = fg_max_y - fg_min_y
    pet_aspect = pet_w / pet_h if pet_h > 0 else 1.0

    # Target canvas size: keep the source's larger dimension as a
    # baseline, computed for the requested target_aspect. Use the
    # pet's natural size as the floor so we never upscale-blur.
    target_w_ratio, target_h_ratio = target_aspect
    target_aspect_ratio = target_w_ratio / target_h_ratio
    # Choose a canvas size large enough that the pet at its target
    # height fits comfortably. Anchor on the pet height.
    available_pet_h_frac = 1.0 - top_room_ratio - bottom_pad_ratio
    if available_pet_h_frac <= 0.1:
        # Sanity: leave at least 10% for the pet.
        available_pet_h_frac = 0.5
    canvas_h = int(round(pet_h / available_pet_h_frac))
    canvas_w = int(round(canvas_h * target_aspect_ratio))

    # Width check: would the pet exceed the side-pad budget?
    available_pet_w_frac = 1.0 - 2 * side_pad_ratio
    max_pet_w = int(round(canvas_w * available_pet_w_frac))
    if pet_w > max_pet_w:
        # Pet is too wide — scale the canvas up so side padding is
        # preserved, accepting more top room as a result.
        canvas_w = int(round(pet_w / available_pet_w_frac))
        canvas_h = int(round(canvas_w / target_aspect_ratio))

    cropped = rgb.crop((fg_min_x, fg_min_y, fg_max_x, fg_max_y))

    # Place pet: centred horizontally, anchored at canvas bottom with
    # the bottom_pad_ratio sliver below.
    bottom_pad_px = int(round(canvas_h * bottom_pad_ratio))
    paste_y = canvas_h - bottom_pad_px - pet_h
    paste_x = (canvas_w - pet_w) // 2

    out = Image.new('RGB', (canvas_w, canvas_h), bg_color)
    out.paste(cropped, (paste_x, paste_y))

    # Ensure min print resolution — same floor _portrait_post_process applies.
    min_w, min_h = PORTRAIT_MIN_SIZE
    if out.width < min_w or out.height < min_h:
        scale = max(min_w / out.width, min_h / out.height)
        out = out.resize(
            (int(out.width * scale), int(out.height * scale)), Image.LANCZOS,
        )
    return out


def _line_art_open_name_band(image: Image.Image) -> Image.Image:
    """Re-canvas a minimal-line-art post-processed master to open extra
    top room for a composited name. Mirror of _modern_open_name_band.

    The base post-process anchors the pet at the bottom with ~12% top
    room (pet dominates, no-name preview reads as designed). When a
    name is added we re-canvas with 22% top room so the name has a
    clear band above the pet's head — name composite_name's zone_top
    of 0.11 lands the name centred in the upper portion of that band.
    """
    is_square = (
        image.height > 0
        and abs((image.width / image.height) - 1.0) < 0.05
    )
    if is_square:
        # Square needs a slightly more generous top so the name and
        # the line-art figure don't crowd each other on the smaller
        # vertical axis.
        return _line_art_reframe_anchor_bottom(
            image,
            top_room_ratio=0.26,
            bottom_pad_ratio=0.02,
            side_pad_ratio=0.06,
            target_aspect=PRINT_ASPECT_1_1,
        )
    return _line_art_reframe_anchor_bottom(
        image,
        top_room_ratio=0.22,
        bottom_pad_ratio=0.02,
        side_pad_ratio=0.06,
        target_aspect=PORTRAIT_RATIO,
    )


def _line_art_post_process(img: Image.Image) -> Image.Image:
    """Post-process for the minimal-line-art style (light + dark variants).

    Removes disconnected stray fragments (phantom vertical lines,
    detached paws, floating eye dots), then reframes to anchor the pet
    at the bottom of the canvas with symmetric L/R padding and ~12%
    top breathing room. The pet dominates the canvas (no more
    floating-in-empty-space look) and nothing gets cropped on the
    sides or top because the canvas size is computed FROM the pet
    bbox rather than cropping the source.

    With-name renders re-canvas through _line_art_open_name_band
    afterward to open additional top room for the name.
    """
    img = _remove_orphan_strokes(img)
    return _line_art_reframe_anchor_bottom(
        img,
        top_room_ratio=0.12,
        bottom_pad_ratio=0.02,
        side_pad_ratio=0.06,
        target_aspect=PORTRAIT_RATIO,
    )


# All colour/painterly styles share the same 4:5 crop + min-size pipeline.
# Ink-only styles (classic, minimal, naturalist) pass through unchanged.
_PORTRAIT_STYLES = [
    "watercolor",
    "minimal-line-art",
    "modern-shape-art",
    "neon-pop-art",
    "renaissance-royalty",
    "bold-graphic-poster",
    "aura-gradient",
    "charcoal",
]

POST_PROCESS: dict[str, Callable[[Image.Image], Image.Image]] = {
    style: _portrait_post_process for style in _PORTRAIT_STYLES
}
# minimal-line-art needs the extra horizontal-centering safety net — the
# single-line aesthetic is high-contrast enough to detect reliably, and
# Gemini's drift on this style is the most visually obvious because the
# background is uniform negative space.
POST_PROCESS["minimal-line-art"] = _line_art_post_process
POST_PROCESS["modern-shape-art"] = _modern_shape_art_post_process
# Tight-crop styles where the AI sometimes leaves asymmetric bg bands
# around the artwork. Detect the bbox, crop to it, re-pad symmetrically
# to 4:5 with edge replication. Result: centred artwork that fills the
# canvas mockup and matches what gets sent to Printful exactly.
# Aura-gradient is excluded — its gradient bg defeats corner-sample
# bg detection (corners differ; tol-based fg masking would catch
# the entire image as fg).
for _style in ("charcoal", "neon-pop-art",
               "renaissance-royalty", "bold-graphic-poster"):
    # Use the safe-zone-preserving post-process — the generic tight-crop
    # treats the calm top band as background and crops it away, putting
    # ears flush against the canvas top and forcing the script to
    # overlap the head.
    POST_PROCESS[_style] = _watercolor_post_process
POST_PROCESS["watercolor"] = _watercolor_post_process


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_font_cache: dict[str, ImageFont.FreeTypeFont] = {}

# Style → Google Font mapping (must match frontend STYLE_FONTS)
STYLE_FONT_MAP: dict[str, dict] = {
    "watercolor":           {"family": "Sacramento",         "google": "Sacramento",                               "file": "Sacramento-Regular.ttf"},
    "minimal-line-art":     {"family": "Raleway",            "google": "Raleway:wght@600",                         "file": "Raleway-SemiBold.ttf"},
    "modern-shape-art":     {"family": "Bebas Neue",         "google": "Bebas+Neue",                               "file": "BebasNeue-Regular.ttf"},
    "neon-pop-art":         {"family": "Bungee",             "google": "Bungee",                                   "file": "Bungee-Regular.ttf"},
    "renaissance-royalty":  {"family": "Cinzel",             "google": "Cinzel:wght@700",                          "file": "Cinzel-Bold.ttf"},
    "bold-graphic-poster":  {"family": "Oswald",             "google": "Oswald:wght@700",                          "file": "Oswald-Bold.ttf"},
    "aura-gradient":        {"family": "Quicksand",          "google": "Quicksand:wght@700",                       "file": "Quicksand-Bold.ttf"},
    "charcoal":             {"family": "Caveat",             "google": "Caveat:wght@500",                         "file": "Caveat-Medium.ttf"},
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
    Decide the name's text colour to GUARANTEE legibility against the
    background it sits on, with the bg's hue used as a soft tint for
    aesthetic integration.

    Earlier this function matched PET luminance (light pet → light
    text, dark pet → dark text). That produced unreadable text whenever
    the pet's tone was close to the bg's tone — most obviously a
    golden retriever (light pet) on cream Modern bg (light bg) gave
    near-white text on near-white bg = invisible. Legibility wins
    over "pet integration": text contrasts with the bg it's drawn on.

    Strategy:
      1. Sample the top 12% region — that's where the title sits.
         Bg luminance decides light-vs-dark text; bg hue feeds the tint.
      2. Light bg → near-ink text. Dark bg → near-cream text.
      3. Achromatic guard: if the bg is essentially grey, drop the
         hue tint and use plain near-black / near-white.

    Returns (text_rgb, line_rgba).
    """
    w, h = image.size
    if w <= 0 or h <= 0:
        return (0, 0, 0), (0, 0, 0, 80)

    # BG region — top 12% strip (where the title will be composited).
    top = image.crop((0, 0, w, max(2, int(h * 0.12))))
    top_pixels = list(top.getdata())
    if not top_pixels:
        bg_r = bg_g = bg_b = 0.5
    else:
        bg_r = sum(p[0] for p in top_pixels) / len(top_pixels) / 255.0
        bg_g = sum(p[1] for p in top_pixels) / len(top_pixels) / 255.0
        bg_b = sum(p[2] for p in top_pixels) / len(top_pixels) / 255.0
    bg_lum = 0.2126 * bg_r + 0.7152 * bg_g + 0.0722 * bg_b

    import colorsys
    bg_h, _bg_l, bg_s = colorsys.rgb_to_hls(bg_r, bg_g, bg_b)

    # Achromatic guard — bg is essentially grey, so any hue tint
    # would read as a colour artefact. Fall back to plain black /
    # white driven entirely by the bg's luminance.
    if bg_s < 0.08:
        if bg_lum >= 0.5:
            return (28, 26, 22), (28, 26, 22, 90)              # near-ink on light bg
        return (245, 240, 232), (245, 240, 232, 110)           # near-white on dark bg

    # Contrast against the bg's luminance band; tint with the bg hue.
    if bg_lum >= 0.5:
        # Light bg → dark text. Deep ink tinted with bg hue.
        out_l = 0.18
        out_s = min(0.40, bg_s * 0.50 + 0.08)
    else:
        # Dark bg → light text. Cream / near-white tinted with bg hue.
        out_l = 0.93
        out_s = min(0.14, bg_s * 0.28)

    cr, cg, cb = colorsys.hls_to_rgb(bg_h, out_l, out_s)
    text_rgb = (
        max(0, min(255, int(round(cr * 255)))),
        max(0, min(255, int(round(cg * 255)))),
        max(0, min(255, int(round(cb * 255)))),
    )
    line_rgba = (text_rgb[0], text_rgb[1], text_rgb[2], 100)
    return text_rgb, line_rgba


# Per-style text rendering config — controls how the name looks on each style
STYLE_TEXT_CONFIG: dict[str, dict] = {
    "watercolor": {
        # Sacramento handwritten script. size_ratio 0.10 gives the name
        # enough visual weight on a large canvas without overwhelming the
        # pet. zone_top 0.17 places the name's vertical centre at 17% of
        # source height — approximately halfway between the canvas top and
        # the pet's ears (y≈28%), which reads as intentionally anchored
        # rather than a floating label at the top edge.
        "size_ratio": 0.10,
        "transform": "title",
        "zone_top": 0.17,
        "letter_spacing": 0,
        "opacity": 1.0,
    },
    "minimal-line-art": {
        # Letter-spacing was 6 on top of FONT_SIZE_SCALE["small"]=0.7 —
        # combined with size_ratio 0.035 the name read as 4 disconnected
        # dots across the canvas. Tuned to a readable but still airy
        # tracked-letter look. zone_top 0.11 lands the name inside the
        # NAME SAFE ZONE the prompt reserves at the top, well above the
        # line-art figure (ears at y≈25-28%).
        "size_ratio": 0.06,
        "transform": "upper",
        "zone_top": 0.11,
        "letter_spacing": 2,
        "opacity": 1.0,
    },
    "modern-shape-art": {
        # 4:5 with-name layout: composite_name re-runs the reframe
        # with pad_top_ratio 0.22 → pet head at y≈18%. zone_top 0.115
        # lands the name lower on the canvas (closer to the pet,
        # anchored rather than floating at the top edge). 1:1 with-name
        # uses pad_top_ratio 0.45 → head at y≈31%; composite_name
        # detects the square aspect and bumps zone_top to 0.155.
        "size_ratio": 0.075,
        "transform": "upper",
        "zone_top": 0.115,
        "letter_spacing": 3,
        "opacity": 1.0,
    },
    "neon-pop-art": {
        # zone_top 0.11 lands the name inside the prompt-reserved NAME
        # SAFE ZONE, above the pet's ears (y≈25-28%).
        "size_ratio": 0.07,
        "transform": "upper",
        "zone_top": 0.11,
        "letter_spacing": 2,
        "opacity": 1.0,
    },
    "renaissance-royalty": {
        "size_ratio": 0.06,
        "transform": "upper",
        "zone_top": 0.11,
        "letter_spacing": 3,
        "opacity": 0.9,
    },
    "bold-graphic-poster": {
        "size_ratio": 0.08,
        "transform": "upper",
        "zone_top": 0.11,
        "letter_spacing": 2,
        "opacity": 1.0,
    },
    "charcoal": {
        "size_ratio": 0.07,
        "transform": "title",
        "zone_top": 0.11,
        "letter_spacing": 2,
        "opacity": 0.9,
    },
    "aura-gradient": {
        "size_ratio": 0.07,
        "transform": "title",
        "zone_top": 0.11,
        "letter_spacing": 1,
        "opacity": 0.85,
    },
}

# Default config for styles not in the map
_DEFAULT_TEXT_CONFIG = {
    "size_ratio": 0.045,
    "transform": "title",
    "zone_top": 0.16,
    "letter_spacing": 0,
    "opacity": 1.0,
}


# Fraction of the canvas reserved as a clean white band at the top when a
# name is composited. 0.22 means the artwork is scaled to 78% height and
# inset below a 22% white band. The band size is chosen so that a name
# placed at zone_top ≥ 0.11 still survives the 10% top crop applied when
# a 4:5 source is shown on a 1:1 canvas (12x12 / 16x16 variants) — and
# so the name has visible white margin around it on every aspect.
_NAME_BAND_RATIO = 0.22


def _reserve_top_band(image: Image.Image, band_ratio: float = _NAME_BAND_RATIO) -> Image.Image:
    """Inset the artwork below a clean white band reserved for the name.

    Most style prompts ask for edge-to-edge artwork (no reserved panels),
    which leaves no room to composite a name without overlapping. This
    helper rebuilds the canvas with a white band on top and the original
    artwork scaled to fit the remainder, so every style has a consistent
    space for the name regardless of how the source was generated.

    The output is the same dimensions as the input — downstream cropping
    logic (square / portrait variants) doesn't need to change.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    w, h = image.size
    band_h = int(h * band_ratio)
    art_h = h - band_h

    scale = art_h / h
    new_w = int(w * scale)
    new_h = art_h
    scaled = image.resize((new_w, new_h), Image.LANCZOS)

    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    art_x = (w - new_w) // 2
    art_y = band_h
    canvas.paste(scaled, (art_x, art_y))
    return canvas


def _modern_open_name_band(image: Image.Image) -> Image.Image:
    """Re-canvas a tight modern-shape-art no-name master to open a
    cream band above the pet for the name. The default reframe packs
    the pet edge-to-edge (pad_top_ratio 0.05); this opens it back up
    so composite_name has somewhere to put the text.

    Square (1:1) gets a larger band because there's less vertical room
    to amortise over — a thin band on a square reads as a clipped
    margin rather than negative space. 4:5 keeps it tighter so the pet
    still dominates on tall canvases.
    """
    is_square = image.height > 0 and abs((image.width / image.height) - 1.0) < 0.05
    if is_square:
        # Square with name needs a generous top band so the name has
        # visible breathing room above (not flush to the canvas top
        # edge) AND clear margin below before the ears start. Universal
        # flush-bottom rule: pad_bottom_ratio=0 keeps the pet's flat
        # chest cut flush at the canvas-face bottom in the mockup —
        # the 1:1 derivative is mockup-only (Printful gets the 4:5
        # master), so wrap-around concerns don't apply here.
        return _modern_shape_art_reframe(
            image,
            pad_top_ratio=0.45,
            pad_side_ratio=0.06,
            pad_bottom_ratio=0,
            target_aspect=PRINT_ASPECT_1_1,
        )
    # pad_top_ratio dialed in over a few iterations:
    #   0.22 → too much cream above; pet looks small / floats in empty space
    #   0.18 → too tight; pet head close to the top edge, name pressed up
    #   0.20 → goldilocks: head at y≈18%, name centred at y≈11% with a
    #           clear cream band above it but no excess empty area.
    # The original code comment ("0.20 lands head at y≈17%") matches this,
    # so we're back to the documented target.
    return _modern_shape_art_reframe(
        image,
        pad_top_ratio=0.20,
        pad_side_ratio=0.02,
        target_aspect=PORTRAIT_RATIO,
    )


def _tighten_top_after_name(
    image: Image.Image,
    crop_frac: float = 0.07,
) -> Image.Image:
    """Shift the composited content upward to remove the empty bg band
    above the name.

    add_background_padding adds a 10% padding ring on every side, and
    the AI's name-safe-zone prompt leaves additional empty space above
    the pet. After composite_name lays the name at zone_top≈0.11 of the
    canvas, that combined empty band reads as "the artwork is floating"
    — there's a visible margin between the canvas top and the first
    rendered glyph.

    This helper crops `crop_frac` (default 7%) off the top and re-pads
    the bottom with the sampled bottom-edge bg colour to preserve the
    original 4:5 aspect ratio. Net effect: name moves toward the top
    edge without clipping (zone_top 0.11 minus 0.07 = 0.04 — name top
    at ≈y=1.5% with breathing room), pet content shifts up the same
    amount, and the now-larger bottom padding matches the bg colour
    so it reads as natural canvas wash, not empty white space.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    w, h = image.size
    crop_px = int(round(h * crop_frac))
    if crop_px <= 0 or crop_px >= h:
        return image

    # Sample the bottom-edge bg colour so the new bottom padding matches
    # the existing canvas tone. Mode-of-coarse-bin matches the strategy
    # used in add_background_padding so a striped/textured edge doesn't
    # produce a muddy averaged colour.
    sample_band = image.crop((0, h - max(2, crop_px // 2), w, h))
    if max(sample_band.size) > 200:
        sample_band = sample_band.resize(
            (max(1, sample_band.size[0] * 200 // max(sample_band.size)),
             max(1, sample_band.size[1] * 200 // max(sample_band.size))),
            Image.LANCZOS,
        )
    pixels = list(sample_band.getdata())
    counts: dict = {}
    for r, g, b in pixels:
        key = (r >> 4, g >> 4, b >> 4)
        counts[key] = counts.get(key, 0) + 1
    if counts:
        mode_key, _ = max(counts.items(), key=lambda kv: kv[1])
        # Recover the bin centre as the fill colour.
        bg = (mode_key[0] * 16 + 8, mode_key[1] * 16 + 8, mode_key[2] * 16 + 8)
    else:
        bg = (255, 255, 255)

    # Crop top, paste onto a same-size canvas filled with bg colour.
    cropped = image.crop((0, crop_px, w, h))  # h - crop_px rows tall
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(cropped, (0, 0))
    return canvas


def composite_name(
    image: Image.Image,
    pet_name: str,
    style: Optional[str] = None,
    font_size_key: str = "small",
) -> Image.Image:
    """
    Composite the pet name onto the artwork itself, in the natural
    breathing room above the pet's head.

    Earlier versions reserved a 22% white band at the top and dropped the
    name into that band. That made the result read as "art print on
    white paper" instead of a wrapped canvas product — the band was
    visually disconnected from the artwork. Now the name sits directly on
    the source: positioned roughly halfway between the top of the canvas
    and where the pet's head begins (~22% of source after
    add_background_padding), with auto-detected text colour for contrast
    against whatever's behind it.
    """
    # Preserve PIL info dict (PNG metadata text chunks) across the .copy()
    # so the metadata-based idempotency check below can see flags written
    # by a previous composite_name pass. The default RGB.copy() preserves
    # info; convert("RGB") on a non-RGB source does too.
    src_info = dict(getattr(image, "info", {}) or {})
    img = image.copy() if image.mode == "RGB" else image.convert("RGB")
    img.info.update(src_info)
    w, h = img.size

    # METADATA IDEMPOTENCY GUARD — defeats the OCR limitation on cursive
    # fonts (watercolor's Caveat script defeats Tesseract — confidence
    # too low to trip the OCR check below — so OCR alone can't catch
    # double-composite on watercolor). PIL's info dict carries PNG text
    # chunks across save/load, so once composite_name has run on a
    # source, the resulting file carries 'pp_named=1' and any subsequent
    # composite_name pass sees it and bails. Catches every style at the
    # earliest possible point, no Tesseract dependency.
    if img.info.get("pp_named") == "1":
        log.warning(
            "[composite_name] input already carries pp_named=1 metadata — "
            "skipping composite for pet_name='%s' to avoid double-name "
            "ghosting.",
            pet_name,
        )
        return img

    # OCR-based idempotency check was REMOVED. It was causing more harm
    # than good — false positives on cubist Modern Shape Art faces (eye
    # shapes, ear tips, nose triangles read as letterforms by Tesseract)
    # made composite_name skip the composite on legitimate fresh
    # generations, producing "Yes toggled but no name visible" bugs.
    #
    # The structural guards above + at /add-name's URL filter cover the
    # actual bug class (named files reused as no-name source) without
    # needing OCR. Keeping the metadata check (pp_named) as the single
    # idempotency layer:
    #   - Reliable: deterministic boolean from PNG text chunks, not a
    #     fuzzy text-detection model
    #   - No false positives on stylized art
    #   - Falls back to allowing the composite if metadata is absent,
    #     which is the right default for fresh files
    #
    # Below the OCR-based skip is preserved as a no-op so the variable
    # bindings and surrounding code don't error — but the threshold check
    # is bypassed entirely.
    # A second composite at slightly different scale / position produces
    # ghost-doubled letters ("EDUARDO RAMIREZ" overlapping itself, JEWEL
    # + WILDER → JEWILDER).
    #
    # The scan zone is the TOP 50% of the canvas — wider than the strict
    # name-safe-zone band — because some style pipelines (e.g.
    # _modern_open_name_band) re-canvas the image and shift an
    # already-composited name from y≈11% to y≈30%, putting it OUTSIDE
    # a strict top-22% scan. Top-50% catches names regardless of where
    # earlier post-processing pushed them. Conservative alpha-char
    # thresholds below keep false-positive risk low on stylized pet art.
    #
    # Falls open silently if Tesseract isn't installed (returns None) —
    # behavior is unchanged from before in that case.
    try:
        # OCR-based idempotency removed (see comment above). Run the
        # detector for diagnostic logging only — the result NEVER causes
        # composite_name to skip. This way ops can still grep the logs
        # for unexpected text in inputs to investigate manually, without
        # the false-positive UX bug of skipping legitimate composites.
        name_band_h = max(1, int(h * 0.50))
        name_band = img.crop((0, 0, w, name_band_h))
        existing_text = _detect_hallucinated_text(name_band)
        normalized = (existing_text or "").strip()
        if existing_text and len(normalized) >= 4 and sum(1 for c in normalized if c.isalpha()) >= 4:
            # Logged at info level — composite_name proceeds anyway since
            # the metadata check is the only guard that actually skips.
            log.info(
                "[composite_name] OCR detected text '%s' in input top 50%% "
                "(pet_name='%s'). NOT skipping — pp_named metadata is the "
                "authoritative idempotency check. If you see double-name "
                "ghosting, the source URL is bypassing the /add-name "
                "_named filter.",
                existing_text, pet_name,
            )
        elif existing_text:
            log.debug(
                "[composite_name] OCR found short/noisy text '%s' (len=%d, "
                "alpha=%d) — likely false positive on stylized art, "
                "proceeding with composite for pet_name='%s'.",
                existing_text, len(normalized),
                sum(1 for c in normalized if c.isalpha()),
                pet_name,
            )
    except Exception as exc:
        log.debug("[composite_name] OCR pre-check skipped: %s", exc)

    # Get style-specific config
    cfg = STYLE_TEXT_CONFIG.get(style, _DEFAULT_TEXT_CONFIG)

    # Watercolor wants true ink-black hand-written script — no auto-
    # detected hue tint, no opacity blend. The other styles use the
    # bg-luminance-driven detection so the name always contrasts with
    # whatever's behind it.
    if style == "watercolor":
        text_color = (0, 0, 0)
    else:
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

    # AUTO-FIT — long names ("JORDAN" + a 0.04 letter-spacing on the
    # bold-graphic-poster style adds ~25% width overhead, "Eduardo
    # Ramirez" pushes to ~95% of canvas width at the default size_ratio)
    # would render past the canvas edge and get clipped at print time
    # ("JORDAN" → "JORDA"). Shrink the font until the text fits within
    # 88% of the canvas width, leaving 6% margin on each side. The
    # minimum 16px floor still applies (set above) so we never render
    # illegible text — we just shrink to fit.
    max_text_width = int(w * 0.88)
    if text_w > max_text_width and text_w > 0:
        shrink = max_text_width / text_w
        new_size = max(16, int(font_size * shrink))
        if new_size < font_size:
            font_size = new_size
            font = get_font(font_size, style=style)
            bbox = draw.textbbox((0, 0), name, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

    # zone_top is now the VERTICAL CENTRE of the name as a fraction of
    # canvas height — interpreted as "halfway between the top of the
    # canvas and the top of the pet's head." The rendered text is
    # centred on this point regardless of font size, so a tall script
    # and a thin sans-serif both align.
    #
    # Modern style: 4:5 with-name reframe (pad_top_ratio 0.20) lands the
    # head at y≈17%, so the cfg zone_top 0.085 puts the name halfway
    # above. 1:1 with-name (pad_top_ratio 0.45) lands the head at y≈31%,
    # so we bump zone_top to 0.155 when we detect the square aspect —
    # halfway between canvas top and head, with clear breathing room
    # both above and below the name.
    zone_top_frac = cfg["zone_top"]
    if style == "modern-shape-art" and h > 0 and abs((w / h) - 1.0) < 0.05:
        zone_top_frac = 0.155
    text_x = (w - text_w) // 2
    text_y = max(0, int(h * zone_top_frac) - text_h // 2)

    draw.text((text_x, text_y), name, fill=text_color, font=font)

    # Stamp idempotency metadata. Callers MUST pass pnginfo through to
    # img.save() for this to persist on disk — see _save_with_pp_named()
    # for the helper. Without that, the metadata lives only in memory.
    img.info["pp_named"] = "1"
    img.info["pp_named_value"] = (pet_name or "").strip()[:60]

    # Note: we no longer call _tighten_top_after_name here. The raw
    # no-name master is already pre-tightened (in `generate`) before
    # composite_name ever sees it, so a second tighten here would
    # double-shift content up and push the name flush against the
    # canvas top edge. The pre-tighten alone gives the name a
    # comfortable margin below the canvas top.
    return img


def _save_with_pp_named(img: Image.Image, path: Path, **save_kwargs) -> None:
    """Save a composited PIL image to PNG with the pp_named=1 metadata
    chunk preserved. Use this in place of img.save(...) for any image
    that has been through composite_name — it ensures the idempotency
    flag survives across the save/load roundtrip so a later composite_name
    pass on the same file early-exits instead of doubling the name.
    """
    from PIL import PngImagePlugin
    pnginfo = save_kwargs.pop("pnginfo", None) or PngImagePlugin.PngInfo()
    if img.info.get("pp_named") == "1":
        pnginfo.add_text("pp_named", "1")
        pp_val = img.info.get("pp_named_value") or ""
        if pp_val:
            pnginfo.add_text("pp_named_value", str(pp_val)[:60])
    img.save(path, "PNG", pnginfo=pnginfo, **save_kwargs)


# ---------------------------------------------------------------------------
# Hallucinated-text safety net
# ---------------------------------------------------------------------------

# Tesseract confidence (0-100) below which a detection is treated as noise.
# 70 is the conventional "very likely real text" threshold; lower values
# pick up too many false positives from pet anatomy (eyes ≈ "O", nose
# triangle ≈ "v", etc.) on stylised illustrations.
# Lowered from 70 → 50 to catch cubist hallucinations on Bold Graphic
# Poster / Modern Shape Art outputs. Gemini regularly slips stylized
# letterforms ("MUS", "TRA", etc.) into the forehead/ear region; at
# 70 confidence Tesseract often missed them on bold-poster fonts. 50
# triggers more retries (slightly slower generation) but eliminates
# the visible-glyph failure mode customers were seeing repeatedly.
_OCR_MIN_CONFIDENCE = 50

# Words shorter than this are ignored — single stray glyphs are almost
# always anatomy mis-read by Tesseract, not actual hallucinated text.
_OCR_MIN_WORD_LENGTH = 3


# ---------------------------------------------------------------------------
# Dark-stencil failure-mode detector
# ---------------------------------------------------------------------------
#
# Recurring Bold Graphic Poster failure: Gemini renders the pet as sparse
# dark cubist fragments with the bg colour visible through gaps between
# fragments — instead of a fully-painted mosaic where every interior pixel
# is a palette accent. The prompt forbids it (RECURRING FAILURE MODE TO
# AVOID) but the model ignores the rule maybe 5–15% of the time, especially
# on dark-coated dogs.
#
# Detection algorithm:
#   1. Downscale to 64×80 for speed (2 ms vs 200 ms full-res).
#   2. Build a binary silhouette mask: each pixel is "silhouette" if it is
#      NOT close to any of the bg colours within a tolerance.
#   3. Morphologically close the silhouette mask (dilate then erode). This
#      fills the gaps between fragments — a normal portrait closes to its
#      own silhouette, a dark-stencil portrait closes to a much larger
#      filled-in pet-shape with all the gaps now inside the closed region.
#   4. Count "leak" pixels: closed-mask=1 but original-mask=0 (i.e. bg
#      colour visible inside what should be the pet body).
#   5. Compare leak_ratio = leaks / closed_silhouette_pixels against a
#      threshold. Above the threshold = dark-stencil failure.
#
# Threshold calibration (subject to tuning from prod telemetry):
#   ratio < 0.05  → clean fully-painted portrait
#   ratio 0.05–0.15 → small acceptable gaps (e.g. spaces between ears + body)
#   ratio > 0.20 → dark-stencil failure (bg leaking through pet body)

_BG_BLEED_DOWNSCALE = (64, 80)
_BG_BLEED_TOLERANCE = 28        # max RGB-component delta to call a pixel "bg"
_BG_BLEED_CLOSE_KERNEL = 7      # odd; covers ~10% of the downscaled width
_BG_BLEED_THRESHOLD = 0.20      # leak ratio that flips the failure flag
_BG_BLEED_MIN_PET_PX = 200      # if silhouette < this, image is mostly bg


def _hex_to_rgb_tuple(hex_str: str) -> tuple[int, int, int]:
    """'#RRGGBB' or 'RRGGBB' → (r, g, b). Defensive against odd inputs."""
    s = hex_str.strip().lstrip("#")
    if len(s) != 6:
        return (255, 255, 255)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (255, 255, 255)


def _bg_colors_for_detection(style: str, style_vars: Optional[dict]) -> list[tuple[int, int, int]]:
    """Return the list of (r, g, b) bg colours to test for the given
    style+vars, used by the dark-stencil detector. Empty list disables
    detection — for styles where the bg isn't a known canonical colour.
    """
    sv = style_vars or {}
    if style == "bold-graphic-poster":
        palette_id = sv.get("poster_palette") or "teal"
        if palette_id not in POSTER_PALETTES:
            return []
        p = POSTER_PALETTES[palette_id]
        return [_hex_to_rgb_tuple(p["bg_left_hex"]), _hex_to_rgb_tuple(p["bg_right_hex"])]
    if style == "modern-shape-art":
        bg_id = sv.get("modern_bg_color")
        if bg_id and bg_id in MODERN_BG_COLORS:
            # MODERN_BG_COLORS values are (hex, name) tuples — index [0]
            # for the hex. Passing the whole tuple to _hex_to_rgb_tuple
            # raises 'tuple has no attribute strip' and crashes the
            # entire Modern-style render flow before the candidate is
            # even generated.
            hex_code, _name = MODERN_BG_COLORS[bg_id]
            return [_hex_to_rgb_tuple(hex_code)]
    return []


def _detect_bg_bleed_through(
    image: Image.Image,
    bg_colors: list[tuple[int, int, int]],
) -> Optional[float]:
    """Return the leak ratio if the image shows the dark-stencil failure
    (bg colour visible inside the pet silhouette), or None when the image
    passes. Pure-PIL — no numpy dependency. ~5 ms per call at the 64×80
    downscale.

    None is also returned when bg_colors is empty (detector disabled),
    when the silhouette is too small to evaluate (mostly bg image), or
    when the leak ratio is below the threshold.
    """
    if not bg_colors:
        return None
    try:
        from PIL import ImageFilter
    except ImportError:
        return None

    rgb = image.convert("RGB").resize(_BG_BLEED_DOWNSCALE, Image.LANCZOS)
    pixels = list(rgb.getdata())
    w, h = rgb.size

    tol = _BG_BLEED_TOLERANCE

    def _is_bg(p: tuple) -> bool:
        r, g, b = p[0], p[1], p[2]
        for br, bgg, bb in bg_colors:
            if abs(r - br) <= tol and abs(g - bgg) <= tol and abs(b - bb) <= tol:
                return True
        return False

    # Silhouette mask as L-mode image: 255 = pet, 0 = bg.
    sil_data = bytes(0 if _is_bg(p) else 255 for p in pixels)
    sil_mask = Image.frombytes("L", (w, h), sil_data)

    sil_count_raw = sum(1 for b in sil_data if b == 255)
    if sil_count_raw < _BG_BLEED_MIN_PET_PX:
        return None

    # Morphological closing: dilate (MaxFilter) then erode (MinFilter).
    # Fills the gaps a fragmented dark-stencil silhouette has between
    # its disconnected fragments, so we can count "interior bg" pixels.
    closed = sil_mask.filter(ImageFilter.MaxFilter(_BG_BLEED_CLOSE_KERNEL))
    closed = closed.filter(ImageFilter.MinFilter(_BG_BLEED_CLOSE_KERNEL))
    closed_data = closed.tobytes()

    # Count: pixels inside the closed region (closed=255) and "leaks"
    # (closed=255 but original silhouette=0 → bg colour leaking through
    # the pet body).
    closed_count = 0
    leak_count = 0
    for orig_b, closed_b in zip(sil_data, closed_data):
        if closed_b == 255:
            closed_count += 1
            if orig_b == 0:
                leak_count += 1

    if closed_count < _BG_BLEED_MIN_PET_PX:
        return None

    leak_ratio = leak_count / closed_count
    return leak_ratio if leak_ratio > _BG_BLEED_THRESHOLD else None


# ---------------------------------------------------------------------------
# Flat-sticker failure-mode detector
# ---------------------------------------------------------------------------
#
# Recurring failure mode: Gemini renders the pet as a single dark uniform
# silhouette ("flat sticker") instead of the multi-tone WPAP / cubist
# faceting the prompt asks for. Cousin of dark-stencil but distinct: a
# flat sticker has NO bg leaking through (it's a solid filled shape),
# but ALSO has no internal colour variation. Both fail the WPAP aesthetic
# differently and need separate detection.
#
# Detection algorithm:
#   1. Build the silhouette mask (pixels NOT close to bg colours), same
#      as the dark-stencil detector.
#   2. Within the silhouette pixels, sample a luminance distribution.
#   3. If the std-dev of luminance inside the silhouette is below a
#      threshold, the pet is a flat solid blob — failure.
#
# Rationale: WPAP / cubist faceting always produces multi-tone palette
# accents distributed across the silhouette, regardless of the pet's
# real-world coat colour (a black cat on a teal palette still gets
# charcoal + ivory + accent blocks from the palette, NOT one solid
# black shape). Std-dev of luminance in a properly-faceted portrait is
# typically 35–60 (on the 0–255 scale). A flat-sticker output sits
# below 15.
#
# Threshold calibration (subject to tuning from prod telemetry):
#   stddev > 30 → healthy faceted portrait
#   stddev 15–30 → low-contrast palette but acceptable
#   stddev < 15 → flat-sticker failure

_FLAT_STICKER_MIN_STDDEV = 15.0
_FLAT_STICKER_MIN_PET_PX = 200


def _detect_flat_sticker(
    image: Image.Image,
    bg_colors: list[tuple[int, int, int]],
) -> Optional[float]:
    """Return the in-silhouette luminance std-dev if the image shows the
    flat-sticker failure (pet rendered as a single uniform colour blob),
    or None when the image passes. Pure-PIL — no numpy dependency.

    None is also returned when bg_colors is empty (detector disabled) or
    when the silhouette is too small to evaluate (mostly bg image).
    """
    if not bg_colors:
        return None

    rgb = image.convert("RGB").resize(_BG_BLEED_DOWNSCALE, Image.LANCZOS)
    pixels = list(rgb.getdata())
    tol = _BG_BLEED_TOLERANCE

    def _is_bg(p: tuple) -> bool:
        r, g, b = p[0], p[1], p[2]
        for br, bgg, bb in bg_colors:
            if abs(r - br) <= tol and abs(g - bgg) <= tol and abs(b - bb) <= tol:
                return True
        return False

    # Collect luminance values for silhouette pixels only. Rec. 709
    # luminance: 0.2126·R + 0.7152·G + 0.0722·B.
    lums: list[float] = []
    for p in pixels:
        if _is_bg(p):
            continue
        r, g, b = p[0], p[1], p[2]
        lums.append(0.2126 * r + 0.7152 * g + 0.0722 * b)

    if len(lums) < _FLAT_STICKER_MIN_PET_PX:
        return None

    # Population std-dev (no Bessel correction needed; we have ≥200
    # samples).
    mean = sum(lums) / len(lums)
    var = sum((x - mean) ** 2 for x in lums) / len(lums)
    stddev = var ** 0.5

    return stddev if stddev < _FLAT_STICKER_MIN_STDDEV else None


def _detect_hallucinated_text(image: Image.Image) -> Optional[str]:
    """Return the first text fragment Tesseract reads from the image with
    confidence above _OCR_MIN_CONFIDENCE, or None if the image looks clean.

    Used to catch Gemini ignoring the no-text instruction and rendering
    the pet's name as decorative lettering above the head — a recurring
    failure mode on modern-shape-art and bold-graphic-poster styles.

    The function returns None on any error (Tesseract not installed,
    binary missing, etc.) so production never fails open. Set the env
    var PP_DISABLE_OCR_CHECK=1 to bypass the check entirely.
    """
    if os.environ.get("PP_DISABLE_OCR_CHECK") == "1":
        return None
    try:
        import pytesseract
    except ImportError:
        log.debug("[ocr-check] pytesseract not installed — skipping")
        return None
    try:
        # PSM 11 = sparse text, no assumed page structure. Best fit for
        # an illustration that *might* contain text floating anywhere.
        data = pytesseract.image_to_data(
            image,
            output_type=pytesseract.Output.DICT,
            config="--psm 11",
        )
    except Exception as e:
        log.warning("[ocr-check] tesseract failed (%s) — assuming clean", e)
        return None

    texts = data.get("text") or []
    confs = data.get("conf") or []
    for txt, raw_conf in zip(texts, confs):
        word = (txt or "").strip()
        if len(word) < _OCR_MIN_WORD_LENGTH:
            continue
        # Tesseract returns confidence as int or string; normalise.
        try:
            conf = int(float(raw_conf))
        except (ValueError, TypeError):
            conf = -1
        if conf < _OCR_MIN_CONFIDENCE:
            continue
        # Only flag words that are predominantly alphabetic — random
        # punctuation soup ("!@#") is almost always noise.
        alpha_count = sum(1 for c in word if c.isalpha())
        if alpha_count >= _OCR_MIN_WORD_LENGTH:
            return word
    return None


# ---------------------------------------------------------------------------
# Image utilities
# ---------------------------------------------------------------------------

_WATERMARK_LOGO_LIGHT: Image.Image | None = None   # white — for dark backgrounds
_WATERMARK_LOGO_DARK: Image.Image | None = None    # dark grey — for light backgrounds


def _build_watermark_logo(color: tuple) -> Image.Image:
    """Build a single-color RGBA watermark logo from the black-on-white source PNG."""
    logo_path = Path(__file__).parent / "assets" / "watermark-logo.png"
    if not logo_path.exists():
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    src = Image.open(logo_path).convert("L")
    # Ink density: dark source pixels → high ink, white background → 0
    ink = src.point(lambda p: 255 - p)
    # 1% — the mockup should read as the finished piece, not as a
    # preview sample. The watermark is still present (last-mile
    # screenshot deterrent) but visible only on close inspection.
    # Note: at 1% the mark effectively disappears on saturated
    # mid-brightness fills (neon hot pink, vivid blue) — IP
    # protection here leans on the un-watermarked print PNG never
    # being served, not on this overlay.
    OPACITY = 0.01
    alpha = ink.point(lambda p: int(p * OPACITY))
    rgba = Image.new("RGBA", src.size, color + (0,))
    rgba.putalpha(alpha)
    return rgba


def _get_watermark_logo(target_width: int, dark: bool = False) -> Image.Image:
    """Return the Pet Printables wordmark at target_width.

    dark=True → dark grey letterforms (for light-background images).
    dark=False → white letterforms (for dark-background images).
    Both variants are cached after first load.
    """
    global _WATERMARK_LOGO_LIGHT, _WATERMARK_LOGO_DARK
    if dark:
        if _WATERMARK_LOGO_DARK is None:
            _WATERMARK_LOGO_DARK = _build_watermark_logo((30, 30, 30))
        logo = _WATERMARK_LOGO_DARK
    else:
        if _WATERMARK_LOGO_LIGHT is None:
            _WATERMARK_LOGO_LIGHT = _build_watermark_logo((255, 255, 255))
        logo = _WATERMARK_LOGO_LIGHT

    logo_h = int(target_width * logo.height / logo.width)
    return logo.resize((target_width, logo_h), Image.LANCZOS)


def apply_preview_watermark(image: Image.Image) -> Image.Image:
    """Tile a translucent diagonal Pet Printables logo across the image so a
    customer can't simply screenshot or right-click-save the on-page
    preview and skip the purchase. The watermark is heavy enough to be
    obviously present in a screenshot but light enough that the customer
    can still meaningfully evaluate the artwork.

    This function only runs for the customer-facing web previews
    (small WebPs displayed on the PDP, in the cart, in the order admin).
    The hi-res PNG print files that go to Printful are NEVER watermarked
    — fulfillment renders the original artwork.
    """
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    base = rgb.convert("RGBA")
    w, h = base.size

    # Sample overall brightness to choose white (dark images) or dark grey (light images).
    grey = base.convert("L")
    avg_brightness = sum(grey.getdata()) // (w * h)
    use_dark_logo = avg_brightness > 148

    # Logo tile width = ~28% of the image width — readable but not overwhelming.
    logo_w = max(80, int(w * 0.28))
    logo = _get_watermark_logo(logo_w, dark=use_dark_logo)
    lw, lh = logo.size

    # Tile onto a diagonal overlay: draw onto a square whose side equals the
    # image diagonal, rotate, then center-crop back to image dimensions.
    diag = int((w * w + h * h) ** 0.5)
    tile = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))

    step_x = lw + lw // 2    # 1.5× logo width between centres horizontally
    step_y = lh + lh // 2    # 1.5× logo height between centres vertically

    for ty in range(-lh, diag + lh, step_y):
        # Stagger every other row by half a step so stamps don't align in columns
        row_index = (ty + lh) // step_y
        x_offset = (row_index % 2) * (step_x // 2)
        for tx in range(-lw + x_offset, diag + lw, step_x):
            # Paste without mask: copies all 4 channels as-is so the logo's
            # alpha is not premultiplied against itself (which halves it twice).
            tile.paste(logo, (tx, ty))

    rotated = tile.rotate(-30, resample=Image.BICUBIC, expand=False)
    # Center-crop the rotated overlay back to image size
    rx = (rotated.width - w) // 2
    ry = (rotated.height - h) // 2
    overlay = rotated.crop((rx, ry, rx + w, ry + h))

    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out


def save_web_preview(
    image: Image.Image,
    out_path: Path,
    max_width: int = 800,
    watermark: bool = True,
) -> Path:
    """
    Save a fast-loading web preview: resize to max_width, optionally
    overlay a "PREVIEW" watermark, then convert to WebP at q80.

    The watermark is on by default because every WebP this function
    produces is shown to a customer in the browser. Pass watermark=False
    only when generating a non-customer preview (internal QA, debug).

    Typical output: ~60-120 KB vs 2-5 MB for the full PNG.
    Returns the path to the saved .webp file.
    """
    preview_path = out_path.with_suffix(".webp")
    img = image if image.mode == "RGB" else image.convert("RGB")
    w, h = img.size
    if w > max_width:
        scale = max_width / w
        img = img.resize((max_width, int(h * scale)), Image.LANCZOS)
    if watermark:
        img = apply_preview_watermark(img)
    img.save(preview_path, "WEBP", quality=80)
    log.info("           web  → %s (%dx%d, %d KB)%s",
             preview_path.name, img.width, img.height,
             preview_path.stat().st_size // 1024,
             " [watermarked]" if watermark else "")
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


# ---------------------------------------------------------------------------
# Eye-quality critic (Gemini-as-judge)
# ---------------------------------------------------------------------------
#
# Phase 2 detector for failure modes that don't show up in pixel statistics:
#   • creepy eyes (saturated headlight irises, taxidermy stare)
#   • missing eyes (fluffy-faced breeds where eyes get lost in fur)
#   • divergent gaze (one pupil left, the other right)
#
# These three classes are hard to catch with PIL math because they require
# knowing where the eyes ARE in the image — and pet eyes occupy a tiny
# fraction of the canvas (typically <2%), with no fixed location across
# breeds and styles. Rather than ship an eye-region segmentation model
# (~10 MB on disk, ~50 ms inference), we send the generated image to
# Gemini Flash text-only as a critic and ask it to rate the eyes on three
# axes. Cost is ~$0.0001 per call (vs. ~$0.04 for the original Gemini
# image generation), so even firing on every BGP/MSA generation, it's
# rounding-error in the per-portrait budget.
#
# Failure-mode definition: any axis scoring < 3 on the 1–5 scale flips
# the verdict to "fail" and the orchestrator decides whether to retry.
#
# Disable in production by setting env var PP_DISABLE_EYE_CRITIC=1.

_EYE_CRITIC_MIN_SCORE = 3
_EYE_CRITIC_PROMPT = (
    "Look at this pet portrait. Rate the EYES on three axes (1=worst, "
    "5=best) and return JSON only — no markdown, no code fence, no prose:\n"
    "\n"
    "  visible — both eyes clearly drawn and discernible (5), partially "
    "obscured by fur (3), or entirely missing/blocked (1)\n"
    "  non_creepy — eyes look natural/alive (5), or glowing/headlight/"
    "taxidermy/uncanny-valley (1). Saturated yellow/red irises against "
    "a saturated background are usually creepy. Tiny pinprick pupils "
    "inside large saturated irises are usually creepy.\n"
    "  gaze_symmetric — both pupils point in the same direction "
    "relative to their irises (5), or diverge / one centred + one "
    "off-axis / different vertical heights (1)\n"
    "\n"
    "Return EXACTLY this JSON shape:\n"
    '{"visible": N, "non_creepy": N, "gaze_symmetric": N, '
    '"issues": "brief explanation if any score is below 4, else empty"}'
)


def _critique_eyes(
    image_bytes: bytes,
    mime_type: str = "image/png",
) -> Optional[dict]:
    """Send the generated portrait to Gemini Flash text-only and ask it
    to rate the eyes on three axes (visible, non_creepy, gaze_symmetric).

    Returns the rating dict on success, or None on any error (Gemini
    unreachable, JSON parse failure, env-var disable, missing API key).
    Fails OPEN — the caller must treat None as "no signal, ship the
    candidate." This keeps the critic from blocking real generations
    when Gemini Flash itself has an outage.

    The dict has shape:
        {"visible": int, "non_creepy": int, "gaze_symmetric": int,
         "issues": str, "min_score": int}

    `min_score` is computed from the three axes for easy threshold-
    based filtering by the caller.
    """
    if os.environ.get("PP_DISABLE_EYE_CRITIC") == "1":
        return None
    if not os.environ.get("GEMINI_API_KEY", ""):
        return None

    try:
        client = _get_client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",  # text-only critic, ~$0.0001/call
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        types.Part.from_text(text=_EYE_CRITIC_PROMPT),
                    ],
                )
            ],
        )

        text = ""
        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

        # Strip markdown fence even though the prompt forbids it.
        import json as _json
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        result = _json.loads(text)
        # Coerce score fields to int and clamp 1–5. Any missing axis
        # defaults to 5 (best) so a partially-malformed response only
        # trips the verdict on the axes the model actually returned.
        def _score(key: str) -> int:
            v = result.get(key, 5)
            try:
                return max(1, min(5, int(v)))
            except (TypeError, ValueError):
                return 5

        out = {
            "visible": _score("visible"),
            "non_creepy": _score("non_creepy"),
            "gaze_symmetric": _score("gaze_symmetric"),
            "issues": str(result.get("issues") or "")[:160],
        }
        out["min_score"] = min(out["visible"], out["non_creepy"], out["gaze_symmetric"])
        return out

    except Exception as exc:
        # Fail open — log the issue, return None so the caller ships
        # the candidate without retry.
        log.debug("[eye-critic] failed: %s", exc)
        return None


def add_name_to_image(
    image_bytes: bytes,
    style: str,
    pet_name: str,
    max_retries: int = 2,
    background_mode: Optional[str] = "auto",
    style_vars: Optional[dict] = None,
) -> bytes:
    """Take an already-generated portrait and ask Gemini to add the pet's name
    into the existing artwork — preserving every detail of the original image.

    This avoids the problem of two separate Gemini calls producing two different
    artworks when we want "same image with/without name".
    """
    client = _get_client()
    name_block = _name_integration(style, pet_name, background_mode, style_vars)
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


# Variation hints appended to the prompt when the customer regenerates
# the same photo + style combo. Bold Graphic Poster's tightly-constrained
# output space causes Gemini to converge on near-identical compositions
# from the same input — even with different uuids and different R2 keys,
# customers see "the same image" twice in the cart and assume one was
# overwritten. Picking one of these per generation breaks the convergence
# without changing the baseline aesthetic.
_VARIATION_HINTS = (
    "Lean toward the cooler end of the accent palette in the pet's shading.",
    "Lean toward the warmer end of the accent palette in the pet's shading.",
    "Favor slightly larger polygonal shapes inside the pet silhouette.",
    "Favor slightly smaller polygonal shapes for finer faceting detail.",
    "Tilt the head a touch toward the camera-left side.",
    "Tilt the head a touch toward the camera-right side.",
    "Render with a marginally tighter crop on the head and chest.",
    "Render with a marginally wider framing — slightly more bg around the pet.",
)


def call_gemini(
    photo_path: Path,
    style: str,
    style_vars: Optional[dict] = None,
    max_retries: int = 2,
    pet_name: str = "",
    background_mode: Optional[str] = "auto",
    variation_seed: Optional[int] = None,
) -> bytes:
    """Send photo + prompt to Gemini; return raw PNG/JPEG bytes of the generated image.

    When pet_name is provided, the name is integrated into the artwork natively
    (hand-painted into watercolor, engraved into renaissance, etc.) rather than
    composited as a flat text overlay afterward.

    When variation_seed is provided, a small variation hint is appended to the
    prompt so re-generations of the same (photo, style) combo produce visibly
    different outputs. The customer-visible baseline stays the same on the
    first generation (no seed → no hint).

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
            + _composition_rule(has_name=False)
            + _NO_BORDER_RULE
        )
    # Accept only an int seed. The job-record round-trip stores the absence
    # of a seed as "" (Redis HSET string-coerces None → ""), and `"" is
    # not None` evaluates True — so a naive `is not None` check used to
    # fall through into `"" % len(_VARIATION_HINTS)` and raise
    # "TypeError: not all arguments converted during string formatting"
    # which leaked to the customer's UI.
    if isinstance(variation_seed, int):
        hint = _VARIATION_HINTS[variation_seed % len(_VARIATION_HINTS)]
        prompt = prompt + "\n\nVARIATION FOR THIS RENDER: " + hint
        log.info(
            "[generate] variation_seed=%d → hint='%s'",
            variation_seed, hint[:60],
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
            # Keep the model text in server logs only — never include it in the
            # exception message, which gets serialized to the customer-facing
            # job.error field. A raw model dump is a horrible customer experience.
            log.error(
                "Gemini returned no image. Model response: %s",
                " | ".join(text_parts) or "no details",
            )
            raise RuntimeError("Gemini returned no image")

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
    variation_seed: Optional[int] = None,
) -> tuple[Path, Path, Path, Path]:
    """
    Generate a portrait and composite the pet name onto it.

    Uses a semaphore to limit concurrent Gemini calls and prevent OOM.
    Raises RuntimeError('BUSY') if the semaphore cannot be acquired within 2s,
    which app.py maps to a 503 response so the frontend can retry.

    Returns:
        (raw_path, composited_path, web_preview_path, raw_web_preview_path)

        - raw_path / composited_path: hi-res PNGs for Printful fulfillment.
          NEVER shown to the customer directly — these have no watermark.
        - web_preview_path / raw_web_preview_path: small WebPs for
          customer display (with-name and no-name respectively). Both
          carry the diagonal "PREVIEW" watermark.
    """
    if not _generation_semaphore.acquire(timeout=2):
        raise RuntimeError("BUSY")

    try:
        return _generate_inner(  # type: ignore[return-value]
            photo_path, pet_name, style, output_dir, style_vars,
            background_mode, variation_seed,
        )
    finally:
        _generation_semaphore.release()


def _generate_inner(
    photo_path: "str | Path",
    pet_name: str,
    style: str,
    output_dir: Optional[Path],
    style_vars: Optional[dict],
    background_mode: Optional[str] = "auto",
    variation_seed: Optional[int] = None,
) -> tuple[Path, Path]:
    import uuid as _uuid
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    photo = Path(photo_path)
    uid   = _uuid.uuid4().hex[:10]  # unique per request — no file collisions

    # Modern style uses background_mode as a colour-palette selector
    # (cream/clay/sage/etc.) rather than auto/light/dark. Lift the chosen
    # colour into style_vars so the prompt builder picks it up, and reset
    # background_mode to 'auto' so the generic light/dark override block
    # doesn't try to layer on top.
    if style == "modern-shape-art" and background_mode in MODERN_BG_COLORS:
        style_vars = {**(style_vars or {}), "modern_bg_color": background_mode}
        background_mode = "auto"
    # Bold Graphic Poster does the same with paired-tone palettes — lift the
    # chosen palette id into style_vars["poster_palette"] so the prompt
    # builder injects the bg + accent colours, and reset background_mode.
    if style == "bold-graphic-poster" and background_mode in POSTER_PALETTES:
        style_vars = {**(style_vars or {}), "poster_palette": background_mode}
        background_mode = "auto"
    # Watercolor uses background_mode as a wash-tint colour selector
    # (paper/cream/blush/etc.) — lift into style_vars["watercolor_bg"] and
    # reset background_mode so no generic light/dark override is appended.
    if style == "watercolor" and background_mode in WATERCOLOR_BG_COLORS:
        style_vars = {**(style_vars or {}), "watercolor_bg": background_mode}
        background_mode = "auto"

    log.info("[generate] %s  '%s'  ←  %s", style, pet_name, photo.name)

    # Preview generation: ONE Gemini call — no-name version only.
    # The with-name version is generated lazily by add_name_endpoint
    # when the user adds to cart (halves per-portrait Gemini cost).
    #
    # OCR safety net: Gemini sometimes hallucinates text into the no-name
    # source despite explicit "NO TEXT" instructions in the prompt — most
    # commonly the pet's name or generic placeholder names rendered as
    # decorative lettering above the head. If composite_name later draws
    # the real name on top, the two layers ghost into doubled letters
    # ("RROGEER"). We OCR the output and regenerate up to 2 extra times
    # if Tesseract reads any high-confidence multi-character text.
    _OCR_MAX_REGEN = 2
    raw_bytes: Optional[bytes] = None
    ai_image_no_name: Optional[Image.Image] = None
    # Bg colours used to detect the "dark stencil" failure mode (bg leaking
    # through pet silhouette). Empty list disables detection — only BGP and
    # modern-shape-art currently have canonical bg hexes we can test.
    _bg_bleed_palette = _bg_colors_for_detection(style, style_vars)
    for ocr_attempt in range(_OCR_MAX_REGEN + 1):
        candidate_bytes = call_gemini(
            photo, style, style_vars, pet_name="",
            background_mode=background_mode,
            variation_seed=variation_seed,
        )
        candidate_img = Image.open(BytesIO(candidate_bytes))
        candidate_img.load()

        # Failure-mode check #1: hallucinated text. Triggers retry.
        leaked = _detect_hallucinated_text(candidate_img)
        if leaked is not None:
            log.warning(
                "[generate] OCR detected hallucinated text '%s' on attempt %d/%d "
                "— regenerating no-name source",
                leaked, ocr_attempt + 1, _OCR_MAX_REGEN + 1,
            )
            candidate_img.close()
            continue

        # Failure-mode check #2: dark-stencil bg-bleed. LOG ONLY for now —
        # this is a new detector and we want to see the false-positive rate
        # in production before triggering retries. Once telemetry confirms
        # the threshold catches real failures without false-positiving on
        # legit gappy silhouettes, swap the warning for a `continue` to
        # enable retry. The ratio is logged so ops can grep
        # "[generate] dark-stencil ratio=" and tune the threshold.
        if _bg_bleed_palette:
            leak_ratio = _detect_bg_bleed_through(candidate_img, _bg_bleed_palette)
            if leak_ratio is not None:
                log.warning(
                    "[generate] dark-stencil ratio=%.3f detected on attempt %d/%d "
                    "(style=%s) — NOT retrying yet (logging-only mode); "
                    "shipping this candidate",
                    leak_ratio, ocr_attempt + 1, _OCR_MAX_REGEN + 1, style,
                )
            else:
                log.debug("[generate] dark-stencil check passed (style=%s)", style)

        # Failure-mode check #3: flat-sticker. LOG ONLY for now — same
        # rollout pattern as dark-stencil. Emits std-dev so we can tune
        # the threshold from production data. Grep
        # "[generate] flat-sticker stddev=" to see hit rate.
        if _bg_bleed_palette:
            stddev = _detect_flat_sticker(candidate_img, _bg_bleed_palette)
            if stddev is not None:
                log.warning(
                    "[generate] flat-sticker stddev=%.2f detected on attempt %d/%d "
                    "(style=%s) — NOT retrying yet (logging-only mode); "
                    "shipping this candidate",
                    stddev, ocr_attempt + 1, _OCR_MAX_REGEN + 1, style,
                )
            else:
                log.debug("[generate] flat-sticker check passed (style=%s)", style)

        # Failure-mode check #4: eye-quality critic (Gemini-as-judge).
        # LOG ONLY for now — same calibration rollout as dark-stencil.
        # Scoped to BGP + modern-shape-art because those are where eye
        # failures surfaced (creepy/glowing irises, missing eyes on
        # fluffy faces, divergent gaze). The critic costs ~$0.0001 per
        # call — rounding error on the $0.04 base generation cost — so
        # firing it on every candidate is fine. Set
        # PP_DISABLE_EYE_CRITIC=1 to bypass.
        if style in ("bold-graphic-poster", "modern-shape-art"):
            critique = _critique_eyes(candidate_bytes)
            if critique is not None:
                if critique["min_score"] < _EYE_CRITIC_MIN_SCORE:
                    log.warning(
                        "[generate] eye-critic min_score=%d (visible=%d "
                        "non_creepy=%d gaze=%d) issues=%r on attempt %d/%d "
                        "(style=%s) — NOT retrying yet (logging-only mode); "
                        "shipping this candidate",
                        critique["min_score"], critique["visible"],
                        critique["non_creepy"], critique["gaze_symmetric"],
                        critique["issues"], ocr_attempt + 1,
                        _OCR_MAX_REGEN + 1, style,
                    )
                else:
                    log.debug(
                        "[generate] eye-critic passed min_score=%d (style=%s)",
                        critique["min_score"], style,
                    )
            # critique is None → fail-open (Gemini critic unreachable or
            # disabled). Don't log warning — that's the expected disabled
            # path, not a failure.

        # All blocking checks passed — accept this candidate.
        raw_bytes = candidate_bytes
        ai_image_no_name = candidate_img
        break
    else:
        # Exhausted retries — ship the last attempt rather than failing the
        # whole job. Composite_name will still ghost, but at least the
        # customer gets a portrait. The warning above is the breadcrumb
        # for ops to look at the prompt or model behaviour.
        log.error(
            "[generate] OCR safety net exhausted after %d attempts — "
            "shipping last no-name source anyway",
            _OCR_MAX_REGEN + 1,
        )
        raw_bytes = candidate_bytes
        ai_image_no_name = candidate_img

    # Add background padding so the pet has breathing room around it.
    # Gemini routinely ignores prompt-based size constraints and renders
    # pets edge-to-edge — this guarantees breathing room programmatically.
    if style in _PORTRAIT_STYLES:
        if style == "modern-shape-art":
            # Smart reframe: detect pet bounding box and compose with exact
            # target margins (10% top, 8% sides, 0% bottom if flat cut).
            # This handles both edge-to-edge and over-padded AI outputs.
            padded = _modern_shape_art_reframe(ai_image_no_name)
        elif style == "neon-pop-art":
            # Single-saturated-bg style: edge replication produces streaks
            # when the pet touches an edge (especially the bottom, which
            # the composition rule explicitly allows). Sample the bg colour
            # from the top corners — pet never reaches there — and fill
            # the padding ring with that solid colour.
            #
            # pad_bottom_ratio=0 enforces the universal flush-bottom rule.
            sw, sh = ai_image_no_name.size
            cs = max(8, min(sw, sh) // 50)
            corners = (
                ai_image_no_name.crop((0, 0, cs, cs)).getdata(),
                ai_image_no_name.crop((sw - cs, 0, sw, cs)).getdata(),
            )
            pixels = list(corners[0]) + list(corners[1])
            n = len(pixels)
            bg = (
                sum(p[0] for p in pixels) // n,
                sum(p[1] for p in pixels) // n,
                sum(p[2] for p in pixels) // n,
            )
            padded = add_background_padding(
                ai_image_no_name, padding_ratio=0.17, solid_bg_color=bg,
                pad_bottom_ratio=0,
            )
            padded = _center_horizontal_weight(padded)
        elif style == "bold-graphic-poster":
            # Cubist 2-tone vertical-split bg. We KNOW the exact target
            # bg colours from the palette pick, so pad with the canonical
            # 2-tone split rather than edge-replicating the AI's outermost
            # 1px row/column. Edge replication used to leak cream/white
            # into the bleed band when Gemini ignored the edge-to-edge
            # rule and rendered the split bg with a sliver of cream above
            # it — _pad_split_bg eliminates that whole failure mode.
            palette_id = (style_vars or {}).get("poster_palette") or "teal"
            if palette_id in POSTER_PALETTES:
                _palette = POSTER_PALETTES[palette_id]
                # Trim any off-palette margin Gemini left around the
                # colored split before padding. Catches both directions:
                # near-white slivers above the split, AND subtle perimeter
                # darkening / vignette that would otherwise read as a
                # darker frame between the bleed and the artwork in the
                # final printed canvas.
                trimmed = _trim_off_palette_margin(ai_image_no_name, _palette)
                padded = _pad_split_bg(
                    trimmed, _palette,
                    padding_ratio=0.17, pad_bottom_ratio=0,
                )
                # BG FLATTEN — Gemini reliably reads the prompt's "vertical
                # 2-tone split" but routinely drops a soft vignette / corner
                # darkening / faint room-shadow into one of the halves. Even
                # with the prompt explicitly forbidding it, the failure mode
                # recurs on every other roll. Snap any pixel that is "close
                # enough" to the expected bg hex (per the customer's
                # poster_palette pick) to the EXACT bg hex. The pet uses
                # palette accent colours that sit far from either bg colour
                # in RGB space, so the snap leaves the cubist faceting intact
                # while flattening any soft bg variation away.
                # Cool palettes (teal/cobalt/forest/violet) override
                # interior_tol downward so natural pet-shadow tones in the
                # pet's bg-adjacent colour family don't get snapped to bg
                # and read as silhouette breaches.
                _interior_tol = int(_palette.get("interior_tol", 90))
                padded = _flatten_poster_bg(
                    padded, _palette, interior_tol=_interior_tol,
                )
                padded = _remove_poster_halos(padded, _palette)
                # GATED PALETTE SNAP — kills the "transparent-PNG-pasted-
                # on-bg" white-fringe / light-mauve halo look that destroys
                # print quality on canvas. _remove_poster_halos catches
                # PURE-white halos (RGB > 235), but Gemini's anti-aliasing
                # often leaves intermediate-colour edge pixels (light
                # cream, light mauve, light bg-tinted accent) along the
                # pet/bg boundary that aren't pure white but read as halo
                # at print scale.
                #
                # The unconditional snap was disabled previously because
                # it collapsed warm-coat pets on cool palettes into bg.
                # max_distance=45 fixes that: anti-aliased intermediates
                # sit ~10-30 RGB from the nearest palette member and get
                # snapped clean; genuinely off-palette pet pixels (>100
                # RGB from any member) stay untouched.
                padded = _snap_poster_to_palette(
                    padded, _palette, max_distance=45,
                )
                # FRAMING RESCUE — programmatic safety net for when
                # Gemini ignores the prompt's shoulders-up framing rules
                # and renders a face-zoom or floating pet. Detects the
                # pet bbox via palette knowledge (pet = anything that
                # isn't bg_left or bg_right within tol), rebuilds the
                # canvas at 4:5 with the canonical 38% seam, and
                # composites the pet via mask anchored at the canvas
                # bottom with symmetric L/R padding and 16% top room.
                # The with-name flow re-canvases through
                # _bgp_open_name_band afterward to open a 24% band for
                # the name.
                padded = _bgp_reframe_anchor_bottom(
                    padded, _palette,
                    top_room_ratio=0.21,
                    bottom_pad_ratio=0.0,
                    side_pad_ratio=0.11,
                    target_aspect=(4, 5),
                )
            else:
                padded = add_background_padding(
                    ai_image_no_name, padding_ratio=0.17, pad_bottom_ratio=0,
                )
                padded = _center_horizontal_weight(padded)
        else:
            # All other organic-bg styles (watercolor, charcoal, aura,
            # renaissance, line art): pad top + sides only so the pet's
            # natural bottom (fur fade, paw line) lands flush at the
            # canvas bottom — universal flush-bottom rule.
            padded = add_background_padding(
                ai_image_no_name, padding_ratio=0.17, pad_bottom_ratio=0,
            )
            padded = _center_horizontal_weight(padded)
        ai_image_no_name.close()
        ai_image_no_name = padded

    # Per-style post-processing — produces the 4:5 master (the
    # default print aspect for tall canvas variants 12×16, 16×20).
    processed_no_name = POST_PROCESS.get(style, lambda img: img)(ai_image_no_name)
    if processed_no_name is not ai_image_no_name:
        ai_image_no_name.close()
    ai_image_no_name = processed_no_name

    # Save per-aspect no-name masters: 4:5 (default), 3:4 (canvas-12x16),
    # and 1:1 (canvas-12x12 / 16x16). The 3:4 is a side-crop of the 4:5
    # master (vertical positions preserved); the 1:1 is the style-aware
    # derive_aspect output so each style's pet positioning still lands
    # correctly on a square canvas. Each PNG gets a watermarked WebP
    # preview alongside it for storefront PDP display.
    #
    # The "with-name" placeholder slots (comp_path / comp_path_3x4 /
    # comp_path_1x1 / web_path) alias the matching no-name file at
    # preview time — generate_with_name_on_demand() overwrites the
    # composited_* fields in the job record with real with-name PNGs +
    # WebPs at add-to-cart time. This means a customer who never adds a
    # name still ships a real per-aspect no-name file to Printful's
    # mockup task and order, instead of cover-cropping a 4:5 source.
    # _tighten_top_after_name is intentionally NOT called here — with
    # the universal flush-bottom rule the pet already sits at the canvas
    # bottom edge; re-tightening would re-pad the bottom with a sampled
    # edge colour that's now the pet itself, not bg.

    raw_path = out / f"{uid}_{style}_raw.png"
    ai_image_no_name.save(raw_path, "PNG", dpi=(300, 300))
    log.info("           raw (no name) → %s (%dx%d @ 300 DPI)",
             raw_path, ai_image_no_name.width, ai_image_no_name.height)
    raw_web_path = save_web_preview(ai_image_no_name, raw_path, watermark=True)

    # 3:4 derivative — centred side-crop of the 4:5 master. 4:5 (0.80) →
    # 3:4 (0.75) is a sides-only crop, so name-zone position and pet
    # flush-bottom land at the same fractional Y on every canvas.
    derived_3x4 = crop_to_ratio(ai_image_no_name, PRINT_ASPECT_3_4, gravity="center")
    raw_path_3x4 = out / f"{uid}_{style}_raw_3x4.png"
    derived_3x4.save(raw_path_3x4, "PNG", dpi=(300, 300))
    log.info("           raw 3x4 (no name) → %s (%dx%d @ 300 DPI)",
             raw_path_3x4, derived_3x4.width, derived_3x4.height)
    raw_web_path_3x4 = save_web_preview(derived_3x4, raw_path_3x4, watermark=True)
    derived_3x4.close()

    # 1:1 derivative — style-aware derive_aspect so each style's pet
    # positioning + corner-sampled bg padding lands correctly on the
    # square canvas (a naïve crop would lose the chest cut + bg halo).
    derived_1x1 = derive_aspect(ai_image_no_name, PRINT_ASPECT_1_1, style)
    min_w, min_h = PORTRAIT_MIN_SIZE
    if derived_1x1.width < min_w or derived_1x1.height < min_h:
        scale = max(min_w / derived_1x1.width, min_h / derived_1x1.height)
        derived_1x1 = derived_1x1.resize(
            (int(derived_1x1.width * scale), int(derived_1x1.height * scale)),
            Image.LANCZOS,
        )
    raw_path_1x1 = out / f"{uid}_{style}_raw_1x1.png"
    derived_1x1.save(raw_path_1x1, "PNG", dpi=(300, 300))
    log.info("           raw 1x1 (no name) → %s (%dx%d @ 300 DPI)",
             raw_path_1x1, derived_1x1.width, derived_1x1.height)
    raw_web_path_1x1 = save_web_preview(derived_1x1, raw_path_1x1, watermark=True)
    derived_1x1.close()

    # With-name placeholder aliases — overwritten by /add-name at
    # add-to-cart time. Keeping them aliased here means the customer
    # who never adds a name still ships the no-name file as their
    # "composited" output, which is the correct behaviour.
    comp_path = raw_path
    web_path = raw_web_path
    comp_path_3x4 = raw_path_3x4
    comp_path_1x1 = raw_path_1x1

    # Returned in slot-stable order. Slots 0–5 preserve the historical
    # 6-tuple shape (raw, comp, web, raw_web, raw_1x1, comp_1x1) so
    # existing audit/test scripts continue to unpack the same way; the
    # 1:1 derivative is now a real file instead of an alias of the 4:5.
    # Slots 6–9 are new: 3:4 PNG/WebP + 1:1 WebP + 3:4 with-name
    # placeholder, used by the worker to populate per-aspect job-record
    # fields and by /add-name to override the with-name placeholders.
    return (
        raw_path,           # 0: 4:5 PNG, no name
        comp_path,          # 1: 4:5 PNG, with-name placeholder = raw_path
        web_path,           # 2: 4:5 WebP, with-name placeholder = raw_web_path
        raw_web_path,       # 3: 4:5 WebP, watermarked
        raw_path_1x1,       # 4: 1:1 PNG, no name (real per-aspect derivative)
        comp_path_1x1,      # 5: 1:1 PNG, with-name placeholder = raw_path_1x1
        raw_path_3x4,       # 6: 3:4 PNG, no name
        raw_web_path_3x4,   # 7: 3:4 WebP, watermarked
        raw_web_path_1x1,   # 8: 1:1 WebP, watermarked
        comp_path_3x4,      # 9: 3:4 PNG, with-name placeholder = raw_path_3x4
    )


# ---------------------------------------------------------------------------
# On-demand: add name to an already-generated portrait
# ---------------------------------------------------------------------------

def generate_with_name_on_demand(
    no_name_image_bytes: bytes,
    pet_name: str,
    style: str,
    output_dir: Optional[Path] = None,
    background_mode: Optional[str] = "auto",
) -> tuple[Path, Path, Path, Path, Path, Path]:
    """Add the pet's name to an already-generated no-name portrait.
    Called at add-to-cart time to halve the up-front Gemini cost.

    Returns: (
        comp_path_4x5,  web_preview_path,
        comp_path_3x4,  web_preview_path_3x4,
        comp_path_1x1,  web_preview_path_1x1,
    )

    Three per-aspect derivatives so each canvas variant's mockup task
    receives a source matching its front-face aspect (no Printful
    cover-crop zoom-up):
      4:5  → canvas-16x20 / canvas-16x20-framed
      3:4  → canvas-12x16 / canvas-12x16-framed
      1:1  → canvas-12x12 / canvas-16x16 (and framed equivalents)

    The 3:4 derivative is a centred side-crop of the already-composited
    4:5 master — vertical positions are preserved, so the name + pet
    flush-bottom land in the same fractional places without re-running
    the per-style reframe / composite_name pipeline.
    """
    if not _generation_semaphore.acquire(timeout=2):
        raise RuntimeError("BUSY")

    try:
        out = output_dir or OUTPUT_DIR
        out.mkdir(parents=True, exist_ok=True)

        import uuid as _uuid
        uid = _uuid.uuid4().hex[:10]

        log.info("[generate_with_name] %s '%s' (PIL composite)", style, pet_name)

        # Use PIL for name compositing — gives exact pixel control over
        # font size and position, unlike Gemini which renders the name
        # far too large regardless of prompt constraints.
        base_image = Image.open(BytesIO(no_name_image_bytes))
        base_image.load()
        processed = POST_PROCESS.get(style, lambda img: img)(base_image)
        if processed is not base_image:
            base_image.close()

        # 4:5 with name — open the name band on modern AND minimal-
        # line-art (the no-name masters for both are packed tight, so
        # we re-canvas to make room) and composite the text on the
        # post-processed master.
        if style == "modern-shape-art":
            laid_out = _modern_open_name_band(processed)
        elif style == "minimal-line-art":
            laid_out = _line_art_open_name_band(processed)
        elif style == "bold-graphic-poster":
            _palette_id = background_mode if background_mode in POSTER_PALETTES else "teal"
            _palette = POSTER_PALETTES[_palette_id]
            laid_out = _bgp_open_name_band(processed, _palette)
        else:
            laid_out = processed
        composited = composite_name(laid_out, pet_name, style=style)

        safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
        comp_path = out / f"{uid}_{style}_{safe_name}_named.png"
        _save_with_pp_named(composited, comp_path, dpi=(300, 300))
        log.info("           comp (with name) → %s (%dx%d @ 300 DPI)",
                 comp_path, composited.width, composited.height)

        web_path = save_web_preview(composited, comp_path)

        # 3:4 derivative — centred side-crop of the 4:5 master. 4:5 (0.80)
        # → 3:4 (0.75) is sides-only crop, so name centre and pet bottom
        # stay at the same fractional y position the customer sees on the
        # 4:5 preview. Used for canvas-12x16 mockups + print files so
        # Printful never has to cover-crop a 4:5 source onto a 3:4 face.
        composited_3x4 = crop_to_ratio(composited, PRINT_ASPECT_3_4, gravity="center")
        # crop_to_ratio doesn't preserve PIL info dict — re-stamp pp_named.
        composited_3x4.info["pp_named"] = "1"
        composited_3x4.info["pp_named_value"] = composited.info.get("pp_named_value", "")
        comp_path_3x4 = out / f"{uid}_{style}_{safe_name}_named_3x4.png"
        _save_with_pp_named(composited_3x4, comp_path_3x4, dpi=(300, 300))
        log.info("           comp 3x4 (with name) → %s (%dx%d @ 300 DPI)",
                 comp_path_3x4, composited_3x4.width, composited_3x4.height)
        web_path_3x4 = save_web_preview(composited_3x4, comp_path_3x4)

        # 1:1 derivative with the name baked in. Derive the 1:1 master
        # from the NO-NAME source first, then composite the name on the
        # 1:1 separately. Compositing on the 4:5 first and then deriving
        # 1:1 would crop the top 20% of source — eating the name, which
        # sits in that band on 4:5. composite_name detects the square
        # aspect and uses a tighter zone_top so the name lands halfway
        # between the canvas top and the (now closer-to-top) pet head.
        derived_1x1 = derive_aspect(processed, PRINT_ASPECT_1_1, style)
        min_w, min_h = PORTRAIT_MIN_SIZE
        if derived_1x1.width < min_w or derived_1x1.height < min_h:
            scale = max(min_w / derived_1x1.width, min_h / derived_1x1.height)
            derived_1x1 = derived_1x1.resize(
                (int(derived_1x1.width * scale), int(derived_1x1.height * scale)),
                Image.LANCZOS,
            )
        if style == "modern-shape-art":
            laid_out_1x1 = _modern_open_name_band(derived_1x1)
        elif style == "minimal-line-art":
            laid_out_1x1 = _line_art_open_name_band(derived_1x1)
        elif style == "bold-graphic-poster":
            _palette_id = background_mode if background_mode in POSTER_PALETTES else "teal"
            _palette = POSTER_PALETTES[_palette_id]
            laid_out_1x1 = _bgp_open_name_band(derived_1x1, _palette)
        else:
            laid_out_1x1 = derived_1x1
        composited_1x1 = composite_name(laid_out_1x1, pet_name, style=style)
        comp_path_1x1 = out / f"{uid}_{style}_{safe_name}_named_1x1.png"
        _save_with_pp_named(composited_1x1, comp_path_1x1, dpi=(300, 300))
        log.info("           comp 1x1 (with name) → %s (%dx%d @ 300 DPI)",
                 comp_path_1x1, composited_1x1.width, composited_1x1.height)

        # Watermarked 1:1 WebP — the PDP square mockup renders this
        # for customer display when wantsName is on, so the diagonal
        # Pet Printables watermark sits over the artwork the same way
        # it does on the 4:5 preview. The un-watermarked PNG above is
        # the print file Printful fetches; this WebP is display-only.
        web_path_1x1 = save_web_preview(composited_1x1, comp_path_1x1)

        composited.close()
        composited_3x4.close()
        composited_1x1.close()
        derived_1x1.close()
        return (
            comp_path, web_path,
            comp_path_3x4, web_path_3x4,
            comp_path_1x1, web_path_1x1,
        )
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

    _gen_paths = generate(args.photo_path, args.pet_name, args.style, style_vars=style_vars)
    raw_path, comp_path, web_path, raw_web_path = _gen_paths[0], _gen_paths[1], _gen_paths[2], _gen_paths[3]
    print(f"\nRaw output:   {raw_path}")
    print(f"Composited:   {comp_path}")
    print(f"Web preview:  {web_path}")


if __name__ == "__main__":
    main()
