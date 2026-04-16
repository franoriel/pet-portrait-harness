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
  'modern-oil-paint':     { family: 'Playfair Display',   css: "'Playfair Display', serif",     google: 'Playfair+Display:ital,wght@0,700;1,700' },
  'neon-pop-art':         { family: 'Bungee',             css: "'Bungee', sans-serif",          google: 'Bungee' },
  'renaissance-royalty':  { family: 'Cinzel',             css: "'Cinzel', serif",               google: 'Cinzel:wght@700' },
  'cozy-film-grain':      { family: 'Libre Baskerville',  css: "'Libre Baskerville', serif",    google: 'Libre+Baskerville:ital,wght@0,400;1,400' },
  'rainbow-bridge':       { family: 'Sacramento',         css: "'Sacramento', cursive",         google: 'Sacramento' },
  'bold-graphic-poster':  { family: 'Oswald',             css: "'Oswald', sans-serif",          google: 'Oswald:wght@700' },
  'aura-gradient':        { family: 'Quicksand',          css: "'Quicksand', sans-serif",       google: 'Quicksand:wght@500;700' },
};

const FONT_SIZES = [
  { id: 'small',  label: 'S', scale: 0.7 },
  { id: 'medium', label: 'M', scale: 1.0 },
  { id: 'large',  label: 'L', scale: 1.35 },
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

const STYLES = [
  {
    id: 'soft-watercolour',
    name: 'Soft Watercolour',
    description: 'Pastel washes, soft edges, dreamy feel',
    badge: 'Most popular',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #F5C5A3, #C5D5E8)',
  },
  {
    id: 'minimal-line-art',
    name: 'Minimal Line Art',
    description: 'Clean lines on off-white, high contrast',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #E8E8E8, #FFFFFF)',
  },
  {
    id: 'modern-oil-paint',
    name: 'Modern Oil Paint',
    description: 'Rich brush strokes, warm studio light',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #8B6F5C, #C4A882)',
  },
  {
    id: 'neon-pop-art',
    name: 'Neon Pop Art',
    description: 'Bold outlines, saturated color, comic feel',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #FF6B6B, #4ECDC4)',
  },
  {
    id: 'renaissance-royalty',
    name: 'Renaissance Royalty',
    description: 'Classic portrait, muted palette, regal',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #8B7355, #5C4A3A)',
  },
  {
    id: 'cozy-film-grain',
    name: 'Cozy Film Grain',
    description: 'Soft vintage tones, subtle grain and vignette',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #D4C5A9, #A89880)',
  },
  {
    id: 'rainbow-bridge',
    name: 'Rainbow Bridge',
    description: 'Soft clouds, warm glow, serene memorial mood',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #FFB3BA, #BAFFC9, #BAE1FF)',
  },
  {
    id: 'bold-graphic-poster',
    name: 'Bold Graphic Poster',
    description: 'Flat vector shapes, strong color blocking',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #2C2C2C, #E8E0D8)',
  },
  {
    id: 'aura-gradient',
    name: 'Aura Gradient',
    description: 'Glowing color halos, soft and dreamy',
    available: true,
    gradientPlaceholder: 'linear-gradient(135deg, #C5A3E8, #A3C5E8)',
  },
];

/* ── Prices & variant map ──────────────────────────────────── */

const PRICES = {
  canvas: { '10x10': '$37.00 CAD', '10x20': '$57.50 CAD', '12x18': '$55.00 CAD', '12x24': '$63.00 CAD' },
  poster: { 'default': '$36.11 CAD' },
  // TODO: Add mug once created in Printful
  // mug: { '11oz': '$24.99 CAD' },
};

const VARIANT_MAP = {
  'canvas-10x10': 47156486209685,
  'canvas-10x20': 47156486242453,
  'canvas-12x18': 47156486275221,
  'canvas-12x24': 47156486307989,
  'poster-default': 47167380521109,
  // TODO: Add mug variant IDs once created
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

function saveSession(state) {
  try {
    const data = {
      version: 1,
      petName: state.petName,
      styleId: state.selectedStyleId,
      fontSize: state.fontSize || 'medium',
      jobId: state.jobId,
      previewDataUrls: state.previewDataUrls || [],
      previewCdnUrls: state.previewCdnUrls || [],
      selectedPreviewIndex: state.selectedPreviewIndex,
      generatedAt: new Date().toISOString(),
      imageFilename: state.imageFilename || '',
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
    if (age > 7 * 24 * 60 * 60 * 1000) { localStorage.removeItem(LS_KEY); return null; }
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
  'modern-oil-paint': 'modern-oil-paint',
  'neon-pop-art': 'neon-pop-art',
  'renaissance-royalty': 'renaissance-royalty',
  'cozy-film-grain': 'cozy-film-grain',
  'rainbow-bridge': 'rainbow-bridge',
  'bold-graphic-poster': 'bold-graphic-poster',
  'aura-gradient': 'aura-gradient',
};

async function generatePortrait({ imageFile, styleId, petName }) {
  const formData = new FormData();
  formData.append('photo', imageFile);
  formData.append('pet_name', petName || 'Pet');
  formData.append('style', STYLE_MAP[styleId] || 'classic');

  // Step 1: Submit the job
  const submitRes = await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    body: formData,
  });

  if (submitRes.status === 503 || submitRes.status === 429) {
    throw new Error('BUSY');
  }
  if (!submitRes.ok && submitRes.status !== 202) {
    const err = await submitRes.json().catch(() => ({}));
    throw new Error(err.error || 'Generation failed');
  }

  const submitData = await submitRes.json();
  const jobId = submitData.job_id;

  if (!jobId) {
    // Legacy backend — response already contains the result
    const previews = [submitData.composited, submitData.raw]
      .filter(Boolean)
      .map(p => p.startsWith('http') ? p : `${API_BASE}${p}`);
    return { jobId: 'job-' + Date.now(), previews, filename: submitData.filename || '', cdn: submitData.cdn || false };
  }

  // Step 2: Poll /status/<job_id> until complete
  const POLL_INTERVAL = 2000;  // 2s between polls
  const MAX_POLL_TIME = 120000; // 120s total timeout
  const start = Date.now();

  while (Date.now() - start < MAX_POLL_TIME) {
    await new Promise(r => setTimeout(r, POLL_INTERVAL));

    const pollRes = await fetch(`${API_BASE}/status/${jobId}`);
    if (!pollRes.ok) throw new Error('Failed to check generation status');

    const status = await pollRes.json();

    if (status.status === 'complete') {
      const previews = [status.composited, status.raw]
        .filter(Boolean)
        .map(p => p.startsWith('http') ? p : `${API_BASE}${p}`);
      return { jobId, previews, filename: status.filename || '', cdn: status.cdn === '1' || status.cdn === true };
    }

    if (status.status === 'failed') {
      throw new Error(status.error || 'Generation failed');
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

const ACCEPTED_TYPES = ['image/jpeg', 'image/png'];
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

/* ── usePortraitFlow hook ──────────────────────────────────── */

function usePortraitFlow() {
  // Check for saved session on mount
  const saved = loadSession();
  const [state, setState] = useState({
    stage: saved ? STAGES.PREVIEW : STAGES.UPLOAD,
    photo: null,
    photoThumbnailUrl: null,
    photoDimensions: null,
    photoWarning: null,
    photoError: null,
    petName: saved?.petName || '',
    selectedStyleId: saved?.styleId || null,
    generationStatus: saved ? 'success' : 'idle',
    previewImages: (saved?.previewDataUrls?.length ? saved.previewDataUrls : saved?.previewCdnUrls) || [],
    previewDataUrls: saved?.previewDataUrls || [],
    previewCdnUrls: saved?.previewCdnUrls || [],
    selectedPreviewIndex: saved?.selectedPreviewIndex || 0,
    fontSize: saved?.fontSize || 'medium',
    jobId: saved?.jobId || null,
    restoredSession: !!saved,
  });

  const update = useCallback((patch) => {
    setState(prev => ({ ...prev, ...patch }));
  }, []);

  const setPhoto = useCallback(async (file) => {
    if (!file) return;
    const clearPhoto = (error) => {
      setState(prev => {
        if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
        return { ...prev, photo: null, photoThumbnailUrl: null, photoDimensions: null, photoError: error, photoWarning: null };
      });
    };
    if (!ACCEPTED_TYPES.includes(file.type)) { clearPhoto('Please upload a JPG or PNG file.'); return; }
    if (file.size > MAX_FILE_SIZE) { clearPhoto('This file is over 15 MB. Please use a smaller photo.'); return; }
    const dims = await readImageDimensions(file);
    const thumbUrl = URL.createObjectURL(file);
    let warning = null;
    if (dims && (dims.width < MIN_DIMENSION || dims.height < MIN_DIMENSION)) {
      warning = "This photo might work, but a clearer one usually gives a better result. Want to try another?";
    }
    setState(prev => {
      if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
      return { ...prev, photo: file, photoThumbnailUrl: thumbUrl, photoDimensions: dims, photoWarning: warning, photoError: null };
    });
  }, []);

  const selectStyle = useCallback((styleId) => {
    const style = STYLES.find(s => s.id === styleId);
    if (style && style.available) update({ selectedStyleId: styleId });
  }, [update]);

  const generatingRef = useRef(false);

  const generate = useCallback(async () => {
    if (!state.photo || !state.selectedStyleId) return;
    if (generatingRef.current) return; // prevent double-clicks
    generatingRef.current = true;
    update({ stage: STAGES.GENERATING, generationStatus: 'loading', generationError: null });
    try {
      const result = await generateWithRetry({
        imageFile: state.photo,
        styleId: state.selectedStyleId,
        petName: state.petName,
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
        selectedPreviewIndex: 0, jobId: result.jobId, restoredSession: false,
        imageFilename: result.filename, generationError: null,
      };
      update(newState);
      saveSession({ ...state, ...newState });

      // Fire background mockup generation (non-blocking, with retry)
      if (result.filename) {
        ['canvas', 'poster'].forEach(productType => {
          const fetchMockup = (retries = 1) => {
            fetch(`${API_BASE}/mockups`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ image_filename: result.filename, product_type: productType }),
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
      let userError = 'Something went wrong. Please try again.';
      if (msg === 'TIMEOUT') userError = 'This is taking longer than usual. Please try again — it usually works on the second attempt.';
      else if (msg === 'BUSY') userError = 'Our servers are busy right now. Please wait a moment and try again.';
      update({ stage: STAGES.PREVIEW, generationStatus: 'error', generationError: userError });
    } finally {
      generatingRef.current = false;
    }
  }, [state.photo, state.selectedStyleId, state.petName, update]);

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

  const startFresh = useCallback(() => {
    clearSession();
    setState(prev => {
      if (prev.photoThumbnailUrl) URL.revokeObjectURL(prev.photoThumbnailUrl);
      return {
        stage: STAGES.UPLOAD, photo: null, photoThumbnailUrl: null, photoDimensions: null,
        photoWarning: null, photoError: null, petName: '', selectedStyleId: null,
        generationStatus: 'idle', previewImages: [], previewDataUrls: [], selectedPreviewIndex: 0,
        jobId: null, restoredSession: false,
      };
    });
  }, []);

  return {
    state, setPhoto, selectStyle, generate, selectPreview, goToStage,
    retryFromUpload, retryFromStyle, startFresh, update,
    canContinueFromUpload: state.photo && !state.photoError,
    canGenerate: state.photo && state.selectedStyleId,
  };
}

/* ── Shared style fragments ────────────────────────────────── */

const s = {
  primaryBtn: {
    fontFamily: fontSans, fontWeight: 600, fontSize: '12px',
    letterSpacing: '0.12em', textTransform: 'uppercase',
    background: tokens.colorCta, color: tokens.colorWhite,
    border: 'none', borderRadius: tokens.radiusButton,
    padding: '0 28px', minHeight: '52px', width: '100%',
    cursor: 'pointer', transition: 'background 0.2s', outline: 'none',
  },
  primaryBtnDisabled: { opacity: 0.35, cursor: 'not-allowed' },
  outlineBtn: {
    fontFamily: fontSans, fontWeight: 500, fontSize: '12px',
    letterSpacing: '0.12em', textTransform: 'uppercase',
    color: tokens.colorCta, background: tokens.colorWhite,
    border: `1.5px solid ${tokens.colorBorder}`, borderRadius: tokens.radiusButton,
    padding: '0 28px', minHeight: '48px', minWidth: '200px',
    cursor: 'pointer', outline: 'none', transition: 'border-color 0.15s',
  },
  secondaryLink: {
    fontFamily: fontSans, fontWeight: 400, fontSize: '13px',
    color: tokens.colorMuted, background: 'none', border: 'none',
    padding: '8px 0', cursor: 'pointer', outline: 'none',
    textDecoration: 'none', transition: 'color 0.15s',
  },
  secondaryLinkUnderline: {
    fontFamily: fontSans, fontWeight: 400, fontSize: '13px',
    color: tokens.colorMuted, background: 'none', border: 'none',
    padding: '8px 0', cursor: 'pointer', outline: 'none',
    textDecoration: 'underline', transition: 'color 0.15s',
  },
  bodyMuted: {
    fontFamily: fontSans, fontWeight: 400, fontSize: '13px',
    color: tokens.colorMuted, lineHeight: 1.6, margin: 0,
  },
  serifItalic: {
    fontFamily: fontSerif, fontWeight: 400, fontStyle: 'italic',
    color: tokens.colorBrand,
  },
  smallCaps: {
    fontFamily: fontSans, fontWeight: 500, fontSize: '11px',
    letterSpacing: '0.14em', textTransform: 'uppercase',
    color: tokens.colorMuted,
  },
  serifHeading: {
    fontFamily: fontSerif, fontWeight: 400, fontStyle: 'italic',
    fontSize: '28px', color: tokens.colorBrand,
    margin: '0 0 20px 0', lineHeight: 1.2,
  },
  photoGuidelines: {
    fontFamily: fontSerif, fontStyle: 'italic', fontSize: '15px',
    color: tokens.colorMuted, lineHeight: 1.8, marginBottom: '32px',
  },
  input: {
    fontFamily: fontSans, fontSize: '15px', color: tokens.colorBrand,
    background: 'transparent', border: 'none',
    borderBottom: `1.5px solid ${tokens.colorBorder}`, borderRadius: 0,
    padding: '12px 0', width: '100%', boxSizing: 'border-box',
    outline: 'none', minHeight: '48px', transition: 'border-color 0.15s',
  },
  sectionWrap: { animation: 'pf-fade-in 0.45s ease forwards' },
};

const primaryBtnStyle = (enabled) => ({ ...s.primaryBtn, ...(enabled ? {} : s.primaryBtnDisabled) });

/* ── StepIndicator ─────────────────────────────────────────── */

function StepIndicator({ current, total = 4 }) {
  return React.createElement('div', {
    style: { marginBottom: '32px' },
    'aria-label': `Step ${current} of ${total}`,
    role: 'progressbar',
    'aria-valuenow': current,
    'aria-valuemin': 1,
    'aria-valuemax': total,
  },
    React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 10px' } },
      `Step ${current} of ${total}`
    ),
    React.createElement('div', {
      style: { height: '1px', background: tokens.colorBorder, position: 'relative' },
    },
      React.createElement('div', {
        style: {
          position: 'absolute', top: 0, left: 0, height: '1px',
          background: tokens.colorAccent,
          width: `${(current / total) * 100}%`,
          transition: 'width 0.4s ease',
        },
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

function PetNameInput({ id, value, onChange }) {
  return React.createElement('div', { style: { marginBottom: '28px' } },
    React.createElement('label', {
      htmlFor: id,
      style: { ...s.smallCaps, display: 'block', marginBottom: '4px' },
    }, "Your pet\u2019s name"),
    React.createElement('input', {
      id, type: 'text', placeholder: 'e.g. Biscuit',
      value, onChange, maxLength: 40, style: s.input,
    }),
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

function UploadStep({ state, setPhoto, update, canContinue, onContinue }) {
  const cameraRef = useRef(null);
  const fileRef = useRef(null);

  const handleFile = useCallback((e) => {
    const file = e.target.files?.[0];
    if (file) setPhoto(file);
  }, [setPhoto]);

  const handlePetName = useCallback((e) => update({ petName: e.target.value }), [update]);
  const hasPhoto = state.photo && !state.photoError;

  return React.createElement('div', { style: s.sectionWrap },
    React.createElement(StepIndicator, { current: 1 }),

    // Pet name FIRST — emotional hook, personal immediately
    React.createElement(PetNameInput, { id: 'pf-pet-name', value: state.petName, onChange: handlePetName }),

    // Photo upload — compact and action-oriented
    React.createElement('p', {
      style: { ...s.smallCaps, margin: '0 0 10px' },
    }, 'Upload their best photo'),

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
              style: { fontFamily: fontSans, fontSize: '14px', fontWeight: 500, color: tokens.colorBrand, margin: '0 0 2px' },
            }, 'Photo uploaded \u2713'),
            React.createElement('button', {
              type: 'button', style: { ...s.secondaryLinkUnderline, fontSize: '13px' },
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
          React.createElement('div', { style: { display: 'flex', gap: '10px', flexWrap: 'wrap', justifyContent: 'center' } },
            React.createElement('button', {
              type: 'button', style: { ...s.primaryBtn, width: 'auto', padding: '12px 24px', fontSize: '13px' },
              onClick: () => cameraRef.current?.click(),
            }, '\uD83D\uDCF7  TAKE A PHOTO'),
            React.createElement('button', {
              type: 'button', style: { ...s.outlineBtn, padding: '12px 24px', fontSize: '13px' },
              onClick: () => fileRef.current?.click(),
            }, 'UPLOAD'),
          ),
          React.createElement(HiddenFileInput, { inputRef: cameraRef, onChange: handleFile, capture: 'environment' }),
        ),

    // Hidden file input (shared)
    React.createElement(HiddenFileInput, { inputRef: fileRef, onChange: handleFile }),

    // Warning / error
    state.photoWarning && React.createElement('p', {
      style: { ...s.bodyMuted, color: tokens.colorWarning, marginBottom: '12px' }, role: 'alert',
    }, state.photoWarning),
    state.photoError && React.createElement('p', {
      style: { ...s.bodyMuted, color: tokens.colorError, marginTop: '10px' }, role: 'alert',
    }, state.photoError),

    // Photo tips — inline, compact
    React.createElement('div', {
      style: {
        display: 'flex', gap: '12px', flexWrap: 'wrap', margin: '16px 0 24px',
        justifyContent: 'center',
      },
    },
      ['\uD83D\uDC41 Face clearly visible', '\u2600\uFE0F Good lighting', '\uD83D\uDC3E One pet per photo'].map(tip =>
        React.createElement('span', {
          key: tip,
          style: { fontFamily: fontSans, fontSize: '12px', color: tokens.colorMuted },
        }, tip),
      ),
    ),

    // Continue button — always visible, disabled state communicates what's needed
    React.createElement('button', {
      type: 'button',
      style: primaryBtnStyle(canContinue),
      disabled: !canContinue, onClick: onContinue,
      'aria-label': 'Continue to style selection',
    }, canContinue ? 'CHOOSE YOUR STYLE \u2192' : 'ADD PHOTO & NAME TO CONTINUE'),
  );
}

/* ── StyleStep ─────────────────────────────────────────────── */

function StyleStep({ state, selectStyle, onGenerate, canGenerate, onBack }) {
  // Preload all style fonts so they're ready by preview step
  useEffect(() => {
    STYLES.forEach(style => { if (style.available) loadGoogleFont(style.id); });
  }, []);

  return React.createElement('div', { style: s.sectionWrap },
    React.createElement(StepIndicator, { current: 2 }),
    React.createElement('h2', { style: s.serifHeading }, 'Choose your artistic finish'),

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
          // Thumbnail
          React.createElement('div', {
            style: { width: '100%', aspectRatio: '1/1', background: style.gradientPlaceholder, position: 'relative' },
          },
            // Badge
            style.badge && React.createElement('span', {
              style: {
                position: 'absolute', top: '8px', left: '8px',
                ...s.smallCaps, fontSize: '9px', letterSpacing: '0.1em',
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
                fontSize: '12px',
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
                  ...s.smallCaps, fontSize: '10px',
                  color: tokens.colorMuted, background: 'rgba(255,255,255,0.85)',
                  borderRadius: '4px', padding: '4px 10px',
                },
              }, 'Available soon'),
            ),
          ),
          // Card body — style name + font preview
          React.createElement('div', { style: { padding: '8px 8px 10px' } },
            React.createElement('p', {
              style: {
                fontFamily: fontSans, fontWeight: 600, fontSize: '11px',
                color: selected ? tokens.colorAccent : tokens.colorBrand,
                margin: '0 0 2px', lineHeight: 1.3,
              },
            }, style.name),
            // Show pet name preview in the style's font (or "Abc" if no name)
            React.createElement('p', {
              style: {
                fontFamily: (STYLE_FONTS[style.id] || {}).css || fontSerif,
                fontWeight: 700, fontSize: '13px',
                color: tokens.colorMuted,
                margin: 0, lineHeight: 1.2,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              },
            }, state.petName || 'Abc'),
          ),
        );
      }),
    ),

    React.createElement('div', { style: { marginTop: '28px', display: 'flex', flexDirection: 'column', gap: '10px' } },
      React.createElement('button', {
        type: 'button',
        style: primaryBtnStyle(canGenerate),
        disabled: !canGenerate, onClick: onGenerate,
        'aria-label': 'Create my portrait',
      }, 'CREATE MY PORTRAIT'),
      React.createElement('button', {
        type: 'button',
        style: { ...s.secondaryLink, textAlign: 'center', width: '100%' },
        onClick: onBack,
      }, '\u2190 Back to photo'),
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
        ...s.serifItalic, fontSize: '20px', margin: 0, minHeight: '28px',
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

/* ── PreviewStep ───────────────────────────────────────────── */

function PreviewStep({ state, update, selectPreview, onContinue, retryFromUpload, retryFromStyle, generate }) {
  if (state.generationStatus === 'error') {
    return React.createElement('div', { style: { ...s.sectionWrap, textAlign: 'center', padding: '48px 16px' } },
      React.createElement('h2', {
        style: { ...s.serifItalic, fontSize: '22px', marginBottom: '8px' },
      }, 'Something went off-leash'),
      React.createElement('p', {
        style: { ...s.bodyMuted, fontSize: '14px', marginBottom: '28px' },
      }, "It happens. Your photo and style are saved \u2014 just try again."),
      React.createElement('button', {
        type: 'button', style: { ...s.primaryBtn, marginBottom: '14px' },
        onClick: generate, 'aria-label': 'Try generating the portrait again',
      }, 'TRY AGAIN'),
      React.createElement('button', {
        type: 'button', style: { ...s.secondaryLink, width: '100%', textAlign: 'center' },
        onClick: retryFromUpload,
      }, 'Try another photo'),
    );
  }

  const mainImage = state.previewImages[state.selectedPreviewIndex];
  const styleFontDef = STYLE_FONTS[state.selectedStyleId] || STYLE_FONTS['soft-watercolour'];
  const currentSizeDef = FONT_SIZES.find(f => f.id === (state.fontSize || 'medium')) || FONT_SIZES[1];
  const nameBasePx = 32;
  const nameFontSize = Math.round(nameBasePx * currentSizeDef.scale);

  // Load the style-specific Google Font
  useEffect(() => {
    if (state.selectedStyleId) loadGoogleFont(state.selectedStyleId);
  }, [state.selectedStyleId]);

  return React.createElement('div', { style: { ...s.sectionWrap, animation: 'pf-reveal-up 0.6s ease forwards' } },
    React.createElement(StepIndicator, { current: 3 }),

    // Main preview
    React.createElement('div', {
      style: {
        width: '100%', maxWidth: '520px', margin: '0 auto 20px', borderRadius: tokens.radiusCard,
        overflow: 'hidden', boxShadow: '0 12px 40px rgba(28, 28, 28, 0.12)',
      },
    },
      React.createElement('img', {
        src: mainImage, alt: state.petName ? `Portrait of ${state.petName}` : 'Your pet portrait preview',
        style: { width: '100%', display: 'block' },
      }),
    ),

    // Thumbnails
    state.previewImages.length > 1 && React.createElement('div', {
      style: { display: 'flex', gap: '10px', justifyContent: 'center', marginBottom: '20px' },
      role: 'listbox', 'aria-label': 'Preview variants',
    },
      state.previewImages.map((src, idx) =>
        React.createElement('button', {
          key: idx, type: 'button', role: 'option',
          'aria-selected': idx === state.selectedPreviewIndex,
          'aria-label': `Preview variant ${idx + 1}`,
          onClick: () => selectPreview(idx),
          style: {
            width: '56px', height: '56px', borderRadius: '10px', overflow: 'hidden',
            border: idx === state.selectedPreviewIndex ? `2px solid ${tokens.colorAccent}` : `1px solid ${tokens.colorBorder}`,
            padding: 0, cursor: 'pointer', outline: 'none', background: 'none',
          },
        },
          React.createElement('img', {
            src, alt: `Variant ${idx + 1}`,
            style: { width: '100%', height: '100%', objectFit: 'cover', display: 'block' },
          }),
        )
      ),
    ),

    // Pet name treatment with style-matched font
    React.createElement('div', { style: { textAlign: 'center', marginBottom: '16px' } },
      React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 4px' } }, 'Your bespoke portrait'),
      state.petName && React.createElement('p', {
        style: {
          fontFamily: styleFontDef.css, fontWeight: 700,
          fontSize: `${nameFontSize}px`, color: tokens.colorBrand,
          margin: 0, letterSpacing: '0.04em', transition: 'all 0.3s ease',
        },
      }, state.petName),
    ),

    // Font size selector
    state.petName && React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', marginBottom: '24px' },
      role: 'group', 'aria-label': 'Name size',
    },
      React.createElement('span', {
        style: { fontFamily: fontSans, fontSize: '11px', color: tokens.colorMuted, textTransform: 'uppercase', letterSpacing: '0.1em', marginRight: '4px' },
      }, 'Name size'),
      FONT_SIZES.map(size =>
        React.createElement('button', {
          key: size.id, type: 'button',
          'aria-label': `${size.id} name size`,
          'aria-pressed': state.fontSize === size.id,
          onClick: () => {
            update({ fontSize: size.id });
            saveSession({ ...state, fontSize: size.id });
          },
          style: {
            width: '36px', height: '36px', borderRadius: '8px',
            border: state.fontSize === size.id ? `2px solid ${tokens.colorAccent}` : `1px solid ${tokens.colorBorder}`,
            background: state.fontSize === size.id ? tokens.colorAccentLight : tokens.colorWhite,
            color: state.fontSize === size.id ? tokens.colorAccent : tokens.colorMuted,
            fontFamily: fontSans, fontWeight: 600, fontSize: '13px',
            cursor: 'pointer', outline: 'none', transition: 'all 0.2s',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          },
        }, size.label)
      ),
    ),

    // Actions
    React.createElement('div', { style: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '10px' } },
      React.createElement('button', {
        type: 'button', style: s.primaryBtn, onClick: onContinue,
        'aria-label': 'Choose format',
      }, 'CHOOSE FORMAT'),
      React.createElement('button', {
        type: 'button', style: s.secondaryLinkUnderline,
        onClick: retryFromUpload,
      }, 'Not quite right? Try another photo'),
      React.createElement('button', {
        type: 'button', style: s.secondaryLinkUnderline,
        onClick: retryFromStyle,
      }, 'Try a different style'),
    ),
  );
}

/* ── ProductGallery ────────────────────────────────────────── */

function ProductGallery({ state, retryFromStyle, startFresh }) {
  const mainImage = state.previewImages[state.selectedPreviewIndex];

  return React.createElement('div', { style: { ...s.sectionWrap, animation: 'pf-reveal-up 0.6s ease forwards' } },
    React.createElement(StepIndicator, { current: 4, total: 4 }),

    // Portrait reminder (small)
    React.createElement('div', {
      style: { display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '28px', padding: '16px', background: tokens.colorWhite, borderRadius: tokens.radiusCard, border: `1px solid ${tokens.colorBorder}` },
    },
      React.createElement('img', {
        src: mainImage, alt: state.petName ? `Portrait of ${state.petName}` : 'Your portrait',
        style: { width: '72px', height: '72px', borderRadius: '10px', objectFit: 'cover' },
      }),
      React.createElement('div', { style: { flex: 1 } },
        React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 2px' } }, 'Your portrait'),
        state.petName && React.createElement('p', {
          style: { fontFamily: fontSerif, fontStyle: 'italic', fontSize: '20px', color: tokens.colorBrand, margin: 0 },
        }, state.petName),
      ),
    ),

    // Heading
    React.createElement('h2', { style: { ...s.serifHeading, marginBottom: '8px' } }, 'Choose your keepsake'),
    React.createElement('p', { style: { ...s.bodyMuted, marginBottom: '24px' } }, 'Select a format to see sizes, pricing, and details.'),

    // Product cards (vertical stack)
    React.createElement('div', {
      style: { display: 'flex', flexDirection: 'column', gap: '14px', marginBottom: '28px' },
    },
      PRODUCT_CATALOGUE.map((product) =>
        React.createElement('a', {
          key: product.handle,
          href: product.available ? `/products/${product.handle}` : undefined,
          onClick: product.available ? undefined : (e) => e.preventDefault(),
          'aria-label': product.available ? `View ${product.name} options` : `${product.name} \u2014 coming soon`,
          style: {
            display: 'flex', alignItems: 'center', gap: '16px',
            padding: '20px', borderRadius: tokens.radiusCard,
            border: `1px solid ${tokens.colorBorder}`,
            background: tokens.colorWhite,
            cursor: product.available ? 'pointer' : 'default',
            textDecoration: 'none', transition: 'all 0.2s',
            opacity: product.available ? 1 : 0.55,
            position: 'relative', overflow: 'hidden',
          },
        },
          // Portrait thumbnail on the product
          React.createElement('div', {
            style: {
              width: '80px', height: '80px', borderRadius: '10px', overflow: 'hidden',
              flexShrink: 0, background: tokens.colorAccentLight,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            },
          },
            React.createElement('img', {
              src: mainImage, alt: '',
              style: { width: '100%', height: '100%', objectFit: 'cover', display: 'block' },
            }),
          ),
          // Product info
          React.createElement('div', { style: { flex: 1 } },
            React.createElement('p', {
              style: { fontFamily: fontSans, fontWeight: 500, fontSize: '15px', color: tokens.colorBrand, margin: '0 0 3px' },
            }, product.name),
            React.createElement('p', {
              style: { fontFamily: fontSans, fontWeight: 400, fontSize: '12px', color: tokens.colorMuted, margin: '0 0 6px', lineHeight: 1.4 },
            }, product.sub),
            React.createElement('p', {
              style: { fontFamily: fontSans, fontWeight: 600, fontSize: '14px', color: product.available ? tokens.colorBrand : tokens.colorMuted, margin: 0 },
            }, product.available ? `From ${product.fromPrice}` : product.fromPrice),
          ),
          // Arrow
          product.available && React.createElement('span', {
            style: { fontFamily: fontSans, fontSize: '18px', color: tokens.colorMuted, flexShrink: 0 },
            'aria-hidden': true,
          }, '\u2192'),
        ),
      ),
    ),

    // Guarantee strip
    React.createElement('div', {
      style: { borderTop: `1px solid ${tokens.colorBorder}`, paddingTop: '20px', marginBottom: '24px', display: 'flex', flexDirection: 'column', gap: '8px' },
    },
      ['Preview before we print', "Not in love? We\u2019ll try again, free", 'Still not right? Full refund, no questions'].map((line, i) =>
        React.createElement('p', { key: i, style: s.bodyMuted },
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
      React.createElement('button', {
        type: 'button', style: s.secondaryLinkUnderline,
        onClick: startFresh,
      }, 'Start over with a new photo'),
    ),
  );
}

/* ── TrustBar ──────────────────────────────────────────────── */

function PageHero() {
  return React.createElement('div', {
    style: { textAlign: 'center', marginBottom: '28px', padding: '0 8px' },
  },
    // Page title — clear, emotional, benefit-driven
    React.createElement('h1', {
      style: {
        fontFamily: fontSerif, fontWeight: 400, fontStyle: 'italic',
        fontSize: 'clamp(28px, 7vw, 38px)', color: tokens.colorBrand,
        margin: '0 0 8px', lineHeight: 1.15,
      },
    }, 'Turn your pet into art'),
    React.createElement('p', {
      style: {
        fontFamily: fontSans, fontSize: '15px', color: tokens.colorMuted,
        margin: '0 0 16px', lineHeight: 1.5,
      },
    }, 'Upload a photo, pick a style \u2014 we\u2019ll create a one-of-a-kind portrait you can hang on your wall.'),

    // Social proof + trust — compact row
    React.createElement('div', {
      style: {
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        gap: '6px', flexWrap: 'wrap', marginBottom: '4px',
      },
    },
      // Stars
      React.createElement('span', {
        style: { color: '#D4A84B', fontSize: '14px', letterSpacing: '1px' },
        'aria-hidden': true,
      }, '\u2605\u2605\u2605\u2605\u2605'),
      React.createElement('span', {
        style: { fontFamily: fontSans, fontSize: '13px', fontWeight: 500, color: tokens.colorBrand },
      }, '4.9/5'),
      React.createElement('span', {
        style: { fontFamily: fontSans, fontSize: '13px', color: tokens.colorMuted },
      }, '\u00B7 124+ happy pet parents'),
    ),

    // Trust badges — horizontal, clean
    React.createElement('div', {
      style: {
        display: 'flex', justifyContent: 'center', gap: '16px', flexWrap: 'wrap',
        marginTop: '12px',
      },
    },
      ['Preview before you pay', 'Free shipping over $75', 'Satisfaction guaranteed'].map(text =>
        React.createElement('span', {
          key: text,
          style: {
            fontFamily: fontSans, fontSize: '11px', fontWeight: 500,
            color: tokens.colorMuted, display: 'flex', alignItems: 'center', gap: '4px',
          },
        },
          React.createElement('span', { style: { color: tokens.colorSuccess, fontSize: '10px' } }, '\u2713'),
          text,
        ),
      ),
    ),
  );
}

/* ── Main PortraitFlow component ───────────────────────────── */

function PortraitFlow() {
  const flow = usePortraitFlow();
  const { state } = flow;
  useEffect(() => { injectKeyframes(); }, []);

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
        state, selectStyle: flow.selectStyle, onGenerate: flow.generate,
        canGenerate: flow.canGenerate, onBack: () => flow.goToStage(STAGES.UPLOAD),
      }); break;
    case STAGES.GENERATING:
      content = React.createElement(GeneratingState); break;
    case STAGES.PREVIEW:
      content = React.createElement(PreviewStep, {
        state, update: flow.update, selectPreview: flow.selectPreview,
        onContinue: () => flow.goToStage(STAGES.GALLERY),
        retryFromUpload: flow.retryFromUpload, retryFromStyle: flow.retryFromStyle,
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
  );
}

/* ── Mount ─────────────────────────────────────────────────── */

const pfRoot = document.getElementById('portrait-flow-root');
const pfShippingDate = pfRoot?.dataset?.shippingDate || '';
const pfOccasion = pfRoot?.dataset?.occasion || '';
if (pfRoot) {
  ReactDOM.createRoot(pfRoot).render(React.createElement(PortraitFlow));
}
