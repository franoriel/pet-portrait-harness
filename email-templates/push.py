"""Push local Klaviyo email templates up to Klaviyo via the Templates API.

Usage:
    KLAVIYO_API_KEY=pk_... python3 email-templates/push.py
    KLAVIYO_API_KEY=pk_... python3 email-templates/push.py 02      # only the gift email
    KLAVIYO_API_KEY=pk_... python3 email-templates/push.py list    # list templates in Klaviyo

If a template ID 404s, falls back to name lookup, then offers to create a
new template if no match is found by name.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

# slug -> (template_id_hint, filename, expected_name)
# template_id_hint may be stale; the script falls back to name lookup if PATCH 404s.
TEMPLATES = {
    "01": ("UUeDPj", "01-order-confirmation.html", "PP — 01 Order Confirmation v2"),
    "02": ("Tsw3XT", "02-gift-download.html", "PP — 02 A Gift For You v2 (24h Download)"),
}

API_KEY = os.environ.get("KLAVIYO_API_KEY", "").strip()
if not API_KEY:
    sys.exit("KLAVIYO_API_KEY not set in environment")

HERE = Path(__file__).parent
HEADERS = {
    "Authorization": f"Klaviyo-API-Key {API_KEY}",
    "revision": "2024-10-15",
    "Content-Type": "application/vnd.api+json",
    "accept": "application/vnd.api+json",
}


def list_all_templates() -> list[dict]:
    """Page through all templates and return raw API objects."""
    out: list[dict] = []
    url = "https://a.klaviyo.com/api/templates/"
    while url:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            sys.exit(f"list templates failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        out.extend(body.get("data") or [])
        url = ((body.get("links") or {}).get("next")) or None
    return out


def find_template_id_by_name(name: str) -> str | None:
    """Return Klaviyo template id whose name matches `name` (case-insensitive,
    falls back to substring match on the slug like 'gift' or 'order')."""
    templates = list_all_templates()
    name_lc = name.lower()
    for t in templates:
        if (t.get("attributes") or {}).get("name", "").lower() == name_lc:
            return t["id"]
    # Substring fallback — find anything containing key tokens.
    tokens = [w for w in name_lc.replace("—", " ").split() if len(w) > 3]
    for t in templates:
        n = (t.get("attributes") or {}).get("name", "").lower()
        if all(tok in n for tok in tokens[:3]):
            return t["id"]
    return None


def patch_template(template_id: str, html: str) -> tuple[int, str]:
    url = f"https://a.klaviyo.com/api/templates/{template_id}/"
    body = {
        "data": {
            "type": "template",
            "id": template_id,
            "attributes": {"html": html},
        }
    }
    r = requests.patch(url, headers=HEADERS, json=body, timeout=15)
    return r.status_code, r.text


def create_template(name: str, html: str) -> str:
    url = "https://a.klaviyo.com/api/templates/"
    body = {
        "data": {
            "type": "template",
            "attributes": {"name": name, "editor_type": "CODE", "html": html},
        }
    }
    r = requests.post(url, headers=HEADERS, json=body, timeout=15)
    if r.status_code >= 400:
        sys.exit(f"create template failed: {r.status_code} {r.text[:300]}")
    new_id = r.json()["data"]["id"]
    print(f"     created new template id={new_id}")
    return new_id


def push(slug: str) -> None:
    template_id, filename, expected_name = TEMPLATES[slug]
    html = (HERE / filename).read_text(encoding="utf-8")

    code, text = patch_template(template_id, html)

    if code == 404:
        print(f"     id {template_id} 404'd — falling back to name lookup ({expected_name!r})")
        found_id = find_template_id_by_name(expected_name)
        if found_id:
            print(f"     found by name: {found_id}")
            template_id = found_id
            code, text = patch_template(template_id, html)
        else:
            print(f"     no template named {expected_name!r} — creating new one")
            template_id = create_template(expected_name, html)
            code = 200

    if code >= 400:
        print(f"FAIL {slug} ({template_id}): {code} {text[:300]}")
        sys.exit(1)
    print(f"OK   {slug} ({template_id}) <- {filename} ({len(html)} bytes)")


def list_cmd() -> None:
    for t in list_all_templates():
        attrs = t.get("attributes") or {}
        print(f"{t['id']:>10}  {attrs.get('name','(unnamed)')}")


def main() -> None:
    args = sys.argv[1:]
    if args == ["list"]:
        list_cmd()
        return
    targets = args or list(TEMPLATES.keys())
    for slug in targets:
        if slug not in TEMPLATES:
            sys.exit(f"unknown template slug: {slug} (valid: {', '.join(TEMPLATES)}, or 'list')")
        push(slug)


if __name__ == "__main__":
    main()
