#!/usr/bin/env python3
"""
Scaffold the "Contest Entry Confirmation" flow in Klaviyo via the API.

What this creates (as a DRAFT — nothing sends until you activate it):

  Trigger: metric "Contest Entry Submitted"
    ↓
  Email 1 (Confirmation)
    ↓ wait 1 day
  Email 2 (Boost your odds)
    ↓ wait 5 days
  Email 3 (Mid-contest social proof)
    ↓ wait 5 days
  Email 4 (Final 48h)
    ↓ end

The script needs four things already in your Klaviyo account before it
can run successfully:
  1. The four templates uploaded (run upload_klaviyo_templates.py first)
  2. The "Contest Entry Submitted" metric (created on first event fire
     from your live /contest/entry endpoint — submit a test entry first)
  3. KLAVIYO_API_KEY in env (Profiles: Read + Templates: Read +
     Flows: Read + Flows: Write + Metrics: Read)
  4. requests Python package

Usage:
  export KLAVIYO_API_KEY="pk_xxx..."
  python3 create_klaviyo_flow.py --dry-run     # show what will be sent
  python3 create_klaviyo_flow.py               # actually create

If you re-run the script when the flow already exists, it does nothing
(safer than overwriting a flow you've started editing manually). To
re-scaffold, delete or rename the existing one in Klaviyo first.

Klaviyo's Flows API has tighter constraints than the rest of the API —
some action types are beta and the schema shifts between revisions.
If this script gets a 400/422 from the create call, it'll print the
exact response body so you can adjust. Fallback is always to build the
flow manually using marketing/klaviyo-flow-emails.md as a guide.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

try:
    import requests
except ImportError:
    print("This script needs the `requests` package: pip install requests")
    sys.exit(1)

KLAVIYO_API_ROOT = "https://a.klaviyo.com/api"
KLAVIYO_API_REVISION = "2024-10-15"

FLOW_NAME = "Contest Entry Confirmation"
METRIC_NAME = "Contest Entry Submitted"

# Templates in send order. Names must exactly match what
# upload_klaviyo_templates.py created (Klaviyo lookup is by name).
TEMPLATE_NAMES = [
    "Contest · 1 · Instant confirmation",
    "Contest · 2 · Boost your odds (T+24h)",
    "Contest · 3 · Mid-contest social proof (T+7d)",
    "Contest · 4 · Final 48h",
]
# Delay BEFORE each email, in days. Email 1 fires immediately on trigger.
DELAYS_DAYS = [0, 1, 5, 5]


def auth_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": KLAVIYO_API_REVISION,
        "Content-Type": "application/json",
        "accept": "application/json",
    }


def find_metric_id(api_key: str, name: str) -> Optional[str]:
    """Look up a metric by exact name. Returns None if not found."""
    url: Optional[str] = f"{KLAVIYO_API_ROOT}/metrics"
    while url:
        r = requests.get(url, headers=auth_headers(api_key), timeout=15)
        if r.status_code >= 400:
            raise SystemExit(f"List metrics failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        for item in body.get("data", []):
            if (item.get("attributes") or {}).get("name") == name:
                return item.get("id")
        url = ((body.get("links") or {}).get("next")) or None
    return None


def find_template_ids(api_key: str, names: list[str]) -> dict[str, str]:
    """Map name → template_id for each requested template."""
    out: dict[str, str] = {}
    url: Optional[str] = f"{KLAVIYO_API_ROOT}/templates"
    wanted = set(names)
    while url and wanted - set(out):
        r = requests.get(url, headers=auth_headers(api_key), timeout=15)
        if r.status_code >= 400:
            raise SystemExit(f"List templates failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        for item in body.get("data", []):
            name = (item.get("attributes") or {}).get("name", "")
            if name in wanted:
                out[name] = item.get("id", "")
        url = ((body.get("links") or {}).get("next")) or None
    return out


def find_flow_by_name(api_key: str, name: str) -> Optional[str]:
    """Return flow_id if a flow with this exact name already exists."""
    url: Optional[str] = f"{KLAVIYO_API_ROOT}/flows"
    while url:
        r = requests.get(url, headers=auth_headers(api_key), timeout=15)
        if r.status_code >= 400:
            raise SystemExit(f"List flows failed: {r.status_code} {r.text[:300]}")
        body = r.json()
        for item in body.get("data", []):
            if (item.get("attributes") or {}).get("name") == name:
                return item.get("id")
        url = ((body.get("links") or {}).get("next")) or None
    return None


def build_flow_definition(metric_id: str, template_ids: list[str]) -> dict:
    """Construct the flow definition. Linear: trigger → (delay → email)*4.

    Klaviyo's flow definition schema uses an `actions` array where each
    action has an `id` and links to the next via the parent's
    references. This produces a simple linear flow with no branches.
    """
    actions: list[dict] = []
    for i, (delay_days, tid) in enumerate(zip(DELAYS_DAYS, template_ids), start=1):
        if delay_days > 0:
            actions.append({
                "type": "time-delay",
                "id": f"delay-{i}",
                "data": {
                    "delay_seconds": delay_days * 86400,
                    "delay_unit": "days",
                    "delay_value": delay_days,
                },
            })
        actions.append({
            "type": "send-email",
            "id": f"email-{i}",
            "data": {
                "template_id": tid,
                "smart_sending_enabled": True,
                "transactional": False,
            },
        })

    return {
        "name": FLOW_NAME,
        "definition": {
            "triggers": [
                {
                    "type": "metric",
                    "id": metric_id,
                }
            ],
            "profile_filter": None,
            "actions": actions,
        },
        "status": "draft",
    }


def create_flow(api_key: str, attributes: dict) -> str:
    body = {"data": {"type": "flow", "attributes": attributes}}
    r = requests.post(
        f"{KLAVIYO_API_ROOT}/flows",
        headers=auth_headers(api_key), json=body, timeout=30,
    )
    if r.status_code >= 400:
        # Print the response body so the user can see what Klaviyo wants.
        # Their Flows API is beta-ish and the schema occasionally drifts.
        print()
        print("Klaviyo rejected the flow create call:")
        print(f"  Status: {r.status_code}")
        print(f"  Body  : {r.text[:1200]}")
        print()
        print("Fallback: build the flow manually in Klaviyo's UI using")
        print("marketing/klaviyo-flow-emails.md as a guide.")
        raise SystemExit(1)
    return r.json()["data"]["id"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold the Contest Entry Confirmation flow.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the JSON that would be POSTed without making the call.")
    args = parser.parse_args()

    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    if not api_key:
        print("KLAVIYO_API_KEY is not set.")
        print("  export KLAVIYO_API_KEY=\"pk_xxx...\"")
        return 1
    if not api_key.startswith("pk_"):
        print("KLAVIYO_API_KEY doesn't look right — should start with 'pk_'.")
        return 1

    print(f"Checking for existing flow named '{FLOW_NAME}'…")
    existing_flow = find_flow_by_name(api_key, FLOW_NAME)
    if existing_flow:
        print(f"  ✓ Flow already exists (id {existing_flow}). Nothing to do.")
        print(f"  Open: https://www.klaviyo.com/flow/{existing_flow}/edit")
        return 0
    print("  Not found — will create.")

    print()
    print(f"Looking up metric '{METRIC_NAME}'…")
    metric_id = find_metric_id(api_key, METRIC_NAME)
    if not metric_id:
        print(f"  ✗ Metric '{METRIC_NAME}' doesn't exist in your account yet.")
        print()
        print("  Klaviyo creates metrics on first event fire. Submit a test")
        print("  entry through /contest/entry first (or visit the live funnel")
        print("  and complete one entry with your real email). Then re-run.")
        return 1
    print(f"  ✓ Metric id: {metric_id}")

    print()
    print("Looking up template IDs…")
    template_map = find_template_ids(api_key, TEMPLATE_NAMES)
    missing = [n for n in TEMPLATE_NAMES if n not in template_map]
    if missing:
        print("  ✗ Missing templates:")
        for m in missing:
            print(f"      - {m}")
        print("  Run upload_klaviyo_templates.py first.")
        return 1
    template_ids = [template_map[n] for n in TEMPLATE_NAMES]
    for n, tid in zip(TEMPLATE_NAMES, template_ids):
        print(f"  ✓ {n}  →  id {tid}")

    print()
    print("Building flow definition…")
    attributes = build_flow_definition(metric_id, template_ids)
    actions = attributes["definition"]["actions"]
    print(f"  Trigger : metric {metric_id} ({METRIC_NAME})")
    print(f"  Steps   : {len(actions)} action(s)")
    for a in actions:
        if a["type"] == "send-email":
            print(f"    · send-email  template={a['data']['template_id']}")
        elif a["type"] == "time-delay":
            print(f"    · time-delay  {a['data']['delay_value']} {a['data']['delay_unit']}")
    print(f"  Status  : draft (won't send until you activate)")
    print()

    if args.dry_run:
        print("Dry run — request body that WOULD be POSTed:")
        print(json.dumps({"data": {"type": "flow", "attributes": attributes}}, indent=2))
        return 0

    print("Creating flow…")
    flow_id = create_flow(api_key, attributes)
    print(f"  ✓ Created flow id {flow_id}")
    print()
    print("Open in Klaviyo to review + activate:")
    print(f"  https://www.klaviyo.com/flow/{flow_id}/edit")
    print()
    print("Reminder: the flow is in DRAFT. Switch to LIVE in the top-right")
    print("toggle after you've verified each step + previewed the templates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
