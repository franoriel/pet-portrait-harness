# Klaviyo email runbook — Pet Printables

Two transactional email templates live in Klaviyo. This doc covers the wiring needed for them to fire on real orders.

## Templates (v2 — production-ready)

| Name | Template ID | Edit |
| --- | --- | --- |
| `PP — 01 Order Confirmation v2` | `UUeDPj` | https://www.klaviyo.com/email-editor/UUeDPj/edit |
| `PP — 02 A Gift For You v2 (24h Download)` | `Tsw3XT` | https://www.klaviyo.com/email-editor/Tsw3XT/edit |

⚠️ V1 templates (`TC4Ns6`, `UsWBWn`) had two issues — Liquid variables prefixed with underscores (Klaviyo blocks those) and reliance on Shopify line-item-property paths that didn't render in preview. **Delete the v1 templates from the Klaviyo Templates list** before going live.

## Personalisation — uses Klaviyo profile properties (set by backend on order)

Both templates pull from `person.X` profile properties set on the customer's Klaviyo profile when the order webhook fires. This pattern is reliable, previews correctly, and avoids the Shopify line-item-property parsing trap.

| Variable | Set by | Notes |
| --- | --- | --- |
| `{{ first_name }}` | Klaviyo Shopify integration | Customer first name |
| `{{ person.pet_name }}` | Backend on order | First pet name (multi-portrait orders use the first dedupe entry) |
| `{{ person.pet_preview_url }}` | Backend on order | Public CDN URL of the first portrait's preview |
| `{{ person.portrait_count }}` | Backend on order | Number of unique portraits in the order — drives single vs multi copy branches |
| `{{ person.order_url }}` | Backend on order | HMAC-signed Flask URL → `/portraits/<token>`, 90d TTL. **The gift email's single CTA destination.** No customer-account login required. |
| `{{ person.last_order_id }}` | Backend on order | Shopify order id — debug aid |
| `{{ person.last_order_number }}` | Backend on order | Shopify order name (e.g. `#1042`) |
| `{{ event.extra.order_number }}` | Klaviyo Shopify | Order number — comes free with `Placed Order` |
| `{{ event.extra.order_status_url }}` | Klaviyo Shopify | Track-your-order link — comes free with `Placed Order` |
| `{{ event.extra.value }}` | Klaviyo Shopify | Order total |

Every variable has a graceful default so the template renders cleanly even before the backend wiring is complete. The gift email's CTA falls back to `https://petprintables.ca/account` when `person.order_url` is missing — `sections/customers-account.liquid` surfaces the same digital-files block when a customer hits `/account` directly, so the fallback isn't a dead end on classic Shopify customer accounts.

## Backend wiring

When a Shopify order is paid, the Pet Printables Railway backend (`_push_klaviyo_order_event` in `app.py`) does the following:

1. Dedupe the order's line items into unique portraits keyed by `(pet_name, style, preview_url, show_name)`.
2. For each portrait: sign a 30-day download token for the original, generate four social variants (Square, Story, Portrait, Wallpaper) and upload them to R2 under `social/{order_id}/{portrait_id}/{variant}.jpg`, sign 30-day download tokens for each.
3. Build a `digital_files` record (portraits + downloads + expiry) and persist it in **two** places:
   - Local Redis store keyed by `order_id` — read by Flask `/portraits/<token>` to render the gift page.
   - Shopify order metafield `petprintables.digital_files` — read by `sections/customers-order.liquid` and `sections/customers-account.liquid` so logged-in customers on **classic** customer accounts also see the portraits.
4. Sign a 90-day order token (`make_order_token`) and POST to Klaviyo Profile API setting `pet_name`, `pet_preview_url`, `portrait_count`, `order_url`, `last_order_id`, `last_order_number` on the customer's profile.

⚠️ Shopify's **New** Customer Accounts UI does not render theme Liquid. The `customers-*.liquid` blocks only fire on stores still on classic accounts. The Flask `/portraits/<token>` page is the canonical, account-version-agnostic surface — keeping `person.order_url` populated on every order is critical.

## Setting up the flows in Klaviyo (UI work, ~5 min each)

Klaviyo's MCP doesn't expose flow creation, so this is a one-time UI step.

### Flow 1: Order Confirmation
1. Klaviyo → Flows → Create from scratch
2. Trigger: Metric → `Placed Order` (Shopify, id `T4dfHg`)
3. Action: Email → choose template `PP — 01 Order Confirmation v2`
4. Subject: `{{ person.pet_name|default:"Your" }} portrait is on its way`
5. Send delay: immediate
6. Set Live

