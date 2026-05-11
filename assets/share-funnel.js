/* Share Funnel — top-of-funnel pet portrait generator.
 *
 * Hits the same /generate + /status endpoints as portrait-flow.js, then
 * renders the raw portrait into Feed (1080x1080) and Story (1080x1920)
 * compositions client-side using <canvas>. Wires Web Share API where
 * available, falls back to <a download>. Fires tracking via window.pp_track
 * (defined in layout/theme.liquid) — events fuel Meta lookalike audiences
 * and GA4 conversion paths.
 */
(function () {
  'use strict';

  const root = document.getElementById('sf-root');
  if (!root) return;

  const API = (root.dataset.api || '').replace(/\/$/, '');
  const TURNSTILE_KEY = root.dataset.turnstileKey || '';
  const BRAND = {
    hashtag: root.dataset.brandHashtag || '#petprintables',
    handle:  root.dataset.brandHandle  || '@petprintables',
    domain:  root.dataset.brandDomain  || 'petprintables.ca',
  };

  // Style list — kept in sync with assets/portrait-flow.js STYLES + the
  // Flask /generate whitelist. Background defaults are deliberately
  // chosen so the funnel never has to surface a palette picker.
  const STYLES = [
    { id: 'soft-watercolour',    name: 'Watercolour',    img: 'example-soft-watercolour.webp',    background: 'auto'  },
    { id: 'modern-shape-art',    name: 'Modern',         img: 'example-modern-shape-art-v2.webp', background: 'clay'  },
    { id: 'bold-graphic-poster', name: 'Bold Poster',    img: 'example-bold-graphic-poster.webp', background: 'teal'  },
    { id: 'minimal-line-art',    name: 'Line Art',       img: 'example-minimal-line-art.webp',    background: 'auto'  },
    { id: 'neon-pop-art',        name: 'Neon Pop',       img: 'example-neon-pop-art.webp',        background: 'auto'  },
    { id: 'charcoal',            name: 'Charcoal',       img: 'example-charcoal.webp',            background: 'auto'  },
    { id: 'renaissance-royalty', name: 'Renaissance',    img: 'example-renaissance-royalty.webp', background: 'auto'  },
    { id: 'aura-gradient',       name: 'Aura',           img: 'example-aura-gradient.webp',       background: 'auto'  },
  ];

  // Resolve example-image base path off this script's own URL so it works
  // whether served by Shopify (with ?v= cache buster) or the standalone
  // preview HTML. Matches the heuristic in portrait-flow.js.
  const _selfScript = document.querySelector('script[src*="share-funnel"]');
  const _selfSrc = _selfScript ? _selfScript.src : '';
  const ASSET_BASE = _selfSrc ? _selfSrc.replace(/share-funnel[^/?]*([?][^/]*)?$/, '') : '';
  const CACHE_BUST = ((_selfSrc.match(/[?&]v=([^&]+)/) || [])[1] || '');
  const QS = CACHE_BUST ? `?v=${CACHE_BUST}` : '';

  const track = (name, props) => {
    try { (window.pp_track || function(){})(name, props || {}); } catch (e) {}
  };

  /* ── State ───────────────────────────────────────────────── */
  const state = {
    petName: '',
    photoFile: null,
    photoDataURL: null,
    styleId: null,
    jobId: null,
    portraitURL: null,
    portraitImage: null,
    format: 'feed', // 'feed' or 'story'
    composedBlob: null,
    composedDataURL: null,
  };

  /* ── DOM helpers ─────────────────────────────────────────── */
  const $ = (sel, ctx) => (ctx || root).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || root).querySelectorAll(sel));
  const steps = $$('.sf-step');
  const showStep = (name) => {
    steps.forEach(el => el.classList.toggle('is-active', el.dataset.step === name));
    window.scrollTo({ top: root.getBoundingClientRect().top + window.scrollY - 20, behavior: 'smooth' });
    track('funnel_step_view', { step: name });
  };

  /* ── Tracking on initial section view ────────────────────── */
  track('funnel_view', { funnel: 'share_pet' });
  track('ViewContent', { content_name: 'share_pet_funnel', content_category: 'funnel' });

  /* ── Step 0 → 1: hero CTA ────────────────────────────────── */
  $('[data-action="start"]').addEventListener('click', () => {
    track('funnel_hero_cta', {});
    showStep('upload');
    mountTurnstile();
  });

  /* ── Step 1: upload form ─────────────────────────────────── */
  const petNameInput = $('#sf-pet-name');
  const photoInput = $('#sf-photo');
  const dropZone = $('.sf-drop');
  const dropPreview = $('.sf-drop__preview');
  const termsCheckbox = $('#sf-terms');
  const toStyleBtn = $('[data-action="to-style"]');
  const uploadError = $('[data-error="upload"]');

  const validateUpload = () => {
    const ok = !!(state.photoFile && termsCheckbox.checked);
    toStyleBtn.disabled = !ok;
  };

  petNameInput.addEventListener('input', (e) => {
    state.petName = e.target.value.trim();
    $$('[data-pet-name]').forEach(el => el.textContent = state.petName || 'your pet');
  });

  const handlePhoto = (file) => {
    if (!file) return;
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) {
      uploadError.textContent = 'Please upload a JPG, PNG, or WebP image.';
      uploadError.classList.remove('sf-hidden');
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      uploadError.textContent = 'Image too large (max 20 MB).';
      uploadError.classList.remove('sf-hidden');
      return;
    }
    uploadError.classList.add('sf-hidden');
    state.photoFile = file;
    const reader = new FileReader();
    reader.onload = (e) => {
      state.photoDataURL = e.target.result;
      dropPreview.src = e.target.result;
      dropZone.classList.add('is-filled');
      validateUpload();
    };
    reader.readAsDataURL(file);
    track('funnel_photo_uploaded', { size_kb: Math.round(file.size / 1024) });
    track('Lead', { content_category: 'funnel', content_name: 'photo_uploaded' });
  };

  dropZone.addEventListener('click', () => photoInput.click());
  dropZone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); photoInput.click(); }
  });
  photoInput.addEventListener('change', (e) => handlePhoto(e.target.files[0]));
  ['dragenter', 'dragover'].forEach(evt => dropZone.addEventListener(evt, (e) => {
    e.preventDefault(); dropZone.classList.add('is-dragover');
  }));
  ['dragleave', 'drop'].forEach(evt => dropZone.addEventListener(evt, (e) => {
    e.preventDefault(); dropZone.classList.remove('is-dragover');
  }));
  dropZone.addEventListener('drop', (e) => handlePhoto(e.dataTransfer.files[0]));
  termsCheckbox.addEventListener('change', validateUpload);

  toStyleBtn.addEventListener('click', () => {
    showStep('style');
    renderStyleGrid();
    track('funnel_upload_submitted', { has_pet_name: !!state.petName });
    track('CompleteRegistration', { content_category: 'funnel', content_name: 'upload_submitted' });
  });

  /* ── Step 2: style picker ────────────────────────────────── */
  const styleGrid = $('#sf-style-grid');
  const generateBtn = $('[data-action="generate"]');

  function renderStyleGrid() {
    if (styleGrid.childElementCount) return;
    styleGrid.innerHTML = STYLES.map(s => `
      <button type="button" class="sf-style" data-style="${s.id}" data-bg="${s.background}" aria-label="${s.name}">
        <img src="${ASSET_BASE}${s.img}${QS}" alt="${s.name}" loading="lazy">
        <span class="sf-style__name">${s.name}</span>
      </button>
    `).join('');
    $$('.sf-style', styleGrid).forEach(el => el.addEventListener('click', () => {
      $$('.sf-style', styleGrid).forEach(x => x.classList.remove('is-active'));
      el.classList.add('is-active');
      state.styleId = el.dataset.style;
      generateBtn.disabled = false;
      track('funnel_style_picked', { style_id: state.styleId });
      track('AddToWishlist', { content_ids: [state.styleId], content_category: 'style' });
    }));
  }

  $('[data-action="back-to-upload"]').addEventListener('click', () => showStep('upload'));

  generateBtn.addEventListener('click', () => {
    if (!state.styleId || !state.photoFile) return;
    submitGenerate();
  });

  /* ── Step 3: generation + polling ────────────────────────── */
  const progressBar = $('#sf-progress-bar');
  const progressCopy = $('[data-progress-copy]');

  const PROGRESS_COPY = [
    'Brushing in the first layer of colour.',
    'Refining their eyes and expression.',
    'Adding the finishing texture.',
    'Almost there — final pass.',
  ];

  async function submitGenerate() {
    showStep('generating');
    track('funnel_generation_start', { style_id: state.styleId });
    track('InitiateCheckout', { content_ids: [state.styleId], content_category: 'generation' });

    const fd = new FormData();
    fd.append('photo', state.photoFile);
    fd.append('pet_name', state.petName);
    fd.append('style', state.styleId);
    const bg = ($('.sf-style.is-active', styleGrid) || {}).dataset?.bg || 'auto';
    fd.append('background_mode', bg);
    fd.append('terms_accepted_at', new Date().toISOString());
    fd.append('turnstile_token', getTurnstileToken());

    let resp;
    try {
      resp = await fetch(`${API}/generate`, { method: 'POST', body: fd });
    } catch (e) {
      return showGenerationError('Could not reach the server. Check your connection and try again.');
    }
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || !data.job_id) {
      return showGenerationError(data.error || 'Generation request failed.');
    }
    state.jobId = data.job_id;
    pollStatus();
  }

  function showGenerationError(msg) {
    track('funnel_generation_error', { message: (msg || '').slice(0, 80) });
    progressCopy.textContent = msg;
    progressCopy.style.color = 'var(--color-danger, #9e3b33)';
    // Offer retry
    setTimeout(() => { showStep('style'); progressCopy.style.color = ''; }, 2500);
  }

  async function pollStatus() {
    let attempt = 0;
    const tick = async () => {
      attempt += 1;
      // Animate progress: 0% → 90% over ~40s; final 10% jumps on completion.
      const pct = Math.min(90, attempt * 3);
      progressBar.style.width = pct + '%';
      progressCopy.textContent = PROGRESS_COPY[Math.min(PROGRESS_COPY.length - 1, Math.floor(attempt / 6))];

      let resp;
      try {
        resp = await fetch(`${API}/status/${encodeURIComponent(state.jobId)}`);
      } catch (e) {
        return setTimeout(tick, 2500);
      }
      const data = await resp.json().catch(() => ({}));
      if (data.status === 'complete') {
        progressBar.style.width = '100%';
        const url = absolutize(data.raw_preview || data.composited || data.raw);
        return onPortraitReady(url);
      }
      if (data.status === 'failed') {
        return showGenerationError(data.error || 'Generation failed. Please try a different photo.');
      }
      if (attempt > 80) {
        return showGenerationError('This is taking longer than usual. Please try again.');
      }
      setTimeout(tick, 2000);
    };
    tick();
  }

  function absolutize(url) {
    if (!url) return url;
    if (/^https?:\/\//.test(url)) return url;
    if (url.startsWith('/')) return API + url;
    return url;
  }

  /* ── Step 4: share — render variants on <canvas> ─────────── */
  function onPortraitReady(url) {
    state.portraitURL = url;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      state.portraitImage = img;
      showStep('share');
      renderVariant(state.format);
      track('funnel_generation_complete', { style_id: state.styleId });
    };
    img.onerror = () => showGenerationError('Could not load the finished portrait. Please try again.');
    img.src = url;
  }

  const canvas = $('#sf-canvas');
  const ctx = canvas.getContext('2d');

  $$('.sf-tab').forEach(tab => tab.addEventListener('click', () => {
    $$('.sf-tab').forEach(t => { t.classList.remove('is-active'); t.setAttribute('aria-selected', 'false'); });
    tab.classList.add('is-active');
    tab.setAttribute('aria-selected', 'true');
    state.format = tab.dataset.format;
    renderVariant(state.format);
    track('funnel_format_switch', { format: state.format });
  }));

  function renderVariant(format) {
    if (format === 'story') {
      canvas.width = 1080; canvas.height = 1920;
      drawStory();
    } else {
      canvas.width = 1080; canvas.height = 1080;
      drawFeed();
    }
    // Cache as blob/dataURL for share/download.
    state.composedDataURL = canvas.toDataURL('image/png');
    canvas.toBlob((blob) => { state.composedBlob = blob; }, 'image/png');
  }

  function drawFeed() {
    const w = canvas.width, h = canvas.height;
    // Cream backdrop matching brand bg.
    ctx.fillStyle = '#FAF8F5';
    ctx.fillRect(0, 0, w, h);

    // Subtle linen vignette so the share doesn't read flat.
    const grd = ctx.createRadialGradient(w/2, h/2, w*0.2, w/2, h/2, w*0.7);
    grd.addColorStop(0, 'rgba(255,255,255,0)');
    grd.addColorStop(1, 'rgba(0,0,0,0.06)');
    ctx.fillStyle = grd;
    ctx.fillRect(0, 0, w, h);

    // Portrait — center-fit-cover the 3:4 source into the inner square.
    const pad = 80;
    drawCover(state.portraitImage, pad, pad, w - pad * 2, h - pad * 2);

    // Caption: pet name (italic serif) + handle in bottom strip.
    const padBottom = 56;
    ctx.textAlign = 'left';
    if (state.petName) {
      ctx.font = 'italic 600 64px "Cormorant Garamond", "Georgia", serif';
      ctx.fillStyle = '#1C1C1C';
      ctx.fillText(state.petName, pad, h - padBottom - 28);
    }
    ctx.font = '500 26px "Inter", system-ui, sans-serif';
    ctx.fillStyle = '#6B6B63';
    ctx.fillText(BRAND.handle, pad, h - padBottom + 14);

    // Right-aligned domain tag.
    ctx.textAlign = 'right';
    ctx.font = '500 22px "Inter", system-ui, sans-serif';
    ctx.fillStyle = '#8B7D6B';
    ctx.fillText(BRAND.domain, w - pad, h - padBottom + 14);
  }

  function drawStory() {
    const w = canvas.width, h = canvas.height;
    // Warm vertical gradient.
    const grd = ctx.createLinearGradient(0, 0, 0, h);
    grd.addColorStop(0, '#F3EDE6');
    grd.addColorStop(0.55, '#E4DDD4');
    grd.addColorStop(1, '#D9CFC2');
    ctx.fillStyle = grd;
    ctx.fillRect(0, 0, w, h);

    // Portrait fills upper portion (3:4 fits naturally → height ~1280 at w=960).
    const imgW = 960;
    const imgH = imgW * 4 / 3; // 1280
    const imgX = (w - imgW) / 2;
    const imgY = 160;
    // Soft shadow under portrait.
    ctx.shadowColor = 'rgba(0,0,0,0.18)';
    ctx.shadowBlur = 40;
    ctx.shadowOffsetY = 12;
    drawCover(state.portraitImage, imgX, imgY, imgW, imgH, 24);
    ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0;

    // Pet name + tagline.
    const captionY = imgY + imgH + 80;
    ctx.textAlign = 'center';
    if (state.petName) {
      ctx.font = 'italic 600 110px "Cormorant Garamond", "Georgia", serif';
      ctx.fillStyle = '#1C1C1C';
      ctx.fillText(state.petName, w / 2, captionY);
    } else {
      ctx.font = 'italic 600 96px "Cormorant Garamond", "Georgia", serif';
      ctx.fillStyle = '#1C1C1C';
      ctx.fillText('Pet portrait, made instantly', w / 2, captionY);
    }
    ctx.font = '500 32px "Inter", system-ui, sans-serif';
    ctx.fillStyle = '#6B6B63';
    ctx.fillText('made my pet a portrait at', w / 2, captionY + 70);

    // Domain pill.
    const pillText = BRAND.domain;
    ctx.font = '600 38px "Inter", system-ui, sans-serif';
    const pillW = ctx.measureText(pillText).width + 80;
    const pillH = 76;
    const pillX = (w - pillW) / 2;
    const pillY = captionY + 110;
    roundRect(ctx, pillX, pillY, pillW, pillH, 38);
    ctx.fillStyle = '#2F2F2A';
    ctx.fill();
    ctx.fillStyle = '#FFFFFF';
    ctx.textBaseline = 'middle';
    ctx.fillText(pillText, w / 2, pillY + pillH / 2);
    ctx.textBaseline = 'alphabetic';

    // Hashtag at very bottom.
    ctx.font = '500 28px "Inter", system-ui, sans-serif';
    ctx.fillStyle = '#8B7D6B';
    ctx.fillText(BRAND.hashtag, w / 2, h - 80);
  }

  /* draw img into rect with object-fit: cover semantics + optional rounded corners */
  function drawCover(img, x, y, w, h, radius) {
    const ir = img.width / img.height;
    const tr = w / h;
    let sx, sy, sw, sh;
    if (ir > tr) {
      // image wider than target — crop sides
      sh = img.height; sw = sh * tr; sx = (img.width - sw) / 2; sy = 0;
    } else {
      sw = img.width; sh = sw / tr; sx = 0; sy = (img.height - sh) / 2;
    }
    if (radius) {
      ctx.save();
      roundRect(ctx, x, y, w, h, radius);
      ctx.clip();
      ctx.drawImage(img, sx, sy, sw, sh, x, y, w, h);
      ctx.restore();
    } else {
      ctx.drawImage(img, sx, sy, sw, sh, x, y, w, h);
    }
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  /* ── Share + Download ────────────────────────────────────── */
  $('[data-action="share"]').addEventListener('click', async () => {
    const platform = 'instagram_or_facebook';
    const filename = `pet-portrait-${state.format}.png`;
    track('funnel_share_click', { format: state.format, platform });
    track('Share', { content_category: 'pet_portrait', content_name: state.format });

    // Web Share API Level 2 (files) — supported by Safari iOS, Chrome Android.
    if (navigator.canShare && state.composedBlob) {
      const file = new File([state.composedBlob], filename, { type: 'image/png' });
      if (navigator.canShare({ files: [file] })) {
        try {
          await navigator.share({
            files: [file],
            title: state.petName ? `${state.petName} — pet portrait` : 'My pet portrait',
            text: `Made a free pet portrait at ${BRAND.domain} ${BRAND.hashtag}`,
          });
          track('funnel_share_completed', { format: state.format });
          return;
        } catch (e) {
          // user cancelled or share blocked — fall through to download
        }
      }
    }
    // Fallback: download the image so user can manually upload.
    downloadComposed(filename);
  });

  $('[data-action="download"]').addEventListener('click', () => {
    const filename = `pet-portrait-${state.format}.png`;
    track('funnel_download', { format: state.format });
    downloadComposed(filename);
  });

  function downloadComposed(filename) {
    const url = state.composedDataURL || canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  $('[data-action="pdp-cta"]').addEventListener('click', () => {
    track('funnel_pdp_cta_click', { format: state.format, style_id: state.styleId });
    track('AddToCart', { content_ids: [state.styleId], content_category: 'soft_cta' });
  });

  $('[data-action="restart"]').addEventListener('click', () => {
    state.styleId = null;
    state.jobId = null;
    state.portraitURL = null;
    state.portraitImage = null;
    generateBtn.disabled = true;
    $$('.sf-style').forEach(x => x.classList.remove('is-active'));
    showStep('style');
    track('funnel_restart', {});
  });

  /* ── Cloudflare Turnstile ────────────────────────────────── */
  function getTurnstileToken() {
    try {
      if (window.turnstile && window._sfTurnstileWidgetId !== undefined) {
        return window.turnstile.getResponse(window._sfTurnstileWidgetId) || '';
      }
    } catch (e) {}
    return '';
  }

  function mountTurnstile() {
    if (!TURNSTILE_KEY) return; // dev mode — backend may also be in dev mode
    if (window._sfTurnstileMounted) return;
    window._sfTurnstileMounted = true;

    if (!document.querySelector('script[src*="challenges.cloudflare.com/turnstile"]')) {
      const s = document.createElement('script');
      s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onSfTurnstileLoad';
      s.async = true; s.defer = true;
      document.head.appendChild(s);
    }
    window.onSfTurnstileLoad = function () {
      const container = document.getElementById('sf-turnstile');
      if (!container) return;
      window._sfTurnstileWidgetId = window.turnstile.render(container, {
        sitekey: TURNSTILE_KEY,
        size: 'flexible',
        theme: 'light',
        appearance: 'interaction-only',
      });
    };
    // If turnstile was already loaded earlier on the page (e.g. revisited
    // step), render immediately.
    if (window.turnstile && typeof window.onSfTurnstileLoad === 'function') {
      try { window.onSfTurnstileLoad(); } catch (e) {}
    }
  }
})();
