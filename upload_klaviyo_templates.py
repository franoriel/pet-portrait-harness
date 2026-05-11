#!/usr/bin/env python3
"""
Upload (or update) the 6 contest HTML templates to Klaviyo.

Usage:
  # Dry run — shows what would happen, makes no API calls that mutate state
  python upload_klaviyo_templates.py --dry-run

  # Actually create/update the templates
  python upload_klaviyo_templates.py

The script reads KLAVIYO_API_KEY from your environment. The simplest path:

  export KLAVIYO_API_KEY="pk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  python upload_klaviyo_templates.py --dry-run
  python upload_klaviyo_templates.py

Idempotent: it lists existing templates first and updates any whose name
matches one of ours. New templates get created. Other templates in your
account are left alone.

The API key needs these scopes (Klaviyo → Account → API Keys → Edit Key):
  - Templates: Read
  - Templates: Write
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("This script needs the `requests` package. Install with:")
    print("  pip install requests")
    sys.exit(1)

KLAVIYO_API_ROOT = "https://a.klaviyo.com/api"
KLAVIYO_API_REVISION = "2024-10-15"

# Files in marketing/klaviyo-templates/ paired with the Klaviyo template
# names you'll see in the Klaviyo UI. Edit the names here if you'd rather
# they appear differently in your template library — the file paths stay
# the same.
TEMPLATES = [
    ("email-1-confirmation.html",  "Contest · 1 · Instant confirmation"),
    ("email-2-boost.html",         "Contest · 2 · Boost your odds (T+24h)"),
    ("email-3-social-proof.html",  "Contest · 3 · Mid-contest social proof (T+7d)"),
    ("email-4-final-48h.html",     "Contest · 4 · Final 48h"),
    ("email-5-winner.html",        "Contest · 5 · Winner (post-draw)"),
    ("email-6-consolation.html",   "Contest · 6 · Consolation (post-draw)"),
]

HERE = Path(__file__).resolve().parent
TEMPLATE_DIR = HERE / "marketing" / "klaviyo-templates"


def auth_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": KLAVIYO_API_REVISION,
        "Content-Type": "application/json",
        "accept": "application/json",
    }


def list_existing_templates(api_key: str) -> dict[str, str]:
    """Return a dict of name → template_id for all templates in the account.

    Klaviyo paginates at 10 templates per page; we follow `links.next` until
    exhausted. For a small account this is one or two requests.
    """
    out: dict[str, str] = {}
    url: Optional[str] = f"{KLAVIYO_API_ROOT}/templates"
    while url:
        r = requests.get(url, headers=auth_headers(api_key), timeout=15)
        if r.status_code >= 400:
            raise SystemExit(f"List templates failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        for item in body.get("data", []):
            name = (item.get("attributes") or {}).get("name", "")
            tid = item.get("id", "")
            if name and tid:
                out[name] = tid
        url = ((body.get("links") or {}).get("next")) or None
    return out


def create_template(api_key: str, name: str, html: str) -> str:
    """Create a new template; returns the new template_id."""
    body = {
        "data": {
            "type": "template",
            "attributes": {
                "name": name,
                "editor_type": "CODE",
                "html": html,
            },
        }
    }
    r = requests.post(
        f"{KLAVIYO_API_ROOT}/templates",
        headers=auth_headers(api_key), json=body, timeout=30,
    )
    if r.status_code >= 400:
        raise SystemExit(f"Create failed for {name}: {r.status_code} {r.text[:500]}")
    return r.json()["data"]["id"]


def update_template(api_key: str, template_id: str, name: str, html: str) -> None:
    """PATCH an existing template's name + html in place."""
    body = {
        "data": {
            "type": "template",
            "id": template_id,
            "attributes": {
                "name": name,
                "editor_type": "CODE",
                "html": html,
            },
        }
    }
    r = requests.patch(
        f"{KLAVIYO_API_ROOT}/templates/{template_id}",
        headers=auth_headers(api_key), json=body, timeout=30,
    )
    if r.status_code >= 400:
        raise SystemExit(f"Update failed for {name} ({template_id}): {r.status_code} {r.text[:500]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload Klaviyo contest templates.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without making mutating API calls.")
    args = parser.parse_args()

    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    if not api_key:
        print("KLAVIYO_API_KEY is not set.")
        print("Export your key first:")
        print("  export KLAVIYO_API_KEY=\"pk_xxx...\"")
        return 1
    if not api_key.startswith("pk_"):
        print(f"KLAVIYO_API_KEY doesn't look right — should start with 'pk_'. Got: {api_key[:6]}…")
        return 1

    # Pre-flight: confirm every file exists before doing anything destructive.
    missing = [f for f, _ in TEMPLATES if not (TEMPLATE_DIR / f).is_file()]
    if missing:
        print(f"Missing files in {TEMPLATE_DIR}:")
        for m in missing:
            print(f"  - {m}")
        return 1

    print(f"Looking up existing templates in Klaviyo…")
    try:
        existing = list_existing_templates(api_key)
    except SystemExit as e:
        print(e)
        return 1
    print(f"  Found {len(existing)} existing template(s) in the account.")

    actions = []
    for filename, klaviyo_name in TEMPLATES:
        path = TEMPLATE_DIR / filename
        html = path.read_text(encoding="utf-8")
        size_kb = len(html) // 1024
        if klaviyo_name in existing:
            actions.append(("UPDATE", filename, klaviyo_name, existing[klaviyo_name], html, size_kb))
        else:
            actions.append(("CREATE", filename, klaviyo_name, None, html, size_kb))

    print()
    print("Plan:")
    for verb, filename, name, tid, _html, size_kb in actions:
        suffix = f" (id {tid})" if tid else ""
        print(f"  {verb:6s}  {name:50s}  ←  {filename} ({size_kb} KB){suffix}")
    print()

    if args.dry_run:
        print("Dry run — nothing was sent to Klaviyo.")
        print("Re-run without --dry-run to apply.")
        return 0

    print("Applying…")
    for verb, filename, name, tid, html, _ in actions:
        if verb == "CREATE":
            new_id = create_template(api_key, name, html)
            print(f"  ✓ CREATE  {name}  →  id {new_id}")
        else:
            update_template(api_key, tid, name, html)
            print(f"  ✓ UPDATE  {name}  (id {tid})")
        # Light rate-limit cushion — Klaviyo's templates API is 75/min.
        time.sleep(0.4)

    print()
    print("All done. Open Klaviyo → Templates to see them.")
    print("  https://www.klaviyo.com/template/list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