### Flow 2: A Gift For You (Digital Download)
1. Same trigger as above (`Placed Order`)
2. **Time delay: 30 minutes after the trigger** — the backend needs this window to generate social variants and push `person.order_url` to the profile. Cutting the delay shorter risks the email firing with an empty `order_url` (CTA falls back to `/account`).
3. Action: Email → choose template `PP — 02 A Gift For You v2 (24h Download)`
4. Subject: `A small gift for {{ person.pet_name|default:"you" }}`
5. Set Live

## Flow 3: Memorial Order Confirmation

Fires instead of the standard order confirmation when a customer checked "This is a memorial" on the cart page. Uses template `PP — 03 Memorial Order Confirmation`.

**How the signal reaches Klaviyo:** The cart page writes `gift_type = "Memorial"` as a Shopify cart attribute. Shopify carries cart attributes into the order as `note_attributes` (an array of `{name, value}` objects). The Klaviyo Shopify integration surfaces these on the `Placed Order` event under `event.extra.note_attributes`.

**Setup (UI work, ~5 min):**
1. Upload the template: `KLAVIYO_API_KEY=... python3 email-templates/push.py 03`
   (push.py will need a `03` entry added — see push.py for the pattern; template name `PP — 03 Memorial Order Confirmation`)
2. Klaviyo → Flows → Create from scratch
3. Trigger: Metric → `Placed Order` (Shopify)
4. Add a **Flow Filter** immediately after the trigger:
   - Property: `event.extra.note_attributes` → contains → `{name: "gift_type", value: "Memorial"}`
   - In Klaviyo's UI: "Properties about someone" is wrong here — use "Properties about the event". Path: `note_attributes[].value` contains `Memorial`, or use the structured filter: `note_attributes` → any item where `name` equals `gift_type` AND `value` equals `Memorial`.
   - Practical shortcut: filter on `event.extra.note` → contains → `Memorial message` (the cart note is always set to `"Memorial message"` or `"Memorial message: <text>"` when gift_type is Memorial).
5. Action: Email → choose template `PP — 03 Memorial Order Confirmation`
6. Subject: `{{ person.pet_name|default:"Your portrait" }} — we will take good care of this`
7. Send delay: **immediate** (no 30-min window needed — no order_url dependency)
8. Set Live

**Suppression rule on Flows 1 and 2:** Add the inverse filter to Flows 1 and 2 so standard confirmation emails do not also fire on memorial orders:
- Flow filter: `note` does NOT contain `Memorial message`
  (or the structured version: `note_attributes` → no item where `value` equals `Memorial`)

**Template variables used (same as template 01):**
- `{{ first_name }}` — customer first name
- `{{ event.line_items.0.properties.pet_name }}` — pet name
- `{{ event.line_items.0.properties.preview_url }}` — portrait preview image
- `{{ event.extra.order_status_url }}` — order tracking link
- `{{ event.extra.order_number }}`, `{{ event.extra.line_items }}`, totals — order summary

The template does not use `person.order_url` so there is no 30-minute race to worry about.

## Troubleshooting: gift email CTA goes to `/account` instead of the portraits page

Symptom: customer clicks the gift email's CTA and lands on `https://petprintables.ca/account` (a Shopify customer-account sign-in / orders page) instead of the Flask `/portraits/<token>` digital-files page.

Root cause: `person.order_url` was empty on the customer's Klaviyo profile when the email rendered, so the template fell back to `https://petprintables.ca/account`.

Diagnostic steps:
1. **Verify the live Klaviyo template matches the local file.** Run `KLAVIYO_API_KEY=... python3 email-templates/push.py 02` to push `02-gift-download.html` up. Editor sessions in Klaviyo can drift the live template away from this file.
2. **Verify the backend pushed `order_url` to the customer profile.** In Railway logs, grep for `[klaviyo] order=<id> ✓ profile push OK` — the success log enumerates the property names. If `order_url` is missing from that list, check upstream for `[klaviyo] order=<id> no unique portraits to publish` or social-variant errors that aborted the push.
3. **Verify the flow's 30-minute delay is still in place.** Cutting the delay short causes a race where the email fires before the backend finishes posting `order_url`.
4. **As a partial fallback**, `sections/customers-account.liquid` now surfaces the digital-files block inline for the customer's recent orders that have a `petprintables.digital_files` metafield, so customers on **classic** Shopify customer accounts see their portraits even when the email link drops them on `/account`. Customers on Shopify's **New Customer Accounts** still need the email's `order_url` to be populated — there's no Liquid surface available there.

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

1. Klaviyo Brand Library — uploading the cream/dark-ink palette + Cormorant Garamond + Inter as approved fonts will make all future template work click-to-pick instead of hand-coded.
