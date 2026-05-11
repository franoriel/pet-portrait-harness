/* Share Quick — handles the mobile share-out flow from Klaviyo emails.
 *
 * URL params:
 *   ?img=<encoded portrait URL>  — the user's portrait file
 *   ?ref=<referral code>         — their contest referral code
 *   ?pet=<encoded pet name>      — pet name for caption + headline
 *   ?platform=<auto-route>       — optional: ig|fb|wa|x|copy|system
 *
 * When `platform` is set, the corresponding action fires immediately
 * on page load so the user lands directly in the right app/dialog
 * (one-tap from email button → share dialog).
 */
(function () {
  'use strict';

  const root = document.getElementById('sq-root');
  if (!root) return;

  const BRAND = {
    handle:  root.dataset.brandHandle  || '@petprintables',
    hashtag: root.dataset.brandHashtag || '#petprintables',
    domain:  root.dataset.brandDomain  || 'petprintables.ca',
  };

  const params = new URLSearchParams(window.location.search);
  const imgURL  = params.get('img')      || '';
  const refCode = (params.get('ref')     || '').toUpperCase().slice(0, 20);
  const petName = (params.get('pet')     || '').slice(0, 40);
  const autoPlatform = (params.get('platform') || '').toLowerCase();

  // Build the share URLs from these inputs ------------------------------------

  // The funnel URL is what we want friends to land on (with the entrant's
  // referral code so the referrer gets credit). The portrait image URL is
  // what the entrant attaches to their post manually.
  const funnelURL = `https://${BRAND.domain}/pages/free-pet-portrait${refCode ? `?ref=${encodeURIComponent(refCode)}` : ''}`;

  const petLabel = petName || 'my pet';
  const caption = `Made ${BRAND.handle} turn ${petLabel} into a portrait — get yours free at ${funnelURL} ${BRAND.hashtag}`;

  // Paint the page -----------------------------------------------------------

  document.querySelectorAll('[data-pet-name]').forEach(el => {
    el.textContent = petName || 'your pet';
  });

  const imgEl = document.getElementById('sq-image');
  const imgFallback = document.getElementById('sq-image-fallback');
  if (imgURL) {
    imgEl.src = imgURL;
    imgEl.onerror = () => {
      imgEl.style.display = 'none';
      imgFallback.style.display = 'block';
    };
  } else {
    imgEl.style.display = 'none';
    imgFallback.style.display = 'block';
  }

  const captionEl = document.getElementById('sq-caption-text');
  if (captionEl) captionEl.textContent = caption;

  // Tracking — funnel telemetry helper from layout/theme.liquid
  const track = (name, props) => {
    try { (window.pp_track || function(){})(name, props || {}); } catch (e) {}
  };
  track('share_page_view', { ref: refCode, has_platform_hint: !!autoPlatform });

  // Toast helper -------------------------------------------------------------

  const toast = document.getElementById('sq-toast');
  let toastTimer = null;
  function showToast(msg, ms) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('is-visible');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('is-visible'), ms || 2200);
  }

  // Clipboard copy of caption
  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(caption);
      showToast('Caption copied');
      track('share_caption_copied', { ref: refCode });
      const btn = root.querySelector('[data-action="copy-caption"]');
      if (btn) {
        btn.textContent = 'Copied ✓';
        btn.classList.add('is-copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('is-copied'); }, 2200);
      }
      return true;
    } catch (e) {
      // Fallback for older Safari / non-secure contexts
      const textarea = document.createElement('textarea');
      textarea.value = caption;
      textarea.style.position = 'fixed'; textarea.style.left = '-9999px';
      document.body.appendChild(textarea);
      textarea.select();
      try { document.execCommand('copy'); showToast('Caption copied'); return true; }
      catch (e2) { showToast('Could not copy — long-press to select'); return false; }
      finally { document.body.removeChild(textarea); }
    }
  }

  // Image download
  async function downloadImage() {
    if (!imgURL) { showToast('No image to save'); return false; }
    track('share_image_downloaded', { ref: refCode });
    try {
      const r = await fetch(imgURL, { mode: 'cors' });
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pet-portrait${petName ? '-' + petName.toLowerCase().replace(/[^a-z0-9]/g, '-') : ''}.png`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      showToast('Saved to device');
      return true;
    } catch (e) {
      // Cross-origin fallback — open in a new tab so user can long-press save
      window.open(imgURL, '_blank');
      showToast('Long-press the image to save it');
      return false;
    }
  }

  // Native Web Share API (Level 2 — files)
  async function nativeShareWithFile() {
    if (!navigator.canShare || !navigator.share || !imgURL) return false;
    try {
      const r = await fetch(imgURL, { mode: 'cors' });
      const blob = await r.blob();
      const file = new File([blob], 'pet-portrait.png', { type: blob.type || 'image/png' });
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({
          files: [file],
          title: petName ? `${petName} — pet portrait` : 'Pet portrait',
          text: caption,
        });
        track('share_completed', { method: 'web_share_api', ref: refCode });
        return true;
      }
    } catch (e) { /* user cancelled or share blocked */ }
    return false;
  }

  // Platform handlers --------------------------------------------------------

  async function shareInstagram() {
    // No URL-based composer for Instagram. The realistic flow:
    // 1. copy caption to clipboard, 2. download image so it's on device,
    // 3. deep-link into Instagram. After that the user picks Story vs
    // Feed inside the app and pastes the caption.
    await copyCaption();
    await downloadImage();
    track('share_clicked', { platform: 'instagram', ref: refCode });

    // Try the IG mobile app deep link first. On desktop or if IG isn't
    // installed, this just no-ops and the user sees the success toast.
    showToast('Caption copied · saving image · opening Instagram');
    setTimeout(() => {
      // instagram://library?LocalIdentifier=... opens the share sheet with
      // the most recently saved image on iOS. On Android, instagram://camera
      // opens the camera/composer. We try both with a fallback.
      const isAndroid = /Android/i.test(navigator.userAgent);
      const link = isAndroid ? 'intent://share#Intent;package=com.instagram.android;scheme=https;end' : 'instagram://library';
      window.location.href = link;
      // Final fallback after a moment — open Instagram in a new tab on web
      setTimeout(() => {
        if (document.visibilityState === 'visible') {
          window.open('https://www.instagram.com/', '_blank');
        }
      }, 1200);
    }, 600);
  }

  async function shareFacebook() {
    // Facebook sharer URL — shares the funnel link. The link preview
    // pulls Open Graph tags from /pages/free-pet-portrait. The portrait
    // image isn't attached directly via this URL (Meta doesn't support
    // that). To attach the actual image, user would need the Save +
    // open FB flow — but the LINK is what we want spreading anyway.
    track('share_clicked', { platform: 'facebook', ref: refCode });
    const target = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(funnelURL)}&quote=${encodeURIComponent(caption)}`;
    window.open(target, '_blank', 'noopener,width=600,height=600');
  }

  function shareWhatsApp() {
    track('share_clicked', { platform: 'whatsapp', ref: refCode });
    const target = `https://wa.me/?text=${encodeURIComponent(caption)}`;
    window.open(target, '_blank', 'noopener');
  }

  function shareX() {
    track('share_clicked', { platform: 'x', ref: refCode });
    // X (Twitter) intent. Text includes the link so the auto-card preview
    // will pull from OG tags on the funnel page.
    const target = `https://twitter.com/intent/tweet?text=${encodeURIComponent(caption)}`;
    window.open(target, '_blank', 'noopener,width=600,height=600');
  }

  async function shareSystem() {
    track('share_clicked', { platform: 'system', ref: refCode });
    // First try Web Share Level 2 (files) — opens iOS/Android share sheet
    // with IG, FB Messenger, AirDrop, Messages, etc. all available.
    const ok = await nativeShareWithFile();
    if (ok) return;
    // Fallback for desktop / older browsers — copy caption + image URL.
    await copyCaption();
    if (imgURL) {
      try { window.open(imgURL, '_blank'); } catch (e) {}
    }
    showToast('Caption copied. Image opened in new tab.');
  }

  // Button bindings ----------------------------------------------------------

  root.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-action]');
    if (!btn) return;
    e.preventDefault();
    const action = btn.dataset.action;
    switch (action) {
      case 'copy-caption': copyCaption(); break;
      case 'download':        downloadImage(); break;
      case 'share-instagram': shareInstagram(); break;
      case 'share-facebook':  shareFacebook(); break;
      case 'share-whatsapp':  shareWhatsApp(); break;
      case 'share-x':         shareX(); break;
      case 'share-system':    shareSystem(); break;
    }
  });

  // Auto-route from ?platform= ----------------------------------------------

  if (autoPlatform) {
    const dispatch = {
      'ig': shareInstagram, 'instagram': shareInstagram,
      'fb': shareFacebook,  'facebook':  shareFacebook,
      'wa': shareWhatsApp,  'whatsapp':  shareWhatsApp,
      'x':  shareX,         'twitter':   shareX,
      'system': shareSystem, 'native': shareSystem,
    };
    const fn = dispatch[autoPlatform];
    if (fn) setTimeout(fn, 300); // small delay so the page paints first
  }
})();
