/* ============================================================
   PORTRAIT FLOW — Self-contained React widget
   Mounts into #portrait-flow-root inside a Shopify Dawn section.
   No external UI libraries. Inline styles with design tokens.
   Premium editorial aesthetic — Cormorant Garamond + Inter.
   ============================================================ */

const { useState, useCallback, useRef, useEffect } = React;

/* ── Design tokens ─────────────────────────────────────────── */

const tokens = {
  colorBrand:       '#1C1C1C',
  colorCta:         '#2F2F2A',
  colorCtaHover:    '#3D3D36',
  colorAccent:      '#8B7D6B',
  colorAccentLight: '#F3EDE6',
  colorMuted:       '#8A8A82',
  colorBorder:      '#E4DDD4',
  colorSurface:     '#FAF8F5',
  colorWhite:       '#FFFFFF',
  colorError:       '#9E3B33',
  colorWarning:     '#8B7D3C',
  colorSuccess:     '#5C7A5E',
  radiusCard:       '16px',
  radiusButton:     '6px',
  spacingUnit:      '8px',
};

const fontSerif = "'Cormorant Garamond', serif";
const fontSans  = "'Inter', sans-serif";

/* ── Style → font mapping ─────────────────────────────────── */
// Each style gets a complementary Google Font for the pet name
// Fonts are loaded on-demand via Google Fonts CSS

const STYLE_FONTS = {
  'soft-watercolour':     { family: 'Dancing Script',     css: "'Dancing Script', cursive",     google: 'Dancing+Script:wght@700' },
  'minimal-line-art':     { family: 'Raleway',            css: "'Raleway', sans-serif",         google: 'Raleway:wght@300;600' },
  'modern-shape-art':     { family: 'Space Grotesk',      css: "'Space Grotesk', sans-serif",   google: 'Space+Grotesk:wght@400;500;700' },
  'neon-pop-art':         { family: 'Bungee',             css: "'Bungee', sans-serif",          google: 'Bungee' },
  'renaissance-royalty':  { family: 'Cinzel',             css: "'Cinzel', serif",               google: 'Cinzel:wght@700' },
  'bold-graphic-poster':  { family: 'Oswald',             css: "'Oswald', sans-serif",          google: 'Oswald:wght@700' },
  'aura-gradient':        { family: 'Quicksand',          css: "'Quicksand', sans-serif",       google: 'Quicksand:wght@500;700' },
};

const FONT_SIZES = [
  { id: 'small',  label: 'S', scale: 0.7 },
];

// Background options — tell Gemini whether to lean light or dark for the
// scene around the pet. 'auto' lets the style's default palette decide.
const BACKGROUND_OPTIONS = [
  { id: 'auto',  label: 'Auto',  sub: 'Style default' },
  { id: 'light', label: 'Light', sub: 'Soft & airy' },
  { id: 'dark',  label: 'Dark',  sub: 'Moody & rich' },
];

// Load a Google Font dynamically
const _loadedFonts = new Set();
function loadGoogleFont(styleId) {
  const fontDef = STYLE_FONTS[styleId];
  if (!fontDef || _loadedFonts.has(styleId)) return;
  _loadedFonts.add(styleId);
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = `https://fonts.googleapis.com/css2?family=${fontDef.google}&display=swap`;
  document.head.appendChild(link);
}

/* ── Style catalogue ───────────────────────────────────────── */

// `backgrounds` declares which background modes are offered for a given style.
// Only styles where a light/dark inversion produces a meaningfully different —
// and still on-brand — result expose the selector (soft watercolour, minimal
// line art, bold graphic poster). The rest keep their baked-in look and the
// background card is hidden.
const STYLES = [
  {
    id: 'soft-watercolour',
    name: 'Soft Watercolour',
    badge: 'Most popular',
    available: true,
    exampleImage: 'example-soft-watercolour.webp',
    backgrounds: ['auto', 'light', 'dark'],
  },
  {
    id: 'minimal-line-art',
    name: 'Minimal Line Art',
    available: true,
    exampleImage: 'example-minimal-line-art.webp',
    backgrounds: ['auto', 'light', 'dark'],
  },
  {
    id: 'modern-shape-art',
    name: 'Modern',
    available: true,
    exampleImage: 'example-modern-shape-art.webp',
    backgrounds: ['auto'],
  },
  {
    id: 'neon-pop-art',
    name: 'Neon Pop Art',
    available: true,
    exampleImage: 'example-neon-pop-art.webp',
    backgrounds: ['auto'],
  },
  {
    id: 'renaissance-royalty',
    name: 'Renaissance Royalty',
    available: true,
    exampleImage: 'example-renaissance-royalty.webp',
    backgrounds: ['auto'],
  },
  {
    id: 'bold-graphic-poster',
    name: 'Bold Graphic Poster',
    available: true,
    exampleImage: 'example-bold-graphic-poster.webp',
    backgrounds: ['auto', 'light', 'dark'],
  },
  {
    id: 'aura-gradient',
    name: 'Aura Gradient',
    available: true,
    exampleImage: 'example-aura-gradient.webp',
    backgrounds: ['auto'],
  },
];

// Quick lookup — used by StyleStep UI filter + saved-session normalization.
function backgroundsFor(styleId) {
  const style = STYLES.find(s => s.id === styleId);
  return (style && style.backgrounds) || ['auto'];
}

// Human-readable name for a style id. Falls back to the slug if unknown.
function styleNameFor(styleId) {
  const s = STYLES.find(x => x.id === styleId);
  return (s && s.name) || (styleId || '');
}

// Per-style on-brand affirmations shown on the style step the moment a
// customer picks one. Framework: 4Ps Picture → Promise. The eyebrow
// validates the customer's taste; the line paints what the pet is about
// to look like. `line` starts lowercase so it can be preceded by either
// "{Pet} is " (sentence reads "Buddy is about to look…") or capitalised
// at the first letter when no name is provided.
const STYLE_AFFIRMATIONS = {
  'soft-watercolour':    { tag: 'The classic',          line: 'about to look heart-melting in soft watercolour.' },
  'minimal-line-art':    { tag: 'Quiet confidence',     line: 'going to look effortless in clean line art.' },
  'modern-shape-art':    { tag: 'Designer pick',        line: 'about to anchor a room with modern shapes.' },
  'neon-pop-art':        { tag: 'Loud and lovable',     line: 'going to glow — saturated and electric.' },
  'renaissance-royalty': { tag: 'Hall of portraits',    line: 'about to be ennobled, oil-painted in flattering light.' },
  'bold-graphic-poster': { tag: 'Poster-shop energy',   line: 'getting the bold-colour, sharp-lines treatment.' },
  'aura-gradient':       { tag: 'Halo treatment',       line: 'getting the soft-gradient halo treatment.' },
};

// Resolve asset base path for style example images
const _pfScript = document.querySelector('script[src*="portrait-flow"]');
const _pfAssetBase = _pfScript ? _pfScript.src.replace(/portrait-flow[^/]*$/, '') : '';

/* ── Prices & variant map ──────────────────────────────────── */

const PRICES = {
  canvas: {
    '12x12': '$79.99 CAD',
    '12x16': '$84.99 CAD',
    '16x16': '$99.99 CAD',
    '16x20': '$109.99 CAD',
  },
  poster: { 'default': '$36.11 CAD' },
};

const VARIANT_MAP = {
  'canvas-12x12': 47267971760277,
  'canvas-12x16': 47267971793045,
  'canvas-16x16': 47267971825813,
  'canvas-16x20': 47267971858581,
  'canvas-16x20-framed': 47267981885589,
  'poster-default': 47167380521109,
};

/* ── Flow stages ───────────────────────────────────────────── */

const STAGES = {
  UPLOAD: 'upload',
  STYLE: 'style',
  GENERATING: 'generating',
  PREVIEW: 'preview',
  GALLERY: 'gallery',
};

const GENERATION_RESET = { generationStatus: 'idle', previewImages: [], previewDataUrls: [], jobId: null };

/* ── Product catalogue (cards link to real Shopify PDPs) ──── */

const PRODUCT_CATALOGUE = [
  {
    handle: 'canvas',
    name: 'Gallery Canvas',
    sub: '1.5\u2033 depth \u00B7 archival inks \u00B7 ready to hang',
    fromPrice: '$37.00 CAD',
    available: true,
  },
  {
    handle: 'poster',
    name: 'Fine Art Print',
    sub: 'Heavyweight matte paper \u00B7 unframed',
    fromPrice: '$36.11 CAD',
    available: true,
  },
  {
    handle: 'mug',
    name: 'Ceramic Mug',
    sub: 'Dishwasher-safe \u00B7 11 oz',
    fromPrice: 'Coming soon',
    available: false,
  },
];

/* ── localStorage persistence ─────────────────────────────── */

const LS_KEY = 'petPrintables_session';
const LIBRARY_KEY = 'petPrintables_library';
const LIBRARY_EXPIRY_MS = 24 * 60 * 60 * 1000; // 24 hours — images stored server-side for 24h max

/* ── Portrait library (multi-portrait support) ──────────────
 * Each entry: { id, petName, styleId, previewUrl, printUrl,
 *               noNameUrl, createdAt, imageFilename, jobId,
 *               originalPhotoUrl }
 * Lets users save multiple portraits and order any of them. */
function loadLibrary() {
  try {
    const raw = localStorage.getItem(LIBRARY_KEY);
    if (!raw) return [];
    const list = JSON.parse(raw);
    if (!Array.isArray(list)) return [];
    // Drop expired entries
    const now = Date.now();
    return list.filter(p => {
      const age = now - new Date(p.createdAt).getTime();
      return age < LIBRARY_EXPIRY_MS;
    });
  } catch { return []; }
}

function saveLibrary(list) {
  try {
    localStorage.setItem(LIBRARY_KEY, JSON.stringify(list));
  } catch {}
}

function addToLibrary(state) {
  if (!state.imageFilename && !state.previewCdnUrls?.length) return;
  const list = loadLibrary();
  const id = state.jobId || state.imageFilename || `p-${Date.now()}`;
  // Dedupe — replace if an entry with same id already exists
  const filtered = list.filter(p => p.id !== id);
  filtered.unshift({
    id,
    petName: state.petName || 'Pet',
    styleId: state.selectedStyleId || 'soft-watercolour',
    previewUrl: (state.previewCdnUrls || [])[0] || (state.previewImages || [])[0] || '',
    noNameUrl: (state.previewCdnUrls || [])[0] || '',
    printUrl: state.printFileUrl || '',
    createdAt: state.generatedAt || new Date().toISOString(),
    imageFilename: state.imageFilename || '',
    jobId: state.jobId || '',
    originalPhotoUrl: state.originalPhotoUrl || '',
  });
  // Cap at 10 most recent
  saveLibrary(filtered.slice(0, 10));
}

function removeFromLibrary(id) {
  const list = loadLibrary().filter(p => p.id !== id);
  saveLibrary(list);
}

// Human-friendly "days left" for a portrait
function daysRemaining(createdAt) {
  try {
    const age = Date.now() - new Date(createdAt).getTime();
    return Math.max(0, Math.ceil((LIBRARY_EXPIRY_MS - age) / (24 * 60 * 60 * 1000)));
  } catch { return 0; }
}

function saveSession(state) {
  try {
    const data = {
      version: 1,
      petName: state.petName,
      styleId: state.selectedStyleId,
      fontSize: state.fontSize || 'small',
      backgroundMode: state.backgroundMode || 'auto',
      jobId: state.jobId,
      previewDataUrls: state.previewDataUrls || [],
      previewCdnUrls: state.previewCdnUrls || [],
      selectedPreviewIndex: state.selectedPreviewIndex,
      generatedAt: new Date().toISOString(),
      imageFilename: state.imageFilename || '',
      originalPhotoUrl: state.originalPhotoUrl || '',
      printFileUrl: state.printFileUrl || '',
    };
    localStorage.setItem(LS_KEY, JSON.stringify(data));
  } catch (e) { /* quota exceeded — silently fail */ }
}

function loadSession() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data.version !== 1) return null;
    // Accept either data URLs or CDN URLs
    if (!data.previewDataUrls?.length && !data.previewCdnUrls?.length && !data.imageFilename) return null;
    // Expire after 7 days
    const age = Date.now() - new Date(data.generatedAt).getTime();
    if (age > 24 * 60 * 60 * 1000) { localStorage.removeItem(LS_KEY); return null; }
    return data;
  } catch { return null; }
}

function clearSession() {
  try { localStorage.removeItem(LS_KEY); } catch {}
}

async function imageUrlToDataUrl(url) {
  try {
    const res = await fetch(url);
    const blob = await res.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.onerror = () => resolve(null);
      reader.readAsDataURL(blob);
    });
  } catch { return null; }
}

/* ── API integration ───────────────────────────────────────── */

const API_BASE = (typeof window !== 'undefined' && window.petPrintables?.previewApi)
  || 'https://web-production-a392e.up.railway.app';

/* ── Backend warmup ───────────────────────────────────────
   Railway cold-starts can take 5-15s. We ping /health as soon
   as the page loads so the container is warm by the time the
   user finishes uploading and selecting a style.
   ───────────────────────────────────────────────────────── */
let _backendReady = false;
let _warmupPromise = null;

function warmupBackend() {
  if (_warmupPromise) return _warmupPromise;
  _warmupPromise = fetch(`${API_BASE}/health`, { method: 'GET', mode: 'cors' })
    .then(r => { _backendReady = r.ok; return _backendReady; })
    .catch(() => { _backendReady = false; return false; });
  // Re-ping every 4 minutes to keep container alive during session
  setTimeout(() => { _warmupPromise = null; warmupBackend(); }, 240000);
  return _warmupPromise;
}

// Fire warmup immediately on script load
if (typeof window !== 'undefined') warmupBackend();

const STYLE_MAP = {
  'soft-watercolour': 'watercolor',
  'minimal-line-art': 'minimal-line-art',
  'modern-shape-art': 'modern-shape-art',
  'neon-pop-art': 'neon-pop-art',
  'renaissance-royalty': 'renaissance-royalty',
  'bold-graphic-poster': 'bold-graphic-poster',
  'aura-gradient': 'aura-gradient',
};

// Cloudflare Turnstile token getter — returns current token from the widget.
// The widget is rendered once on the create page and auto-refreshes tokens.
function getTurnstileToken() {
  try {
    if (window.turnstile && window._pfTurnstileWidgetId !== undefined) {
      return window.turnstile.getResponse(window._pfTurnstileWidgetId) || '';
    }
  } catch {}
  return '';
}

async function generatePortrait({ imageFile, styleId, petName, termsAcceptedAt, backgroundMode }) {
  const formData = new FormData();
  formData.append('photo', imageFile);
  formData.append('pet_name', petName || 'Pet');
  formData.append('style', STYLE_MAP[styleId] || 'classic');
  formData.append('background_mode', backgroundMode || 'auto');
  formData.append('turnstile_token', getTurnstileToken());
  // Photo-license audit trail — ISO timestamp of when the customer
  // accepted the upload terms. Backend logs this with the job.
  if (termsAcceptedAt) {
    formData.append('terms_accepted_at', termsAcceptedAt);
  }

  // Step 1: Submit the job
  const submitRes = await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    body: formData,
  });

  if (submitRes.status === 503) {
    const e = new Error('BUSY'); e.status = submitRes.status; throw e;
  }
  if (!submitRes.ok && submitRes.status !== 202) {
    const err = await submitRes.json().catch(() => ({}));
    const e = new Error(err.error || 'Generation failed');
    e.code = err.code || '';
    e.detail = err.detail || '';
    e.status = submitRes.status;
    throw e;
  }

  const submitData = await submitRes.json();
  const jobId = submitData.job_id;

  if (!jobId) {
    // Legacy backend — response already contains the result
    const previews = [submitData.composited, submitData.raw]
      .filter(Boolean)
      .map(p => p.startsWith('http') ? p : `${API_BASE}${p}`);
    return { jobId: 'job-' + Date.now(), previews, filename: submitData.filename || '', cdn: submitData.cdn || false, originalPhoto: submitData.original_cdn || '' };
  }

  // Step 2: Poll /status/<job_id> until complete.
  // Poll every 1s for the first 10s (when most jobs finish) then back off to
  // 2s. Rate limit on /status is 300/10min, so this stays well within budget.
  const MAX_POLL_TIME = 120000; // 120s total timeout
  const start = Date.now();

  while (Date.now() - start < MAX_POLL_TIME) {
    const elapsed = Date.now() - start;
    const interval = elapsed < 10000 ? 1000 : 2000;
    await new Promise(r => setTimeout(r, interval));

    const pollRes = await fetch(`${API_BASE}/status/${jobId}`);
    if (!pollRes.ok) throw new Error('Failed to check generation status');

    const status = await pollRes.json();

    if (status.status === 'complete') {
      const previews = [status.composited, status.raw]
        .filter(Boolean)
        .map(p => p.startsWith('http') ? p : `${API_BASE}${p}`);
      const originalPhoto = status.original_cdn || '';
      // Hi-res PNG URL for Printful fulfillment (3000x3750+ @ 300 DPI)
      const printFileUrl = status.composited_png_cdn
        ? (status.composited_png_cdn.startsWith('http') ? status.composited_png_cdn : `${API_BASE}${status.composited_png_cdn}`)
        : '';
      return { jobId, previews, filename: status.filename || '', cdn: status.cdn === '1' || status.cdn === true, originalPhoto, printFileUrl };
    }

    if (status.status === 'failed') {
      const e = new Error(status.error || 'Generation failed');
      e.code = status.code || 'worker_failed';
      throw e;
    }
    // else queued or processing — keep polling
  }

  throw new Error('TIMEOUT');
}

