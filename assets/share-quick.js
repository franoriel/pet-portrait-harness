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
  const logoURL = root.dataset.logoUrl || '';

  // Default format is per-platform: IG share goes to Story (9:16),
  // everything else stays Feed (1:1) since it works universally.
  // ?format= can override.
  const explicitFormat = (params.get('format') || '').toLowerCase();
  let activeFormat = explicitFormat === 'story' ? 'story'
                  : explicitFormat === 'feed'  ? 'feed'
                  : (autoPlatform === 'ig' || autoPlatform === 'instagram') ? 'story'
                  : 'feed';

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
  const formatToggle = document.getElementById('sq-format-toggle');

  // Hidden canvas for compositing. Visible <img> displays the canvas as
  // a data URL so the layout doesn't shift while we re-render.
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');

  let portraitImage = null;   // The raw 3:4 portrait loaded from imgURL
  let brandLogoImage = null;  // Wordmark for Story top-of-frame
  let composedBlob = null;    // Latest composed PNG blob (for share/download)

  // Preload the wordmark — best-effort; Story still renders if it fails.
  if (logoURL) {
    const lg = new Image();
    lg.crossOrigin = 'anonymous';
    lg.onload = () => { brandLogoImage = lg; renderActiveFormat(); };
    lg.onerror = () => {};
    lg.src = logoURL;
  }

  // Load the raw portrait — kicks off render once decoded.
  if (imgURL) {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      portraitImage = img;
      renderActiveFormat();
      reflectFormatToggle();
    };
    img.onerror = () => {
      imgEl.style.display = 'none';
      imgFallback.style.display = 'block';
    };
    img.src = imgURL;
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

  // Compositing — mirrors drawFeed/drawStory in assets/share-funnel.js.
  // TODO: extract into a shared assets/portrait-compose.js if the
  // logic forks. For now duplicated to keep the surface small.
  function renderActiveFormat() {
    if (!portraitImage) return;
    if (activeFormat === 'story') {
      canvas.width = 1080; canvas.height = 1920;
      drawStory();
    } else {
      canvas.width = 1080; canvas.height = 1080;
      drawFeed();
    }
    try {
      imgEl.src = canvas.toDataURL('image/png');
    } catch (e) {
      // Tainted canvas (CORS) — fall back to showing the raw portrait
      imgEl.src = imgURL;
    }
    canvas.toBlob((blob) => { composedBlob = blob; }, 'image/png');
  }

  function drawFeed() {
    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = '#FAF8F5';
    ctx.fillRect(0, 0, w, h);

    const grd = ctx.createRadialGradient(w/2, h/2, w*0.2, w/2, h/2, w*0.7);
    grd.addColorStop(0, 'rgba(255,255,255,0)');
    grd.addColorStop(1, 'rgba(0,0,0,0.06)');
    ctx.fillStyle = grd;
    ctx.fillRect(0, 0, w, h);

    const pad = 80;
    drawCover(portraitImage, pad, pad, w - pad * 2, h - pad * 2);

    const padBottom = 56;
    ctx.textAlign = 'left';
    if (petName) {
      ctx.font = 'italic 600 64px "Cormorant Garamond", "Georgia", serif';
      ctx.fillStyle = '#1C1C1C';
      ctx.fillText(petName, pad, h - padBottom - 28);
    }
    ctx.font = '500 26px "Inter", system-ui, sans-serif';
    ctx.fillStyle = '#6B6B63';
    ctx.fillText(BRAND.handle, pad, h - padBottom + 14);

    ctx.textAlign = 'right';
    ctx.font = '500 22px "Inter", system-ui, sans-serif';
    ctx.fillStyle = '#8B7D6B';
    ctx.fillText(BRAND.domain, w - pad, h - padBottom + 14);
  }

  function drawStory() {
    const w = canvas.width, h = canvas.height;
    const grd = ctx.createLinearGradient(0, 0, 0, h);
    grd.addColorStop(0, '#F3EDE6');
    grd.addColorStop(0.55, '#E4DDD4');
    grd.addColorStop(1, '#D9CFC2');
    ctx.fillStyle = grd;
    ctx.fillRect(0, 0, w, h);

    const logoTop = 80;
    let postLogoY = logoTop;
    if (brandLogoImage && brandLogoImage.naturalWidth) {
      const logoW = 280;
      const logoH = logoW * (brandLogoImage.naturalHeight / brandLogoImage.naturalWidth);
      ctx.drawImage(brandLogoImage, (w - logoW) / 2, logoTop, logoW, logoH);
      postLogoY = logoTop + logoH;
    }

    const sideMargin = 40;
    const imgW = w - sideMargin * 2;
    const imgH = imgW * 4 / 3;
    const imgX = sideMargin;
    const imgY = postLogoY + 40;
    ctx.shadowColor = 'rgba(0,0,0,0.18)';
    ctx.shadowBlur = 40;
    ctx.shadowOffsetY = 14;
    drawCover(portraitImage, imgX, imgY, imgW, imgH, 24);
    ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;

    if (petName) {
      const nameBaselineY = imgY + imgH + 180;
      const maxNameWidth = w - 160;
      let fontPx = 180;
      ctx.font = `italic 500 ${fontPx}px "Cormorant Garamond", "Georgia", serif`;
      while (ctx.measureText(petName).width > maxNameWidth && fontPx > 80) {
        fontPx -= 8;
        ctx.font = `italic 500 ${fontPx}px "Cormorant Garamond", "Georgia", serif`;
      }
      ctx.textAlign = 'center';
      ctx.fillStyle = '#1C1C1C';
      ctx.fillText(petName, w / 2, nameBaselineY);
    }
  }

  function drawCover(img, x, y, w, h, radius) {
    const ir = img.width / img.height;
    const tr = w / h;
    let sx, sy, sw, sh;
    if (ir > tr) { sh = img.height; sw = sh * tr; sx = (img.width - sw) / 2; sy = 0; }
    else         { sw = img.width;  sh = sw / tr; sx = 0; sy = (img.height - sh) / 2; }
    if (radius) {
      ctx.save();
      roundRect(ctx, x, y, w, h, radius); ctx.clip();
      ctx.drawImage(img, sx, sy, sw, sh, x, y, w, h);
      ctx.restore();
    } else {
      ctx.drawImage(img, sx, sy, sw, sh, x, y, w, h);
    }
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath(); c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  function reflectFormatToggle() {
    if (!formatToggle) return;
    formatToggle.querySelectorAll('[data-format]').forEach(btn => {
      btn.classList.toggle('is-active', btn.dataset.format === activeFormat);
      btn.setAttribute('aria-selected', String(btn.dataset.format === activeFormat));
    });
  }

  if (formatToggle) {
    formatToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-format]');
      if (!btn) return;
      const next = btn.dataset.format;
      if (next === activeFormat) return;
      activeFormat = next;
      reflectFormatToggle();
      renderActiveFormat();
      track('share_format_switch', { format: activeFormat });
    });
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

  // Resolves to a Blob of the composed PNG (Feed or Story, whichever is
  // active). Falls back to fetching the raw portrait if compositing
  // hasn't run yet (image not loaded). Filename encodes pet + format
  // so users can tell the saved files apart.
  function composedFilename() {
    const petSlug = petName ? '-' + petName.toLowerCase().replace(/[^a-z0-9]/g, '-') : '';
    return `pet-portrait${petSlug}-${activeFormat}.png`;
  }
  async function getComposedBlob() {
    if (composedBlob) return composedBlob;
    // Compositing not done — re-render and wait for toBlob
    return new Promise((resolve) => {
      try {
        if (portraitImage) renderActiveFormat();
        if (composedBlob) { resolve(composedBlob); return; }
        canvas.toBlob((blob) => { composedBlob = blob; resolve(blob); }, 'image/png');
      } catch (e) { resolve(null); }
    });
  }

  // Image download — uses the composed canvas (Feed 1080² or Story 1080×1920).
  async function downloadImage() {
    track('share_image_downloaded', { ref: refCode, format: activeFormat });
    try {
      const blob = await getComposedBlob();
      if (!blob) throw new Error('no blob');
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = composedFilename();
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      showToast(`Saved · ${activeFormat === 'story' ? 'Story (9:16)' : 'Feed (1:1)'}`);
      return true;
    } catch (e) {
      // Fallback: fetch the raw and save what we have
      if (imgURL) {
        try {
          const r = await fetch(imgURL, { mode: 'cors' });
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = composedFilename();
          document.body.appendChild(a); a.click(); document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(url), 5000);
          showToast('Saved raw portrait (format compositing failed)');
          return true;
        } catch (_) {}
        window.open(imgURL, '_blank');
        showToast('Long-press the image to save it');
      }
      return false;
    }
  }

  // Native Web Share API (Level 2 — files). Shares the composed PNG so
  // iOS/Android share sheet can hand it to IG / Messenger / AirDrop
  // already formatted for the active surface.
  async function nativeShareWithFile() {
    if (!navigator.canShare || !navigator.share) return false;
    try {
      const blob = await getComposedBlob();
      if (!blob) return false;
      const file = new File([blob], composedFilename(), { type: 'image/png' });
      if (navigator.canShare({ files: [file] })) {
        await navigator.share({
          files: [file],
          title: petName ? `${petName} — pet portrait` : 'Pet portrait',
          text: caption,
        });
        track('share_completed', { method: 'web_share_api', ref: refCode, format: activeFormat });
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
