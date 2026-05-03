# Klaviyo email runbook — Pet Printables

Two transactional email templates have been uploaded to Klaviyo. This doc covers the remaining work to make them actually fire on real orders.

## Templates

| Name in Klaviyo | Template ID | File |
| --- | --- | --- |
| `PP — 01 Order Confirmation` | `TC4Ns6` | `01-order-confirmation.html` |
| `PP — 02 A Gift For You (24h Download)` | `UsWBWn` | `02-gift-download.html` |

Edit either at `https://www.klaviyo.com/email-editor/<TEMPLATE_ID>/edit`.

## What needs to happen for the templates to work end-to-end

### 1. Cart attributes (Shopify-side, frontend) — REQUIRED

Both templates personalise on `event.line_items.0.properties.pet_name` and `event.line_items.0.properties._preview_url`. Those need to be set as line-item properties on the cart at the moment of add-to-cart.

**Today**: the portrait-flow.js add-to-cart call should already be passing pet_name. Verify it also passes the preview image URL as `_preview_url` (underscore prefix hides it from the cart UI). Search `assets/portrait-flow.js` for the add-to-cart payload — it sets `properties.pet_name`. Add `properties._preview_url` alongside it pointing to the CDN URL of the generated preview.

**Optional but recommended**: also set `properties._download_url` and `properties._download_expires_at` once the signed-URL endpoint is built (see #3 below).

### 2. Customer metafield — pet name memory

Write the pet name(s) to a customer metafield on order completion so the next order remembers them.

**Approach** (Shopify Flow, no code):
1. Shopify admin → Apps → Shopify Flow → create new workflow
2. Trigger: `Order created`
3. Action: `Update customer metafield`
4. Namespace: `pet_printables`, Key: `pet_names`, Type: `list.single_line_text`
5. Value: a Liquid expression that pulls each line item's `pet_name` property:
   ```liquid
   {% assign names = "" %}
   {% for item in order.lineItems %}
     {% for prop in item.customAttributes %}
       {% if prop.key == "pet_name" %}
         {% assign names = names | append: prop.value | append: "," %}
       {% endif %}
     {% endfor %}
   {% endfor %}
   {{ names | split: "," | uniq | compact | join: ", " }}
   ```
6. Save and turn it on.

That metafield is then available as `customer.metafields.pet_printables.pet_names` everywhere in Liquid (welcome-back banner, account page, etc.).

### 3. Signed download URL — REQUIRED for the gift email to work

The "DOWNLOAD HIGH-RES" button needs a URL that:
- Returns the high-res rendered portrait (not the preview)
- Expires 24 hours after the email is sent
- Is unique to the order so links can't be shared / abused indefinitely

**Backend stub** (to be added to `app.py`):
```python
@app.route("/api/portrait/download/<token>")
def portrait_download(token):
    # Verify signed token, check expiry, look up the high-res file path
    payload = verify_download_token(token)  # raises if invalid/expired
    return send_file(payload.high_res_path, as_attachment=True)
```

**Token signing**: HMAC-sign `{order_id, line_item_id, expires_at}` with a server secret. The Shopify Order webhook (or a Klaviyo flow Update Customer Profile action) writes the token + URL into the order's note attributes or directly onto the line item property `_download_url`.

The signed URL gets injected into the email via the line-item property template variable. No further work in Klaviyo needed once that property is set on the cart.

### 4. Gallery submission landing page — REQUIRED for the "Submit to gallery" button

Email links to `/pages/share-portrait?order=<ID>&token=<TOKEN>`. Build that page:
1. New Shopify page template `templates/page.share-portrait.json` + matching section
2. Form: image preview (server-rendered from order id), optional note textarea, submit button
3. Form posts to `/api/gallery/submit` on the Pet Printables backend (Railway)
4. Backend stores the submission, queues for moderation, and on approval adds it to the gallery-grid display

Skeleton already lives in `sections/gallery-grid.liquid` — submissions become the `block` source instead of the `default_examples` fallback.

### 5. Social share landing pages — REQUIRED for the share buttons

Email links to `/share/instagram?order=<ID>` and `/share/facebook?order=<ID>`.

- Instagram: web doesn't directly support story-share via URL. The page should detect mobile and either (a) trigger a native share intent with the image attached, or (b) prompt the user to download the image then re-upload to IG. Include the `@mypetprintables` tag prompt visibly on the page.
- Facebook: same pattern, with the FB share dialog (`https://www.facebook.com/sharer/sharer.php?u=<URL>`).

This can be a single landing page that branches by `?platform=instagram|facebook`.

## Setting up the flows in Klaviyo (UI work, ~5 min each)

Klaviyo's MCP doesn't expose flow creation, so this is a UI step.

### Flow 1: Order Confirmation
1. Klaviyo → Flows → Create flow → Build from scratch
2. Trigger: **Metric** → `Placed Order` (Shopify, id `T4dfHg`)
3. Filter: `event.extra.line_items` properties `_preview_url` is set (or filter on a specific Shopify product tag like `pet-portrait`)
4. Action: **Email** → choose template `PP — 01 Order Confirmation`
5. Subject: `{{ event.line_items.0.properties.pet_name|default:"Your" }} portrait is on the way`
6. Pre-header text matches the `<div style="display:none">` in the template
7. Send delay: immediate
8. Save → Live

### Flow 2: A Gift For You (24h Download)
1. Same trigger as above (`Placed Order`)
2. Same filter
3. **Time delay: 30 minutes** after the trigger (lets the order confirmation arrive first, primes recipient for a follow-up)
4. Action: **Email** → choose template `PP — 02 A Gift For You (24h Download)`
5. Subject: `A small gift for {{ event.line_items.0.properties.pet_name|default:"you" }}`
6. Save → Live

## Brand voice notes for future emails

- Voice: reflective, warm, declarative — Brianna Wiest meets commerce. See the About page for reference (`sections/about.liquid`).
- **No em dashes** anywhere in copy. Use periods, semicolons, colons, commas, or restructure the sentence. This is a hard rule.
- Headings in Cormorant Garamond italic. Body in Inter. Handwritten accents in Caveat (with cursive fallback for clients that strip web fonts).
- Single primary CTA per email. Dark `#2F2F2A` button, white text, all-caps with `letter-spacing: 0.06em`.
- Cream `#FAF8F5` body background. Cards on `#FFFFFF` or `#F3EDE6` (warm taupe tint) for visual rhythm.

## Open questions

1. **Memorial flag** — when a customer is ordering a memorial piece, do they self-identify? If yes (cart note, hidden tag), we can branch the order-confirmation flow into a quieter variant.
2. **Recovery offer** — if you want a 10% nudge in an abandoned-preview flow, name it. I left it out of these two since they're for completed orders.
3. **Klaviyo brand kit** — uploading your logo + brand colours to Klaviyo's Brand Library will let the inline editor offer them as one-click choices in any future template. Worth doing once.