/* Retry wrapper — up to 3 retries with exponential backoff + jitter.
   Ensures backend is warm before first attempt. Handles cold starts,
   transient 503s, and network blips gracefully. */
async function generateWithRetry(params, maxRetries = 3) {
  // Ensure backend is warm before first generation attempt
  if (!_backendReady) {
    try { await Promise.race([warmupBackend(), new Promise(r => setTimeout(r, 8000))]); }
    catch { /* proceed anyway */ }
  }

  let lastErr;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await generatePortrait(params);
    } catch (err) {
      lastErr = err;
      const msg = err.message || '';
      // Retry on transient errors (cold start, capacity, network)
      const isTransient = msg === 'TIMEOUT' || msg === 'BUSY'
        || msg === 'Failed to fetch' || msg.includes('NetworkError')
        || msg.includes('Load failed') || msg.includes('network');
      if (isTransient && attempt < maxRetries) {
        // Exponential backoff with jitter: ~2s, ~5s, ~10s
        const base = Math.pow(2, attempt + 1) * 1000;
        const jitter = Math.random() * 1000;
        await new Promise(r => setTimeout(r, base + jitter));
        continue;
      }
      throw err; // non-retryable — throw immediately
    }
  }
  throw lastErr;
}

/* ── Keyframe styles (injected once) ──────────────────────── */

const KEYFRAME_CSS = `
@keyframes pf-watercolor-pulse {
  0%, 100% { opacity: 0.35; transform: scale(1); }
  50% { opacity: 0.75; transform: scale(1.015); }
}
@keyframes pf-progress-indeterminate {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(200%); }
}
@keyframes pf-fade-in {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes pf-reveal-up {
  from { opacity: 0; transform: translateY(24px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes pf-chase-spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
@keyframes pf-chase-unspin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(-360deg); }
}
@keyframes pf-chase-unspin-flip {
  0% { transform: scaleX(-1) rotate(0deg); }
  100% { transform: scaleX(-1) rotate(-360deg); }
}
@keyframes pf-chase-bounce-1 {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-5px); }
}
@keyframes pf-chase-bounce-2 {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-5px); }
}
@keyframes pf-morph-square {
  0%, 100% { border-radius: 6%; transform: rotate(0deg); }
  50% { border-radius: 50%; transform: rotate(180deg); }
}
@keyframes pf-phrase-fade {
  0% { opacity: 0; transform: translateY(8px); }
  15% { opacity: 1; transform: translateY(0); }
  85% { opacity: 1; transform: translateY(0); }
  100% { opacity: 0; transform: translateY(-8px); }
}
@keyframes pf-marquee {
  0% { transform: translateX(0); }
  100% { transform: translateX(-50%); }
}
@keyframes pf-urgency-pulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.015); }
}
@keyframes pf-newsletter-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes pf-newsletter-pop {
  from { opacity: 0; transform: scale(0.94) translateY(8px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
@keyframes pf-style-celebrate {
  0%   { opacity: 0; transform: translateY(6px) scale(0.97); }
  60%  { opacity: 1; transform: translateY(-2px) scale(1.015); }
  100% { opacity: 1; transform: translateY(0)    scale(1); }
}
@keyframes pf-sparkle-0 { 0%{opacity:0;transform:translate(0,0) scale(0.5)} 30%{opacity:1} 100%{opacity:0;transform:translate(-22px,-18px) scale(1)} }
@keyframes pf-sparkle-1 { 0%{opacity:0;transform:translate(0,0) scale(0.5)} 30%{opacity:1} 100%{opacity:0;transform:translate( 26px,-12px) scale(1.1)} }
@keyframes pf-sparkle-2 { 0%{opacity:0;transform:translate(0,0) scale(0.5)} 30%{opacity:1} 100%{opacity:0;transform:translate( 18px, 22px) scale(0.9)} }
@keyframes pf-sparkle-3 { 0%{opacity:0;transform:translate(0,0) scale(0.5)} 30%{opacity:1} 100%{opacity:0;transform:translate(-18px, 24px) scale(1)} }
@keyframes pf-sparkle-4 { 0%{opacity:0;transform:translate(0,0) scale(0.5)} 30%{opacity:1} 100%{opacity:0;transform:translate(  2px,-26px) scale(0.85)} }
@keyframes pf-sparkle-5 { 0%{opacity:0;transform:translate(0,0) scale(0.5)} 30%{opacity:1} 100%{opacity:0;transform:translate( 30px,  6px) scale(1.05)} }
@media (prefers-reduced-motion: reduce) {
  .pf-marquee-track { animation: none !important; }
}

/* Preview screen — desktop two-column layout. The preview image and the
   action stack (heading + chip + CTA + secondary links) compete for
   vertical space on a single column, pushing the CTA below the fold on
   1080p displays. Splitting into two columns at >=900px puts the
   preview on the left and the actions on the right, both visible at
   once with no scroll. Mobile keeps the original stacked layout. */
@media (min-width: 900px) {
  .pf-preview-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr);
    gap: 48px;
    align-items: center;
    max-width: 1080px;
    margin: 0 auto;
  }
  .pf-preview-grid__media { grid-column: 1; margin: 0 !important; max-width: 100% !important; }
  .pf-preview-grid__copy  { grid-column: 2; text-align: left; }
  .pf-preview-grid__copy .pf-preview-grid__center { text-align: center; }
  /* Step indicator stays at the very top, full width above the grid. */
  .pf-preview-grid__indicator { grid-column: 1 / -1; margin-bottom: 8px; }
}
`;

let keyframesInjected = false;
function injectKeyframes() {
  if (keyframesInjected) return;
  const el = document.createElement('style');
  el.textContent = KEYFRAME_CSS;
  document.head.appendChild(el);
  keyframesInjected = true;
}

/* ── Helpers ───────────────────────────────────────────────── */

function readImageDimensions(file) {
  return new Promise((resolve) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      resolve({ width: img.naturalWidth, height: img.naturalHeight });
      URL.revokeObjectURL(url);
    };
    img.onerror = () => {
      resolve(null);
      URL.revokeObjectURL(url);
    };
    img.src = url;
  });
}

const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp'];
const MAX_FILE_SIZE = 15 * 1024 * 1024;
const MIN_DIMENSION = 800;

/* ── Camera SVG icon ───────────────────────────────────────── */

function CameraIcon({ size = 32 }) {
  return React.createElement('svg', {
    xmlns: 'http://www.w3.org/2000/svg',
    width: size, height: size,
    fill: 'none', viewBox: '0 0 24 24',
    stroke: tokens.colorMuted, strokeWidth: 1,
    'aria-hidden': true,
  },
    React.createElement('path', {
      strokeLinecap: 'round', strokeLinejoin: 'round',
      d: 'M6.827 6.175A2.31 2.31 0 015.186 7.23c-.38.054-.757.112-1.134.175C2.999 7.58 2.25 8.507 2.25 9.574V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9.574c0-1.067-.75-1.994-1.802-2.169a47.865 47.865 0 00-1.134-.175 2.31 2.31 0 01-1.64-1.055l-.822-1.316a2.192 2.192 0 00-1.736-1.039 48.774 48.774 0 00-5.232 0 2.192 2.192 0 00-1.736 1.039l-.821 1.316z',
    }),
    React.createElement('path', {
      strokeLinecap: 'round', strokeLinejoin: 'round',
      d: 'M16.5 12.75a4.5 4.5 0 11-9 0 4.5 4.5 0 019 0z',
    }),
  );
}

/* ── Inline CTA icons — universally-readable symbols for accessibility.
 *   All use stroke="currentColor" so they inherit each button's text color,
 *   which keeps them legible on both dark primary buttons and secondary
 *   links without per-button styling. Stroke weight matches the thin,
 *   editorial line-art feel of CameraIcon above.
 * ────────────────────────────────────────────────────────────── */
function CtaIcon({ paths, size = 16, strokeWidth = 1.75 }) {
  return React.createElement('svg', {
    xmlns: 'http://www.w3.org/2000/svg',
    width: size, height: size, viewBox: '0 0 24 24',
    fill: 'none', stroke: 'currentColor',
    strokeWidth, strokeLinecap: 'round', strokeLinejoin: 'round',
    'aria-hidden': true,
    style: { flexShrink: 0 },
  },
    ...paths.map((d, i) => React.createElement('path', { key: i, d })),
  );
}

function ArrowRightIcon(props) {
  return React.createElement(CtaIcon, {
    ...props, paths: ['M5 12h14', 'M13 6l6 6-6 6'],
  });
}

function ArrowLeftIcon(props) {
  return React.createElement(CtaIcon, {
    ...props, paths: ['M19 12H5', 'M11 18l-6-6 6-6'],
  });
}

function RefreshIcon(props) {
  return React.createElement(CtaIcon, {
    ...props, paths: [
      'M3 12a9 9 0 0 1 15.5-6.3L21 8',
      'M21 3v5h-5',
      'M21 12a9 9 0 0 1-15.5 6.3L3 16',
      'M3 21v-5h5',
    ],
  });
}

// Sparkle/regenerate-from-scratch icon — used by the "Regenerate Portrait"
// link on the preview screen so it visually distinguishes from the
// "Try another" same-style refresh.
function SparkleIcon(props) {
  return React.createElement(CtaIcon, {
    ...props, paths: [
      'M12 3l1.9 5.8a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-1.9a2 2 0 0 0 1.3-1.3z',
      'M5 3v4',
      'M3 5h4',
      'M19 17v4',
      'M17 19h4',
    ],
  });
}

// Wraps a button's content with inline-flex so the icon and text label
// align cleanly (neither primaryBtn nor secondaryLink style sets flex
// themselves). Accepts any React element for the icon.
function iconLabel(icon, label, iconPosition) {
  const pos = iconPosition || 'left';
  return React.createElement('span', {
    style: {
      display: 'inline-flex', alignItems: 'center',
      gap: '8px', justifyContent: 'center',
    },
  },
    pos === 'left' ? icon : null,
    React.createElement('span', null, label),
    pos === 'right' ? icon : null,
  );
}

/* ── usePortraitFlow hook ──────────────────────────────────── */

// Check for pending PDP-initiated flow (user uploaded on PDP, needs to pick style)
function loadPending() {
  try {
    const raw = localStorage.getItem('petPrintables_pending');
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (data.version !== 1 || !data.photoDataUrl) return null;
    return data;
  } catch { return null; }
}

function clearPending() {
  try { localStorage.removeItem('petPrintables_pending'); } catch {}
}

// Convert a data URL back into a File object
async function dataUrlToFile(dataUrl, filename, mimeType) {
  const res = await fetch(dataUrl);
  const blob = await res.blob();
  return new File([blob], filename || 'pet-photo.jpg', { type: mimeType || blob.type || 'image/jpeg' });
}

