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

const GENERATION_RESET = { generationStatus: 'idle', previewImages: [], previewDataUrls: [], jobId: null, queuePosition: 0 };

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
      jobId: state.jobId,
      previewDataUrls: state.previewDataUrls || [],
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
    if (data.version !== 1 || !data.previewDataUrls?.length) return null;
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

// Map widget style IDs to backend style keys
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

async function submitPortrait({ imageFile, styleId, petName }) {
  const formData = new FormData();
  formData.append('photo', imageFile);
  formData.append('pet_name', petName || 'Pet');
  formData.append('style', STYLE_MAP[styleId] || 'classic');

  const res = await fetch(`${API_BASE}/generate`, { method: 'POST', body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || 'Submission failed');
  }
  const data = await res.json();
  return { jobId: data.job_id, position: data.position || 0 };
}

async function pollJobStatus(jobId) {
  const res = await fetch(`${API_BASE}/status/${jobId}`);
  if (!res.ok) throw new Error('Status check failed');
  return res.json();
}

function resolveUrl(url) {
  if (!url) return '';
  if (url.startsWith('http')) return url;
  return `${API_BASE}${url}`;
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
    previewImages: saved?.previewDataUrls || [],
    previewDataUrls: saved?.previewDataUrls || [],
    selectedPreviewIndex: saved?.selectedPreviewIndex || 0,
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

  const pollingRef = useRef(null);

  // Clean up polling on unmount
  useEffect(() => {
    return () => { if (pollingRef.current) clearInterval(pollingRef.current); };
  }, []);

  const generate = useCallback(async () => {
    if (!state.photo || !state.selectedStyleId) return;
    update({ stage: STAGES.GENERATING, generationStatus: 'loading', queuePosition: 0 });

    try {
      // Step 1: Submit — returns instantly with a job ID
      const { jobId, position } = await submitPortrait({
        imageFile: state.photo, styleId: state.selectedStyleId, petName: state.petName,
      });
      update({ jobId: jobId, queuePosition: position });

      // Step 2: Poll /status/<job_id> every 2.5s until complete or failed
      await new Promise((resolve, reject) => {
        const POLL_INTERVAL = 2500;
        const MAX_POLL_TIME = 5 * 60 * 1000; // 5 min safety timeout
        const startTime = Date.now();

        pollingRef.current = setInterval(async () => {
          try {
            if (Date.now() - startTime > MAX_POLL_TIME) {
              clearInterval(pollingRef.current);
              pollingRef.current = null;
              reject(new Error('timeout'));
              return;
            }

            const status = await pollJobStatus(jobId);

            if (status.status === 'queued') {
              update({ queuePosition: status.position || 0 });
            } else if (status.status === 'processing') {
              update({ queuePosition: 0, generationStatus: 'processing' });
            } else if (status.status === 'complete') {
              clearInterval(pollingRef.current);
              pollingRef.current = null;

              const previews = [status.composited, status.raw]
                .filter(Boolean)
                .map(resolveUrl);
              const dataUrls = await Promise.all(previews.map(imageUrlToDataUrl));
              const validDataUrls = dataUrls.filter(Boolean);
              const newState = {
                stage: STAGES.PREVIEW, generationStatus: 'success',
                previewImages: validDataUrls.length ? validDataUrls : previews,
                previewDataUrls: validDataUrls,
                selectedPreviewIndex: 0, jobId: jobId, restoredSession: false,
                imageFilename: status.filename || '',
              };
              update(newState);
              saveSession({ ...state, ...newState });

              // Fire background mockup generation (non-blocking)
              if (status.filename) {
                ['canvas', 'poster'].forEach(productType => {
                  fetch(`${API_BASE}/mockups`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_filename: status.filename, product_type: productType }),
                  })
                  .then(r => r.ok ? r.json() : null)
                  .then(data => {
                    if (!data || !data.mockups) return;
                    try {
                      const session = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
                      if (!session.mockups) session.mockups = {};
                      session.mockups[productType] = data.mockups;
                      localStorage.setItem(LS_KEY, JSON.stringify(session));
                    } catch (e) { /* ignore storage errors */ }
                  })
                  .catch(() => { /* mockup generation is best-effort */ });
                });
              }

              resolve();
            } else if (status.status === 'failed') {
              clearInterval(pollingRef.current);
              pollingRef.current = null;
              reject(new Error(status.error || 'Generation failed'));
            }
          } catch (pollErr) {
            // Network blip — keep polling, don't fail yet
          }
        }, POLL_INTERVAL);
      });
    } catch {
      update({ stage: STAGES.PREVIEW, generationStatus: 'error' });
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
    React.createElement('h2', { style: s.serifHeading }, 'Begin with a photo'),

    // Conditional: thumbnail or dropzone
    hasPhoto
      ? React.createElement('div', {
          style: { display: 'flex', alignItems: 'center', gap: '16px', marginBottom: '24px' },
        },
          React.createElement('img', {
            src: state.photoThumbnailUrl, alt: 'Selected pet photo',
            style: { width: '100px', height: '100px', borderRadius: tokens.radiusCard, objectFit: 'cover' },
          }),
          React.createElement('button', {
            type: 'button', style: s.secondaryLinkUnderline,
            onClick: () => fileRef.current?.click(), 'aria-label': 'Change photo',
          }, 'Change photo'),
        )
      : React.createElement('div', {
          style: dropzoneStyle, role: 'group', 'aria-label': 'Photo upload area',
          onDragOver: (e) => e.preventDefault(),
          onDrop: (e) => { e.preventDefault(); const file = e.dataTransfer?.files?.[0]; if (file) setPhoto(file); },
        },
          React.createElement(CameraIcon, { size: 36 }),
          React.createElement('button', {
            type: 'button', style: { ...s.primaryBtn, width: 'auto', minWidth: '200px' },
            onClick: () => cameraRef.current?.click(), 'aria-label': 'Take a photo with your camera',
          }, 'TAKE A PHOTO'),
          React.createElement(HiddenFileInput, { inputRef: cameraRef, onChange: handleFile, capture: 'environment' }),
          React.createElement('button', {
            type: 'button', style: s.outlineBtn,
            onClick: () => fileRef.current?.click(), 'aria-label': 'Upload from library',
          }, 'UPLOAD FROM LIBRARY'),
        ),

    // Hidden file input (shared across both states)
    React.createElement(HiddenFileInput, { inputRef: fileRef, onChange: handleFile }),

    // Warning / error
    state.photoWarning && React.createElement('p', {
      style: { ...s.bodyMuted, color: tokens.colorWarning, marginBottom: '16px' }, role: 'alert',
    }, state.photoWarning),
    state.photoError && React.createElement('p', {
      style: { ...s.bodyMuted, color: tokens.colorError, marginTop: '14px' }, role: 'alert',
    }, state.photoError),

    // Pet name + guidelines (always visible)
    React.createElement(PetNameInput, { id: 'pf-pet-name', value: state.petName, onChange: handlePetName }),
    React.createElement(PhotoGuidelines),

    // Continue button (only when photo selected)
    hasPhoto && React.createElement('button', {
      type: 'button',
      style: primaryBtnStyle(canContinue),
      disabled: !canContinue, onClick: onContinue,
      'aria-label': 'Continue to style selection',
    }, 'CONTINUE'),
  );
}

