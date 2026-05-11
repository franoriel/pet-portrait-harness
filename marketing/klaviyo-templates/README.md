# Klaviyo HTML templates — Free Pet Portrait Giveaway

Six standalone HTML emails, ready to import to Klaviyo.

## Files

| # | File | When it sends | Klaviyo trigger |
|---|---|---|---|
| 1 | `email-1-confirmation.html` | T+0 — instant | Metric `Contest Entry Submitted` |
| 2 | `email-2-boost.html` | T+24h | Same flow, after delay |
| 3 | `email-3-social-proof.html` | T+7d (conditional) | Same flow, after delay + split |
| 4 | `email-4-final-48h.html` | T-2d before draw_date | Same flow, wait-until-date |
| 5 | `email-5-winner.html` | After draw (manual) | Segment campaign, winner only |
| 6 | `email-6-consolation.html` | After draw (manual) | Segment campaign, non-winners |

## How to import to Klaviyo

1. Klaviyo → **Email → Templates → Create Template**
2. Choose **Code Editor** (not the drag-drop builder)
3. Paste the entire contents of one HTML file into the editor
4. Save → preview with a real profile that has the merge-tag properties
5. In your flow, attach this template to the corresponding email step

## Merge tags used

All from the `Contest Entry Submitted` event, fired by `/contest/entry`:

| Tag | Source | Example value |
|---|---|---|
| `{{ event.pet_name\|default:"your pet" }}` | Form input | `Biscuit` |
| `{{ event.preferred_style\|default:"watercolour" }}` | Style picker | `soft-watercolour` |
| `{{ event.pet_preview_url }}` | Generation result | `https://web-production-a392e.up.railway.app/preview/abc.webp` |
| `{{ event.contest_referral_code }}` | Backend-assigned | `BISC-7K2A` |
| `{{ event.contest_referred_by\|default:"" }}` | URL `?ref=` param | `BUDDY-3X1Q` |
| `{{ person.first_name\|default:"there" }}` | Klaviyo profile (only present if subscribed before) | `Gio` |

For emails 5 and 6 (winner / consolation) — these send via a one-shot **Campaign** (not flow), targeting Klaviyo segments. Klaviyo's `event.*` tags still resolve if you build the campaign with "Use most recent event of this metric" as the personalization source.

## Brand tokens used (consistent across all 6 emails)

| Token | Value | Used for |
|---|---|---|
| Background | `#FAF8F5` | Outer body |
| Surface | `#FFFFFF` | Email card |
| Primary ink | `#1C1C1C` | Headlines, body text |
| Muted ink | `#6B6B63` | Sub-copy, footers |
| Accent | `#8B7D6B` | Eyebrow text, secondary CTA |
| Success | `#2F4A35` | Entry confirmation badge |
| Border | `#E4DDD4` | Card outline |
| Heading font | `Cormorant Garamond` italic 500 + `Georgia` fallback | All H1/H2 |
| Body font | `Inter` 400/500 + `Helvetica, Arial` fallback | Everything else |
| Max content width | `600px` | Standard email width |

## Mobile / dark-mode notes

- All emails are single-column at ≤480px (table-based responsive).
- Background color `#FAF8F5` is light enough that automatic dark-mode inversion (Gmail iOS) tends to invert it to a dark canvas with the white card readable on top — acceptable degradation.
- No web fonts loaded over `@import` (slow + blocked in Outlook). Google Fonts via `<link rel="stylesheet">` is included but with proper fallbacks so the email is readable without it.
- All CTAs are bulletproof buttons (table-cell with padding) — render correctly in Outlook 2007–2019.

## Required Klaviyo footer block

Klaviyo automatically appends an unsubscribe footer. You don't need to add one. **Do not remove the merge tag `{% unsubscribe %}` that Klaviyo inserts.**

## Testing before going live

1. Hit `/contest/entry` once with your own email so a profile exists.
2. Klaviyo → flow → **Preview** → choose your profile.
3. Verify each merge tag resolves (not displayed as `{{ event.pet_name }}` literal).
4. Send a test send to your inbox — check spam folder if not visible.
5. Mobile test: open on your phone, scroll through.
6. Dark-mode test: enable dark mode on Gmail iOS, re-open.