function usePortraitFlow() {
  // Check for saved session first, then pending PDP upload
  const saved = loadSession();
  const pending = !saved ? loadPending() : null;

  const [state, setState] = useState({
    // If pending from PDP, skip to Style step with photo + name pre-filled
    stage: saved ? STAGES.PREVIEW : (pending ? STAGES.STYLE : STAGES.UPLOAD),
    photo: null,
    photoThumbnailUrl: pending?.photoDataUrl || null,
    photoDimensions: null,
    photoWarning: null,
    photoError: null,
    petName: saved?.petName || pending?.petName || '',
    selectedStyleId: saved?.styleId || null,
    generationStatus: saved ? 'success' : 'idle',
    previewImages: (saved?.previewDataUrls?.length ? saved.previewDataUrls : saved?.previewCdnUrls) || [],
    previewDataUrls: saved?.previewDataUrls || [],
    previewCdnUrls: saved?.previewCdnUrls || [],
    selectedPreviewIndex: saved?.selectedPreviewIndex || 0,
    fontSize: 'var(--text-sm)',
    backgroundMode: saved?.backgroundMode || 'auto',
    imageFilename: saved?.imageFilename || '',
    originalPhotoUrl: saved?.originalPhotoUrl || '',
    printFileUrl: saved?.printFileUrl || '',
    jobId: saved?.jobId || null,
    restoredSession: !!saved,
    pendingPhoto: pending,  // hold pending for later conversion to File

    // Photo-license + Terms acceptance — legally required before generating.
    // We record the timestamp so the backend can log it as an audit trail.
    termsAccepted: false,
    termsAcceptedAt: null,
  });

  // If we have pending PDP data, reconstruct the File object asynchronously
  useEffect(() => {
    if (pending && !state.photo) {
      dataUrlToFile(pending.photoDataUrl, pending.photoName, pending.photoType)
        .then(file => {
          setState(prev => ({ ...prev, photo: file }));
          clearPending();
        })
        .catch(() => { /* ignore — user can re-upload */ });
    }
  }, []);

  const update = useCallback((patch) => {
    setState(prev => ({ ...prev, ...patch }));
  }, []);

  const setPhoto = useCallback(async (file) => {
    if (!file) return;
    const clearPhoto = (error, errorTips) => {
      setState(prev => {
        if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
        return {
          ...prev, photo: null, photoThumbnailUrl: null, photoDimensions: null,
          photoError: error, photoErrorTips: errorTips || null,
          photoWarning: null, photoWarningTips: null,
        };
      });
    };
    const name = (file.name || '').toLowerCase();
    const type = (file.type || '').toLowerCase();
    const isHeic = type === 'image/heic' || type === 'image/heif'
      || name.endsWith('.heic') || name.endsWith('.heif');
    if (isHeic) {
      clearPhoto(
        'HEIC photos from iPhone aren\u2019t supported yet.',
        [
          'On iPhone: open the photo, tap Share \u2192 Mail \u2014 iOS converts it to JPG.',
          'Or change Settings \u2192 Camera \u2192 Formats \u2192 Most Compatible, then retake.',
        ],
      );
      return;
    }
    if (!ACCEPTED_TYPES.includes(type)) {
      clearPhoto(
        'Please upload a JPG, PNG, or WebP file.',
        ['Most photo apps can export as JPG or PNG \u2014 look for \u201cShare\u201d or \u201cExport As\u201d.'],
      );
      return;
    }
    if (file.size > MAX_FILE_SIZE) {
      clearPhoto(
        'This file is over 15 MB. Please use a smaller photo.',
        [
          'On iPhone, when emailing, choose \u201cMedium\u201d size.',
          'Or take a screenshot of the photo to shrink the file.',
        ],
      );
      return;
    }
    const dims = await readImageDimensions(file);
    if (!dims) {
      clearPhoto(
        'We couldn\u2019t open this photo. The file may be damaged.',
        [
          'Try opening it in your Photos app, re-saving, and uploading again.',
          'Or choose a different photo.',
        ],
      );
      return;
    }
    const thumbUrl = URL.createObjectURL(file);
    let warning = null;
    let warningTips = null;
    if (dims.width < MIN_DIMENSION || dims.height < MIN_DIMENSION) {
      warning = "This photo might work, but a clearer one usually gives a better result. Want to try another?";
      warningTips = [
        'Use the original photo rather than a screenshot or download.',
        'Pick a photo where your pet fills most of the frame.',
      ];
    }
    setState(prev => {
      if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
      return {
        ...prev, photo: file, photoThumbnailUrl: thumbUrl, photoDimensions: dims,
        photoWarning: warning, photoWarningTips: warningTips,
        photoError: null, photoErrorTips: null,
      };
    });
  }, []);

  const selectStyle = useCallback((styleId) => {
    const style = STYLES.find(s => s.id === styleId);
    if (!style || !style.available) return;
    // If the incoming style doesn't support the currently-selected background
    // mode, snap back to 'auto' so the customer never lands on an unsupported
    // combo (e.g. picking Dark under Oil Paint, then switching to Minimal).
    setState(prev => {
      const allowed = (style.backgrounds || ['auto']);
      const nextMode = allowed.includes(prev.backgroundMode || 'auto')
        ? (prev.backgroundMode || 'auto')
        : 'auto';
      return { ...prev, selectedStyleId: styleId, backgroundMode: nextMode };
    });
  }, []);

  const generatingRef = useRef(false);

  const generate = useCallback(async () => {
    if ((!state.photo && !state.imageFilename) || !state.selectedStyleId) return;
    if (generatingRef.current) return; // prevent double-clicks
    generatingRef.current = true;
    update({ stage: STAGES.GENERATING, generationStatus: 'loading', generationError: null, generationErrorTips: null });
    try {
      // If we don't have the File object (restored session / retry from style),
      // fetch the image from the stored URL and create a File from it
      let imageFile = state.photo;
      if (!imageFile && (state.originalPhotoUrl || state.imageFilename)) {
        // Use original photo URL (not the generated portrait) for re-generation
        const imgUrl = state.originalPhotoUrl
          || `${API_BASE}/preview/${state.imageFilename}`;
        try {
          const resp = await fetch(imgUrl);
          if (!resp.ok) throw new Error('fetch failed');
          const blob = await resp.blob();
          imageFile = new File([blob], 'pet-photo.jpg', { type: blob.type || 'image/jpeg' });
        } catch (e) {
          update({
            stage: STAGES.STYLE, generationStatus: 'idle',
            generationError: 'Could not reload your photo.',
            generationErrorTips: ['Please upload it again, then pick your style.'],
          });
          generatingRef.current = false;
          return;
        }
      }
      const result = await generateWithRetry({
        imageFile,
        styleId: state.selectedStyleId,
        petName: state.petName,
        termsAcceptedAt: state.termsAcceptedAt,
        backgroundMode: state.backgroundMode || 'auto',
      });
      // Try to convert preview URLs to base64 for localStorage,
      // but always keep the original URLs as fallback
      const dataUrls = await Promise.all(result.previews.map(imageUrlToDataUrl));
      const validDataUrls = dataUrls.filter(Boolean);
      const newState = {
        stage: STAGES.PREVIEW, generationStatus: 'success',
        previewImages: validDataUrls.length ? validDataUrls : result.previews,
        previewDataUrls: validDataUrls,
        previewCdnUrls: result.previews,  // always save original URLs as fallback
        originalPhotoUrl: result.originalPhoto || state.originalPhotoUrl || '',
        printFileUrl: result.printFileUrl || '',  // hi-res PNG for Printful
        selectedPreviewIndex: 0, jobId: result.jobId, restoredSession: false,
        imageFilename: result.filename, generationError: null, generationErrorTips: null,
      };
      update(newState);
      saveSession({ ...state, ...newState });
      addToLibrary({ ...state, ...newState });

      // Fire background mockup generation (non-blocking, with retry)
      if (result.filename) {
        ['canvas'].forEach(productType => {
          const fetchMockup = (retries = 1) => {
            // Prefer the R2 CDN URL so Printful fetches a durable URL rather
            // than the ephemeral Railway /preview/ host. Falls back to filename
            // if previews didn't make it into result for some reason.
            const cdnUrl = (result.previews && result.previews[0]) || '';
            fetch(`${API_BASE}/mockups`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                image_filename: result.filename,
                image_url: cdnUrl,
                product_type: productType,
              }),
            })
            .then(r => {
              if ((r.status === 503 || r.status === 429) && retries > 0) {
                setTimeout(() => fetchMockup(retries - 1), 5000);
                return null;
              }
              return r.ok ? r.json() : null;
            })
            .then(data => {
              if (!data || !data.mockups) return;
              try {
                const session = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
                if (!session.mockups) session.mockups = {};
                session.mockups[productType] = data.mockups;
                localStorage.setItem(LS_KEY, JSON.stringify(session));
              } catch (e) { /* ignore storage errors */ }
            })
            .catch(() => { if (retries > 0) setTimeout(() => fetchMockup(retries - 1), 5000); });
          };
          fetchMockup();
        });
      }
    } catch (err) {
      const msg = err.message || '';
      const code = err.code || '';
      const detail = (err.detail || '').toString();
      const status = err.status || 0;
      let userError = 'Something went wrong on our end.';
      let userTips = ['Your photo and style are saved — try again in a moment.'];
      let sendToUpload = false;

      if (msg === 'TIMEOUT') {
        userError = 'This is taking longer than usual.';
        userTips = ['Please try again — it usually works on the second attempt.'];
      } else if (msg === 'BUSY' || status === 503) {
        userError = 'Our servers are busy right now.';
        userTips = ['Please wait a moment, then try again.'];
      } else if (code === 'rate_limited' || status === 429) {
        userError = msg || 'Too many tries in a row.';
        userTips = ['Please wait a minute before generating again.'];
      } else if (code === 'turnstile_failed') {
        userError = 'We couldn\u2019t verify you\u2019re human.';
        userTips = ['Please complete the verification challenge, then try again.'];
      } else if (code === 'terms_stale' || code === 'terms_required' || code === 'terms_invalid') {
        userError = 'The photo-terms acceptance didn\u2019t go through.';
        userTips = ['Please re-check the terms box on the upload step, then try again.'];
        sendToUpload = true;
      } else if (/only create portraits of pets/i.test(msg)) {
        // Gemini classifier returned is_pet=false. Inspect the free-text
        // `detail` it returned and map it to a specific, actionable message
        // so the user understands exactly why their photo was rejected.
        const d = detail.toLowerCase();
        if (/human|person|people|man|woman|child|baby|selfie|face of a/.test(d)) {
          userError = 'This looks like a photo of a person, not a pet.';
          userTips = [
            'Upload a clear photo where your dog, cat, or other pet is the main subject.',
            'If your pet is in the photo with you, try a crop that shows just your pet.',
          ];
        } else if (/screenshot|screen\s?shot|app|website|browser|meme/.test(d)) {
          userError = 'This looks like a screenshot or meme, not an original photo.';
          userTips = [
            'Upload the original photo from your camera roll instead of a screenshot.',
            'Screenshots are usually low-resolution and won\u2019t print well.',
          ];
        } else if (/cartoon|drawing|illustration|painting|sketch|animated|ai[-\s]?generated|stuffed|plush|toy/.test(d)) {
          userError = 'This looks like a drawing, cartoon, or toy — we need a real photograph.';
          userTips = [
            'Please upload an actual photo of your real pet taken with your camera.',
            'AI-generated or cartoon images won\u2019t work with our process.',
          ];
        } else if (/logo|text|sign|document|book|poster/.test(d)) {
          userError = 'We couldn\u2019t find a pet in this image.';
          userTips = [
            'Make sure your pet is the main subject, with their face clearly visible.',
            'Avoid photos that are mostly text, logos, or documents.',
          ];
        } else if (/landscape|scenery|building|car|food|object|plant|flower/.test(d)) {
          userError = 'We couldn\u2019t find a pet in this photo.';
          userTips = [
            'Upload a photo where your pet is the main subject and clearly in frame.',
            'Avoid photos of objects, landscapes, or food.',
          ];
        } else if (/wild|lion|tiger|bear|dolphin|elephant|giraffe|zoo/.test(d)) {
          userError = 'We only create portraits of domesticated pets.';
          userTips = ['Try a photo of your dog, cat, rabbit, bird, or other household pet.'];
        } else if (/blank|solid|color|black|white|empty|unidentifiable|unclear/.test(d)) {
          userError = 'We couldn\u2019t identify anything in this image.';
          userTips = [
            'The photo may be blank, too dark, or corrupted — please try a different one.',
            'Make sure the photo opens normally in your Photos app before uploading.',
          ];
        } else if (/nsfw|violent|inappropriate|offensive/.test(d)) {
          userError = 'That image can\u2019t be used.';
          userTips = ['Please upload a normal photo of your pet.'];
        } else if (detail) {
          // Fallback: surface Gemini\u2019s free-text reason verbatim so the user
          // still gets context, even if our keyword rules didn\u2019t match.
          userError = 'We couldn\u2019t use this photo: ' + detail;
          userTips = [
            'Upload a clear, well-lit photo where your pet\u2019s face is visible.',
            'Use the original photo from your camera roll — not a screenshot or download.',
          ];
        } else {
          userError = msg;
          userTips = [
            'Upload a clear, front-facing photo of your dog, cat, or other pet.',
            'Good lighting and a visible face give the best results.',
          ];
        }
        sendToUpload = true;
      } else if (/verify your photo/i.test(msg)) {
        userError = 'We couldn\u2019t read this photo.';
        userTips = [
          'The file may be damaged or in an unsupported format.',
          'Re-save the photo as JPG or PNG in your Photos app, then try again.',
        ];
        sendToUpload = true;
      } else if (/unsupported file type|real jpg|real png|real webp|content-type/i.test(msg)) {
        userError = 'We can only accept JPG, PNG, or WebP photos.';
        userTips = [
          'iPhone HEIC photos: open in Photos, tap Share \u2192 Mail to convert to JPG.',
          'Most photo apps can export as JPG or PNG under \u201cShare\u201d or \u201cExport As\u201d.',
        ];
        sendToUpload = true;
      } else if (/pet name/i.test(msg)) {
        userError = msg;
        userTips = ['Use 1\u201320 letters, numbers, spaces, hyphens, periods, or apostrophes.'];
        sendToUpload = true;
      } else if (msg === 'Failed to fetch' || msg.includes('NetworkError') || msg.includes('Load failed') || /network/i.test(msg)) {
        userError = 'We couldn\u2019t reach our servers.';
        userTips = ['Check your internet connection, then try again.'];
      } else if (msg && msg !== 'Generation failed') {
        // Pass through a specific backend message (e.g. worker-level failure)
        userError = msg;
        userTips = ['Your photo and style are saved — try again in a moment.'];
      }

      // PDP-initiated flows skip the UPLOAD step, so bouncing them to
      // UPLOAD on a terms/validation error can create a confusing loop.
      // If the user never accepted terms yet (i.e. never went through
      // UPLOAD in this session), route terms errors back to STYLE so
      // they see the inline PhotoLicenseConsent there instead.
      let nextStage;
      if (sendToUpload) {
        const termsErr = code === 'terms_required' || code === 'terms_stale' || code === 'terms_invalid';
        nextStage = (termsErr && !state.termsAccepted) ? STAGES.STYLE : STAGES.UPLOAD;
      } else {
        nextStage = STAGES.PREVIEW;
      }
      update({
        stage: nextStage,
        generationStatus: 'error',
        generationError: userError,
        generationErrorTips: userTips,
      });
    } finally {
      generatingRef.current = false;
    }
  }, [state.photo, state.imageFilename, state.originalPhotoUrl, state.selectedStyleId, state.petName, state.backgroundMode, state.termsAccepted, state.termsAcceptedAt, state.stage, update]);

  const selectPreview = useCallback((idx) => update({ selectedPreviewIndex: idx }), [update]);
  const goToStage = useCallback((stage) => update({ stage }), [update]);

  const retryFromUpload = useCallback(() => {
    setState(prev => {
      if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
      return { ...prev, stage: STAGES.UPLOAD, photo: null, photoThumbnailUrl: null, photoDimensions: null, photoWarning: null, photoError: null, ...GENERATION_RESET };
    });
  }, []);

  const retryFromStyle = useCallback(() => {
    update({ stage: STAGES.STYLE, selectedStyleId: null, ...GENERATION_RESET });
  }, [update]);

  // Start Over — clear the in-flight session from localStorage AND reset every
  // piece of flow state back to initial. The library (petPrintables_library,
  // different key) is intentionally left alone so previous portraits remain
  // accessible via "Your Saved Portraits". Also clears the pending PDP
  // handoff so a refresh doesn't re-hydrate the just-cleared photo.
  const startFresh = useCallback(() => {
    clearSession();
    clearPending();
    setState(prev => {
      if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
      return {
        stage: STAGES.UPLOAD,
        photo: null, photoThumbnailUrl: null, photoDimensions: null,
        photoWarning: null, photoWarningTips: null,
        photoError: null, photoErrorTips: null,
        petName: '', selectedStyleId: null,
        generationStatus: 'idle',
        generationError: null, generationErrorTips: null,
        previewImages: [], previewDataUrls: [], previewCdnUrls: [],
        selectedPreviewIndex: 0,
        fontSize: 'var(--text-sm)',
        backgroundMode: 'auto',
        imageFilename: '', originalPhotoUrl: '', printFileUrl: '',
        jobId: null, restoredSession: false,
        pendingPhoto: null,
        termsAccepted: false, termsAcceptedAt: null,
      };
    });
  }, []);

  return {
    state, setPhoto, selectStyle, generate, selectPreview, goToStage,
    retryFromUpload, retryFromStyle, startFresh, update,
    canContinueFromUpload: state.photo && !state.photoError && state.termsAccepted,
    canGenerate: (state.photo || state.imageFilename) && state.selectedStyleId && state.termsAccepted,
  };
}

/* ── Shared style fragments ────────────────────────────────── */

const s = {
  primaryBtn: {
    fontFamily: fontSans, fontWeight: 600, fontSize: 'var(--text-xs)',
    letterSpacing: '0.12em', textTransform: 'uppercase',
    background: tokens.colorCta, color: tokens.colorWhite,
    border: 'none', borderRadius: tokens.radiusButton,
    padding: '0 28px', minHeight: '52px', width: '100%',
    cursor: 'pointer', transition: 'background 0.2s', outline: 'none',
  },
  primaryBtnDisabled: { opacity: 0.35, cursor: 'not-allowed' },
  outlineBtn: {
    fontFamily: fontSans, fontWeight: 500, fontSize: 'var(--text-xs)',
    letterSpacing: '0.12em', textTransform: 'uppercase',
    color: tokens.colorCta, background: tokens.colorWhite,
    border: `1.5px solid ${tokens.colorBorder}`, borderRadius: tokens.radiusButton,
    padding: '0 28px', minHeight: '48px', minWidth: '200px',
    cursor: 'pointer', outline: 'none', transition: 'border-color 0.15s',
  },
  secondaryLink: {
    fontFamily: fontSans, fontWeight: 400, fontSize: 'var(--text-sm)',
    color: tokens.colorMuted, background: 'none', border: 'none',
    padding: '8px 0', cursor: 'pointer', outline: 'none',
    textDecoration: 'none', transition: 'color 0.15s',
  },
  secondaryLinkUnderline: {
    fontFamily: fontSans, fontWeight: 400, fontSize: 'var(--text-sm)',
    color: tokens.colorMuted, background: 'none', border: 'none',
    padding: '8px 0', cursor: 'pointer', outline: 'none',
    textDecoration: 'underline', transition: 'color 0.15s',
  },
  bodyMuted: {
    fontFamily: fontSans, fontWeight: 400, fontSize: 'var(--text-sm)',
    color: tokens.colorMuted, lineHeight: 1.6, margin: 0,
  },
  serifItalic: {
    fontFamily: fontSerif, fontWeight: 400, fontStyle: 'italic',
    color: tokens.colorBrand,
  },
  smallCaps: {
    fontFamily: fontSans, fontWeight: 500, fontSize: 'var(--text-xs)',
    letterSpacing: '0.12em', textTransform: 'uppercase',
    color: tokens.colorMuted,
  },
  serifHeading: {
    fontFamily: fontSerif, fontWeight: 400, fontStyle: 'italic',
    fontSize: 'clamp(var(--text-lg), 5vw, var(--text-xl))', color: tokens.colorBrand,
    margin: '0 0 20px 0', lineHeight: 1.2,
  },
  photoGuidelines: {
    fontFamily: fontSerif, fontStyle: 'italic', fontSize: 'var(--text-base)',
    color: tokens.colorMuted, lineHeight: 1.8, marginBottom: '32px',
  },
  input: {
    fontFamily: fontSans, fontSize: 'var(--text-base)', color: tokens.colorBrand,
    background: 'transparent', border: 'none',
    borderBottom: `1.5px solid ${tokens.colorBorder}`, borderRadius: 0,
    padding: '12px 0', width: '100%', boxSizing: 'border-box',
    outline: 'none', minHeight: '48px', transition: 'border-color 0.15s',
  },
  sectionWrap: { animation: 'pf-fade-in 0.45s ease forwards' },
};

const primaryBtnStyle = (enabled) => ({ ...s.primaryBtn, ...(enabled ? {} : s.primaryBtnDisabled) });

/* ── StepIndicator ─────────────────────────────────────────── */

const STEP_LABELS = ['Upload', 'Style', 'Preview', 'Customize'];

function StepIndicator({ current, total = 4 }) {
  return React.createElement('div', {
    style: { marginBottom: '28px' },
    'aria-label': `Step ${current} of ${total}: ${STEP_LABELS[current - 1]}`,
    role: 'progressbar',
    'aria-valuenow': current,
    'aria-valuemin': 1,
    'aria-valuemax': total,
  },
    // Step dots + labels
    React.createElement('div', {
      style: {
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
        position: 'relative', padding: '0 4px', marginBottom: '0',
      },
    },
      // Connecting line (behind dots)
      React.createElement('div', {
        style: {
          position: 'absolute', top: '13px', left: '30px', right: '30px',
          height: '2px', background: tokens.colorBorder, zIndex: 0,
        },
      },
        // Filled portion
        React.createElement('div', {
          style: {
            height: '100%', background: tokens.colorAccent,
            width: `${((current - 1) / (total - 1)) * 100}%`,
            transition: 'width 0.4s ease',
            borderRadius: '1px',
          },
        }),
      ),
      // Step circles + labels
      STEP_LABELS.slice(0, total).map((label, i) => {
        const stepNum = i + 1;
        const isComplete = stepNum < current;
        const isCurrent = stepNum === current;
        const isFuture = stepNum > current;
        return React.createElement('div', {
          key: i,
          style: {
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            position: 'relative', zIndex: 1, flex: '0 0 auto',
          },
        },
          // Paw icon
          React.createElement('div', {
            style: {
              width: isCurrent ? '36px' : '32px',
              height: isCurrent ? '36px' : '32px',
              borderRadius: '50%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              transition: 'all 0.3s ease',
              background: isComplete || isCurrent ? tokens.colorAccent : tokens.colorWhite,
              border: isFuture ? `1.5px solid ${tokens.colorBorder}` : `2px solid ${tokens.colorAccent}`,
              boxSizing: 'border-box',
              boxShadow: isCurrent ? `0 0 0 4px ${tokens.colorAccent}26` : 'none',
            },
          },
            // Paw print SVG
            React.createElement('svg', {
              width: isCurrent ? '20' : '18', height: isCurrent ? '20' : '18', viewBox: '0 0 24 24',
              fill: isComplete || isCurrent ? tokens.colorWhite : tokens.colorMuted,
              xmlns: 'http://www.w3.org/2000/svg',
              'aria-hidden': true,
            },
              // Pad (main heel)
              React.createElement('ellipse', { cx: '12', cy: '17', rx: '5', ry: '4' }),
              // Upper left toe
              React.createElement('ellipse', { cx: '6.5', cy: '10.5', rx: '2', ry: '2.6' }),
              // Upper right toe
              React.createElement('ellipse', { cx: '17.5', cy: '10.5', rx: '2', ry: '2.6' }),
              // Top left toe
              React.createElement('ellipse', { cx: '9.5', cy: '6.5', rx: '1.8', ry: '2.4' }),
              // Top right toe
              React.createElement('ellipse', { cx: '14.5', cy: '6.5', rx: '1.8', ry: '2.4' }),
            ),
          ),
          // Label
          React.createElement('span', {
            style: {
              fontFamily: fontSans, fontSize: 'var(--text-xs)', fontWeight: isCurrent ? 700 : 500,
              color: isCurrent ? tokens.colorBrand : tokens.colorMuted,
              marginTop: '7px', textTransform: 'uppercase', letterSpacing: '0.06em',
            },
          }, `${i + 1}. ${label}`),
        );
      }),
    ),
  );
}

