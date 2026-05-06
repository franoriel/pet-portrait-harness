"""Quick visual test for _tight_crop_to_4_5.

Builds a synthetic 4:5 image with the artwork drawn off-centre (mimicking
the user's "Spud watercolour" / "Gigi charcoal" bug pattern), runs the
tight-crop function, writes both to disk for visual comparison.
"""
from pathlib import Path
from PIL import Image, ImageDraw

from generate import _tight_crop_to_4_5


OUT_DIR = Path(__file__).parent / "output" / "tight_crop_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def make_off_centre_charcoal_like(w=800, h=1000) -> Image.Image:
    """Cream paper with a dark sketch, paper occupying the LEFT 65%
    of the source — the asymmetry pattern from Gigi's screenshot."""
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    paper_left, paper_right = 80, int(w * 0.70)
    paper_top, paper_bot = 80, h - 80
    d.rectangle((paper_left, paper_top, paper_right, paper_bot), fill=(248, 240, 220))
    cx = (paper_left + paper_right) // 2
    cy = (paper_top + paper_bot) // 2
    d.ellipse((cx - 80, cy - 100, cx + 80, cy + 100), outline=(40, 30, 25), width=4)
    d.ellipse((cx - 120, cy - 200, cx + 120, cy - 80), outline=(40, 30, 25), width=4)
    return img


def make_off_centre_watercolour_like(w=800, h=1000) -> Image.Image:
    """Watercolour wash + pet shape positioned in the LEFT 60% of the
    source — the asymmetry pattern from Spud's screenshot."""
    img = Image.new("RGB", (w, h), (255, 254, 250))
    d = ImageDraw.Draw(img)
    cx, cy = int(w * 0.42), int(h * 0.55)
    for r, alpha in [(380, 25), (300, 60), (220, 110), (140, 170)]:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(245 - alpha // 4, 230 - alpha // 3, 210 - alpha // 2))
    d.ellipse((cx - 120, cy - 60, cx + 120, cy + 160), fill=(80, 55, 35))
    d.ellipse((cx - 90, cy - 200, cx + 90, cy - 60), fill=(95, 65, 45))
    return img


def main():
    cases = {
        "charcoal_off_centre": make_off_centre_charcoal_like(),
        "watercolour_off_centre": make_off_centre_watercolour_like(),
    }
    for name, src in cases.items():
        src.save(OUT_DIR / f"{name}_BEFORE.png")
        result = _tight_crop_to_4_5(src)
        result.save(OUT_DIR / f"{name}_AFTER.png")
        sw, sh = src.size
        rw, rh = result.size
        print(f"{name}: {sw}x{sh} ({sw/sh:.3f}) → {rw}x{rh} ({rw/rh:.3f})")
    print(f"\nWrote {len(cases) * 2} images to {OUT_DIR}")


if __name__ == "__main__":
    main()
