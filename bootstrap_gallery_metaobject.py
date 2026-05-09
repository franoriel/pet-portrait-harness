#!/usr/bin/env python3
"""
One-shot setup: create the `gallery_submission` metaobject definition in
Shopify so the backend's /gallery/submit/<token> endpoint has somewhere to
write customer submissions.

Run once after deploying. Idempotent — re-running prints the existing
definition rather than creating a duplicate.

Usage:
    SHOPIFY_SHOP_DOMAIN=myshop.myshopify.com \\
    SHOPIFY_ADMIN_API_TOKEN=shpat_xxx \\
    python bootstrap_gallery_metaobject.py

Required scopes on the admin token: write_metaobject_definitions, write_metaobjects, write_files.
"""

import json
import os
import sys

import requests

API_VERSION = "2024-10"
TYPE = "gallery_submission"

DEFINITION = {
    "name": "Gallery Submission",
    "type": TYPE,
    "description": "Customer-submitted portraits surfaced on /pages/gallery. Created automatically when a customer clicks 'Submit to Gallery' in their digital-gift email.",
    "access": {
        "storefront": "PUBLIC_READ",
    },
    "capabilities": {
        "publishable": {"enabled": True},
    },
    "fieldDefinitions": [
        {
            "key": "image",
            "name": "Portrait image",
            "type": "file_reference",
            "validations": [
                {"name": "file_type_options", "value": json.dumps(["Image"])},
            ],
            "required": True,
        },
        {
            "key": "pet_name",
            "name": "Pet name",
            "type": "single_line_text_field",
            "required": False,
        },
        {
            "key": "category",
            "name": "Category",
            "type": "single_line_text_field",
            "description": "dogs | cats | other — drives the gallery filter buttons.",
            "required": False,
        },
        {
            "key": "order_id",
            "name": "Order ID",
            "type": "single_line_text_field",
            "description": "Shopify order ID this submission came from. Used as the metaobject handle suffix for idempotency.",
            "required": False,
        },
        {
            "key": "submitted_at",
            "name": "Submitted at",
            "type": "date_time",
            "required": False,
        },
    ],
}

CREATE_MUTATION = """
  mutation MetaobjectDefinitionCreate($definition: MetaobjectDefinitionCreateInput!) {
    metaobjectDefinitionCreate(definition: $definition) {
      metaobjectDefinition { id type name }
      userErrors { field message code }
    }
  }
"""

EXISTING_QUERY = """
  query ExistingDefinition($type: String!) {
    metaobjectDefinitionByType(type: $type) {
      id
      type
      name
      fieldDefinitions { key name type { name } }
    }
  }
"""


def _post(domain: str, token: str, query: str, variables: dict) -> dict:
    url = f"https://{domain}/admin/api/{API_VERSION}/graphql.json"
    resp = requests.post(
        url,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body


def main() -> int:
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip().replace("https://", "").rstrip("/")
    token = os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()
    if not domain or not token:
        print("ERROR: set SHOPIFY_SHOP_DOMAIN and SHOPIFY_ADMIN_API_TOKEN", file=sys.stderr)
        return 1

    existing = _post(domain, token, EXISTING_QUERY, {"type": TYPE})
    found = (existing.get("data") or {}).get("metaobjectDefinitionByType")
    if found:
        print(f"✓ Definition already exists: {found['name']} ({found['type']})")
        print(f"  id={found['id']}")
        print(f"  fields: {', '.join(f['key'] for f in found.get('fieldDefinitions') or [])}")
        return 0

    created = _post(domain, token, CREATE_MUTATION, {"definition": DEFINITION})
    payload = ((created.get("data") or {}).get("metaobjectDefinitionCreate") or {})
    user_errors = payload.get("userErrors") or []
    if user_errors:
        print("✗ userErrors:", file=sys.stderr)
        for e in user_errors:
            print(f"  - {e.get('field')}: {e.get('message')} ({e.get('code')})", file=sys.stderr)
        return 2
    md = payload.get("metaobjectDefinition") or {}
    print(f"✓ Created definition {md.get('name')!r} (type={md.get('type')})")
    print(f"  id={md.get('id')}")
    print()
    print("Next steps:")
    print("  1. Confirm the existing /pages/gallery template renders fine (no submissions yet).")
    print("  2. Trigger a test order or POST a test profile to Klaviyo to populate gallery_submit_url.")
    print("  3. Click the email's 'Submit to Gallery' button — should redirect to /pages/gallery-thanks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