/* ── Shared upload sub-components ──────────────────────────── */

const dropzoneStyle = {
  minHeight: '280px',
  border: `1.5px dashed ${tokens.colorBorder}`,
  borderRadius: tokens.radiusCard,
  background: tokens.colorWhite,
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '16px',
  cursor: 'pointer',
  transition: 'border-color 0.15s',
  padding: '32px 24px',
};

function HiddenFileInput({ inputRef, onChange, capture }) {
  return React.createElement('input', {
    ref: inputRef, type: 'file', accept: 'image/jpeg,image/png',
    ...(capture ? { capture } : {}),
    onChange, style: { display: 'none' }, 'aria-hidden': true,
  });
}

const PET_NAME_MAX = 20;

function PetNameInput({ id, value, onChange }) {
  const len = (value || '').length;
  const nearLimit = len >= PET_NAME_MAX - 3;
  return React.createElement('div', { style: { marginBottom: '28px' } },
    React.createElement('label', {
      htmlFor: id,
      style: {
        ...s.smallCaps, display: 'flex', alignItems: 'baseline',
        justifyContent: 'space-between', marginBottom: '4px',
      },
    },
      React.createElement('span', null, "Your pet\u2019s name"),
      React.createElement('span', {
        'aria-live': 'polite',
        style: {
          fontFamily: fontSans, fontSize: 'var(--text-xs)', letterSpacing: 0,
          textTransform: 'none', fontWeight: 500,
          color: nearLimit ? tokens.colorWarning : tokens.colorMuted,
        },
      }, `${len}/${PET_NAME_MAX}`),
    ),
    React.createElement('input', {
      id, type: 'text', placeholder: 'e.g. Biscuit',
      value,
      onChange: (e) => {
        // Enforce the cap even on paste — slice before calling the upstream
        // change handler so state never holds a value > PET_NAME_MAX.
        if (e.target.value.length > PET_NAME_MAX) {
          e.target.value = e.target.value.slice(0, PET_NAME_MAX);
        }
        onChange(e);
      },
      maxLength: PET_NAME_MAX,
      style: s.input,
    }),
    React.createElement('p', {
      style: {
        ...s.bodyMuted, fontSize: 'var(--text-xs)', marginTop: '6px', marginBottom: 0,
      },
    }, `Optional. Up to ${PET_NAME_MAX} characters — short names print the cleanest.`),
  );
}

function PhotoGuidelines() {
  return React.createElement('div', { style: s.photoGuidelines },
    React.createElement('p', { style: { margin: 0 } }, 'Face clearly visible, good lighting'),
    React.createElement('p', { style: { margin: 0 } }, 'No heavy filters or screenshots'),
    React.createElement('p', { style: { margin: 0 } }, 'One pet per photo works best'),
  );
}

/* ── UploadStep ────────────────────────────────────────────── */

/* ── YourPortraits gallery — shows saved portraits at top of upload step ─── */
function YourPortraits({ onOrderPortrait }) {
  const library = loadLibrary();
  if (!library.length) return null;

  return React.createElement('div', {
    style: {
      marginBottom: '24px', padding: '16px',
      background: tokens.colorWhite, borderRadius: tokens.radiusCard,
      border: `1px solid ${tokens.colorBorder}`,
    },
  },
    React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' },
    },
      React.createElement('p', { style: { ...s.smallCaps, margin: 0 } }, 'Your saved portraits'),
      React.createElement('span', {
        style: { fontFamily: fontSans, fontSize: 'var(--text-xs)', color: tokens.colorMuted },
      }, `${library.length} saved`),
    ),

    React.createElement('div', {
      style: { display: 'flex', gap: '10px', overflowX: 'auto', paddingBottom: '4px', WebkitOverflowScrolling: 'touch' },
    },
      library.map(p => {
        const daysLeft = daysRemaining(p.createdAt);
        return React.createElement('div', {
          key: p.id,
          style: { flex: '0 0 auto', width: '120px', textAlign: 'left' },
        },
          React.createElement('button', {
            type: 'button',
            onClick: () => onOrderPortrait(p),
            style: {
              width: '120px', padding: 0, border: 'none', background: 'none',
              cursor: 'pointer', outline: 'none', textAlign: 'left',
            },
            'aria-label': `Order ${p.petName}'s portrait`,
          },
            React.createElement('img', {
              src: p.previewUrl, alt: `${p.petName} portrait`,
              style: {
                width: '120px', height: '150px', objectFit: 'cover',
                borderRadius: '10px', display: 'block',
                boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
              },
            }),
            React.createElement('p', {
              style: {
                fontFamily: fontSerif, fontStyle: 'italic', fontSize: 'var(--text-base)',
                color: tokens.colorBrand, margin: '8px 0 2px', lineHeight: 1.2,
              },
            }, p.petName),
            React.createElement('p', {
              style: {
                fontFamily: fontSans, fontSize: 'var(--text-xs)', color: daysLeft <= 3 ? tokens.colorWarning : tokens.colorMuted,
                margin: 0,
              },
            }, daysLeft <= 3 ? `Expires in ${daysLeft}d` : `${daysLeft}d left`),
          ),
        );
      }),
    ),

    React.createElement('p', {
      style: {
        fontFamily: fontSans, fontSize: 'var(--text-xs)', color: tokens.colorMuted,
        margin: '12px 0 0', textAlign: 'center',
      },
    }, 'Tap to order again, or create a new one below'),
  );
}

function UploadStep({ state, setPhoto, update, canContinue, onContinue }) {
  const cameraRef = useRef(null);
  const fileRef = useRef(null);

  const handleFile = useCallback((e) => {
    const file = e.target.files?.[0];
    if (file) setPhoto(file);
  }, [setPhoto]);

  const handlePetName = useCallback((e) => update({ petName: e.target.value }), [update]);
  const hasPhoto = state.photo && !state.photoError;

  // Order an existing portrait — restore its data into session and go to step 4
  const handleOrderSaved = useCallback((portrait) => {
    try {
      const saved = {
        version: 1,
        petName: portrait.petName,
        styleId: portrait.styleId,
        fontSize: 'var(--text-sm)',
        jobId: portrait.jobId,
        previewDataUrls: [],
        previewCdnUrls: [portrait.previewUrl].filter(Boolean),
        selectedPreviewIndex: 0,
        generatedAt: portrait.createdAt,
        imageFilename: portrait.imageFilename || '',
        originalPhotoUrl: portrait.originalPhotoUrl || '',
        printFileUrl: portrait.printUrl || portrait.previewUrl,
      };
      localStorage.setItem(LS_KEY, JSON.stringify(saved));
      // Reload — the flow will restore the session and land at PREVIEW
      window.location.reload();
    } catch (e) {
      console.error('Failed to restore portrait:', e);
    }
  }, []);

  return React.createElement('div', { style: s.sectionWrap },
    React.createElement(StepIndicator, { current: 1 }),

    // Saved portraits (if any) — shown above the upload form
    React.createElement(YourPortraits, { onOrderPortrait: handleOrderSaved }),

    // Pet name FIRST — emotional hook, personal immediately
    React.createElement(PetNameInput, { id: 'pf-pet-name', value: state.petName, onChange: handlePetName }),

    // Photo upload — compact and action-oriented
    React.createElement('p', {
      style: { ...s.smallCaps, margin: '0 0 10px' },
    }, 'Upload the photo that makes you smile'),

    hasPhoto
      ? React.createElement('div', {
          style: {
            display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '20px',
            padding: '12px 16px', background: tokens.colorWhite,
            borderRadius: tokens.radiusCard, border: `1px solid ${tokens.colorBorder}`,
          },
        },
          React.createElement('img', {
            src: state.photoThumbnailUrl, alt: 'Selected pet photo',
            style: { width: '72px', height: '72px', borderRadius: '12px', objectFit: 'cover' },
          }),
          React.createElement('div', { style: { flex: 1 } },
            React.createElement('p', {
              style: { fontFamily: fontSans, fontSize: 'var(--text-sm)', fontWeight: 500, color: tokens.colorBrand, margin: '0 0 2px' },
            }, 'Photo uploaded \u2713'),
            React.createElement('button', {
              type: 'button', style: { ...s.secondaryLinkUnderline, fontSize: 'var(--text-sm)' },
              onClick: () => fileRef.current?.click(),
            }, 'Change photo'),
          ),
        )
      : React.createElement('div', {
          style: {
            ...dropzoneStyle,
            minHeight: '180px', padding: '24px 20px', gap: '12px',
          },
          role: 'group', 'aria-label': 'Photo upload area',
          onDragOver: (e) => e.preventDefault(),
          onDrop: (e) => { e.preventDefault(); const file = e.dataTransfer?.files?.[0]; if (file) setPhoto(file); },
        },
          React.createElement(CameraIcon, { size: 32 }),
          React.createElement('button', {
            type: 'button', style: { ...s.primaryBtn, width: 'auto', padding: '12px 32px', fontSize: 'var(--text-sm)' },
            onClick: () => fileRef.current?.click(),
          }, 'UPLOAD PHOTO'),
        ),

    // Hidden file input (shared)
    React.createElement(HiddenFileInput, { inputRef: fileRef, onChange: handleFile }),

    // Warning / error
    state.photoWarning && React.createElement('div', {
      role: 'alert', style: { marginBottom: '12px' },
    },
      React.createElement('p', {
        style: { ...s.bodyMuted, color: tokens.colorWarning, margin: 0 },
      }, state.photoWarning),
      state.photoWarningTips && state.photoWarningTips.length > 0 &&
        React.createElement('ul', {
          style: {
            ...s.bodyMuted, color: tokens.colorWarning,
            margin: '6px 0 0', paddingLeft: '18px',
          },
        },
          state.photoWarningTips.map((tip, i) =>
            React.createElement('li', { key: i, style: { marginBottom: '2px' } }, tip),
          ),
        ),
    ),
    state.photoError && React.createElement('div', {
      role: 'alert', style: { marginTop: '10px' },
    },
      React.createElement('p', {
        style: { ...s.bodyMuted, color: tokens.colorError, margin: 0 },
      }, state.photoError),
      state.photoErrorTips && state.photoErrorTips.length > 0 &&
        React.createElement('ul', {
          style: {
            ...s.bodyMuted, color: tokens.colorError,
            margin: '6px 0 0', paddingLeft: '18px',
          },
        },
          state.photoErrorTips.map((tip, i) =>
            React.createElement('li', { key: i, style: { marginBottom: '2px' } }, tip),
          ),
        ),
    ),

    // Photo tips — MBR "Relief": disarms the "do I need a pro photo?"
    // anxiety, then lists what we actually need in plain language.
    React.createElement('p', {
      style: {
        ...s.bodyMuted, fontSize: 'var(--text-xs)', textAlign: 'center',
        margin: '14px auto 24px', maxWidth: '380px', lineHeight: 1.5,
      },
    }, 'Phone photos work great. Just look for a clear face, decent lighting, and one pet per photo — we handle the rest.'),

    // Photo license + Terms acceptance — required before generating
    React.createElement(PhotoLicenseConsent, {
      accepted: state.termsAccepted,
      onChange: (checked) => update({
        termsAccepted: checked,
        termsAcceptedAt: checked ? new Date().toISOString() : null,
      }),
    }),

    // Continue button — always visible, disabled state communicates what's needed
    React.createElement('button', {
      type: 'button',
      style: primaryBtnStyle(canContinue),
      disabled: !canContinue, onClick: onContinue,
      'aria-label': 'Continue to style selection',
    },
      canContinue
        ? iconLabel(React.createElement(ArrowRightIcon), 'CHOOSE YOUR STYLE', 'right')
        : (!state.photo
            ? 'ADD A PHOTO TO CONTINUE'
            : (!state.termsAccepted
                ? 'ACCEPT PHOTO TERMS TO CONTINUE'
                : 'ADD A PHOTO TO CONTINUE'))
    ),
  );
}

/* ── PhotoLicenseConsent ───────────────────────────────────── */

function PhotoLicenseConsent({ accepted, onChange }) {
  return React.createElement('label', {
    htmlFor: 'pf-terms-accept',
    style: {
      display: 'flex', alignItems: 'flex-start', gap: '10px',
      padding: '12px 14px', margin: '0 0 18px',
      background: tokens.colorWhite,
      border: `1px solid ${accepted ? tokens.colorBrand : tokens.colorBorder}`,
      borderRadius: tokens.radiusCard, cursor: 'pointer',
      transition: 'border-color 0.15s ease',
    },
  },
    React.createElement('input', {
      id: 'pf-terms-accept', type: 'checkbox',
      checked: !!accepted,
      onChange: (e) => onChange(e.target.checked),
      style: { marginTop: '3px', width: '16px', height: '16px',
               accentColor: tokens.colorBrand, flexShrink: 0 },
      'aria-describedby': 'pf-terms-text',
    }),
    React.createElement('span', {
      id: 'pf-terms-text',
      style: { fontFamily: fontSans, fontSize: 'var(--text-xs)', lineHeight: 1.5,
               color: tokens.colorMuted },
    },
      'I confirm I own all rights to this photo (or have permission from the ' +
      'owner), and I grant Pet Printables a non-exclusive licence to reproduce, ' +
      'modify, and print it solely to fulfil my order. ',
      React.createElement('a', {
        href: '/policies/terms-of-service', target: '_blank', rel: 'noopener',
        style: { color: tokens.colorBrand, textDecoration: 'underline' },
        onClick: (e) => e.stopPropagation(),
      }, 'Read full terms'),
      '.',
    ),
  );
}

/* ── StyleStep ─────────────────────────────────────────────── */