/* ── StyleStep ─────────────────────────────────────────────── */

function StyleStep({ state, selectStyle, onGenerate, canGenerate, onBack }) {
  return React.createElement('div', { style: s.sectionWrap },
    React.createElement(StepIndicator, { current: 2 }),
    React.createElement('h2', { style: s.serifHeading }, 'Choose your artistic finish'),

    React.createElement('div', {
      style: {
        display: 'flex', gap: '12px', overflowX: 'auto', paddingBottom: '8px',
        WebkitOverflowScrolling: 'touch', scrollbarWidth: 'none',
        margin: '0 -16px', padding: '0 16px 8px',
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
            flex: '0 0 164px',
            border: selected ? `1.5px solid ${tokens.colorAccent}` : `1px solid ${tokens.colorBorder}`,
            borderRadius: tokens.radiusCard, background: selected ? tokens.colorAccentLight : tokens.colorWhite,
            padding: 0, cursor: style.available ? 'pointer' : 'default',
            textAlign: 'left', outline: 'none', overflow: 'hidden', transition: 'all 0.2s',
            position: 'relative',
          },
        },
          // Thumbnail
          React.createElement('div', {
            style: { width: '100%', height: '140px', background: style.gradientPlaceholder, position: 'relative' },
          },
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
          // Card body
          React.createElement('div', { style: { padding: '12px 14px 16px' } },
            style.badge && React.createElement('span', {
              style: {
                display: 'inline-block',
                ...s.smallCaps, fontSize: '9px', letterSpacing: '0.1em',
                color: tokens.colorWhite, background: tokens.colorSuccess,
                borderRadius: '3px', padding: '3px 7px',
                margin: '0 0 6px',
              },
            }, style.badge),
            React.createElement('p', {
              style: {
                fontFamily: fontSans, fontWeight: 500, fontSize: '14px',
                color: selected ? tokens.colorAccent : tokens.colorBrand,
                margin: '0 0 3px',
              },
            }, style.name),
            React.createElement('p', {
              style: { fontFamily: fontSans, fontWeight: 400, fontSize: '12px', color: tokens.colorMuted, margin: 0, lineHeight: 1.4 },
            }, style.description),
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
  'Almost there\u2026',
];

function GeneratingState({ queuePosition, generationStatus }) {
  const [phraseIdx, setPhraseIdx] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setPhraseIdx(prev => (prev + 1) % LOADING_PHRASES.length), 2500);
    return () => clearInterval(timer);
  }, []);

  const isQueued = queuePosition > 0;
  const isProcessing = generationStatus === 'processing' || (queuePosition === 0 && generationStatus === 'loading');

  let statusText = 'Usually just a few seconds';
  if (isQueued && queuePosition === 1) {
    statusText = "You\u2019re next \u2014 starting soon";
  } else if (isQueued) {
    statusText = `You\u2019re #${queuePosition} in line \u2014 won\u2019t be long`;
  } else if (isProcessing) {
    statusText = 'Painting your portrait now\u2026';
  }

  return React.createElement('div', {
    style: {
      ...s.sectionWrap, display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', minHeight: '360px', textAlign: 'center', gap: '24px', padding: '40px 16px',
    },
    role: 'status', 'aria-live': 'polite', 'aria-label': 'Generating your portrait',
  },
    // Watercolor wash
    React.createElement('div', {
      style: {
        width: '100%', maxWidth: '400px', height: '200px', borderRadius: tokens.radiusCard,
        background: 'linear-gradient(135deg, #E8DDD0 0%, #D4C5B0 40%, #C5CCD4 100%)',
        animation: 'pf-watercolor-pulse 3.5s ease-in-out infinite',
      },
    }),
    // Phrase
    React.createElement('p', {
      style: { ...s.serifItalic, fontSize: '20px', margin: 0, minHeight: '28px' },
    }, LOADING_PHRASES[phraseIdx]),
    // Status line (queue position or processing)
    React.createElement('p', {
      style: { ...s.bodyMuted, minHeight: '20px', transition: 'opacity 0.3s' },
    }, statusText),
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

function PreviewStep({ state, selectPreview, onContinue, retryFromUpload, retryFromStyle, generate }) {
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
        width: 800, height: 1000,
        style: { width: '100%', height: 'auto', display: 'block' },
        fetchPriority: 'high', decoding: 'async',
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

    // Pet name treatment
    React.createElement('div', { style: { textAlign: 'center', marginBottom: '24px' } },
      React.createElement('p', { style: { ...s.smallCaps, margin: '0 0 4px' } }, 'Your bespoke portrait'),
      state.petName && React.createElement('p', {
        style: {
          fontFamily: fontSerif, fontStyle: 'italic', fontWeight: 400,
          fontSize: '32px', color: tokens.colorBrand, margin: 0, letterSpacing: '0.02em',
        },
      }, state.petName),
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

function TrustBar() {
  const badges = [
    'Ships in 3\u20135 business days',
    pfShippingDate && pfOccasion ? `Order by ${pfShippingDate} for ${pfOccasion} delivery` : null,
    'Free returns',
  ].filter(Boolean);

  const giftPills = ['Dog moms', 'Cat people', 'Birthdays', 'Gotcha days', 'Rainbow bridge memorials'];

  return React.createElement('div', { style: { marginBottom: '32px' } },
    // Trust line
    React.createElement('p', {
      style: { ...s.smallCaps, fontSize: '10px', margin: '0 0 14px', lineHeight: 1.8 },
      'aria-label': 'Trust and shipping information',
    }, badges.join('  \u00B7  ')),

    // Gift pills
    React.createElement('p', {
      style: { ...s.smallCaps, fontSize: '10px', margin: '0 0 8px' },
    }, 'A beautiful gift for'),
    React.createElement('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
      giftPills.map((pill) =>
        React.createElement('span', {
          key: pill,
          style: {
            fontFamily: fontSans, fontWeight: 500, fontSize: '12px',
            color: tokens.colorAccent, background: tokens.colorAccentLight,
            borderRadius: '20px', padding: '5px 12px', whiteSpace: 'nowrap',
          },
        }, pill),
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
      content = React.createElement(GeneratingState, {
        queuePosition: state.queuePosition || 0,
        generationStatus: state.generationStatus,
      }); break;
    case STAGES.PREVIEW:
      content = React.createElement(PreviewStep, {
        state, selectPreview: flow.selectPreview,
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
    style: { fontFamily: fontSans, maxWidth: '600px', margin: '0 auto', padding: '32px 20px', background: tokens.colorSurface },
  },
    React.createElement(TrustBar),
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
