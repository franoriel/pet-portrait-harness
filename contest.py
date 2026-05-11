"""
Contest-entry plumbing for the Share Funnel.

Three responsibilities:
1. Capture a contest entry as a Klaviyo profile + custom event so the
   marketing flow (welcome email, 3-day nudge, final-24h nudge, winner
   notification) can fire.
2. Mirror the entry to Meta as a server-side Lead event via Conversions
   API with hashed PII, so Meta has enough match quality to seed a
   lookalike audience from this cohort (vs. the weaker browser-only
   Pixel signal).
3. Deterministic referral codes that travel in share captions and ride
   back as ?ref=BUDDY-7X2K when a friend lands on the page.

Env vars (graceful no-op when unset):
- KLAVIYO_API_KEY            private API key (same one used for order push)
- KLAVIYO_CONTEST_LIST_ID    list to add entries to (e.g. "VySGe8")
- META_PIXEL_ID              the same Pixel ID the sales channel uses
- META_CAPI_ACCESS_TOKEN     system-user token from Meta Business Manager
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

KLAVIYO_API_ROOT = "https://a.klaviyo.com/api"
META_CAPI_ROOT = "https://graph.facebook.com/v19.0"
CONTEST_EVENT_NAME = "Contest Entry Submitted"
BONUS_EVENT_NAME = "Contest Entries Earned"
REFERRAL_EVENT_NAME = "Contest Referral Earned"

# Per-bonus-type values. Single source of truth — front-end UI labels
# should match these. Anything not in this dict is rejected by /contest/earn.
BONUS_VALUES = {
    "story_share":  5,    # User self-attests they shared portrait to IG Story
    "ig_post_tag": 10,    # User self-attests they tagged 2 friends in an IG post
    "sms_consent":  2,    # Bonus for SMS opt-in (granted at entry time, not via /earn)
    "referral":     3,    # Earned per referee who completes /contest/entry
}


# ---------------------------------------------------------------------------
# Referral codes
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^A-Z0-9]")

def referral_code(pet_name: str, email: str) -> str:
    """Short, shareable, deterministic code.

    Pattern: <PET-SLUG-4>-<EMAIL-HASH-4>. Truncates pet slug to 4 chars so
    long names don't blow out the URL, and the email hash gives ~16M unique
    codes per pet slug — enough headroom that collisions in a single contest
    are effectively zero. Deterministic so a user re-entering the funnel
    sees the same code (and re-uses their share links).

    Example: pet "Biscuit", email "x@y.com" → "BISC-7K2A".
    """
    slug = _SLUG_RE.sub("", (pet_name or "PET").upper())[:4] or "PET"
    digest = hashlib.sha256((email or "").lower().strip().encode()).hexdigest()[:4].upper()
    return f"{slug}-{digest}"


# ---------------------------------------------------------------------------
# Klaviyo
# ---------------------------------------------------------------------------

def push_klaviyo_entry(
    *,
    email: str,
    pet_name: str,
    style_id: str,
    preview_url: str,
    code: str,
    referrer_code: Optional[str],
    sms_consent: bool,
    utm: dict,
) -> bool:
    """Create/update a Klaviyo profile and fire the contest event.

    Profile push happens first (so the event has somewhere to land). Then
    we POST the event so the Klaviyo flow (welcome → nudge → winner) can
    branch on `metric = Contest Entry Submitted`.

    Returns True on success, False on any failure (logged but never raises
    — the funnel must keep flowing even if Klaviyo is down).
    """
    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    list_id = os.environ.get("KLAVIYO_CONTEST_LIST_ID", "VySGe8").strip()
    if not api_key:
        log.warning("[klaviyo:contest] ✗ no KLAVIYO_API_KEY — skipping")
        return False

    headers = {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": "2024-10-15",
        "Content-Type": "application/json",
        "accept": "application/json",
    }

    properties = {
        "pet_name": pet_name or "",
        "preferred_style": style_id or "",
        "pet_preview_url": preview_url or "",
        "contest_referral_code": code,
        "contest_referred_by": referrer_code or "",
        "contest_entered_at": int(time.time()),
        "sms_consent": bool(sms_consent),
        "utm_source": utm.get("source", ""),
        "utm_medium": utm.get("medium", ""),
        "utm_campaign": utm.get("campaign", ""),
        "utm_content": utm.get("content", ""),
    }

    try:
        # 1. Upsert profile
        profile_body = {
            "data": {
                "type": "profile",
                "attributes": {"email": email, "properties": properties},
                "meta": {"patch_properties": {"append": {}}},
            }
        }
        r = requests.post(
            f"{KLAVIYO_API_ROOT}/profile-import",
            headers=headers, json=profile_body, timeout=8,
        )
        if r.status_code >= 400:
            log.warning("[klaviyo:contest] profile-import FAIL %s %s", r.status_code, r.text[:200])
            return False

        # 2. Fire the contest event (this is what triggers the Klaviyo flow)
        event_body = {
            "data": {
                "type": "event",
                "attributes": {
                    "properties": properties,
                    "metric": {"data": {"type": "metric", "attributes": {"name": CONTEST_EVENT_NAME}}},
                    "profile": {"data": {"type": "profile", "attributes": {"email": email}}},
                },
            }
        }
        r2 = requests.post(
            f"{KLAVIYO_API_ROOT}/events",
            headers=headers, json=event_body, timeout=8,
        )
        if r2.status_code >= 400:
            log.warning("[klaviyo:contest] event POST FAIL %s %s", r2.status_code, r2.text[:200])
            return False

        log.info("[klaviyo:contest] ✓ email=%s pet=%s code=%s", email, pet_name, code)
        return True
    except Exception as exc:
        log.warning("[klaviyo:contest] EXCEPTION %s", exc)
        return False


def _klaviyo_headers() -> dict:
    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    return {
        "Authorization": f"Klaviyo-API-Key {api_key}",
        "revision": "2024-10-15",
        "Content-Type": "application/json",
        "accept": "application/json",
    }


def lookup_email_by_referral_code(code: str) -> Optional[str]:
    """Find the email of the profile whose contest_referral_code matches.

    Used to attribute referral bonuses back to the original referrer.
    Returns None if no profile, no API key, or any error.
    """
    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    if not api_key or not code:
        return None
    try:
        # Klaviyo profile filter: bracket-notation for custom-property keys.
        filter_str = f'equals(properties["contest_referral_code"],"{code}")'
        r = requests.get(
            f"{KLAVIYO_API_ROOT}/profiles",
            headers=_klaviyo_headers(),
            params={"filter": filter_str, "page[size]": 1},
            timeout=8,
        )
        if r.status_code >= 400:
            log.warning("[klaviyo:lookup] FAIL %s %s", r.status_code, r.text[:200])
            return None
        items = (r.json() or {}).get("data") or []
        if not items:
            return None
        email = (items[0].get("attributes") or {}).get("email")
        return email
    except Exception as exc:
        log.warning("[klaviyo:lookup] EXCEPTION %s", exc)
        return None


def fire_klaviyo_event(
    *, email: str, metric_name: str, properties: dict,
) -> bool:
    """Generic helper — fires a Klaviyo metric event on a given profile."""
    api_key = os.environ.get("KLAVIYO_API_KEY", "").strip()
    if not api_key or not email:
        return False
    try:
        body = {
            "data": {
                "type": "event",
                "attributes": {
                    "properties": properties,
                    "metric": {"data": {"type": "metric", "attributes": {"name": metric_name}}},
                    "profile": {"data": {"type": "profile", "attributes": {"email": email}}},
                },
            }
        }
        r = requests.post(
            f"{KLAVIYO_API_ROOT}/events",
            headers=_klaviyo_headers(), json=body, timeout=8,
        )
        if r.status_code >= 400:
            log.warning("[klaviyo:event] %s FAIL %s %s", metric_name, r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:
        log.warning("[klaviyo:event] %s EXCEPTION %s", metric_name, exc)
        return False


def record_bonus_claim(
    *, email: str, bonus_type: str, proof_url: str = "",
) -> tuple[bool, int]:
    """Record a self-attested bonus claim (story_share / ig_post_tag).

    Fires a `Contest Entries Earned` event on the entrant's profile. The
    event property `entries_earned` carries the bonus value so Klaviyo
    can sum per profile. Returns (success, bonus_value).

    Note: dedup (one claim per bonus_type per profile) is enforced by
    the segment query at draw time, not at write time — Klaviyo doesn't
    cheaply support write-time uniqueness. The audit log captures every
    attempt with timestamp.
    """
    if bonus_type not in BONUS_VALUES or bonus_type == "referral":
        return False, 0
    bonus = BONUS_VALUES[bonus_type]
    ok = fire_klaviyo_event(
        email=email,
        metric_name=BONUS_EVENT_NAME,
        properties={
            "bonus_type": bonus_type,
            "entries_earned": bonus,
            "proof_url": proof_url or "",
            "claimed_at": int(time.time()),
        },
    )
    log.info("[contest:bonus] email=%s type=%s bonus=%d ok=%s", email, bonus_type, bonus, ok)
    return ok, bonus


def credit_referrer(referrer_code: str, referee_email: str, referee_pet_name: str) -> bool:
    """Attribute a +N entry to the referrer when their referee enters.

    Looks up the referrer's email by their referral code, then fires a
    `Contest Referral Earned` event on that profile. Idempotent at the
    event level — Klaviyo dedupes events with identical properties +
    same minute, so accidental double-fires are absorbed.
    """
    if not referrer_code:
        return False
    referrer_email = lookup_email_by_referral_code(referrer_code)
    if not referrer_email:
        log.info("[contest:refer] code=%s no profile found (may not exist yet)", referrer_code)
        return False
    ok = fire_klaviyo_event(
        email=referrer_email,
        metric_name=REFERRAL_EVENT_NAME,
        properties={
            "entries_earned": BONUS_VALUES["referral"],
            "referee_email": referee_email,
            "referee_pet_name": referee_pet_name,
            "credited_at": int(time.time()),
        },
    )
    log.info("[contest:refer] code=%s referrer=%s referee=%s ok=%s",
             referrer_code, referrer_email, referee_email, ok)
    return ok


# ---------------------------------------------------------------------------
# Meta Conversions API (server-side Lead)
# ---------------------------------------------------------------------------

def _sha256_lower(value: str) -> str:
    return hashlib.sha256((value or "").lower().strip().encode("utf-8")).hexdigest()


def send_meta_capi_lead(
    *,
    email: str,
    pet_name: str,
    style_id: str,
    client_ip: str,
    user_agent: str,
    event_source_url: str,
    fbc: str = "",
    fbp: str = "",
) -> bool:
    """Server-side Lead event via Meta Conversions API.

    Browser-only Pixel events match ~40-60% of users in Meta's audience.
    Adding a server-side event with hashed email + IP + user-agent + fbc/fbp
    cookies typically lifts match quality to 70-90%, which materially
    improves lookalike-audience seed quality.

    Returns True on success.
    """
    pixel_id = os.environ.get("META_PIXEL_ID", "").strip()
    access_token = os.environ.get("META_CAPI_ACCESS_TOKEN", "").strip()
    if not pixel_id or not access_token:
        log.info("[meta-capi] skipped — META_PIXEL_ID or META_CAPI_ACCESS_TOKEN unset")
        return False

    user_data = {
        "em": [_sha256_lower(email)],
        "client_ip_address": client_ip or "",
        "client_user_agent": user_agent or "",
    }
    if fbc: user_data["fbc"] = fbc
    if fbp: user_data["fbp"] = fbp

    payload = {
        "data": [{
            "event_name": "Lead",
            "event_time": int(time.time()),
            "action_source": "website",
            "event_source_url": event_source_url or "",
            "user_data": user_data,
            "custom_data": {
                "content_name": "contest_entry",
                "content_category": "share_funnel",
                "currency": "CAD",
                "value": 0,
                "pet_name": pet_name or "",
                "style_id": style_id or "",
            },
        }]
    }
    try:
        r = requests.post(
            f"{META_CAPI_ROOT}/{pixel_id}/events",
            params={"access_token": access_token},
            json=payload, timeout=8,
        )
        if r.status_code >= 400:
            log.warning("[meta-capi] Lead FAIL %s %s", r.status_code, r.text[:200])
            return False
        log.info("[meta-capi] ✓ Lead email=%s pet=%s", email, pet_name)
        return True
    except Exception as exc:
        log.warning("[meta-capi] EXCEPTION %s", exc)
        return False


# ---------------------------------------------------------------------------
# Skill-testing question — Canadian giveaway compliance
# ---------------------------------------------------------------------------

# Deterministic per-day so a user reloading the page sees the same question
# but a cron-style rotation makes scraping/scripted entries less attractive.
SKILL_TEST_QUESTIONS = [
    ("12 + 18 - 5", 25),
    ("(3 + 4) × 2", 14),
    ("9 × 3 + 2", 29),
    ("(20 / 4) + 7", 12),
    ("8 + 7 × 2", 22),
    ("(5 × 4) + 9 - 3", 26),
    ("17 - 3 + 6", 20),
]

def todays_skill_test() -> tuple[str, int]:
    idx = (int(time.time()) // 86400) % len(SKILL_TEST_QUESTIONS)
    return SKILL_TEST_QUESTIONS[idx]


def validate_skill_test(answer: str) -> bool:
    """Loose validation — anything that parses to the day's expected int."""
    _, expected = todays_skill_test()
    try:
        return int(str(answer).strip()) == expected
    except (TypeError, ValueError):
        return False