function StyleStep({ state, update, selectStyle, onGenerate, canGenerate, onBack }) {
  // Preload all style fonts so they're ready by preview step
  useEffect(() => {
    STYLES.forEach(style => { if (style.available) loadGoogleFont(style.id); });
  }, []);

  return React.createElement('div', { style: s.sectionWrap },
    React.createElement(StepIndicator, { current: 2 }),
    React.createElement('h2', { style: s.serifHeading },
      state.petName
        ? 'Which style feels like ' + state.petName + '?'
        : 'Which style feels like them?'),

    // Inline error banner — shown when a previous generation attempt
    // failed for a reason we want the user to act on from this step
    // (e.g. terms not accepted, photo rejected by the pet classifier).
    state.generationError && React.createElement('div', {
      role: 'alert',
      style: {
        margin: '14px 0 18px', padding: '14px 16px',
        background: '#FEF3F2', border: '1px solid #FDA29B',
        borderRadius: tokens.radiusCard,
      },
    },
      React.createElement('p', {
        style: {
          fontFamily: fontSans, fontWeight: 600, fontSize: 'var(--text-sm)',
          color: '#912018', margin: '0 0 6px', lineHeight: 1.4,
        },
      }, state.generationError),
      state.generationErrorTips && state.generationErrorTips.length > 0 &&
        React.createElement('ul', {
          style: {
            margin: 0, paddingLeft: '18px',
            fontFamily: fontSans, fontSize: 'var(--text-sm)',
            color: '#7A271A', lineHeight: 1.5,
          },
        },
          state.generationErrorTips.map((tip, i) =>
            React.createElement('li', { key: i }, tip)
          ),
        ),
    ),

    React.createElement('div', {
      style: {
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '10px',
        paddingBottom: '8px',
      },
      role: 'listbox', 'aria-label': 'Portrait style options',
    },
      STYLES.map((style) => {
        const selected = state.selectedStyleId === style.id;
        return React.createElement('button', {
          key: style.id, type: 'button', role: 'option',
          'aria-selected': selected, 'aria-disabled': !style.available,
          'aria-label': `${style.name}${!style.available ? ' \u2014 available soon' : ''}${style.badge ? ` \u2014 ${style.badge}` : ''}`,
          onClick: () => style.available && selectStyle(style.id),
          style: {
            border: selected ? `2px solid ${tokens.colorAccent}` : `1px solid ${tokens.colorBorder}`,
            borderRadius: tokens.radiusCard, background: selected ? tokens.colorAccentLight : tokens.colorWhite,
            padding: 0, cursor: style.available ? 'pointer' : 'default',
            textAlign: 'left', outline: 'none', overflow: 'hidden', transition: 'all 0.2s',
            position: 'relative',
          },
        },
          // Thumbnail — real example portrait
          React.createElement('div', {
            style: { width: '100%', aspectRatio: '1/1', background: tokens.colorSurface, position: 'relative', overflow: 'hidden' },
          },
            style.exampleImage && React.createElement('img', {
              src: _pfAssetBase + style.exampleImage, alt: style.name + ' example',
              loading: 'lazy',
              style: { width: '100%', height: '100%', objectFit: 'cover', display: 'block' },
            }),
            // Badge
            style.badge && React.createElement('span', {
              style: {
                position: 'absolute', top: '8px', left: '8px',
                ...s.smallCaps, fontSize: 'var(--text-xs)', letterSpacing: '0.08em',
                color: tokens.colorWhite, background: tokens.colorSuccess,
                borderRadius: '3px', padding: '3px 7px',
              },
            }, style.badge),
            // Selected checkmark
            selected && React.createElement('div', {
              style: {
                position: 'absolute', top: '8px', right: '8px',
                width: '22px', height: '22px', borderRadius: '50%',
                background: tokens.colorAccent, color: tokens.colorWhite,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 'var(--text-xs)',
              },
            }, '\u2713'),
            // Available soon overlay
            !style.available && React.createElement('div', {
              style: {
                position: 'absolute', inset: 0,
                background: 'rgba(250, 248, 245, 0.6)',
                backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              },
            },
              React.createElement('span', {
                style: {
                  ...s.smallCaps, fontSize: 'var(--text-xs)', fontWeight: 700,
                  color: tokens.colorBrand, background: 'rgba(255,255,255,0.95)',
                  borderRadius: '4px', padding: '6px 12px',
                },
              }, 'Coming soon'),
            ),
          ),
          // Card body — style name + font preview
          React.createElement('div', { style: { padding: '8px 8px 10px' } },
            React.createElement('p', {
              style: {
                fontFamily: fontSans, fontWeight: 600, fontSize: 'var(--text-xs)',
                color: selected ? tokens.colorAccent : tokens.colorBrand,
                margin: '0 0 2px', lineHeight: 1.3,
              },
            }, style.name),
            // Show pet name preview in the style's font (or "Abc" if no name)
            React.createElement('p', {
              style: {
                fontFamily: (STYLE_FONTS[style.id] || {}).css || fontSerif,
                fontWeight: 700, fontSize: 'var(--text-sm)',
                color: tokens.colorMuted,
                margin: 0, lineHeight: 1.2,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              },
            }, state.petName || 'Abc'),
          ),
        );
      }),
    ),

    // Celebration banner — appears the moment a style is picked. Confirms
    // the choice with a per-style affirmation and a brief sparkle burst.
    // Persistent (so the customer keeps seeing what they chose while they
    // tweak background/name), but the burst animation is one-shot via the
    // key= prop forcing a re-mount on every change.
    state.selectedStyleId && (() => {
      const aff = STYLE_AFFIRMATIONS[state.selectedStyleId] || { tag: 'Lovely choice', line: 'going to look great.' };
      const styleName = styleNameFor(state.selectedStyleId);
      // Lines are written lowercase so they can sit either after
      // "{Pet} is " or stand on their own with a capitalised first letter.
      const personalLine = state.petName
        ? state.petName + ' is ' + aff.line
        : aff.line.charAt(0).toUpperCase() + aff.line.slice(1);
      return React.createElement('div', {
        key: 'celebrate-' + state.selectedStyleId,
        role: 'status',
        'aria-live': 'polite',
        style: {
          marginTop: '20px', position: 'relative',
          padding: '16px 18px',
          background: 'linear-gradient(135deg, ' + tokens.colorAccentLight + ' 0%, ' + tokens.colorWhite + ' 70%)',
          border: '1px solid ' + tokens.colorAccent,
          borderRadius: tokens.radiusCard,
          overflow: 'hidden',
          animation: 'pf-style-celebrate 0.55s cubic-bezier(.2,1.2,.4,1) both',
        },
      },
        React.createElement('div', {
          'aria-hidden': true,
          style: {
            position: 'absolute', top: '14px', left: '14px',
            width: 0, height: 0, pointerEvents: 'none',
          },
        },
          [0, 1, 2, 3, 4, 5].map(i => React.createElement('span', {
            key: i,
            style: {
              position: 'absolute', top: 0, left: 0,
              width: '6px', height: '6px', borderRadius: '50%',
              background: i % 2 === 0 ? tokens.colorAccent : tokens.colorSuccess,
              opacity: 0,
              animation: 'pf-sparkle-' + i + ' 0.9s ease-out 0.05s forwards',
            },
          })),
        ),
        React.createElement('div', { style: { display: 'flex', alignItems: 'flex-start', gap: '10px' } },
          // Decorative checkmark badge — replaces the previous sparkle glyph
          React.createElement('span', {
            'aria-hidden': true,
            style: {
              flexShrink: 0,
              width: '20px', height: '20px', borderRadius: '50%',
              background: tokens.colorAccent, color: tokens.colorWhite,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 'var(--text-xs)', fontWeight: 700, marginTop: '2px',
            },
          }, '✓'),
          React.createElement('div', { style: { flex: 1, minWidth: 0 } },
            React.createElement('p', {
              style: { ...s.smallCaps, margin: '0 0 2px', color: tokens.colorAccent, fontSize: 'var(--text-xs)' },
            }, aff.tag + ' · ' + styleName),
            React.createElement('p', {
              style: {
                fontFamily: fontSerif, fontStyle: 'italic',
                fontSize: 'var(--text-base)', color: tokens.colorBrand,
                margin: 0, lineHeight: 1.35,
              },
            }, personalLine),
          ),
        ),
      );
    })(),

    // Background mode selector — only shown when the selected style offers
    // more than one background. Styles that only support 'auto' (rare)
    // get the card hidden entirely; styles that support 2 of 3 (e.g. minimal
    // line art: no 'dark') render just the modes that actually work.
    (() => {
      if (!state.selectedStyleId) return null;
      const allowed = backgroundsFor(state.selectedStyleId);
      const visibleOptions = BACKGROUND_OPTIONS.filter(o => allowed.includes(o.id));
      if (visibleOptions.length < 2) return null;
      const columns = visibleOptions.length;
      return React.createElement('div', {
        style: {
          marginTop: '20px', padding: '16px', background: tokens.colorWhite,
          borderRadius: tokens.radiusCard, border: `1px solid ${tokens.colorBorder}`,
        },
      },
        React.createElement('p', {
          style: { ...s.smallCaps, margin: '0 0 10px', fontSize: 'var(--text-xs)', textAlign: 'center' },
        }, 'Background'),
        React.createElement('div', {
          style: { display: 'grid', gridTemplateColumns: `repeat(${columns}, 1fr)`, gap: '8px' },
          role: 'radiogroup', 'aria-label': 'Background mode',
        },
          visibleOptions.map(opt => {
            const active = (state.backgroundMode || 'auto') === opt.id;
            const swatchBg =
              opt.id === 'light' ? 'linear-gradient(135deg, #FAF6EE, #EFE7D9)' :
              opt.id === 'dark'  ? 'linear-gradient(135deg, #2A2620, #1A1714)' :
                                   `linear-gradient(135deg, ${tokens.colorSurface} 0 50%, #2A2620 50% 100%)`;
            return React.createElement('button', {
              key: opt.id, type: 'button', role: 'radio',
              'aria-checked': active,
              'aria-label': `${opt.label} background — ${opt.sub}`,
              onClick: () => {
                update({ backgroundMode: opt.id });
                saveSession({ ...state, backgroundMode: opt.id });
              },
              style: {
                display: 'flex', flexDirection: 'column', alignItems: 'center',
                gap: '6px', padding: '10px 6px',
                border: active ? `2px solid ${tokens.colorAccent}` : `1px solid ${tokens.colorBorder}`,
                borderRadius: '10px',
                background: active ? tokens.colorAccentLight : tokens.colorWhite,
                cursor: 'pointer', outline: 'none', transition: 'all 0.2s',
              },
            },
              React.createElement('span', {
                'aria-hidden': true,
                style: {
                  width: '36px', height: '22px', borderRadius: '5px',
                  background: swatchBg,
                  border: `1px solid ${tokens.colorBorder}`,
                },
              }),
              React.createElement('span', {
                style: {
                  fontFamily: fontSans, fontWeight: 600, fontSize: 'var(--text-xs)',
                  color: active ? tokens.colorAccent : tokens.colorBrand,
                },
              }, opt.label),
              React.createElement('span', {
                style: {
                  fontFamily: fontSans, fontSize: 'var(--text-xs)',
                  color: tokens.colorMuted, lineHeight: 1.2,
                },
              }, opt.sub),
            );
          }),
        ),
      );
    })(),

    // Name font preview + size selector (only when style is selected)
    state.selectedStyleId && state.petName && React.createElement('div', {
      style: {
        marginTop: '20px', padding: '16px', background: tokens.colorWhite,
        borderRadius: tokens.radiusCard, border: `1px solid ${tokens.colorBorder}`,
        textAlign: 'center',
      },
    },
      React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 6px', fontSize: 'var(--text-xs)' } }, 'Name preview'),
      React.createElement('p', {
        style: {
          fontFamily: (STYLE_FONTS[state.selectedStyleId] || {}).css || fontSerif,
          fontWeight: 700,
          fontSize: `${Math.round(24 * (FONT_SIZES.find(f => f.id === (state.fontSize || 'small')) || FONT_SIZES[0]).scale)}px`,
          color: tokens.colorBrand, margin: '0 0 0',
          // modern-shape-art renders the name in ALL CAPS with wider tracking;
          // every other style uses the printed casing as-is. Keep this preview
          // visually aligned with the actual rendered output.
          textTransform: state.selectedStyleId === 'modern-shape-art' ? 'uppercase' : 'none',
          letterSpacing: state.selectedStyleId === 'modern-shape-art' ? '0.10em' : '0.04em',
          transition: 'all 0.3s ease',
        },
      }, state.petName),
    ),

    // Photo license + Terms acceptance — required before generating.
    // Shown here for users who entered the flow from a PDP (skipping the
    // Upload step where this consent normally lives).
    !state.termsAccepted && React.createElement('div', { style: { marginTop: '20px' } },
      React.createElement(PhotoLicenseConsent, {
        accepted: state.termsAccepted,
        onChange: (checked) => update({
          termsAccepted: checked,
          termsAcceptedAt: checked ? new Date().toISOString() : null,
        }),
      }),
    ),

    React.createElement('div', { style: { marginTop: '24px', display: 'flex', flexDirection: 'column', gap: '10px' } },
      React.createElement('button', {
        type: 'button',
        style: primaryBtnStyle(canGenerate),
        disabled: !canGenerate, onClick: onGenerate,
        'aria-label': 'Create my portrait',
      },
        canGenerate
          ? 'CREATE MY PORTRAIT'
          : (!state.selectedStyleId
              ? 'PICK A STYLE FIRST'
              : (!state.termsAccepted
                  ? 'ACCEPT PHOTO TERMS TO CONTINUE'
                  : 'CREATE MY PORTRAIT'))
      ),
      React.createElement('button', {
        type: 'button',
        style: { ...s.secondaryLink, textAlign: 'center', width: '100%' },
        onClick: onBack,
      }, '\u2190 Back'),
    ),
  );
}

/* ── GeneratingState ───────────────────────────────────────── */

const LOADING_PHRASES = [
  'Capturing every whisker\u2026',
  'Mixing the perfect palette\u2026',
  'Loading the treat jar\u2026',
  'Fluffing the fur\u2026',
  'Adjusting the bowtie\u2026',
  'Consulting the art director (a cat)\u2026',
  'Fetching the fine brushes\u2026',
  'Perfecting the toe beans\u2026',
  'Almost there\u2026',
];

/* Flat-vector kitten SVG — faces right. Small pointy ears, slim body, curled tail. */
function KittenSVG() {
  return React.createElement('svg', {
    width: 36, height: 28, viewBox: '0 0 36 28', fill: 'none',
    xmlns: 'http://www.w3.org/2000/svg', 'aria-hidden': true,
  },
    // Tail (curled up, behind body)
    React.createElement('path', {
      d: 'M8,18 Q4,16 5,12 Q6,9 9,10',
      stroke: '#8B7D6B', strokeWidth: 2.5, fill: 'none', strokeLinecap: 'round',
    }),
    // Body — slim
    React.createElement('ellipse', { cx: 18, cy: 18, rx: 10, ry: 6.5, fill: '#8B7D6B' }),
    // Head
    React.createElement('circle', { cx: 28, cy: 13, r: 5.5, fill: '#8B7D6B' }),
    // Pointy left ear
    React.createElement('polygon', { points: '24,8 22,1 26.5,6', fill: '#8B7D6B' }),
    // Pointy right ear
    React.createElement('polygon', { points: '31,8 29,1 33.5,6', fill: '#8B7D6B' }),
    // Inner ears (pink)
    React.createElement('polygon', { points: '24.3,7.5 23,2.5 25.8,6.2', fill: '#D4A89A' }),
    React.createElement('polygon', { points: '31.3,7.5 30,2.5 32.8,6.2', fill: '#D4A89A' }),
    // Eyes — cat-like (slightly narrower)
    React.createElement('ellipse', { cx: 26.2, cy: 12.5, rx: 1.1, ry: 1.3, fill: '#2F2F2A' }),
    React.createElement('ellipse', { cx: 30, cy: 12.5, rx: 1.1, ry: 1.3, fill: '#2F2F2A' }),
    // Eye highlights
    React.createElement('circle', { cx: 26.5, cy: 12, r: 0.4, fill: '#fff' }),
    React.createElement('circle', { cx: 30.3, cy: 12, r: 0.4, fill: '#fff' }),
    // Tiny triangle nose
    React.createElement('polygon', { points: '28.1,14.2 27.5,15 28.7,15', fill: '#D4A89A' }),
    // Whiskers (3 lines)
    React.createElement('line', { x1: 23, y1: 13.5, x2: 20, y2: 12.5, stroke: '#8B7D6B', strokeWidth: 0.5 }),
    React.createElement('line', { x1: 23, y1: 14.5, x2: 20, y2: 14.5, stroke: '#8B7D6B', strokeWidth: 0.5 }),
    React.createElement('line', { x1: 23, y1: 15.5, x2: 20, y2: 16.5, stroke: '#8B7D6B', strokeWidth: 0.5 }),
    // Legs — thin
    React.createElement('line', { x1: 13, y1: 23, x2: 12, y2: 27, stroke: '#8B7D6B', strokeWidth: 1.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 17, y1: 24, x2: 16, y2: 27, stroke: '#8B7D6B', strokeWidth: 1.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 21, y1: 24, x2: 22, y2: 27, stroke: '#8B7D6B', strokeWidth: 1.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 25, y1: 22, x2: 26, y2: 27, stroke: '#8B7D6B', strokeWidth: 1.8, strokeLinecap: 'round' }),
  );
}

/* Flat-vector puppy SVG — faces right. Floppy ears, big muzzle, tongue out, thick legs.
   Distinctly different silhouette from the kitten: rounder, stockier, golden/tan. */
function PuppySVG() {
  return React.createElement('svg', {
    width: 40, height: 30, viewBox: '0 0 40 30', fill: 'none',
    xmlns: 'http://www.w3.org/2000/svg', 'aria-hidden': true,
  },
    // Tail (wagging up, behind body)
    React.createElement('path', {
      d: 'M7,18 Q3,13 4,8 Q4.5,5 7,7',
      stroke: '#D4A44B', strokeWidth: 3, fill: 'none', strokeLinecap: 'round',
    }),
    // Body — stocky, rounder than kitten
    React.createElement('ellipse', { cx: 19, cy: 19, rx: 12, ry: 8, fill: '#D4A44B' }),
    // Belly patch
    React.createElement('ellipse', { cx: 19, cy: 22, rx: 7, ry: 3.5, fill: '#E8D5A0' }),
    // Head — bigger and rounder
    React.createElement('circle', { cx: 31, cy: 13, r: 7, fill: '#D4A44B' }),
    // Floppy left ear (hangs down past head)
    React.createElement('ellipse', { cx: 25.5, cy: 14, rx: 3, ry: 6, fill: '#B8862D', transform: 'rotate(-10,25.5,14)' }),
    // Floppy right ear (hangs down past head)
    React.createElement('ellipse', { cx: 36, cy: 14, rx: 3, ry: 6, fill: '#B8862D', transform: 'rotate(10,36,14)' }),
    // Big muzzle — much bigger than kitten nose area
    React.createElement('ellipse', { cx: 34, cy: 15.5, rx: 4, ry: 3, fill: '#E8D5A0' }),
    // Eyes — round (dog-like, not narrow like cat)
    React.createElement('circle', { cx: 29, cy: 11.5, r: 1.5, fill: '#2F2F2A' }),
    React.createElement('circle', { cx: 33.5, cy: 11.5, r: 1.5, fill: '#2F2F2A' }),
    // Eye highlights
    React.createElement('circle', { cx: 29.5, cy: 11, r: 0.5, fill: '#fff' }),
    React.createElement('circle', { cx: 34, cy: 11, r: 0.5, fill: '#fff' }),
    // Eyebrows (dogs have expressive brows)
    React.createElement('line', { x1: 27.5, y1: 9.5, x2: 30, y2: 9.2, stroke: '#B8862D', strokeWidth: 0.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 32, y1: 9.2, x2: 34.5, y2: 9.5, stroke: '#B8862D', strokeWidth: 0.8, strokeLinecap: 'round' }),
    // Big round nose
    React.createElement('ellipse', { cx: 34, cy: 14, rx: 1.5, ry: 1.1, fill: '#2F2F2A' }),
    // Tongue hanging out
    React.createElement('path', {
      d: 'M33,17 Q33.5,20 35,20 Q36.5,20 36,17',
      fill: '#E88B8B',
    }),
    // Legs — thicker than kitten
    React.createElement('line', { x1: 12, y1: 25, x2: 11, y2: 29, stroke: '#D4A44B', strokeWidth: 2.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 17, y1: 26, x2: 16, y2: 29, stroke: '#D4A44B', strokeWidth: 2.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 23, y1: 26, x2: 24, y2: 29, stroke: '#D4A44B', strokeWidth: 2.8, strokeLinecap: 'round' }),
    React.createElement('line', { x1: 28, y1: 24, x2: 29, y2: 29, stroke: '#D4A44B', strokeWidth: 2.8, strokeLinecap: 'round' }),
    // Paws (little circles at feet)
    React.createElement('circle', { cx: 11, cy: 29, r: 1.5, fill: '#B8862D' }),
    React.createElement('circle', { cx: 16, cy: 29, r: 1.5, fill: '#B8862D' }),
    React.createElement('circle', { cx: 24, cy: 29, r: 1.5, fill: '#B8862D' }),
    React.createElement('circle', { cx: 29, cy: 29, r: 1.5, fill: '#B8862D' }),
  );
}

