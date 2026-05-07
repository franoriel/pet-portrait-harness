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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
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
  edges where appropriate (ears can crop at the top safe-zone \
  boundary, shoulders can crop at the side edges, chest cuts off at \
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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
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
The reserved name safe zone (top ~22%) is what creates the breathing room \
above the ears.
- The BACKGROUND is ONE single saturated colour, perfectly uniform from \
corner to corner, extending edge-to-edge with NO internal rectangles, \
panels, bars, checker zones, or empty bands. The same colour you see in \
one corner you see in every other corner.
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, soft edges, muted colors, watercolor, \
oil paint, 3D render, blurry, low resolution, text, watermark, border, \
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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
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
centered horizontally. The reserved name safe zone (top ~22%) is what \
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
    "terracotta": ("#C77B58", "dusty terracotta"),
    "mauve":      ("#C9A4A4", "soft dusty mauve"),
    "mustard":    ("#C9A352", "warm mustard ochre"),
    "navy":       ("#1D2A44", "deep navy ink"),
    "charcoal":   ("#2E2A26", "warm charcoal"),
}

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
Transform this photo into a modern, clean, minimalist shape-art pet portrait.

COLOR ACCURACY — THIS IS CRITICAL:
- Reinterpret the animal's fur/coat color as a TINY palette of FLAT shape \
fills — exactly 2-3 colours TOTAL on the entire pet, not more. One \
dominant coat tone fills almost the whole silhouette; one slightly \
darker tone marks the snout / nose / a single shadow plane; optionally \
one third tone marks a clearly-visible coat patch from the photo (a \
white chest blaze, a tan saddle, a calico patch). That is the entire \
coat palette — DO NOT subdivide the body into smaller and smaller \
shadow planes the way a rendered illustration would. A black dog reads \
as one deep charcoal silhouette with maybe a slightly darker snout and \
a small chest patch — nothing else inside the body. A brown dog reads \
as one warm caramel silhouette + snout + (optional) chest patch.
- DO NOT HALLUCINATE MARKINGS — CRITICAL: the source photo is the \
SINGLE SOURCE OF TRUTH for what's on the pet's coat. Before adding \
ANY second tone or shape inside the silhouette, verify it corresponds \
to a real, clearly-visible feature on the actual pet in the photo. \
If the photo shows a solid-colour dog, the result is a SOLID-COLOUR \
silhouette — one tone, full stop. Acceptable second-tone uses (only \
if visible in the photo): a real white chest blaze, a real tan \
saddle on a black-and-tan dog, a real visible calico patch, the \
muzzle if it's noticeably darker than the body. Tabby / spotted / \
patched coats keep ONLY the 1-2 most distinguishing real markings, \
rendered as large simple shape blocks.
- ABSOLUTELY FORBIDDEN — invented patterns the AI adds because "shape \
art needs visual interest": tiger / zebra / brindle stripes that \
aren't on the actual pet, decorative swirls, marble veins, brushy \
texture suggesting fur direction, abstract colour-block panels, \
geometric facets, and any other coat detail that does not appear in \
the source photo. If you find yourself drawing a second tone purely \
to "add interest," DELETE IT and leave the silhouette flat. Boring \
flat silhouette > invented pattern. The recent failure mode is a \
solid-coloured pet rendered with body-wide tiger striping — this \
must NEVER happen.
- COLOR HARMONY — CRITICAL: the pet's coat palette and the {{MODERN_BG_NAME}} \
background must read as ONE intentional, harmonious palette — never as two \
unrelated images stacked. Treat the whole composition like a curated \
mood-board swatch:
  · Match temperature: a WARM background (cream, clay, terracotta, mustard, \
  warm charcoal) pairs with warm-leaning coat tones (caramel, sienna, warm \
  espresso, ivory). A COOL background (sage, navy) pairs with cooler-leaning \
  coat tones (taupe, ash brown, slate, soft greige). If the pet's true color \
  is warm but the background is cool (or vice-versa), bridge them by gently \
  shifting the coat shadows / mid-tones a half-step toward the background's \
  temperature so the pet sits IN the scene, not pasted onto it.
  · Echo the background: at least one secondary coat tone (a shadow plane, \
  fur edge, or chest fluff) should subtly reference the background hue — a \
  whisper of {{MODERN_BG_NAME}} desaturated and tucked into the coat — so \
  the eye reads continuity across the image.
  · Confident contrast, never muddy: the primary coat tones still read \
  clearly against {{MODERN_BG_HEX}} (no disappearing pet, no value-clash \
  vibration). Aim for a relaxed museum-poster feel — the kind of palette \
  that would look intentional in a Charley Harper print or a curated \
  Pinterest moodboard.
  · NEVER drop saturated primaries (pure red, pure blue, pure green, hot \
  pink) into the coat — those break the muted curated feel.
