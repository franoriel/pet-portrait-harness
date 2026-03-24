#!/usr/bin/env python3
"""
Batch mode: generate style variants for every photo in /test_photos.

Usage:
    python batch.py [--name-from-filename]    # default: infer name from filename
    python batch.py --name "Buddy"            # override — use same name for all photos
    python batch.py --workers 4               # parallel Gemini calls (default: 4)

Drop pet photos into test_photos/ then run this script.
Results land in output/.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from generate import ALLOWED_SUFFIXES, PROMPTS, generate

TEST_DIR = Path("test_photos")

# Styles that require extra variables (style_vars) are skipped in batch mode.
# To generate watercolor in batch, run generate.py directly with --style watercolor.
_STYLES_REQUIRING_VARS = {"watercolor"}
STYLES = [s for s in PROMPTS if s not in _STYLES_REQUIRING_VARS]


def name_from_stem(stem: str) -> str:
    """'golden_retriever_max' → 'Max'  |  'luna-the-cat' → 'Luna The Cat'"""
    return stem.replace("_", " ").replace("-", " ").title()


def main():
    parser = argparse.ArgumentParser(
        description="Batch-generate pet portraits for every photo in test_photos/"
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override pet name for all photos (default: inferred from filename)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel Gemini calls (default: 4)",
    )
    args = parser.parse_args()

    TEST_DIR.mkdir(exist_ok=True)
    photos = sorted(p for p in TEST_DIR.iterdir() if p.suffix.lower() in ALLOWED_SUFFIXES)

    if not photos:
        print(
            f"No photos found in {TEST_DIR}/\n"
            "Drop some .jpg / .png / .webp files there and try again.",
            file=sys.stderr,
        )
        sys.exit(0)

    tasks = [
        (photo, args.name or name_from_stem(photo.stem), style)
        for photo in photos
        for style in STYLES
    ]

    total = len(tasks)
    print(f"Found {len(photos)} photo(s) × {len(STYLES)} styles = {total} portraits")
    print(f"Running with {args.workers} parallel worker(s)\n")
    print(f"{'Photo':<35} {'Style':<14} {'Status'}")
    print("─" * 65)

    done = errors = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(generate, str(photo), pet_name, style): (photo, style)
            for photo, pet_name, style in tasks
        }
        for fut in as_completed(futures):
            photo, style = futures[fut]
            label = f"{photo.name:<35} {style:<14}"
            try:
                _, comp_path = fut.result()
                done += 1
                print(f"{label} ✓  {comp_path.name}")
            except Exception as exc:
                errors += 1
                print(f"{label} ✗  ERROR: {exc}")

    print("─" * 65)
    print(f"\nComplete: {done} succeeded, {errors} failed.  Output → output/")


if __name__ == "__main__":
    main()