function GeneratingState() {
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [fadeKey, setFadeKey] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => {
      setPhraseIdx(prev => (prev + 1) % LOADING_PHRASES.length);
      setFadeKey(prev => prev + 1);
    }, 2800);
    return () => clearInterval(timer);
  }, []);

  return React.createElement('div', {
    style: {
      ...s.sectionWrap, display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', minHeight: '360px', textAlign: 'center', gap: '20px', padding: '40px 16px',
    },
    role: 'status', 'aria-live': 'polite', 'aria-label': 'Generating your portrait',
  },
    // Calm loading indicator — puppy and kitten side by side, gentle bounce
    React.createElement('div', {
      style: {
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        gap: '24px', margin: '0 auto 16px', padding: '16px 0',
      },
    },
      React.createElement('div', {
        style: { animation: 'pf-chase-bounce-1 1.2s ease-in-out infinite' },
      }, React.createElement(PuppySVG)),
      // Pulsing center dot
      React.createElement('div', {
        style: {
          width: '10px', height: '10px', borderRadius: '50%',
          background: tokens.colorAccent,
          animation: 'pf-watercolor-pulse 2s ease-in-out infinite',
        },
      }),
      React.createElement('div', {
        style: { animation: 'pf-chase-bounce-2 1.2s ease-in-out 0.3s infinite', transform: 'scaleX(-1)' },
      }, React.createElement(KittenSVG)),
    ),
    // Phrase — with fade animation on change
    React.createElement('p', {
      key: 'phrase-' + fadeKey,
      style: {
        ...s.serifItalic, fontSize: 'var(--text-lg)', margin: 0, minHeight: '28px',
        animation: 'pf-phrase-fade 2.8s ease-in-out',
      },
    }, LOADING_PHRASES[phraseIdx]),
    // Sub
    React.createElement('p', {
      style: { ...s.bodyMuted, margin: 0 },
    }, 'Usually just a few seconds'),
    // Progress bar
    React.createElement('div', {
      style: { width: '100%', maxWidth: '240px', height: '2px', background: tokens.colorBorder, borderRadius: '1px', overflow: 'hidden' },
      role: 'progressbar', 'aria-label': 'Generation progress',
    },
      React.createElement('div', {
        style: {
          width: '35%', height: '100%', background: tokens.colorAccent, borderRadius: '1px',
          animation: 'pf-progress-indeterminate 2s ease-in-out infinite',
        },
      }),
    ),
  );
}

/* ── Newsletter / 10%-off popup ────────────────────────────── */

/* Captures email during the generation wait and applies the PETFAM2026
 * automatic discount via /discount/PETFAM2026. Status is persisted in
 * localStorage as 'signed_up' | 'skipped' | null so the cart-items.liquid
 * recapture modal can detect a skipped state and re-prompt before checkout. */
const NEWSLETTER_LS_KEY = 'pf_newsletter_status';

function loadNewsletterStatus() {
  try { return localStorage.getItem(NEWSLETTER_LS_KEY); } catch { return null; }
}
function saveNewsletterStatus(status) {
  try { localStorage.setItem(NEWSLETTER_LS_KEY, status); } catch {}
}

/* Normalize a North American phone number into E.164 (+15551234567).
 * Strips non-digits, prepends +1 to a 10-digit number, accepts existing
 * +country-code input. Returns null if the result still doesn't look
 * E.164. Klaviyo rejects anything that isn't strict E.164. */
function normalizePhoneE164(raw) {
  if (!raw) return null;
  const trimmed = String(raw).trim();
  // Already E.164-style
  if (/^\+\d{10,15}$/.test(trimmed.replace(/\s|-|\(|\)/g, ''))) {
    return trimmed.replace(/[^\d+]/g, '');
  }
  const digits = trimmed.replace(/\D/g, '');
  if (digits.length === 10) return '+1' + digits;
  if (digits.length === 11 && digits.startsWith('1')) return '+' + digits;
  return null;
}

/* Klaviyo Subscribe API call — single POST that handles email + optional
 * SMS subscription on the same profile. List-level double-opt-in (set in
 * Klaviyo admin) controls whether a confirmation email is sent. */
async function submitNewsletterSignup({ firstName, lastName, email, phoneE164, smsConsent, source }) {
  const klaviyo = (window.petPrintables && window.petPrintables.klaviyo) || {};
  const companyId = klaviyo.publicKey;
  const listId    = klaviyo.listId;
  if (!companyId || !listId) {
    throw new Error('Klaviyo not configured');
  }

  const subscriptions = { email: { marketing: { consent: 'SUBSCRIBED' } } };
  if (phoneE164 && smsConsent) {
    subscriptions.sms = { marketing: { consent: 'SUBSCRIBED' } };
  }

  const profileAttrs = {
    email,
    first_name: firstName,
    last_name: lastName,
    subscriptions,
    properties: { source: source || 'create-page-popup' },
  };
  if (phoneE164 && smsConsent) profileAttrs.phone_number = phoneE164;

  const payload = {
    data: {
      type: 'subscription',
      attributes: {
        custom_source: source || 'Pet Printables — Create Page Popup',
        profile: { data: { type: 'profile', attributes: profileAttrs } },
      },
      relationships: { list: { data: { type: 'list', id: listId } } },
    },
  };

  const res = await fetch(
    'https://a.klaviyo.com/client/subscriptions?company_id=' + encodeURIComponent(companyId),
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'revision': '2024-10-15',
      },
      body: JSON.stringify(payload),
    }
  );
  // Klaviyo returns 202 Accepted on success (subscription queued for processing).
  if (!res.ok && res.status !== 202) {
    const txt = await res.text().catch(() => '');
    throw new Error('Klaviyo signup failed (' + res.status + '): ' + txt.slice(0, 120));
  }

  // Apply the 10%-off discount cookie regardless of whether the customer
  // also opted into SMS — the discount is for joining the email list.
  await fetch('/discount/PETFAM2026', {
    credentials: 'same-origin', redirect: 'manual',
  }).catch(() => {});
  saveNewsletterStatus('signed_up');
}

function NewsletterModal({ isOpen, onClose, onSignedUp }) {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [phone, setPhone] = useState('');
  const [smsOptIn, setSmsOptIn] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState('');

  if (!isOpen) return null;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!firstName.trim() || !lastName.trim()) {
      setError('Need your first and last name.');
      return;
    }
    if (!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setError('That email looks off — double-check it?');
      return;
    }
    let phoneE164 = null;
    if (smsOptIn) {
      phoneE164 = normalizePhoneE164(phone);
      if (!phoneE164) {
        setError('Need a valid phone number to opt into SMS (or uncheck the box).');
        return;
      }
    }
    setError(''); setSubmitting(true);
    try {
      await submitNewsletterSignup({
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        email: email.trim(),
        phoneE164,
        smsConsent: smsOptIn && !!phoneE164,
        source: 'Pet Printables — Create Page Popup',
      });
      setSubmitted(true);
      if (onSignedUp) onSignedUp();
      setTimeout(onClose, 2400);
    } catch (err) {
      console.error('Newsletter signup error:', err);
      setError("Couldn't sign you up. Try again in a sec.");
      setSubmitting(false);
    }
  }

  function handleSkip() {
    saveNewsletterStatus('skipped');
    onClose();
  }

  const inputStyle = { ...s.input, fontSize: 'var(--text-base)' };
  const fieldRowStyle = { display: 'flex', gap: '8px' };

  return React.createElement('div', {
    style: {
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(20, 17, 14, 0.55)',
      backdropFilter: 'blur(2px)', WebkitBackdropFilter: 'blur(2px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '20px', animation: 'pf-newsletter-fade-in 0.2s ease-out',
      overflowY: 'auto',
    },
    role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'pf-newsletter-title',
    onClick: (e) => { if (e.target === e.currentTarget) handleSkip(); },
  },
    React.createElement('div', {
      style: {
        background: tokens.colorWhite, borderRadius: tokens.radiusCard,
        padding: '28px 26px 22px', maxWidth: '440px', width: '100%',
        boxShadow: '0 20px 60px rgba(0,0,0,0.25)',
        animation: 'pf-newsletter-pop 0.25s cubic-bezier(.2,1.2,.4,1)',
        textAlign: 'center',
        maxHeight: 'calc(100vh - 40px)', overflowY: 'auto',
      },
    },
      submitted
        ? [
            // Decorative checkmark in a brand-tone circle — replaces the
            // previous party-popper emoji.
            React.createElement('div', {
              key: 't',
              style: {
                width: '44px', height: '44px', borderRadius: '50%',
                background: tokens.colorAccentLight,
                color: tokens.colorAccent,
                display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 'var(--text-lg)', fontWeight: 700,
                marginBottom: '12px',
              },
              'aria-hidden': true,
            }, '✓'),
            React.createElement('h3', {
              key: 'h',
              style: { ...s.serifHeading, fontSize: 'var(--text-lg)', margin: '0 0 8px', color: tokens.colorBrand },
            }, "You're in. Your dog's proud."),
            React.createElement('p', {
              key: 'p', style: { ...s.bodyMuted, margin: '0 0 8px', fontSize: 'var(--text-sm)' },
            }, '10% off is on your cart automatically.'),
            React.createElement('p', {
              key: 'p2', style: { ...s.bodyMuted, margin: 0, fontSize: 'var(--text-xs)' },
            }, "Check your inbox — we sent a confirmation email to make sure it's really you."),
          ]
        : [
            React.createElement('h3', {
              key: 'h', id: 'pf-newsletter-title',
              style: { ...s.serifHeading, fontSize: 'var(--text-lg)', margin: '0 0 8px', color: tokens.colorBrand, lineHeight: 1.2 },
            }, '10% off? Or pay full price like a stranger?'),
            React.createElement('p', {
              key: 'sub',
              style: { ...s.bodyMuted, margin: '0 0 18px', fontSize: 'var(--text-sm)', lineHeight: 1.5 },
            }, "Join PetFam — 10% off your first order, applied automatically. Your dog already RSVP'd."),
            React.createElement('form', {
              key: 'f', onSubmit: handleSubmit, noValidate: true,
              style: { display: 'flex', flexDirection: 'column', gap: '10px', textAlign: 'left' },
            },
              React.createElement('div', { style: fieldRowStyle },
                React.createElement('input', {
                  type: 'text', name: 'firstName',
                  placeholder: 'First name', autoComplete: 'given-name',
                  value: firstName, onChange: (e) => setFirstName(e.target.value),
                  disabled: submitting, autoFocus: true, required: true,
                  style: { ...inputStyle, flex: 1 },
                  'aria-label': 'First name',
                }),
                React.createElement('input', {
                  type: 'text', name: 'lastName',
                  placeholder: 'Last name', autoComplete: 'family-name',
                  value: lastName, onChange: (e) => setLastName(e.target.value),
                  disabled: submitting, required: true,
                  style: { ...inputStyle, flex: 1 },
                  'aria-label': 'Last name',
                }),
              ),
              React.createElement('input', {
                type: 'email', name: 'email', autoComplete: 'email',
                placeholder: 'you@example.com',
                value: email, onChange: (e) => setEmail(e.target.value),
                disabled: submitting, required: true,
                style: inputStyle, 'aria-label': 'Email address',
              }),
              React.createElement('input', {
                type: 'tel', name: 'phone', autoComplete: 'tel',
                placeholder: 'Phone (optional, for SMS)',
                value: phone, onChange: (e) => setPhone(e.target.value),
                disabled: submitting,
                style: inputStyle, 'aria-label': 'Phone number (optional)',
              }),
              React.createElement('label', {
                style: {
                  display: 'flex', alignItems: 'flex-start', gap: '10px',
                  fontSize: 'var(--text-xs)', color: tokens.colorInk || '#222',
                  lineHeight: 1.5, marginTop: '4px', cursor: 'pointer',
                },
              },
                React.createElement('input', {
                  type: 'checkbox', name: 'smsOptIn',
                  checked: smsOptIn,
                  onChange: (e) => setSmsOptIn(e.target.checked),
                  disabled: submitting,
                  style: { marginTop: '3px', flexShrink: 0, width: '16px', height: '16px', accentColor: tokens.colorAccent },
                }),
                React.createElement('span', null,
                  React.createElement('strong', null, 'Also send me text updates'),
                  ' — by checking this box, you agree to receive recurring marketing texts from Pet Printables at the number above. Consent isn’t a condition of purchase. Msg & data rates may apply. Reply STOP to opt out. ',
                  React.createElement('a', {
                    href: '/policies/terms-of-service', target: '_blank', rel: 'noopener noreferrer',
                    style: { color: tokens.colorAccent || tokens.colorBrand, textDecoration: 'underline' },
                  }, 'Terms'),
                  ' · ',
                  React.createElement('a', {
                    href: '/policies/privacy-policy', target: '_blank', rel: 'noopener noreferrer',
                    style: { color: tokens.colorAccent || tokens.colorBrand, textDecoration: 'underline' },
                  }, 'Privacy'),
                  '.',
                ),
              ),
              error && React.createElement('p', {
                key: 'err', role: 'alert',
                style: { color: tokens.colorWarning || '#B45309', margin: '4px 0 0', fontSize: 'var(--text-xs)' },
              }, error),
              React.createElement('button', {
                type: 'submit', disabled: submitting,
                style: { ...s.primaryBtn, opacity: submitting ? 0.7 : 1, marginTop: '6px' },
              }, submitting ? 'Signing you up…' : 'Yes, I want 10% off'),
            ),
            React.createElement('button', {
              key: 'skip', type: 'button', onClick: handleSkip,
              style: {
                ...s.secondaryLink, marginTop: '12px',
                fontSize: 'var(--text-sm)', color: tokens.colorMuted,
              },
            }, 'No thanks, I love paying full price'),
          ]
    ),
  );
}

/* Floating pill — appears after a customer dismisses the modal so they can
 * still claim the discount without a hard re-prompt. Hidden once they sign up. */
function NewsletterPill({ onClick, visible }) {
  if (!visible) return null;
  return React.createElement('button', {
    type: 'button', onClick,
    'aria-label': 'Reopen the 10% off offer',
    style: {
      position: 'fixed', bottom: '18px', right: '18px', zIndex: 9998,
      background: tokens.colorBrand, color: tokens.colorWhite,
      border: 'none', borderRadius: '999px',
      padding: '12px 18px', fontFamily: fontSans, fontWeight: 600, fontSize: 'var(--text-sm)',
      cursor: 'pointer', boxShadow: '0 6px 18px rgba(0,0,0,0.18)',
      animation: 'pf-newsletter-pop 0.3s cubic-bezier(.2,1.2,.4,1)',
    },
  }, 'Get 10% off');
}

/* ── PreviewStep ───────────────────────────────────────────── */

/* ── UrgencyBanner — 10-minute checkout window countdown ─── */
const URGENCY_SESSION_MS = 10 * 60 * 1000; // 10 minutes

function UrgencyBanner({ generatedAt }) {
  const [timeLeft, setTimeLeft] = useState(() => calcTimeLeft());

  function calcTimeLeft() {
    try {
      const ageMs = Date.now() - new Date(generatedAt).getTime();
      const remaining = URGENCY_SESSION_MS - ageMs;
      if (remaining <= 0) return null;
      const minutes = Math.floor(remaining / 60000);
      const seconds = Math.floor((remaining % 60000) / 1000);
      return { minutes, seconds, totalMs: remaining };
    } catch { return null; }
  }

  useEffect(() => {
    const interval = setInterval(() => setTimeLeft(calcTimeLeft()), 1000);
    return () => clearInterval(interval);
  }, [generatedAt]);

  if (!timeLeft) {
    // Expired — show expired state
    return React.createElement('div', {
      style: {
        maxWidth: '520px', margin: '0 auto 20px',
        background: '#FEE2E2', border: '1.5px solid #DC2626',
        borderRadius: tokens.radiusCard, padding: '14px 18px', textAlign: 'center',
      },
      role: 'alert',
    },
      React.createElement('p', {
        style: {
          fontFamily: fontSans, fontSize: 'var(--text-sm)', fontWeight: 700,
          color: '#991B1B', margin: '0 0 4px', letterSpacing: '0.02em',
        },
      }, 'Your hold expired'),
      React.createElement('p', {
        style: { fontFamily: fontSans, fontSize: 'var(--text-xs)', color: '#7F1D1D', margin: 0, lineHeight: 1.5 },
      }, 'Don\u2019t worry \u2014 your portrait is still saved for 24 hours. Start a new session to pick it back up.'),
    );
  }

  const pad = n => String(n).padStart(2, '0');
  const isUrgent = timeLeft.totalMs < 3 * 60 * 1000; // <3 min = urgent

  return React.createElement('div', {
    style: {
      maxWidth: '520px', margin: '0 auto 20px',
      background: isUrgent ? '#FEE2E2' : '#FEF3E6',
      border: `1.5px solid ${isUrgent ? '#DC2626' : '#D97706'}`,
      borderRadius: tokens.radiusCard,
      padding: '14px 18px', textAlign: 'center',
      boxShadow: isUrgent
        ? '0 0 0 3px rgba(220,38,38,0.15), 0 4px 12px rgba(220,38,38,0.10)'
        : '0 0 0 3px rgba(217,119,6,0.12)',
      animation: isUrgent
        ? 'pf-urgency-pulse 1.2s ease-in-out infinite'
        : 'pf-urgency-pulse 2.5s ease-in-out infinite',
    },
    role: 'alert', 'aria-live': 'polite',
  },
    React.createElement('p', {
      style: {
        fontFamily: fontSans, fontSize: 'var(--text-xs)', fontWeight: 700,
        color: isUrgent ? '#991B1B' : '#B45309',
        margin: '0 0 8px', letterSpacing: '0.10em', textTransform: 'uppercase',
      },
    }, isUrgent
      ? 'Almost time \u2014 your hold is about to drop'
      : '\u23F1\uFE0F This portrait is held just for you'),

    // Large countdown display
    React.createElement('div', {
      style: {
        fontFamily: fontSans, fontWeight: 700,
        fontSize: 'var(--text-2xl)', color: isUrgent ? '#991B1B' : tokens.colorBrand,
        lineHeight: 1, margin: '0 0 6px',
        fontVariantNumeric: 'tabular-nums', letterSpacing: '0.02em',
      },
    }, `${pad(timeLeft.minutes)}:${pad(timeLeft.seconds)}`),

    React.createElement('p', {
      style: {
        fontFamily: fontSans, fontSize: 'var(--text-xs)', color: tokens.colorBrand,
        margin: 0, lineHeight: 1.4, fontWeight: 500,
      },
    }, 'This exact portrait is ',
      React.createElement('strong', { style: { fontWeight: 700 } }, 'one-of-a-kind'),
      ' and can ',
      React.createElement('strong', { style: { fontWeight: 700 } }, 'never be recreated'),
      '.'
    ),
  );
}

