# Klaviyo flow — Free Pet Portrait Giveaway

**Trigger metric:** `Contest Entry Submitted` (fires automatically from `/contest/entry` once Railway env vars are set).

**Klaviyo flow structure:**

```
[Trigger: Contest Entry Submitted]
  ↓
  [Email 1 — Instant confirmation]            (delay: 0 min)
  ↓
  [Wait 24 hours]
  ↓
  [Email 2 — Boost your odds]
  ↓
  [Wait 5 days]   ← skip block if contest period < 2 weeks
  ↓
  [Conditional: contest still open?]
    Yes → [Email 3 — Mid-contest social proof]
    No  → exit
  ↓
  [Wait until 48h before draw_date]
  ↓
  [Email 4 — Final 48h]
  ↓
  [Wait until draw_date + 1h]
  ↓
  [Exit flow — winner / loser flow handled by manual segment send, see bottom]
```

**Merge tags used throughout:**
- `{{ event.pet_name|default:"your pet" }}` — e.g. "Biscuit"
- `{{ event.contest_referral_code }}` — e.g. "BISC-7K2A"
- `{{ event.pet_preview_url }}` — the watermarked WebP preview
- `{{ event.preferred_style }}` — e.g. "soft-watercolour"
- `{{ person.first_name|default:"Hi" }}` — only present if SMS consent gave it

**Brand colors for the template** (use as Klaviyo theme):
- Background: `#FAF8F5` · Card: `#FFFFFF` · Border: `#E4DDD4`
- Primary text: `#1C1C1C` · Muted text: `#6B6B63` · Accent: `#8B7D6B` · Success: `#2F4A35`
- Headline font: `Cormorant Garamond` italic 500 (Google Fonts) — fallback `Georgia, serif`
- Body font: `Inter` 400/500 (Google Fonts) — fallback `Helvetica, Arial, sans-serif`