- NO EYES — CRITICAL: Do NOT render eyes at all. The face is treated \
as pure shape — coat planes, snout, muzzle, brow / eyebrow ridge, ear \
shapes — without any eye marks. Where the eyes would sit, simply \
continue the surrounding fur-tone shape unbroken. NO eye ovals, NO \
dots, NO almonds, NO slits, NO closed-eye squint lines, NO iris, NO \
pupil, NO white sclera, NO catchlight, NO eyelash, NO under-eye \
shading, NO subtle eye suggestion of any kind. This is a 2D shape-art \
treatment in the vein of Matisse cut-outs and Charley Harper's most \
abstracted poster work — the pet's character comes from silhouette, \
ear angle, snout, and coat planes, not from eyes. The face still \
reads as the specific pet through its overall structure.

STYLE:
- Minimalist vector / cut-paper aesthetic — Matisse cut-outs meets \
contemporary Bauhaus. Think pictogram-level simplification: if the \
result wouldn't work as a 2-colour silkscreen logo, it's too detailed.
- FLAT color fills only — no gradients, no painterly texture, no \
airbrush, no photographic detail, no brush strokes, no soft edges.
- Crisp clean edges between shapes. Soft organic curves where \
appropriate (ear outline, cheek line, head silhouette), sharp \
geometric edges where appropriate (collars, jaw line).
- LOW-DIMENSION, FLAT FACE — CRITICAL: Inside the silhouette, the \
face stays almost entirely flat. NO brow furrows, NO forehead \
wrinkles, NO chin folds, NO multiple shadow planes carving up the \
muzzle, NO highlight ridges along the nose bridge, NO fur striation, \
NO whiskers, NO mouth crease lines, NO under-jaw shading. The snout / \
nose is ONE darker shape; the rest of the face is one continuous coat \
tone. A 3-year-old should be able to recognise the breed from the \
silhouette alone — that's the depth target.
- Restrained modern palette: the BACKGROUND is the customer's chosen \
colour {{MODERN_BG_NAME}} ({{MODERN_BG_HEX}}). The pet's coat uses \
exactly 2-3 flat tones (see COLOR ACCURACY). Pet tones must read \
clearly against the {{MODERN_BG_NAME}} background — pick coat shades \
that contrast comfortably with {{MODERN_BG_HEX}}. Quietly confident, \
never garish.
- Generous NEGATIVE SPACE — at least 35% of the canvas is calm, unbroken \
background so the pet shapes have room to breathe.
- THE PET IS THE ONLY SUBJECT. Do NOT add decorative elements, abstract \
shapes, accents, arcs, circles, dots, lines, foliage, geometric ornaments, \
patterns, halos, frames, or any other graphic elements around the pet. The \
composition is just the pet on a single solid background — nothing else.
- Fine art illustration style, high resolution 300dpi, print-ready.

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation).
- Head and chest, calm symmetrical pose. The face has NO eyes (per the \
NO EYES rule above) — character comes from silhouette, ear angle, \
snout, and coat planes. Reads as confident 2D shape art, not a \
photograph and not a stylised cartoon with eyes.
- The PET itself occupies 78-83% of image height — top of ears at \
~15-18% from top, centered horizontally. Ensure the pet is the dominant \
subject filling the canvas confidently, with clean breathing room \
(~15-18% top padding, ~6-8% side padding) at the top and sides — no edge bleed on those three sides.
- BOTTOM SILHOUETTE — CRITICAL: the chest/body must terminate in ONE of \
two ways and never anything in between:
  (a) Taper organically into a soft, curved natural body silhouette \
  that finishes around 88-93% from top, with breathing room below it; OR
  (b) Run as a clean horizontal cut that extends ALL THE WAY to the \
  bottom edge of the canvas — the cut IS the bottom edge, no background \
  band visible underneath.
  NEVER render a flat horizontal chest cut that floats above the bottom \
  edge with background colour visible beneath it — that reads as a \
  truncated, unfinished portrait. If you choose option (b), the dog's \
  body fills the lower portion of the frame edge-to-edge.