function PreviewStep({ state, update, selectPreview, onContinue, retryFromUpload, retryFromStyle, startFresh, generate }) {
  if (state.generationStatus === 'error') {
    const reason = state.generationError || 'Something went off-leash.';
    const tips = state.generationErrorTips && state.generationErrorTips.length
      ? state.generationErrorTips
      : ['Your photo and style are saved \u2014 just try again.'];
    return React.createElement('div', { style: { ...s.sectionWrap, textAlign: 'center', padding: '48px 16px' } },
      React.createElement('h2', {
        style: { ...s.serifItalic, fontSize: 'var(--text-lg)', marginBottom: '10px' },
      }, 'Something went off-leash'),
      React.createElement('p', {
        style: {
          fontFamily: fontSans, fontSize: 'var(--text-base)', lineHeight: 1.5,
          color: tokens.colorBrand, margin: '0 auto 10px', maxWidth: '440px',
        },
        role: 'alert',
      }, reason),
      React.createElement('ul', {
        style: {
          ...s.bodyMuted, fontSize: 'var(--text-sm)', textAlign: 'left',
          listStyle: 'disc', margin: '0 auto 28px', padding: '0 0 0 20px',
          maxWidth: '400px',
        },
      },
        tips.map((tip, i) => React.createElement('li', { key: i, style: { marginBottom: '4px' } }, tip)),
      ),
      React.createElement('button', {
        type: 'button', style: { ...s.primaryBtn, marginBottom: '14px' },
        onClick: generate, 'aria-label': 'Try generating the portrait again',
      }, iconLabel(React.createElement(RefreshIcon), 'TRY AGAIN')),
      React.createElement('button', {
        type: 'button', style: { ...s.secondaryLink, width: '100%', textAlign: 'center' },
        onClick: startFresh,
        'aria-label': 'Start over — clear this portrait and begin a new one',
      }, iconLabel(React.createElement(RefreshIcon, { size: 14 }), 'Start Over')),
    );
  }

  // Single preview (no-name version) — this is what the user confirms
  const mainImage = state.previewImages[0] || state.previewCdnUrls[0];

  return React.createElement('div', {
    className: 'pf-preview-grid',
    style: { ...s.sectionWrap, animation: 'pf-reveal-up 0.6s ease forwards' },
  },
    React.createElement('div', { className: 'pf-preview-grid__indicator' },
      React.createElement(StepIndicator, { current: 3 }),
    ),

    // Main preview (single, large) — left column on desktop, top on mobile
    React.createElement('div', {
      className: 'pf-preview-grid__media',
      style: {
        width: '100%', maxWidth: 'min(520px, 100%)', margin: '0 auto 16px', borderRadius: tokens.radiusCard,
        overflow: 'hidden', boxShadow: '0 12px 40px rgba(28, 28, 28, 0.12)',
      },
    },
      React.createElement('img', {
        src: mainImage, alt: state.petName ? `Portrait of ${state.petName}` : 'Your pet portrait preview',
        style: { width: '100%', display: 'block' },
      }),
    ),

    // Right column on desktop (heading + chip + bridge + actions),
    // stacks below the preview on mobile.
    React.createElement('div', { className: 'pf-preview-grid__copy' },
      // Heading
      React.createElement('p', { style: { ...s.smallCaps, textAlign: 'center', margin: '0 0 6px' } }, 'One-of-one \u00B7 never recreated'),
      state.petName && React.createElement('h2', {
        style: { ...s.serifHeading, textAlign: 'center', marginBottom: '12px' },
      }, state.petName),

      // Selected-style chip — quiet reminder of the choice they made
      state.selectedStyleId && React.createElement('div', {
        style: { display: 'flex', justifyContent: 'center', marginBottom: '20px' },
      },
        React.createElement('span', {
          style: {
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            padding: '6px 12px',
            background: tokens.colorAccentLight,
            border: '1px solid ' + tokens.colorAccent,
            borderRadius: '999px',
            fontFamily: fontSans, fontSize: 'var(--text-xs)', fontWeight: 600,
            color: tokens.colorBrand, letterSpacing: '0.04em',
          },
          'aria-label': 'Selected style: ' + styleNameFor(state.selectedStyleId),
        },
          React.createElement('span', {
            'aria-hidden': true,
            style: {
              display: 'inline-block',
              width: '6px', height: '6px', borderRadius: '50%',
              background: tokens.colorAccent,
            },
          }),
          'Your style: ' + styleNameFor(state.selectedStyleId),
        ),
      ),

      // BAB "Bridge" — closes the gap between "this is a preview" and
      // "this is what arrives on the wall." Free-preview language is the
      // single biggest objection-handler on this step.
      React.createElement('p', {
        style: {
          ...s.bodyMuted, textAlign: 'center',
          fontSize: 'var(--text-xs)',
          margin: '0 auto 18px', maxWidth: '380px',
        },
      }, 'This is your preview — only pay if you love it. We print it just like you see.'),

      // Urgency banner — countdown timer hidden for now. Uncomment to re-enable.
      // React.createElement(UrgencyBanner, { generatedAt: state.generatedAt || new Date().toISOString() }),

      // Actions
      React.createElement('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' } },
        React.createElement('button', {
          type: 'button', style: s.primaryBtn, onClick: onContinue,
          'aria-label': 'Continue to choose size and frame',
        }, iconLabel(React.createElement(ArrowRightIcon), 'PICK SIZE & FRAME', 'right')),
        React.createElement('div', { style: { display: 'flex', gap: '16px', flexWrap: 'wrap', justifyContent: 'center' } },
          React.createElement('button', {
            type: 'button', style: s.secondaryLinkUnderline,
            onClick: retryFromStyle,
            'aria-label': 'Go back to change the art style',
          }, iconLabel(React.createElement(ArrowLeftIcon, { size: 14 }), 'Change style')),
          // Regenerate keeps the current style + photo + name and re-runs the
          // model. Replaces the redundant pair of "Try another" + a separate
          // "Regenerate Portrait" that called startFresh — one labelled
          // action, one clear behaviour.
          React.createElement('button', {
            type: 'button', style: s.secondaryLinkUnderline,
            onClick: generate,
            'aria-label': 'Regenerate a new portrait in the same style',
          }, iconLabel(React.createElement(SparkleIcon, { size: 14 }), 'Regenerate Portrait')),
        ),
      ),
    ),
  );
}

/* ── ProductGallery ────────────────────────────────────────── */

// Canvas configurator — real Shopify variants
// Source: Printful → Shopify product sync (accurate as of 2026-04)
// Retired 2026-04-22 (no live Shopify variants): 8x10 framed, 18x24 framed.
const CANVAS_SIZES = [
  // 12x12 — both unframed + framed
  { id: '12x12', label: '12\u2033 \u00D7 12\u2033', unframedAvailable: true, framedAvailable: true,
    price: 79.99,  variantId: 47267971760277,
    priceFramed: 139.50, variantIdFramed: 47267981787285 },
  // 12x16 — both
  { id: '12x16', label: '12\u2033 \u00D7 16\u2033', unframedAvailable: true, framedAvailable: true,
    price: 84.99,  variantId: 47267971793045,
    priceFramed: 148.50, variantIdFramed: 47267981820053 },
  // 16x16 — both
  { id: '16x16', label: '16\u2033 \u00D7 16\u2033', unframedAvailable: true, framedAvailable: true,
    price: 99.99,  variantId: 47267971825813,
    priceFramed: 162.50, variantIdFramed: 47267981852821 },
  // 16x20 — both
  { id: '16x20', label: '16\u2033 \u00D7 20\u2033', unframedAvailable: true, framedAvailable: true,
    price: 109.99, variantId: 47267971858581,
    priceFramed: 171.50, variantIdFramed: 47267981885589 },
];