**Universal footer** (use Klaviyo's saved footer block):
> Pet Printables · Made in Canada
> You're receiving this because you entered the Free Pet Portrait Giveaway. [Unsubscribe]({{ unsubscribe_link }}) · [View in browser]({{ view_in_browser_link }})

---

## Email 1 — Instant confirmation (T + 0 min)

**Subject:** `You're in 🤍 {{ event.pet_name|default:"your pet" }}'s portrait is ready`

**Preview text:** `Your entry is confirmed. Here's how to bump it.`

**Hero image:** `{{ event.pet_preview_url }}` — full width, centered, max 600px. Alt text: "Your generated pet portrait"

**Body:**

> # You're in.
>
> {{ event.pet_name|default:"Your pet" }}'s portrait is yours, and your entry to win the canvas version is locked in.
>
> **Your referral code:** `{{ event.contest_referral_code }}`
> Every friend who enters with your link adds 3 entries to your name.
>
> [📷 Share your portrait + grab more entries →]({{ event.event_source_url|default:"https://petprintables.ca/pages/free-pet-portrait" }})
>
> Draw: **[draw_date]** · One winner, picked at random.

**Below the hero CTA, a 3-column entry-boost grid:**

| +5 entries | +3 entries each | +10 entries |
|---|---|---|
| Share to IG Story tagging `@petprintables` | Refer a friend who enters | Tag 2 friends in an IG post `#petprintables` |

**Footer block:**
> Want the canvas now instead of waiting? Code `SHARE15` takes 15% off — printed on canvas, gallery-wrapped, shipped from Canada.

---

## Email 2 — Boost your odds (T + 24h)

**Subject:** `One quick way to boost your odds, {{ person.first_name|default:"friend" }}`

**Preview text:** `You have 1 entry. Here's how to get to 19.`

**Body:**

> # Right now you have **1 entry**. Here's how to get to **19**.
>
> The draw is one canvas, one winner — but every action below stacks on your name.
>
> ### Three things, three minutes
>
> **1 · Share your portrait to your Story — +5**
> Save the file and post it with `@petprintables` tagged. We're watching the tag for proof.
>
> **2 · Tag two friends in an Instagram post — +10**
> Use `#petprintables` so we can find it.
>
> **3 · Share your referral link — +3 per friend who enters**
> Your link: `https://petprintables.ca/pages/free-pet-portrait?ref={{ event.contest_referral_code }}`
> No cap on referrals. We've seen people stack 50+ entries this way.
>
> [Open my share page →]({{ event.event_source_url|default:"https://petprintables.ca/pages/free-pet-portrait" }})

**P.S. line:**
> P.S. Still have your portrait? It's the file you generated yesterday — same one we'd print on the canvas. Open the link above to download it again.

---

## Email 3 — Mid-contest social proof (T + 7d, conditional)

Send only if contest period is longer than 2 weeks. Use Klaviyo's split: if `today - event.contest_entered_at < contest_end_date - 5 days`, send; else skip.

**Subject:** `What other people made with their pets`

**Preview text:** `A few favourites from this week's entries.`

**Body:**

> # A glimpse from the entries so far.
>
> No prices, no pitches — just a few of the portraits people made this week. Some of them tagged us; we asked permission to share.

**Then: a 2×3 grid of 6 anonymized portrait thumbnails.** Pull these from Klaviyo's image library after you hand-pick 6 of the best entries from the week. Each thumbnail should be a 200×266 (3:4) crop with pet head visible.

> ### Your portrait is still on the wall list
>
> {{ event.pet_name|default:"Your pet" }} is one of them. Bump your entry count if you haven't yet — every share earns more.
>
> [Get more entries →](https://petprintables.ca/pages/free-pet-portrait?ref={{ event.contest_referral_code }})
>
> ### Draw date
> **[draw_date]** at 12:00 PM ET. One winner. Random.

**P.S. line:**
> P.S. If you don't want a chance at the canvas — totally fine. The free portrait is yours forever, no follow-up emails about it from us after this contest closes.

---

## Email 4 — Final 48h (T-2d before draw)

**Subject:** `48 hours. One canvas. {{ event.pet_name|default:"Your pet" }}'s name in the draw.`

**Preview text:** `Draw is [draw_date]. Last chance to stack entries.`

**Body:**

> # 48 hours left.
>
> The draw is **[draw_date] at 12:00 PM ET**. After that, the canvas ships to one of the names in the pool.
>
> You're already in with **1+ entry**. Here's the fastest way to stack more before the cutoff:
>
> ### Your referral link
> `https://petprintables.ca/pages/free-pet-portrait?ref={{ event.contest_referral_code }}`
>
> Every friend who enters with this link adds **3 entries** to your name. No cap.
>
> Or share your portrait to IG Story tagging `@petprintables` for **+5**.
>
> [Open my share page →]({{ event.event_source_url|default:"https://petprintables.ca/pages/free-pet-portrait" }})

**Below the CTA, three-step countdown reminder:**

> **T-48h** · Now
> **T-24h** · Tomorrow, same time
> **T-0** · Draw + winner email

**P.S. line:**
> P.S. If you'd rather not wait for the draw — `SHARE15` takes 15% off the canvas, shipped from Canada in 5–10 days.

---

## Winner / non-winner emails (separate flow, manual trigger)

These don't sit in the entry-confirmation flow because they fire **once per entrant after the draw**, not on a 24h delay. Build them as a one-shot Klaviyo campaign sent to two segments:

- **Segment A:** profile with `contest_referral_code == [winning_code]` (this is the winner — should be exactly 1 profile)
- **Segment B:** profile in list `KLAVIYO_CONTEST_LIST_ID` and NOT in Segment A (everyone else)

You'll know the winner from a random draw against the entries — either do it manually in Klaviyo (Audience → Segments → Contest Entries → pick a random row) or run `python -c "..."` against the Klaviyo profile export with `random.choice`.

---

### Email 5 — Winner (Segment A only)

**Subject:** `🤍 You won the canvas — {{ event.pet_name }}'s going on the wall`

**Preview text:** `Quick skill-testing question + shipping confirmation inside`

**Body:**

> # You won.
>
> Out of [total_entries] entries across [total_entrants] people, the random draw picked **you**. {{ event.pet_name|default:"Your pet" }}'s portrait is heading to canvas — printed on canvas, gallery-wrapped, shipped from Canada.
>
> ### One quick thing
> Canadian giveaway rules require you to correctly answer a skill-testing question to claim the prize. Reply to this email with the answer to:
>
> **What is (15 + 7) × 2 - 4?**
>
> *(Solve it yourself, no calculator. Be honest.)*
>
> ### Then we'll need
> 1. Your full shipping address (Canadian only, no PO box if avoidable)
> 2. Your preferred canvas size — 16×20 standard, or upgrade to 20×24 / 24×30 if you want to cover the extra production cost
> 3. Confirmation we can use the portrait you generated on **[entry_date]** as the print file
>
> Reply within **7 days of this email** or we'll redraw. Don't make us redraw.
>
> — Gio

---

### Email 6 — Non-winners consolation (Segment B)

**Subject:** `The draw's done. Your portrait isn't going anywhere.`

**Preview text:** `Code SHARE15 for 15% off if you want to skip the next one.`

**Body:**

> # The draw is done.
>
> One canvas, one winner — but the portrait you made of {{ event.pet_name|default:"your pet" }} is still yours, free, forever.
>
> If you want it on the wall instead of waiting for the next giveaway, use **`SHARE15`** for 15% off your first canvas. Same portrait you generated, printed on canvas, gallery-wrapped, shipped from Canada in 5–10 days.
>
> [Get my portrait on canvas →](https://petprintables.ca/pages/create?ref={{ event.contest_referral_code }})
>
> ### Next giveaway
> We'll run another one this season. If you want first dibs (and a bonus entry just for being in this round), stay subscribed — we'll email you when it opens.

**P.S. line:**
> P.S. The portrait file we generated for {{ event.pet_name }} is still accessible. Forwarded the high-res to the email you entered with — check spam if you don't see it, and reply here if it's missing.

---

## Klaviyo template build tips

1. **Use Klaviyo's template designer, not raw HTML import.** It handles dark-mode previews, mobile rendering, and the unsubscribe footer correctly.
2. **Hero image:** drop a placeholder, then set the URL field to `{{ event.pet_preview_url }}` — Klaviyo's previewer will render it for events with non-null preview_url; for designer previews it'll show a broken image, which is normal.
3. **Personalization preview:** Klaviyo → flow → preview → choose a recent profile that has `contest_referral_code` set. If `event.X` tags don't resolve, the metric history on that profile is empty — pick a different profile.
4. **Send-time A/B test (optional):** subject line A "You're in 🤍 ..." vs subject line B "Your entry is locked in: ..." — Klaviyo will auto-pick the winner after 4h on Email 1.
5. **CTR target benchmarks** (industry, not promised numbers): Email 1 open ≥45%, click ≥15%. Email 4 open ≥35%, click ≥10%. Anything dramatically below is a deliverability / DMARC issue, not a copy issue.
6. **Sender:** use `hello@petprintables.ca` not a noreply address. Set Reply-To to the same — winner email 5 specifically expects replies.

## Quick QA before turning the flow live

- [ ] All four emails open in mobile + desktop preview without broken merge tags
- [ ] The referral link `?ref={{ event.contest_referral_code }}` resolves to a real URL in preview mode
- [ ] Unsubscribe link works (Klaviyo handles this automatically — just confirm it's in the footer)
- [ ] Sender name reads "Pet Printables" not "Pet Printables Inc." or your personal name
- [ ] All four emails have a plain-text version (Klaviyo generates this automatically — eyeball it once)
- [ ] DMARC/SPF/DKIM all green in Klaviyo → Settings → Email → Domains (this is a one-time setup; if any are red, fix before launch)
- [ ] Flow is `Live`, not `Draft`
- [ ] Send a test entry through the funnel with your own email — Email 1 should land in your inbox within 60 seconds
