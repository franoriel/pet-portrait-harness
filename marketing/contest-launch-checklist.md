# Contest launch checklist — what Gio has to do

The funnel and contest mechanics are deployed. Code is no-op-safe when env vars are missing, so nothing will break — but the tracking and email automation only fire fully once these are configured.

## 1. Railway env vars (5 min)

In the Railway dashboard for the `pet-portrait-harness` service → Variables tab:

| Variable | Value | Required for |
|---|---|---|
| `KLAVIYO_API_KEY` | Private API key from Klaviyo → Account → API Keys → Create Private API Key (scopes: Profiles, Events — Read/Write) | Email capture, flow trigger |
| `KLAVIYO_CONTEST_LIST_ID` | `VySGe8` (default already in code — only set this if you create a dedicated contest list) | List membership |
| `META_PIXEL_ID` | The 15-digit Pixel ID. Find it in Meta Events Manager → Data sources → your Pixel | Server-side CAPI Lead |
| `META_CAPI_ACCESS_TOKEN` | Conversions API access token. Generate in Events Manager → Settings → Conversions API → Generate access token (system-user) | Server-side CAPI Lead |

After saving, Railway redeploys automatically. Hit `/health` after — log entry shouldn't show any `[meta-capi] skipped` errors after a test entry.

## 2. Klaviyo flow (15 min)

In Klaviyo → Flows → Create Flow → Build your own.

**Trigger:** Metric — `Contest Entry Submitted` (will appear in the dropdown after the first successful entry hits Klaviyo).

**Step 1 — Email immediately**
- Trigger: 0 minutes after metric
- Subject: `{{ event.pet_name|default:"Your pet" }}'s portrait is here ✨`
- Preview text: `High-res file + your entry confirmation inside`
- Body: pull `event.pet_preview_url` for the hero image, `event.contest_referral_code` for the share link, "Share for +5 entries" CTA back to the funnel result page

**Step 2 — Wait 24h**

**Step 3 — Email "boost your odds"**
- Subject: `One quick way to boost your odds`
- Body: refer-a-friend block with `event.contest_referral_code`

**Step 4 — Wait 7 days**

**Step 5 — Conditional: contest still open?**
- If yes → mid-contest update with a refer-link reminder
- If no → skip

**Step 6 — Wait until 48h before draw**

**Step 7 — "Last chance" email**

For the winner notification + consolation emails, create a separate flow triggered by a `Contest Winner Announced` metric you'll fire manually from a small admin route (or via Klaviyo's manual segment send). I haven't built that admin route — it's a Day 30 task.

## 3. Create the Shopify pages (5 min)

Two pages need to exist in admin:

**Page A — Free pet portrait (the funnel)**
- Online Store → Pages → already exists if you created it earlier. If not: Add page → Title "Free pet portrait" → Visibility: Visible → Theme template: `share-pet` → Save
- Final URL: `https://petprintables.ca/pages/free-pet-portrait`

**Page B — Contest rules**
- Online Store → Pages → Add page → Title "Contest rules" → Visibility: Visible → Theme template: `contest-rules` → Save
- Final URL: `https://petprintables.ca/pages/contest-rules`
- Then: Online Store → Customize → top dropdown → select the Contest Rules page → fill in: Sponsor name, **Sponsor mailing address** (required for the no-purchase-necessary mail-in path), Start date, End date, Draw date, ARV

The funnel's email-gate links to `/pages/contest-rules` — make sure that slug is set exactly (no `-1`, `-2` suffix from a duplicate).

## 4. Meta Pixel + sales channel sanity check (10 min)

- Meta Events Manager → Test Events tab → enter your dev URL `petprintables.ca/pages/free-pet-portrait`
- Walk through the funnel with a real email
- You should see: `PageView` (Pixel) + `Lead` (Pixel browser) + `Lead` (CAPI server) within ~60s
- If only the browser Lead fires and not the CAPI one, the `META_CAPI_ACCESS_TOKEN` is wrong or unset

For lookalike audiences (Week 2+):
- Audiences → Create custom audience → Website → Source: `Lead` event, last 30 days → name "Contest Entries"
- Then create lookalike: 1% Canada off that custom audience

## 5. Klaviyo SMS list (5 min, optional but high-leverage)

If you want the SMS bonus-entry mechanic to work end-to-end:
- Klaviyo → Audience → Lists & Segments → make sure SMS list `VySGe8` (or create new) accepts SMS subscriptions in Canada
- Make sure SMS consent flow is documented in Klaviyo's compliance settings (TCPA + CASL — Klaviyo has a Canadian template)

If you skip this, the SMS-consent checkbox still works (just stores the consent flag on the profile), it just won't actually send SMS. Probably fine for V1.

## 6. Test the full flow live (5 min)

After 1–4 are done:
- Visit `https://petprintables.ca/pages/free-pet-portrait?utm_source=test&ref=GIO-TEST`
- Complete the funnel with your personal email
- Confirm:
  - Email arrives in your inbox within ~60 seconds
  - Klaviyo profile shows: `pet_name`, `contest_referral_code`, `contest_referred_by=GIO-TEST`, `utm_source=test`, `sms_consent`
  - Meta Events Manager shows both browser + CAPI `Lead` events for your email
  - Your referral code appears as `<PET_SLUG>-<HASH>` on the share screen
- Then visit `https://petprintables.ca/pages/free-pet-portrait?ref=YOUR_CODE_FROM_TEST` and enter with a different email — confirm a `Contest Referral Earned` log line shows up in Railway

## 7. Launch when:

- [ ] All 4 env vars set in Railway
- [ ] Both pages live in Shopify with correct templates
- [ ] Contest rules page filled out with real dates, sponsor address, ARV
- [ ] Klaviyo flow built and `Live`
- [ ] Test entry submitted + email received + Meta events verified
- [ ] Meta ad set + creative loaded (use `marketing/contest-social-kit.md`)
- [ ] Bio link on Instagram updated to the funnel URL
- [ ] First social post + Story sequence scheduled (Phase 0 from the kit)

## Things I did NOT build (deliberately)

- **Admin route to declare a winner / send winner notification.** Day 30 task — easier to do via Klaviyo manual send the first time, then automate if the contest becomes recurring.
- **Bonus-entry verification.** Self-reported on the result screen. The contest rules already cover this ("subject to audit at draw"). At draw time, audit the IG hashtag + look up referrals by `contest_referred_by` in Klaviyo. For a first contest, this is fine.
- **Real referrer-entry attribution at the right Klaviyo profile.** Currently logs an intent line. To wire it properly, the Klaviyo flow needs a segment lookup by `contest_referral_code == event.contest_referred_by` and increment a custom property on that profile. Easier to do in Klaviyo's UI than in code — left for you.
- **Region detection / Quebec gate.** Relies on entrant self-reporting via skill test + rules acceptance. Standard for small Canadian contests; if entry volumes get big, add IP-region check.