function ProductGallery({ state, retryFromStyle, startFresh }) {
  const mainImage = state.previewImages[0] || state.previewCdnUrls[0];
  const [selectedSize, setSelectedSize] = useState('12x12'); // first unframed size
  const [wantsName, setWantsName] = useState(false);
  const [wantsFrame, setWantsFrame] = useState(false);
  const [generatingNamedPreview, setGeneratingNamedPreview] = useState(false);
  const [namedPreviewUrl, setNamedPreviewUrl] = useState(null);
  const [nameError, setNameError] = useState(null);
  const [loadingPhaseIdx, setLoadingPhaseIdx] = useState(0);

  // Progressive loading copy — cycles so a 10-15s /add-name call feels
  // alive rather than frozen. Each phrase holds ~3.5s.
  const LOADING_PHRASES = [
    'Painting the letters\u2026',
    'Finding the right spot\u2026',
    'Blending into the artwork\u2026',
    'Just a few more seconds\u2026',
  ];
  useEffect(() => {
    if (!generatingNamedPreview) { setLoadingPhaseIdx(0); return; }
    const t = setInterval(() => {
      setLoadingPhaseIdx(i => Math.min(i + 1, LOADING_PHRASES.length - 1));
    }, 3500);
    return () => clearInterval(t);
  }, [generatingNamedPreview]);

  // Only show sizes that exist for the current frame choice
  const availableSizes = CANVAS_SIZES.filter(s =>
    wantsFrame ? s.framedAvailable === true : s.unframedAvailable === true
  );

  // If current selection isn't valid for the new frame choice, fall back
  const activeSize = availableSizes.find(s => s.id === selectedSize) || availableSizes[0];
  const currentPrice = wantsFrame ? activeSize.priceFramed : activeSize.price;
  const currentVariantId = wantsFrame ? activeSize.variantIdFramed : activeSize.variantId;
  const displayImage = (wantsName && namedPreviewUrl) ? namedPreviewUrl : mainImage;

  // When user toggles frame on, ensure selectedSize is valid in the new list
  useEffect(() => {
    if (!availableSizes.find(s => s.id === selectedSize)) {
      setSelectedSize(availableSizes[0].id);
    }
  }, [wantsFrame]);

  // When user toggles name ON and we don't have a named preview yet, generate it
  const handleNameToggle = useCallback((enabled) => {
    setWantsName(enabled);
    setNameError(null);
    if (!enabled) return;
    if (!state.petName) {
      setNameError('Please go back and enter your pet\u2019s name first.');
      return;
    }
    if (namedPreviewUrl) return; // already have it
    if (generatingNamedPreview) return; // already in progress

    setGeneratingNamedPreview(true);
    const API_BASE = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
    const imageUrl = (state.previewCdnUrls && state.previewCdnUrls[0])
      || (state.previewImages && state.previewImages[0])
      || '';

    if (!imageUrl) {
      setNameError('No portrait image available to add name to.');
      setGeneratingNamedPreview(false);
      return;
    }

    console.log('[PetPrintables] Calling /add-name', { imageUrl, petName: state.petName, style: state.selectedStyleId });

    fetch(`${API_BASE}/add-name`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        image_url: imageUrl,
        pet_name: state.petName,
        style: state.selectedStyleId,
        background_mode: state.backgroundMode || 'auto',
      }),
    })
    .then(async r => {
      if (r.ok) return r.json();
      let errMsg = `HTTP ${r.status}`;
      try { const d = await r.json(); errMsg = d.error || errMsg; } catch {}
      throw new Error(errMsg);
    })
    .then(resp => {
      const url = resp.composited || resp.composited_png_cdn;
      if (!url) throw new Error('No image returned');
      setNamedPreviewUrl(url);
      setGeneratingNamedPreview(false);
    })
    .catch(err => {
      console.error('[PetPrintables] Add-name failed:', err);
      setNameError(err.message || 'Could not add name. Please try again.');
      setGeneratingNamedPreview(false);
    });
  }, [namedPreviewUrl, state.petName, state.selectedStyleId, state.previewCdnUrls, state.previewImages, generatingNamedPreview]);

  // Go to PDP with all params
  const handleContinue = useCallback(() => {
    // Save selections to localStorage so PDP picks them up
    try {
      const session = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      session.selectedSize = activeSize.id;
      session.selectedVariantId = currentVariantId;
      session.selectedPrice = currentPrice;
      session.wantsName = wantsName;
      session.wantsFrame = wantsFrame;
      if (namedPreviewUrl) session.namedPreviewUrl = namedPreviewUrl;
      localStorage.setItem(LS_KEY, JSON.stringify(session));
    } catch {}
    // Framed + unframed live on separate Shopify products
    const productHandle = wantsFrame ? 'framed-canvas' : 'canvas';
    const url = currentVariantId
      ? `/products/${productHandle}?variant=${currentVariantId}`
      : `/products/${productHandle}`;
    window.location.href = url;
  }, [activeSize, currentVariantId, currentPrice, wantsName, wantsFrame, namedPreviewUrl]);

  const optionCard = (label, selected, onClick, children, disabled) => React.createElement('button', {
    type: 'button',
    onClick: disabled ? undefined : onClick,
    disabled,
    style: {
      flex: 1, padding: '14px 12px',
      border: selected ? `2px solid ${tokens.colorAccent}` : `1px solid ${tokens.colorBorder}`,
      background: selected ? tokens.colorAccentLight : tokens.colorWhite,
      borderRadius: tokens.radiusCard,
      cursor: disabled ? 'default' : 'pointer',
      textAlign: 'center', outline: 'none', transition: 'all 0.2s',
      fontFamily: fontSans, fontSize: 'var(--text-sm)', fontWeight: 500,
      color: selected ? tokens.colorAccent : tokens.colorBrand,
      opacity: disabled ? 0.5 : 1,
    },
  }, children || label);

  // Live canvas mockup — updates as user changes size/frame
  // Maps size id to aspect ratio (width:height in inches)
  const sizeDims = {
    '12x12': [12, 12], '12x16': [12, 16],
    '16x16': [16, 16], '16x20': [16, 20],
  };
  const [sizeW, sizeH] = sizeDims[selectedSize] || [10, 10];

  return React.createElement('div', { style: { ...s.sectionWrap, animation: 'pf-reveal-up 0.6s ease forwards' } },
    React.createElement(StepIndicator, { current: 4, total: 4 }),

    // Selected-style chip — keeps the customer's choice visible while they
    // pick size and frame. Mirrors the chip on the preview step.
    state.selectedStyleId && React.createElement('div', {
      style: { display: 'flex', justifyContent: 'center', marginBottom: '14px' },
    },
      React.createElement('span', {
        style: {
          display: 'inline-flex', alignItems: 'center', gap: '6px',
          padding: '6px 12px',
          background: tokens.colorAccentLight,
          border: '1px solid ' + tokens.colorAccent,
          borderRadius: '999px',
          fontFamily: fontSans, fontSize: 'var(--text-xs)', fontWeight: 600,
          color: tokens.colorBrand, letterSpacing: '0.04em',
        },
        'aria-label': 'Selected style: ' + styleNameFor(state.selectedStyleId),
      },
        React.createElement('span', {
          'aria-hidden': true,
          style: {
            display: 'inline-block',
            width: '6px', height: '6px', borderRadius: '50%',
            background: tokens.colorAccent,
          },
        }),
        'Your style: ' + styleNameFor(state.selectedStyleId),
      ),
    ),

    // Urgency banner — countdown timer hidden for now. Uncomment to re-enable.
    // React.createElement(UrgencyBanner, { generatedAt: state.generatedAt || new Date().toISOString() }),

    // LIVE MOCKUP — reflects size + frame choices
    React.createElement('div', {
      style: {
        width: '100%', maxWidth: 'min(400px, 100%)', margin: '0 auto 24px',
        aspectRatio: '1/1', borderRadius: tokens.radiusCard,
        overflow: 'hidden', position: 'relative',
        backgroundImage: `url(${_pfAssetBase}linen-texture.webp)`,
        backgroundSize: 'cover', backgroundPosition: 'center',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      },
    },
      // Directional light
      React.createElement('div', {
        style: {
          position: 'absolute', inset: 0, pointerEvents: 'none',
          background: 'radial-gradient(ellipse at 30% 20%, rgba(255,250,240,0.18) 0%, transparent 60%)',
        },
      }),
      // Canvas product (scales to aspect ratio)
      React.createElement('div', {
        style: {
          position: 'relative',
          width: sizeH >= sizeW ? `${(sizeW / sizeH) * 72}%` : '72%',
          height: sizeH >= sizeW ? '72%' : `${(sizeH / sizeW) * 72}%`,
          maxWidth: '72%', maxHeight: '72%',
          boxShadow: wantsFrame
            ? '0 4px 8px rgba(0,0,0,0.14), 0 12px 24px rgba(0,0,0,0.12), 0 24px 48px rgba(0,0,0,0.08)'
            : '0 2px 4px rgba(0,0,0,0.06), 0 6px 12px rgba(0,0,0,0.08), 0 14px 28px rgba(0,0,0,0.10)',
          padding: wantsFrame ? '3%' : 0,
          background: wantsFrame
            ? 'linear-gradient(145deg, #3a2e22 0%, #1f180f 50%, #2a2018 100%)'  // frame wood
            : 'transparent',
          transition: 'all 0.3s ease',
        },
      },
        // Canvas face with portrait
        React.createElement('div', {
          style: {
            position: 'relative', width: '100%', height: '100%',
            background: '#fefdfb', overflow: 'hidden',
            boxShadow: wantsFrame ? 'inset 0 0 0 1px rgba(0,0,0,0.15)' : 'none',
          },
        },
          React.createElement('img', {
            src: displayImage,
            alt: state.petName ? `Portrait of ${state.petName}` : 'Your portrait',
            style: { position: 'absolute', inset: 0, width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center 20%', display: 'block' },
          }),
          // Canvas weave texture
          React.createElement('div', {
            style: {
              position: 'absolute', inset: 0, pointerEvents: 'none',
              mixBlendMode: 'multiply', opacity: 0.08,
              backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='w'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0.6 0 0 0 0 0.55 0 0 0 0 0.5 0 0 0 1 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23w)'/%3E%3C/svg%3E\")",
            },
          }),
          // Edge highlight
          React.createElement('div', {
            style: {
              position: 'absolute', inset: 0, pointerEvents: 'none',
              boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.5), inset 0 -1px 0 rgba(0,0,0,0.05)',
            },
          }),
        ),
      ),
      // Loading overlay when fetching named version — spinner + progressive copy
      generatingNamedPreview && React.createElement('div', {
        style: {
          position: 'absolute', inset: 0,
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: '14px',
          background: 'rgba(250,248,245,0.88)',
          backdropFilter: 'blur(5px)', WebkitBackdropFilter: 'blur(5px)',
          fontFamily: fontSans, color: tokens.colorBrand, zIndex: 5,
          animation: 'pfNameFadeIn 0.25s ease-out',
        },
        role: 'status', 'aria-live': 'polite',
      },
        // Spinner
        React.createElement('span', {
          'aria-hidden': 'true',
          style: {
            width: '36px', height: '36px',
            border: `3px solid ${tokens.colorBorder}`,
            borderTopColor: tokens.colorBrand,
            borderRadius: '50%',
            animation: 'pfNameSpin 0.9s linear infinite',
          },
        }),
        // Active phase message
        React.createElement('span', {
          style: {
            fontSize: 'var(--text-sm)', fontWeight: 600, letterSpacing: '0.02em',
          },
        }, LOADING_PHRASES[loadingPhaseIdx]),
        // Subtext — reassurance
        React.createElement('span', {
          style: {
            fontSize: 'var(--text-xs)', fontWeight: 400, color: tokens.colorMuted,
            maxWidth: '240px', textAlign: 'center', lineHeight: 1.4,
          },
        }, 'Should be done before your dog finishes their next zoomie. Don\u2019t refresh the page.'),
      ),
      // Keyframes injected once per render cycle (React de-dupes by id)
      generatingNamedPreview && React.createElement('style', null,
        '@keyframes pfNameSpin{to{transform:rotate(360deg)}}' +
        '@keyframes pfNameFadeIn{from{opacity:0}to{opacity:1}}'
      ),
      // Size + frame label (bottom right, glass pill)
      React.createElement('div', {
        style: {
          position: 'absolute', bottom: '12px', right: '14px',
          fontFamily: fontSans, fontSize: 'var(--text-xs)', fontWeight: 500, letterSpacing: '0.04em',
          color: '#3a3530', background: 'rgba(255,255,255,0.55)', padding: '6px 12px', borderRadius: '999px',
          border: '1px solid rgba(255,255,255,0.6)',
          boxShadow: '0 2px 8px rgba(0,0,0,0.08), inset 0 1px 0 rgba(255,255,255,0.6)',
          backdropFilter: 'blur(10px) saturate(120%)', zIndex: 3,
        },
      },
        React.createElement('strong', { style: { fontWeight: 600 } }, `${sizeW}\u2033 \u00D7 ${sizeH}\u2033`),
        React.createElement('span', { style: { color: '#a09890', margin: '0 6px' } }, '\u00B7'),
        React.createElement('span', { style: { color: '#7a7369' } }, wantsFrame ? 'Framed' : '1.25\u2033 deep'),
      ),
    ),

    // 1. FRAME toggle — decided first, since it filters available sizes
    React.createElement('div', { style: { marginBottom: '24px' } },
      React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 10px' } }, 'Add a frame?'),
      React.createElement('div', { style: { display: 'flex', gap: '10px' } },
        optionCard(
          'No frame',
          !wantsFrame,
          () => setWantsFrame(false),
          React.createElement('span', null,
            React.createElement('span', { style: { fontWeight: 600 } }, 'No frame'),
            React.createElement('br'),
            React.createElement('span', { style: { fontSize: 'var(--text-xs)', color: tokens.colorMuted } }, 'Gallery wrap · ready to hang'),
          )
        ),
        optionCard(
          'Framed',
          wantsFrame,
          () => setWantsFrame(true),
          React.createElement('span', null,
            React.createElement('span', { style: { fontWeight: 600 } }, 'Framed'),
            React.createElement('br'),
            React.createElement('span', { style: { fontSize: 'var(--text-xs)', color: tokens.colorMuted } }, 'Solid wood · heirloom finish'),
          )
        ),
      ),
    ),

    // 2. SIZE selector — filtered by frame choice
    React.createElement('div', { style: { marginBottom: '24px' } },
      React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 10px' } }, 'Size'),
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '10px' } },
        availableSizes.map(size => {
          const priceVal = wantsFrame ? size.priceFramed : size.price;
          return optionCard(
            size.label,
            selectedSize === size.id,
            () => setSelectedSize(size.id),
            React.createElement('span', null,
              React.createElement('span', { style: { fontWeight: 600 } }, size.label),
              React.createElement('br'),
              React.createElement('span', { style: { fontSize: 'var(--text-xs)', color: tokens.colorMuted } },
                `$${priceVal.toFixed(2)}`
              )
            )
          );
        }),
      ),
    ),

    // 3. NAME toggle
    state.petName && React.createElement('div', { style: { marginBottom: '28px' } },
      React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 10px' } },
        `Add "${state.petName}" to the portrait?`
      ),
      React.createElement('div', { style: { display: 'flex', gap: '10px' } },
        optionCard(
          generatingNamedPreview && wantsName ? 'Adding name\u2026' : 'Yes, with their name',
          wantsName === true,
          () => handleNameToggle(true),
          generatingNamedPreview && wantsName
            ? React.createElement('span', { style: { display: 'inline-flex', alignItems: 'center', gap: '8px' } },
                React.createElement('span', {
                  'aria-hidden': 'true',
                  style: {
                    width: '13px', height: '13px',
                    border: `2px solid ${tokens.colorBorder}`,
                    borderTopColor: tokens.colorBrand,
                    borderRadius: '50%',
                    animation: 'pfNameSpin 0.9s linear infinite',
                    display: 'inline-block',
                  },
                }),
                React.createElement('span', { style: { fontWeight: 600 } }, 'Adding name\u2026'),
              )
            : null,
          generatingNamedPreview,
        ),
        optionCard('No, just the portrait', wantsName === false, () => handleNameToggle(false)),
      ),
      nameError && React.createElement('p', {
        style: {
          fontFamily: fontSans, fontSize: 'var(--text-xs)', color: tokens.colorError,
          margin: '8px 0 0', lineHeight: 1.4,
        },
        role: 'alert',
      }, '\u26A0 ' + nameError),
    ),

    // Summary + CTA
    React.createElement('div', {
      style: {
        padding: '16px', borderRadius: tokens.radiusCard,
        background: tokens.colorWhite, border: `1px solid ${tokens.colorBorder}`,
        marginBottom: '16px',
      },
    },
      React.createElement('div', {
        style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '4px' },
      },
        React.createElement('span', { style: { ...s.smallCaps } }, 'Total'),
        React.createElement('span', {
          style: { fontFamily: fontSerif, fontStyle: 'italic', fontSize: 'var(--text-xl)', color: tokens.colorBrand },
        }, `$${currentPrice.toFixed(2)}`),
      ),
      React.createElement('p', {
        style: { fontFamily: fontSans, fontSize: 'var(--text-xs)', color: tokens.colorMuted, margin: 0 },
      },
        `${activeSize.label} canvas${wantsFrame ? ' · Framed' : ''}${wantsName ? ' · With name' : ''}`
      ),
    ),

    React.createElement('button', {
      type: 'button', style: s.primaryBtn, onClick: handleContinue,
      'aria-label': 'Review your order and add to cart',
    }, iconLabel(React.createElement(ArrowRightIcon), 'REVIEW MY ORDER', 'right')),

    // Guarantee strip
    React.createElement('div', {
      style: { borderTop: `1px solid ${tokens.colorBorder}`, marginTop: '24px', paddingTop: '20px', marginBottom: '20px', display: 'flex', flexDirection: 'column', gap: '6px' },
    },
      ['Preview before we print', "Free redos until it\u2019s right", 'Ships in 7\u201310 days'].map((line, i) =>
        React.createElement('p', { key: i, style: { ...s.bodyMuted, fontSize: 'var(--text-xs)' } },
          React.createElement('span', { style: { color: tokens.colorSuccess, marginRight: '10px' } }, '\u2713'),
          line,
        )
      ),
    ),

    // Secondary actions
    React.createElement('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px' } },
      React.createElement('button', {
        type: 'button', style: s.secondaryLinkUnderline,
        onClick: retryFromStyle,
      }, 'Try a different style'),
    ),
  );
}

/* ── TrustBar ──────────────────────────────────────────────── */

function PageHero() {
  return React.createElement('div', {
    style: { textAlign: 'center', marginBottom: '20px' },
  },
    // Page title
    React.createElement('h1', {
      style: {
        fontFamily: fontSerif, fontWeight: 400, fontStyle: 'italic',
        fontSize: 'clamp(var(--text-xl), 7vw, var(--text-2xl))', color: tokens.colorBrand,
        margin: '0 0 6px', lineHeight: 1.15,
      },
    }, 'Turn your pet into art'),

    // Subtitle
    React.createElement('p', {
      style: {
        fontFamily: fontSans, fontSize: 'var(--text-sm)', color: tokens.colorMuted,
        margin: '0 0 14px', lineHeight: 1.5,
      },
    }, '8 styles \u00B7 You only pay if you love it \u00B7 On your wall in 7\u201310 days'),

    // Marquee — all 8 styles scrolling continuously
    React.createElement('div', {
      style: {
        overflow: 'hidden', margin: '0 -20px',
        maskImage: 'linear-gradient(90deg, transparent, black 10%, black 90%, transparent)',
        WebkitMaskImage: 'linear-gradient(90deg, transparent, black 10%, black 90%, transparent)',
      },
      'aria-label': 'Example pet portraits in all 8 styles',
    },
      React.createElement('div', {
        style: {
          display: 'flex', gap: '16px', width: 'max-content',
          animation: 'pf-marquee 35s linear infinite',
        },
      },
        // Duplicate the set for seamless loop
        [0, 1].map(setIdx =>
          STYLES.map((style, i) =>
            React.createElement('div', {
              key: `${setIdx}-${i}`,
              style: { textAlign: 'center', flex: '0 0 auto', width: '100px' },
              ...(setIdx === 1 ? { 'aria-hidden': true } : {}),
            },
              React.createElement('img', {
                src: _pfAssetBase + style.exampleImage, alt: style.name,
                loading: 'eager',
                style: {
                  width: '100px', height: '125px', objectFit: 'cover',
                  borderRadius: '10px', display: 'block',
                  boxShadow: '0 3px 12px rgba(0,0,0,0.10)',
                },
              }),
              React.createElement('span', {
                style: {
                  fontFamily: fontSans, fontSize: 'var(--text-xs)', fontWeight: 600,
                  color: tokens.colorMuted, textTransform: 'uppercase',
                  letterSpacing: '0.02em', marginTop: '5px', display: 'block',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  maxWidth: '100px',
                },
              }, style.name),
            ),
          ),
        ).flat(),
      ),
    ),
  );
}

/* ── Main PortraitFlow component ───────────────────────────── */

function PortraitFlow() {
  const flow = usePortraitFlow();
  const { state } = flow;
  useEffect(() => { injectKeyframes(); }, []);

  // Newsletter / 10%-off popup state. Shows when generation starts so the
  // user has something to do during the wait. Persists in localStorage so a
  // dismissal carries through to the cart-side recapture modal.
  const initialNlStatus = loadNewsletterStatus();
  const [newsletterModalOpen, setNewsletterModalOpen] = useState(false);
  const [newsletterStatus, setNewsletterStatus] = useState(initialNlStatus);

  // Auto-open the modal once when the user enters the GENERATING stage,
  // unless they've already signed up or skipped in a prior session.
  const generatingPromptShown = useRef(false);
  useEffect(() => {
    if (
      state.stage === STAGES.GENERATING
      && !generatingPromptShown.current
      && newsletterStatus === null
    ) {
      generatingPromptShown.current = true;
      setNewsletterModalOpen(true);
    }
  }, [state.stage, newsletterStatus]);

  let content;
  switch (state.stage) {
    case STAGES.UPLOAD:
      content = React.createElement(UploadStep, {
        state, setPhoto: flow.setPhoto, update: flow.update,
        canContinue: flow.canContinueFromUpload,
        onContinue: () => flow.goToStage(STAGES.STYLE),
      }); break;
    case STAGES.STYLE:
      content = React.createElement(StyleStep, {
        state, update: flow.update, selectStyle: flow.selectStyle, onGenerate: flow.generate,
        canGenerate: flow.canGenerate, onBack: () => flow.goToStage(STAGES.UPLOAD),
      }); break;
    case STAGES.GENERATING:
      content = React.createElement(GeneratingState); break;
    case STAGES.PREVIEW:
      content = React.createElement(PreviewStep, {
        state, update: flow.update, selectPreview: flow.selectPreview,
        onContinue: () => flow.goToStage(STAGES.GALLERY),
        retryFromUpload: flow.retryFromUpload, retryFromStyle: flow.retryFromStyle,
        startFresh: flow.startFresh,
        generate: flow.generate,
      }); break;
    case STAGES.GALLERY:
      content = React.createElement(ProductGallery, {
        state, retryFromStyle: flow.retryFromStyle, startFresh: flow.startFresh,
      }); break;
    default: content = null;
  }

  return React.createElement('div', {
    style: { fontFamily: fontSans, maxWidth: '600px', margin: '0 auto', padding: '24px 20px 40px', background: tokens.colorSurface },
  },
    // Show hero on upload + style steps, hide on later steps (portrait is the hero)
    (state.stage === STAGES.UPLOAD || state.stage === STAGES.STYLE) && React.createElement(PageHero),
    content,
    // Newsletter modal + dismissed-state floating pill. Both opt out
    // automatically once newsletterStatus === 'signed_up'.
    React.createElement(NewsletterModal, {
      isOpen: newsletterModalOpen,
      onClose: () => {
        setNewsletterModalOpen(false);
        setNewsletterStatus(loadNewsletterStatus());
      },
      onSignedUp: () => setNewsletterStatus('signed_up'),
    }),
    React.createElement(NewsletterPill, {
      visible: !newsletterModalOpen && newsletterStatus === 'skipped',
      onClick: () => setNewsletterModalOpen(true),
    }),
  );
}

/* ── Cloudflare Turnstile bot protection ─────────────────────
 * Site key is read from window.petPrintables.turnstileSiteKey
 * (set via Shopify theme settings). Widget renders invisibly on
 * the create page and provides a token for each /generate call.
 */
function mountTurnstile() {
  const siteKey = window.petPrintables && window.petPrintables.turnstileSiteKey;
  if (!siteKey) return; // not configured — skip (dev mode)

  // Container for the widget
  let container = document.getElementById('pf-turnstile');
  if (!container) {
    container = document.createElement('div');
    container.id = 'pf-turnstile';
    container.style.cssText = 'margin:16px auto;display:flex;justify-content:center;';
    document.getElementById('portrait-flow-root')?.appendChild(container);
  }

  // Load Turnstile script once
  if (!document.querySelector('script[src*="challenges.cloudflare.com/turnstile"]')) {
    const script = document.createElement('script');
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onPfTurnstileLoad';
    script.async = true; script.defer = true;
    document.head.appendChild(script);
  }

  window.onPfTurnstileLoad = function () {
    window._pfTurnstileWidgetId = window.turnstile.render(container, {
      sitekey: siteKey,
      size: 'flexible',
      theme: 'light',
      appearance: 'interaction-only',  // invisible unless challenge needed
    });
  };
}

/* ── Mount ─────────────────────────────────────────────────── */

const pfRoot = document.getElementById('portrait-flow-root');
const pfShippingDate = pfRoot?.dataset?.shippingDate || '';
const pfOccasion = pfRoot?.dataset?.occasion || '';
if (pfRoot) {
  ReactDOM.createRoot(pfRoot).render(React.createElement(PortraitFlow));
  mountTurnstile();
}