- The BACKGROUND is ONE single solid colour {{MODERN_BG_NAME}} \
({{MODERN_BG_HEX}}) ONLY, extending edge-to-edge on all four sides. \
Completely uniform — no decoration, no shapes, no lines, no gradients, \
no panels, no bars, no colour blocks, no empty bands. Just one flat \
field of {{MODERN_BG_HEX}} behind the pet.
- Do NOT include any text, words, letters, watermarks, or signatures anywhere.

Avoid: photography, photorealism, painterly brush strokes, oil paint, \
watercolor bleed, film grain, sepia, gradients, drop shadows, 3D \
render, cartoon, anime, neon, busy patterns, ornate details, ANY \
rendered eyes (realistic, stylised, almond, oval, dot, slit, \
closed-line, glowing, or any eye mark whatsoever), iris, pupil, \
sclera, catchlight, eyelash, under-eye shading, whiskers, individual \
fur strands, hatching, brow furrows, forehead wrinkles, chin folds, \
multiple shadow planes inside the silhouette, nose-bridge highlights, \
fur striations, mouth crease lines, made-up tiger / zebra / marble \
stripe patterns that aren't on the actual pet, more than 3 coat \
tones, text, watermark, border, solid color bars or panels at image \
edges, decorative shapes, abstract accents, arcs, circles, dots, \
lines, foliage, halos, frames, patterns, geometric ornaments, \
anything other than the pet on a solid background.\
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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the type:
- The pet's head, ears, fur fly-aways, and ANY graphic accents MUST \
stay BELOW y=22% of the canvas. Top of the tallest ear sits at y≈25-28% \
— never closer to the canvas top.
- Within the top ~22%, the solid background continues uniformly \
edge-to-edge — no halftone, accents, or extra graphic elements inside \
this band.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, strong forward-facing pose, graphic impact
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved name safe zone (top ~22%) is what creates the breathing \
room above the ears.
- The BACKGROUND (single solid flat color) extends edge-to-edge behind the \
pet — one continuous color, NOT split into panels or bands. No reserved \
color blocks, bars, or rectangles anywhere
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, soft edges, gradients, watercolor, painterly strokes, \
3D render, blurry, detailed fur texture, text, watermark, border, \
solid color bars or panels at image edges, horizontal color-band splits, \
pet pushed to canvas edges.\
"""

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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
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
The reserved name safe zone (top ~22%) is what creates the breathing room \
above the ears.
- BOTTOM SILHOUETTE — CRITICAL: the chest must dissolve organically into the \
paper texture with looser strokes, never end in a flat horizontal cut.
- BACKGROUND: warm cream paper texture (#F4EFE7 base) with subtle organic \
paper-fibre grain extending uniformly to all four edges. The same cream tone \
in every corner. NO rectangles, NO frames, NO inner panel of a different \
shade, NO mat, NO border, NO letterbox bar, NO geometric splits. Pet and \
paper are drawn in the same medium in the same pass.
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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
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
The reserved name safe zone (top ~22%) is what creates the breathing room \
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
- White paper background with natural watercolor bleed edges
- Painterly fur texture with subtle fine ink linework on facial features
- Warm soft lighting, no harsh shadows
- Fine art illustration style, high resolution 300dpi, print-ready

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
pet is treated as a calm clean-paper area (see NAME SAFE ZONE).

NAME SAFE ZONE — CRITICAL: A handwritten pet name will be composited \
into the TOP of the finished image. Reserve the upper ~22% of the canvas \
as a CALM, OPEN paper area suitable for legible script:
- The pet's head, ears, fur fly-aways, watercolor splatters, dark wash \
strokes, and ink linework MUST stay BELOW y=22% of the canvas. The top \
of the tallest ear sits at y≈25-28% — never closer to the canvas top.
- Within the top ~22% the paper should remain mostly clean — at most a \
gentle wash of soft colour (3-8% opacity tint, no visible brush detail, \
no splatters, no dark accents, no ink). Think of an artist leaving \
breathing room above the subject for a calligraphed name.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5). It is \
what keeps the pet's eyes/ears from being overwritten by the script.

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Slight natural vignette
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved name safe zone (top ~22%) is what creates the breathing \
room above the ears; the pet still feels confidently present below it.
- The BACKGROUND (watercolor wash and natural bleed edges) extends to every \
edge of the canvas. No reserved panels, bars, color blocks, or empty bands
- Do NOT include any text, words, letters, watermarks, or signatures anywhere

Avoid: photography, photorealism, harsh shadows, dark background, pixelation, \
blurry, low resolution, cartoon, anime, 3D render, clipping, text, watermark, border, \
narrow watercolor wash column with bare white paper at the sides.\
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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
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
name safe zone), bottom of chest at ~96-99% from top, centered \
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

NAME SAFE ZONE — CRITICAL: A handwritten pet name will be composited \
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
The reserved name safe zone (top ~22%) is what creates the breathing \
room above the ears.
- The BACKGROUND (deep pigmented dark watercolor wash) extends to every edge \
of the canvas. No reserved panels, bars, color blocks, or empty bands
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

NAME SAFE ZONE — CRITICAL: A pet name will be composited into the TOP \
of the finished image. Reserve the upper ~22% of the canvas as a CALM \
area for the type:
- The pet's head, ears, fur fly-aways, and ANY accent colors MUST stay \
BELOW y=22% of the canvas. Top of the tallest ear sits at y≈25-28% — \
never closer to the canvas top.
- Within the top ~22%, the solid dark field continues uniformly \
edge-to-edge — no halftone, accents, or extra graphic elements inside \
this band.
- This rule is non-negotiable on every aspect (1:1, 3:4, 4:5).

COMPOSITION:
- Centered portrait, 4:5 aspect ratio (portrait orientation)
- Head and chest, strong forward-facing pose, graphic impact
- The PET itself occupies 70-75% of image height — top of ears at \
~25-28% from top, bottom of chest at ~96-99% from top, centered horizontally. \
The reserved name safe zone (top ~22%) is what creates the breathing room \
above the ears.
- The BACKGROUND (single flat black/dark-gray) extends edge-to-edge behind \
the pet — NOT split into panels or bands. No reserved color blocks, bars, \
or rectangles anywhere
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
    "- CENTERED HORIZONTALLY, EYES IN MIDDLE THIRD: the pet is centered "
    "left-to-right (its vertical axis of symmetry sits exactly on the "
    "image's horizontal centre). Eyes land in the middle of the image, "
    "around 38-44% from top — lower than a traditional portrait so the "
    "subject reads grounded with breathing room above. Never push the "
    "pet left or right of centre.\n"
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
    "- CENTERED HORIZONTALLY, EYES IN UPPER-MIDDLE: the pet is centered "
    "left-to-right (its vertical axis of symmetry sits exactly on the "
    "image's horizontal centre). Eyes land around 30-38% from top — a "
    "touch higher than the with-name composition because the pet is "
    "larger overall. Never push the pet left or right of centre.\n"
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
    "- NO TEXT ANYWHERE IN THE IMAGE. No letters, numbers, words, names, "
    "watermarks, or signatures. Collars and tags render blank. The "
    "breathing room above the pet stays quiet, unbroken background.\n"
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
    "watercolor":          {"auto", "light", "dark"},
    "minimal-line-art":    {"auto", "light", "dark"},
    "modern-shape-art":    {"auto"},
    "neon-pop-art":        {"auto"},
    "renaissance-royalty": {"auto"},
    "bold-graphic-poster": {"auto", "light", "dark"},
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
    name_block = _name_integration(style_id, pet_name, background_mode)
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
    "bold-graphic-poster": _static(_BOLD_GRAPHIC_POSTER_TEMPLATE),
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

    # Solid-bg shortcut: when the caller knows the style has a uniform
    # edge-to-edge background colour (e.g. neon-pop-art, bold-graphic-
    # poster) it can pass solid_bg_color to skip edge replication
    # entirely. Edge replication produces colour streaks when the AI
    # renders the pet touching an edge — for solid-bg styles, "fill the
    # padding with the known bg colour" is both simpler and visibly
    # cleaner.
    if solid_bg_color is not None:
        out = Image.new('RGB', (w + 2 * pad_w, h + 2 * pad_h), solid_bg_color)
        out.paste(img, (pad_w, pad_h))
        return out

    out = Image.new('RGB', (w + 2 * pad_w, h + 2 * pad_h))
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
    bot_strip_1px = img.crop((0, h - 1, w, h))
    bot_replicated = bot_strip_1px.resize((w, pad_h), Image.NEAREST)
    bot_solid = Image.new('RGB', (w, pad_h), _band_mean(bot_strip_1px))
    bot_band = Image.composite(bot_solid, bot_replicated,
                                _vertical_blend_mask(w, pad_h, replicate_at_top=True))
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
    out.paste(img.crop((0, h - 1, 1, h)).resize((pad_w, pad_h), Image.NEAREST), (0, pad_h + h))
    out.paste(img.crop((w - 1, h - 1, w, h)).resize((pad_w, pad_h), Image.NEAREST), (pad_w + w, pad_h + h))

    return out


def _modern_shape_art_reframe(
    img: Image.Image,
    pad_top_ratio: float = 0.05,
    pad_side_ratio: float = 0.02,
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
        return add_background_padding(img, padding_ratio=0.10)
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
        return add_background_padding(img, padding_ratio=0.10)

    # Add inset so we don't clip anti-aliased edges. Antialiased ear
    # tips on a cream background can fall below BG_TOL — without a
    # margin, the bbox cuts into the visible silhouette and re-padding
    # places the ears flush against the canvas edge. 1.5% of bbox
    # dimension gives the soft fade room to be preserved.
    bbox_w = fg_max_x - fg_min_x
    bbox_h = fg_max_y - fg_min_y
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
    """
    img = crop_to_ratio(img, PORTRAIT_RATIO)
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
            return _modern_shape_art_reframe(
                img,
                pad_top_ratio=0.18,
                pad_side_ratio=0.06,
                pad_bottom_ratio=0.16,
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
        solid_bg = style_id in {"neon-pop-art", "bold-graphic-poster"}
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


def _line_art_post_process(img: Image.Image) -> Image.Image:
    """Post-process for the minimal-line-art style (light + dark variants).
    Removes disconnected stray fragments (phantom vertical lines, detached
    paws, floating eye dots) and centers the line work on both axes before
    the standard crop pipeline.
    """
    img = _remove_orphan_strokes(img)
    img = _center_line_art(img)
    return _portrait_post_process(img)


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
    Sample the top 15% of the image to determine if text should be
    light or dark for good contrast.
    Returns (text_rgb, line_rgba) tuple.
    """
    w, h = image.size
    zone_bottom = int(h * 0.15)
    bottom = image.crop((0, 0, w, zone_bottom))
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
        # Sacramento is a thin handwritten script — bumped size_ratio
        # because scripts have low x-height and look small at serif
        # sizing. The watercolor prompt reserves the top ~22% of the
        # canvas as a NAME SAFE ZONE (clean paper / calm wash, pet
        # below y=22%). zone_top 0.11 lands the script's vertical
        # centre inside that band — comfortably above the pet's ears
        # (which sit at y≈25-28%) and well clear of the 1:1 centre
        # crop edge on square canvas variants.
        "size_ratio": 0.08,
        "transform": "title",
        "zone_top": 0.11,
        "letter_spacing": 0,
        "opacity": 0.85,
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
        # Square with name needs a generous top band — name has to fit
        # WITH visible breathing room above (not flush to the canvas
        # top edge) AND clear margin below before the ears start. The
        # wrap canvas eats ~8.6% per edge on a square so pad_bottom is
        # also non-zero — without it the chest cut wraps around the
        # side of the canvas instead of staying on the front face.
        return _modern_shape_art_reframe(
            image,
            pad_top_ratio=0.45,
            pad_side_ratio=0.06,
            pad_bottom_ratio=0.16,
            target_aspect=PRINT_ASPECT_1_1,
        )
    return _modern_shape_art_reframe(
        image,
        pad_top_ratio=0.22,
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

    # Tighten the top empty band so the name sits closer to the canvas
    # top edge instead of floating with a wide empty margin above it.
    # We crop slightly less than `text_y` so the name itself is never
    # clipped — `text_y` is the absolute pixel y-coordinate of the
    # rendered glyph top.
    if h > 0 and text_y > 0:
        # Cap the crop at min(7% of h, half the empty space above name)
        # so we always leave a small breathing margin of bg above the
        # first glyph. half-of-text_y guarantees we don't touch the
        # rendered text even when the styled font has long ascenders.
        crop_frac = min(0.07, (text_y / h) * 0.5)
        if crop_frac > 0.01:
            img = _tighten_top_after_name(img, crop_frac=crop_frac)

    return img


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
    OPACITY = 0.05
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
            + _composition_rule(has_name=False)
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

    # Modern style uses background_mode as a colour-palette selector
    # (cream/clay/sage/etc.) rather than auto/light/dark. Lift the chosen
    # colour into style_vars so the prompt builder picks it up, and reset
    # background_mode to 'auto' so the generic light/dark override block
    # doesn't try to layer on top.
    if style == "modern-shape-art" and background_mode in MODERN_BG_COLORS:
        style_vars = {**(style_vars or {}), "modern_bg_color": background_mode}
        background_mode = "auto"

    log.info("[generate] %s  '%s'  ←  %s", style, pet_name, photo.name)

    # Preview generation: ONE Gemini call — no-name version only.
    # The with-name version is generated lazily by add_name_endpoint
    # when the user adds to cart (halves per-portrait Gemini cost).
    raw_bytes = call_gemini(photo, style, style_vars, pet_name="", background_mode=background_mode)

    ai_image_no_name = Image.open(BytesIO(raw_bytes))
    ai_image_no_name.load()

    # Add background padding so the pet has breathing room around it.
    # Gemini routinely ignores prompt-based size constraints and renders
    # pets edge-to-edge — this guarantees breathing room programmatically.
    if style in _PORTRAIT_STYLES:
        if style == "modern-shape-art":
            # Smart reframe: detect pet bounding box and compose with exact
            # target margins (10% top, 8% sides, 0% bottom if flat cut).
            # This handles both edge-to-edge and over-padded AI outputs.
            padded = _modern_shape_art_reframe(ai_image_no_name)
        elif style in ("neon-pop-art", "bold-graphic-poster"):
            # Solid-bg styles: the prompt asks for a single saturated
            # colour edge-to-edge. Edge replication produces streaks when
            # the pet touches an edge (especially the bottom, which the
            # composition rule explicitly allows). Sample the bg colour
            # from the top corners — pet never reaches there — and fill
            # the padding ring with that solid colour.
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
                ai_image_no_name, padding_ratio=0.12, solid_bg_color=bg
            )
        else:
            padded = add_background_padding(ai_image_no_name, padding_ratio=0.12)
        ai_image_no_name.close()
        ai_image_no_name = padded

    # Per-style post-processing — produces the 4:5 master (the
    # default print aspect for tall canvas variants 12×16, 16×20).
    processed_no_name = POST_PROCESS.get(style, lambda img: img)(ai_image_no_name)
    if processed_no_name is not ai_image_no_name:
        ai_image_no_name.close()
    ai_image_no_name = processed_no_name

    # Save the hi-res no-name 4:5 master and the watermarked WebP that
    # the customer actually sees on the preview screen. Everything else
    # — the with-name PNG (comp_path), the with-name WebP (web_path),
    # and both 1:1 derivatives — is byte-identical to one of these two
    # files at preview time:
    #   - comp_path / comp_path_1x1: no name has been composited yet;
    #     generate_with_name_on_demand() produces the real with-name
    #     files at add-to-cart time.
    #   - web_path: same content as raw_web_path; it'd be a duplicate
    #     watermarked WebP.
    #   - raw_path_1x1: an aspect derivative the cart doesn't reference
    #     anymore (the JS revert dropped per-aspect plumbing — Printful
    #     gets the 4:5 master regardless of canvas variant).
    #
    # Skipping the redundant saves removes ~2-4s of synchronous work
    # before the customer sees the preview. Aliasing the unused outputs
    # to raw_path / raw_web_path keeps the 6-tuple return shape; the
    # worker dedupes by file path before submitting CDN uploads, so the
    # CDN only sees two unique objects per generation.
    raw_path = out / f"{uid}_{style}_raw.png"
    ai_image_no_name.save(raw_path, "PNG", dpi=(300, 300))
    log.info("           raw (no name) → %s (%dx%d @ 300 DPI)",
             raw_path, ai_image_no_name.width, ai_image_no_name.height)

    raw_web_path = save_web_preview(
        ai_image_no_name,
        raw_path,
        watermark=True,
    )

    # Aliases — same files, different roles. The frontend is fine with
    # this at preview time because /add-name regenerates the named PNG
    # and WebP fresh before the customer ever sees a name on the print.
    comp_path = raw_path
    raw_path_1x1 = raw_path
    comp_path_1x1 = raw_path
    web_path = raw_web_path

    return raw_path, comp_path, web_path, raw_web_path, raw_path_1x1, comp_path_1x1


# ---------------------------------------------------------------------------
# On-demand: add name to an already-generated portrait
# ---------------------------------------------------------------------------

def generate_with_name_on_demand(
    no_name_image_bytes: bytes,
    pet_name: str,
    style: str,
    output_dir: Optional[Path] = None,
    background_mode: Optional[str] = "auto",
) -> tuple[Path, Path, Path]:
    """Add the pet's name to an already-generated no-name portrait.
    Called at add-to-cart time to halve the up-front Gemini cost.

    Returns: (comp_path_4x5, web_preview_path, comp_path_1x1)

    The 1:1 derivative is composited from the same name-applied source
    so both square and tall canvas variants get a print file with the
    name baked in at the correct position for each aspect.
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

        # 4:5 with name — open the name band on modern (the no-name
        # master is packed tight, so we re-canvas to make room) and
        # composite the text on the post-processed master.
        if style == "modern-shape-art":
            laid_out = _modern_open_name_band(processed)
        else:
            laid_out = processed
        composited = composite_name(laid_out, pet_name, style=style)

        safe_name = "".join(c for c in pet_name.lower() if c.isalnum()) or "pet"
        comp_path = out / f"{uid}_{style}_{safe_name}_named.png"
        composited.save(comp_path, "PNG", dpi=(300, 300))
        log.info("           comp (with name) → %s (%dx%d @ 300 DPI)",
                 comp_path, composited.width, composited.height)

        web_path = save_web_preview(composited, comp_path)

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
        else:
            laid_out_1x1 = derived_1x1
        composited_1x1 = composite_name(laid_out_1x1, pet_name, style=style)
        comp_path_1x1 = out / f"{uid}_{style}_{safe_name}_named_1x1.png"
        composited_1x1.save(comp_path_1x1, "PNG", dpi=(300, 300))
        log.info("           comp 1x1 (with name) → %s (%dx%d @ 300 DPI)",
                 comp_path_1x1, composited_1x1.width, composited_1x1.height)

        composited.close()
        composited_1x1.close()
        derived_1x1.close()
        return comp_path, web_path, comp_path_1x1
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

    raw_path, comp_path, web_path, raw_web_path = generate(args.photo_path, args.pet_name, args.style,
                                                            style_vars=style_vars)
    print(f"\nRaw output:   {raw_path}")
    print(f"Composited:   {comp_path}")
    print(f"Web preview:  {web_path}")


if __name__ == "__main__":
    main()
