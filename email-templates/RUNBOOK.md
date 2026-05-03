# Klaviyo email runbook — Pet Printables

Two transactional email templates live in Klaviyo. This doc covers the wiring needed for them to fire on real orders.

## Templates (v2 — production-ready)

| Name | Template ID | Edit |
| --- | --- | --- |
| `PP — 01 Order Confirmation v2` | `UUeDPj` | https://www.klaviyo.com/email-editor/UUeDPj/edit |
| `PP — 02 A Gift For You v2 (24h Download)` | `SGEuzS` | https://www.klaviyo.com/email-editor/SGEuzS/edit |

⚠️ V1 templates (`TC4Ns6`, `UsWBWn`) had two issues — Liquid variables prefixed with underscores (Klaviyo blocks those) and reliance on Shopify line-item-property paths that didn't render in preview. **Delete the v1 templates from the Klaviyo Templates list** before going live.

## Personalisation — uses Klaviyo profile properties (set by backend on order)

Both templates pull from `person.X` profile properties set on the customer's Klaviyo profile when the order webhook fires. This pattern is reliable, previews correctly, and avoids the Shopify line-item-property parsing trap.

| Variable | Set by | Notes |
| --- | --- | --- |
| `{{ first_name }}` | Klaviyo Shopify integration | Customer first name |
| `{{ person.pet_name }}` | Backend on order | The pet's name from the cart attribute |
| `{{ person.pet_preview_url }}` | Backend on order | Public CDN URL of the generated preview |
| `{{ person.download_url }}` | Backend on order | Signed URL, 24h TTL — for the gift email's download CTA |
| `{{ person.download_expires_at }}` | Backend on order | Human-readable expiry timestamp |
| `{{ event.extra.order_number }}` | Klaviyo Shopify | Order number — comes free with `Placed Order` |
| `{{ event.extra.order_status_url }}` | Klaviyo Shopify | Track-your-order link — comes free with `Placed Order` |
| `{{ event.extra.value }}` | Klaviyo Shopify | Order total |

Every variable has a graceful default so the template renders cleanly even before the backend wiring is complete.

## Backend wiring (one webhook handler does it all)

When a Shopify order is paid, the Pet Printables Railway backend should:

1. Read line-item properties from the order to get `pet_name` and the preview URL captured at add-to-cart
2. Generate a signed download URL (HMAC token, 24h expiry) pointing to the high-res file
3. Compute `download_expires_at` as a human-readable string ("Saturday at 11pm" or similar)
4. POST to Klaviyo Profile API to set those four properties on the customer's profile:
   - `pet_name`
   - `pet_preview_url`
   - `download_url`
   - `download_expires_at`
5. (Optional) POST to Shopify Admin API to append the pet name to a customer metafield (so future orders remember it). See "Memory" section below.

The MCP tool `klaviyo_update_profile` is the right call for step 4 — it accepts the profile id (or email lookup) and a `properties` dict.

## Setting up the flows in Klaviyo (UI work, ~5 min each)

Klaviyo's MCP doesn't expose flow creation, so this is a one-time UI step.

### Flow 1: Order Confirmation
1. Klaviyo → Flows → Create from scratch
2. Trigger: Metric → `Placed Order` (Shopify, id `T4dfHg`)
3. Action: Email → choose template `PP — 01 Order Confirmation v2`
4. Subject: `{{ person.pet_name|default:"Your" }} portrait is on its way`
5. Send delay: immediate
6. Set Live

### Flow 2: A Gift For You (24h Download)
1. Same trigger as above (`Placed Order`)
2. Time delay: 30 minutes after the trigger
3. Action: Email → choose template `PP — 02 A Gift For You v2 (24h Download)`
4. Subject: `A small gift for {{ person.pet_name|default:"you" }}`
5. Set Live

## Memory: customer metafield for repeat-order pet name

Add a Shopify Flow workflow:
- Trigger: `Order created`
- Action: `Update customer metafield`
- Namespace `pet_printables`, key `pet_names`, type `list.single_line_text`
- Value: Liquid that pulls each line item's `pet_name` property, dedupes, and joins with commas

Once that's set, `customer.metafields.pet_printables.pet_names` is available everywhere in Liquid for welcome-back banners, account pages, etc.

## Brand assets

- Signature image (handwritten "The Pet Printables team" + paw): hosted on Klaviyo CDN at https://d3k81ch9hvuctc.cloudfront.net/company/SPKeMf/images/2d019f06-a2fe-4b25-a371-e7d6f1ad28d8.png. Generated via Nano Banana Pro and stored locally at `email-templates/signature-source.png`.
- Logo: text-based "Pet Printables" in Cormorant Garamond italic for now; swap for an image if/when one is approved.

## Brand voice rules

- Reflective, warm, declarative — Brianna Wiest meets commerce. See `sections/about.liquid`.
- **No em dashes** anywhere. Use periods, semicolons, colons, commas.
- Headings in Cormorant Garamond italic. Body in Inter. Handwritten in Caveat (cursive fallback).
- Single primary CTA per email. Dark `#2F2F2A` button, white text, all-caps with `letter-spacing: 0.06em`.
- Cream `#FAF8F5` body. Cards on `#FFFFFF` or `#F3EDE6` warm taupe tint.

## Open questions

1. Memorial flag — do customers self-identify when ordering a memorial? If yes, branch the order-confirmation flow into a quieter variant (no upsell, longer post-delivery silence).
2. Klaviyo Brand Library — uploading the cream/dark-ink palette + Cormorant Garamond + Inter as approved fonts will make all future template work click-to-pick instead of hand-coded.
