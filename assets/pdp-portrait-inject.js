/* ─────────────────────────────────────────────────────────────
   PDP Portrait Injection
   Reads the saved portrait from localStorage and injects it as
   the hero image on product pages. Renders one client-side
   canvas-on-wall mockup per variant, sourced from the per-aspect
   un-watermarked print PNG so the PDP shows pixel-for-pixel the
   front face Printful prints. Watermark is applied as a CSS
   overlay rather than baked into the source.
   ───────────────────────────────────────────────────────────── */
(function () {
  console.log('[PetPrintables] PDP inject script loaded v4');
  var LS_KEY = 'petPrintables_session';
  var raw;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { /* no storage access */ }

  // No-portrait PDP flow — repurpose the ATC button as a single CTA that
  // takes the customer into /pages/create. Upload + name + style picking
  // all happen there now; the PDP no longer collects them inline. The
  // older IDB / localStorage pending-photo handoff was removed with the
  // inline form — /pages/create is the single source of truth for input.
  function setupPdpPreGenFlow() {
    console.log('[PetPrintables] No portrait yet → CTA → /pages/create');
    function init() {
      var atcBtn = document.querySelector('.atc-btn');
      var form = document.querySelector('.product-form, form[action*="/cart/add"]');
      if (!atcBtn || !form) return;

      var ctaText = 'CREATE MY PORTRAIT →';
      atcBtn.textContent = ctaText;
      atcBtn.setAttribute('type', 'button');
      atcBtn.removeAttribute('name');
      atcBtn.setAttribute('data-pregen', 'true'); // flag so theme.js skips updating it
      // Print-on-demand: variants never go truly out of stock. If
      // Shopify rendered the button as disabled because product.available
      // was false (location-fulfillment misconfig, Printful sync lag,
      // etc.), the customer is blocked from a CTA that just navigates to
      // /pages/create — no inventory check needed. Force-enable so the
      // CTA always works.
      atcBtn.disabled = false;
      atcBtn.removeAttribute('disabled');

      // Other scripts (theme.js variant updates) try to rewrite the ATC label
      // with a price string — restore the CTA text whenever that happens.
      var observer = new MutationObserver(function () {
        if (atcBtn.textContent.trim() !== ctaText) atcBtn.textContent = ctaText;
      });
      observer.observe(atcBtn, { childList: true, characterData: true, subtree: true });

      function go(e) {
        if (e) { e.preventDefault(); e.stopPropagation(); }
        window.location.href = '/pages/create';
      }
      form.addEventListener('submit', go, true);
      atcBtn.addEventListener('click', go, true);
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  }

  // ── Main flow decision ────────────────────────────────────
  // No completed session → run the pre-gen PDP flow
  if (!raw) {
    setupPdpPreGenFlow();
    return;
  }

  var data;
  try { data = JSON.parse(raw); } catch (e) {}
  if (!data || data.version !== 1) {
    setupPdpPreGenFlow();
    return;
  }

  console.log('[PetPrintables] Session found, showing completed portrait flow');

  // Wait for DOM to be parsed before injecting
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runInjection);
  } else {
    runInjection();
  }

  function runInjection() {

  // CDN freshness check — preview jobs now mark complete with local
  // /preview/ URLs while the R2 upload runs in the background. Most
  // customers reach PDP after the upgrade poll in portrait-flow.js
  // has already swapped CDN URLs into localStorage, but a fast click
  // can land here with local URLs still in `data`. Fire a one-shot
  // /status fetch so cart writes never use a /preview/ URL.
  (function upgradeLocalUrlsToCdn() {
    var urls = (data.previewCdnUrls || []).concat([
      data.printFileUrl || '',
      data.noNamePrintFileUrl || '',
      data.printFileUrl3x4 || '',
      data.printFileUrl1x1 || '',
      data.previewUrl3x4 || '',
      data.previewUrl1x1 || '',
    ]);
    var hasLocal = urls.some(function (u) { return u && u.indexOf('/preview/') !== -1; });
    if (!hasLocal || !data.jobId) return;
    var apiBase = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
    fetch(apiBase + '/status/' + encodeURIComponent(data.jobId))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s) return;
        var cdnReady = s.cdn === '1' || s.cdn === true;
        if (!cdnReady) return;
        var absolutize = function (p) { return p && (p.indexOf('http') === 0 ? p : apiBase + p); };
        var cdnPreviews = [s.composited, s.raw_preview || s.raw]
          .filter(Boolean).map(absolutize);
        var upgrade = {
          previewCdnUrls: cdnPreviews,
          printFileUrl: absolutize(s.composited_png_cdn) || data.printFileUrl,
          noNamePrintFileUrl: absolutize(s.raw) || data.noNamePrintFileUrl,
          // Per-aspect no-name URLs — same upgrade pattern. The job
          // record gets these populated by both the initial worker
          // commit and the CDN backfill, so /status returns whichever
          // is most recent.
          printFileUrl3x4: absolutize(s.composited_png_3x4_cdn) || data.printFileUrl3x4,
          printFileUrl1x1: absolutize(s.composited_png_1x1_cdn) || data.printFileUrl1x1,
          previewUrl3x4: absolutize(s.composited_3x4_preview) || data.previewUrl3x4,
          previewUrl1x1: absolutize(s.composited_1x1_preview) || data.previewUrl1x1,
          originalPhotoUrl: s.original_cdn || data.originalPhotoUrl,
        };
        Object.assign(data, upgrade);
        try {
          var session = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
          Object.assign(session, upgrade);
          localStorage.setItem(LS_KEY, JSON.stringify(session));
        } catch (_) { /* ignore */ }
      })
      .catch(function () { /* ignore — cart guard will warn if local URL leaks */ });
  })();

  // Accept either base64 data URLs or CDN URLs — whichever is available
  var previewUrls = (data.previewDataUrls && data.previewDataUrls.length)
    ? data.previewDataUrls
    : (data.previewCdnUrls && data.previewCdnUrls.length)
      ? data.previewCdnUrls
      : [];

  // Last resort: construct URL from imageFilename
  if (!previewUrls.length && data.imageFilename) {
    var apiBase = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
    previewUrls = [apiBase + '/preview/' + data.imageFilename];
  }

  if (!previewUrls.length) return;

  // Check expiry (7 days)
  var age = Date.now() - new Date(data.generatedAt).getTime();
  if (age > 24 * 60 * 60 * 1000) { try { localStorage.removeItem(LS_KEY); } catch (e) {} return; }

  // Default preview (no-name). If user opted in to name on step 4 and we
  // already generated the named preview, use that instead.
  var previewUrl = (data.wantsName !== false && data.namedPreviewUrl)
    ? data.namedPreviewUrl
    : (previewUrls[data.selectedPreviewIndex || 0] || previewUrls[0]);
  var petName = data.petName || '';
  var styleId = data.styleId || 'soft-watercolour';
  var fontSize = data.fontSize || 'medium';
  // Selections from step 4 (size, name, frame, background)
  var selectedSize = data.selectedSize || null;       // e.g. '10x10'
  var wantsName = data.wantsName !== false;           // default true
  var wantsFrame = data.wantsFrame === true;          // default false
  var namedPreviewUrl = data.namedPreviewUrl || null; // with-name preview from step 4
  var backgroundMode = data.backgroundMode || 'auto'; // 'auto' | 'light' | 'dark'

  // Sanitize pet name client-side — mirrors backend allowlist in app.py
  // (letters, numbers, spaces, hyphens, apostrophes, periods; 1–20 chars).
  function sanitizePetName(raw) {
    return String(raw || '')
      .replace(/[^A-Za-z0-9\s\-'\u2019.]/g, '')
      .slice(0, 20)
      .trim();
  }

  // ── Style → font mapping (must match portrait-flow.js) ──
  var STYLE_FONTS = {
    'soft-watercolour':     "'Dancing Script', cursive",
    'minimal-line-art':     "'Caveat', cursive",
    'modern-shape-art':     "'Space Grotesk', sans-serif",
    'neon-pop-art':         "'Bungee', sans-serif",
    'renaissance-royalty':  "'Cinzel', serif",
    'bold-graphic-poster':  "'Oswald', sans-serif",
    'aura-gradient':        "'Quicksand', sans-serif",
  };
  var FONT_SCALES = { small: 0.7, medium: 1.0, large: 1.35 };
  var nameFontCss = STYLE_FONTS[styleId] || "'Cormorant Garamond', serif";
  var nameFontScale = FONT_SCALES[fontSize] || 1.0;

  // Load Google Font for the style
  var GOOGLE_FONTS = {
    'soft-watercolour':     'Dancing+Script:wght@700',
    'minimal-line-art':     'Caveat:wght@400;700',
    'modern-shape-art':     'Space+Grotesk:wght@400;500;700',
    'neon-pop-art':         'Bungee',
    'renaissance-royalty':  'Cinzel:wght@700',
    'bold-graphic-poster':  'Oswald:wght@700',
    'aura-gradient':        'Quicksand:wght@500;700',
  };
  if (GOOGLE_FONTS[styleId]) {
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://fonts.googleapis.com/css2?family=' + GOOGLE_FONTS[styleId] + '&display=swap';
    document.head.appendChild(link);
  }

  // ── Detect product type from URL ────────────────────────
  var pathParts = window.location.pathname.split('/');
  var productHandle = pathParts[pathParts.indexOf('products') + 1] || '';

  // session.mockups was used to cache Printful mockup-task URLs across
  // PDP loads. The PDP no longer calls /mockups — every variant tile is
  // rendered client-side from the un-watermarked print PNG — so any
  // surviving cached entries are dead weight. Drop them on first load
  // so old localStorage doesn't keep stale Printful URLs around.
  if (data.mockups) {
    try {
      var sess = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
      if (sess.mockups) {
        delete sess.mockups;
        localStorage.setItem(LS_KEY, JSON.stringify(sess));
      }
    } catch (_) {}
    delete data.mockups;
  }

  // ── Canvas variant sizes (inches) — matches Shopify SKUs ──
  var VARIANT_SIZES = {
    'canvas': {
      '12x12': { w: 12, h: 12 },
      '12x16': { w: 12, h: 16 },
      '16x16': { w: 16, h: 16 },
      '16x20': { w: 16, h: 20 },
    },
    'framed-canvas': {
      // 8x10 + 18x24 framed retired 2026-04-22 (no live Shopify variants).
      '12x12': { w: 12, h: 12 },
      '12x16': { w: 12, h: 16 },
      '16x16': { w: 16, h: 16 },
      '16x20': { w: 16, h: 20 },
    },
    'poster': {
      '12x18': { w: 12, h: 18 },
    },
  };

  // Detect framed product from the URL handle
  var isFramedProduct = productHandle === 'framed-canvas';

  // Resolve asset base URL for the linen texture
  var _assetBase = '';
  var _scriptTag = document.querySelector('script[src*="pdp-portrait-inject"]');
  if (_scriptTag) _assetBase = _scriptTag.src.replace(/pdp-portrait-inject[^/]*$/, '');

  // ── Tap-to-zoom lightbox for the gallery hero ──────
  // The product gallery slide is locked to a 1:1 aspect ratio (see
  // base.css) so the flex track doesn't jump between mismatched
  // image sizes. That means any 4:5 source rendered into the slide
  // gets cover-cropped by 12.5% on top and bottom — fine for the
  // 1:1 derivative but problematic for legacy URLs without a 1:1
  // file. The lightbox lets the customer see the full uncropped
  // composition even when the slide is forced to crop.
  function openPortraitZoom(src, petName) {
    if (!src) return;
    var existing = document.getElementById('pp-portrait-zoom');
    if (existing) existing.remove();
    var overlay = document.createElement('div');
    overlay.id = 'pp-portrait-zoom';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', petName ? 'Full preview of ' + petName : 'Full portrait preview');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:9999;background:rgba(20,18,16,0.92);'
      + 'display:flex;align-items:center;justify-content:center;padding:6vw;'
      + 'cursor:zoom-out;';
    var fullImg = document.createElement('img');
    fullImg.src = src;
    fullImg.alt = petName ? 'Full portrait of ' + petName : 'Full portrait preview';
    fullImg.style.cssText = 'max-width:100%;max-height:100%;width:auto;height:auto;'
      + 'display:block;border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,0.6);'
      + 'object-fit:contain;background:#fefdfb;';
    overlay.appendChild(fullImg);
    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close preview');
    closeBtn.textContent = '×';
    closeBtn.style.cssText = 'position:absolute;top:16px;right:16px;width:44px;height:44px;'
      + 'border-radius:50%;border:none;background:rgba(255,255,255,0.95);color:#1c1c1c;'
      + 'font-size:28px;line-height:1;cursor:pointer;display:flex;align-items:center;'
      + 'justify-content:center;font-family:sans-serif;font-weight:300;';
    overlay.appendChild(closeBtn);

    function close() {
      overlay.remove();
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    }
    function onKey(e) { if (e.key === 'Escape') close(); }
    overlay.addEventListener('click', close);
    closeBtn.addEventListener('click', function (e) { e.stopPropagation(); close(); });
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    document.body.appendChild(overlay);
  }

  // ── Client-side product mockup — CSS-composed canvas ─────
  // Strategy: linen surface + canvas product (sized per-variant) +
  // canvas weave texture, mirroring Printful's pre-photographed scenes
  // so all mockups read as the same product family. The canvas-face has
  // no fill colour; the portrait covers it edge to edge. That way any
  // style's own background is what sells the canvas face — never a flat
  // white rectangle that the portrait sits on top of.
  //
  // opts:
  //   srcMatchesFace  — source aspect already matches the canvas face
  //                     (per-aspect un-watermarked PNG). Skips the
  //                     crop/scale math entirely and renders the image
  //                     flush. The customer sees pixel-for-pixel the
  //                     front face that Printful prints.
  //   applyCssWatermark — overlay the diagonal Pet Printables watermark
  //                       in CSS rather than relying on a baked-in
  //                       watermark in the source. Use this when the
  //                       source is the un-watermarked print PNG.
  function createClientMockup(portraitSrc, widthIn, heightIn, label, srcIs1x1, styleId, opts) {
    opts = opts || {};
    var srcMatchesFace = !!opts.srcMatchesFace;
    var applyCssWatermark = !!opts.applyCssWatermark;
    // Outer container: square 1:1 slide. Linen backdrop matches the
    // pre-photographed Printful product mockup used for tall variants
    // so the square mockups read as the same product family. The
    // canvas-face fill (formerly #fefdfb) stays transparent — the
    // portrait covers it edge to edge so any style's own background
    // sells the canvas face. No flat white rectangle on linen.
    var container = document.createElement('div');
    container.style.cssText = 'width:100%;aspect-ratio:1/1;border-radius:16px;'
      + 'overflow:hidden;position:relative;'
      + "background-image:url(" + _assetBase + "linen-texture.webp);"
      + 'background-size:cover;background-position:center;'
      + 'display:flex;align-items:center;justify-content:center;';

    // Subtle directional light from upper-left (matches Printful style)
    var lightGradient = document.createElement('div');
    lightGradient.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
      + 'background:radial-gradient(ellipse at 30% 20%, rgba(255,250,240,0.18) 0%, transparent 60%);';
    container.appendChild(lightGradient);

    // Canvas wrapper sized proportionally to variant dimensions.
    // Larger sizes fill more of the container so the visual scale matches reality.
    var MAX_CANVAS_DIM = 20; // largest available canvas dimension (inches)
    var MAX_PCT = 90;        // container % for the largest canvas (16×20)
    var MIN_PCT = 74;        // container % for the smallest canvas (12×12)
    var maxDim = Math.max(widthIn, heightIn);
    var scaledPct = MIN_PCT + ((maxDim - 12) / (MAX_CANVAS_DIM - 12)) * (MAX_PCT - MIN_PCT);

    var productAspect = heightIn / widthIn;
    var canvasStyleW, canvasStyleH;
    if (productAspect >= 1) {
      // Portrait/square: constrain by height
      canvasStyleH = scaledPct;
      canvasStyleW = canvasStyleH / productAspect;
    } else {
      // Landscape (unused currently): constrain by width
      canvasStyleW = scaledPct;
      canvasStyleH = canvasStyleW * productAspect;
    }

    // The canvas wrap holds either the unframed portrait or the framed
    // wood frame. A drop-shadow filter on the wrap puts the shadow
    // around the actual visible artwork silhouette (not around an
    // invisible bounding box), so removing the white canvas-face fill
    // doesn't lose the "product on a wall" depth cue.
    var canvasWrap = document.createElement('div');
    canvasWrap.style.cssText = 'position:relative;'
      + 'width:' + canvasStyleW + '%;height:' + canvasStyleH + '%;'
      + 'max-width:' + scaledPct + '%;max-height:' + scaledPct + '%;'
      + 'border-radius:2px;overflow:visible;'
      + (isFramedProduct
        // Centred Y-only shadow. Earlier versions used a 6px right X
        // offset, which read as "the right side of the frame is
        // thicker" on the small square (12x12) wrap where the
        // asymmetric shadow took a larger share of the slide width.
        ? 'filter:drop-shadow(0 10px 18px rgba(40,28,18,0.22)) drop-shadow(0 2px 4px rgba(40,28,18,0.18));'
        : 'filter:drop-shadow(0 8px 16px rgba(60,45,30,0.18)) drop-shadow(0 1px 3px rgba(60,45,30,0.14));');
    container.appendChild(canvasWrap);

    // If framed: wood frame border wrapping the canvas face
    // If unframed: just the canvas face
    var canvasFace;
    if (isFramedProduct) {
      // Solid black frame. Frame colour is fixed to black for every
      // framed-canvas variant — matches what's printed on the
      // materials section of the PDP and what the customer actually
      // receives. A single solid colour reads cleanly behind any
      // portrait without the concentric-stripe artefacts the earlier
      // gradient stack produced.
      var frame = document.createElement('div');
      frame.style.cssText = 'position:absolute;inset:0;padding:6%;box-sizing:border-box;'
        + 'background:#0E0E0E;border-radius:1px;'
        + 'box-shadow:'
        +   'inset 0 1px 0 rgba(255,255,255,0.06),'
        +   'inset 0 -1px 0 rgba(0,0,0,0.40);';
      canvasWrap.appendChild(frame);

      // Recess where the printed canvas sits. No fill colour — the
      // portrait covers it entirely so any style's own background sells
      // the canvas face. A subtle inset shadow at the top sells the
      // recess depth without competing with the portrait's own edges.
      canvasFace = document.createElement('div');
      canvasFace.style.cssText = 'position:absolute;inset:6%;overflow:hidden;'
        + 'background:transparent;'
        + 'box-shadow:inset 0 2px 5px rgba(0,0,0,0.28);';
      canvasWrap.appendChild(canvasFace);
    } else {
      // Unframed: portrait IS the canvas face. No fill colour at all so
      // we never expose a flat white rectangle for styles whose own
      // background isn't pure cream.
      canvasFace = document.createElement('div');
      canvasFace.style.cssText = 'position:absolute;inset:0;overflow:hidden;'
        + 'background:transparent;'
        + 'border-radius:1px;';
      canvasWrap.appendChild(canvasFace);
    }

    // Portrait image (the user's pet).
    //
    // The source carries a ~10% padding ring (added by add_background_padding
    // server-side); after the 4:5 crop in POST_PROCESS that mostly survives
    // on top/bottom and is stripped from the sides. The name is composited
    // directly onto the artwork at ~y=11% (no separate band — see the
    // composite_name change in generate.py). We crop just enough to remove
    // the visible padding ring without slicing into the name or the pet.
    //
    // Crop region (in source-space fractions):
    //   y: 0.05 → 0.90  (leave a small top breather above the name; remove
    //                    the bottom padding entirely)
    //   x: 0.05 → 0.95  (sides are mostly clean already after the 4:5 crop)
    // Per-aspect crop tuning so the watercolour fills the canvas convincingly:
    //
    //   Square (12×12, 16×16): a 4:5 source on a 1:1 face is taller-relative,
    //     so object-fit:cover already strips ~20% off the bottom. Adding any
    //     extra top/bottom crop ON TOP of that clips into the name and pet.
    //     We zero the top/bot crop and just trim the small side padding —
    //     anchored top-aligned so the name survives the cover crop.
    //
    //   Tall (12×16, 16×20): don't crop the sides. Aggressive side cropping
    //     was previously used to make the watercolour wash bleed off the
    //     canvas edges, but it assumes the source's wash is perfectly
    //     centred. Real generated portraits aren't always symmetric, and
    //     a centre-cropped asymmetric source reads as the portrait being
    //     pushed to one side with white space on the other. Showing the
    //     full source width keeps any natural margin symmetric inside
    //     the canvas face — closer to what the printed product looks like.
    var isSquare = (widthIn === heightIn);
    // Universal flush-bottom rule (server-side: add_background_padding
    // uses pad_bottom_ratio=0 for every style): the 4:5 master has the
    // pet's body bottom at source y=100%. On a non-square non-1x1 face
    // we render the source aspect=face aspect (pre-scaling the image
    // element wider/taller than the canvas face) so the pet's bottom
    // maps to canvas-face y=100% with NO vertical cropping. Sides may
    // overflow and are clipped by the cropWindow.
    //
    // 1:1 derivatives are already composed for the square face (pet,
    // chest cut and name band in final positions), so they're rendered
    // as-is.
    var isFlushBottomMaster = !srcIs1x1 && !isSquare && !srcMatchesFace;
    var cropTopFrac, cropBotFrac, cropSideFrac;
    var coverPosition = 'center top';
    var hScale, vScale, leftPct, topPct;
    var hScaleOverride, vScaleOverride, leftPctOverride, topPctOverride;
    var sampleNeonBg = false;
    if (srcMatchesFace) {
      if (isSquare) {
        // Square face: render flush — no overflow needed.
        hScale = 100;
        vScale = 100;
        leftPct = 0;
        topPct = 0;
      } else {
        // Portrait face: scale up 15% and centre so the pet fills the canvas
        // face rather than showing the empty name-safe band at the top.
        hScale = 115;
        vScale = 115;
        leftPct = -7.5;
        topPct = -7.5;
      }
      coverPosition = 'center center';
    } else if (isFlushBottomMaster) {
      var srcAspect = 4 / 5;                            // PORTRAIT_RATIO
      var faceAspect = widthIn / heightIn;              // e.g. 0.75 for 12×16
      // Scale 15% larger than flush-fill to allow centred vertical crop;
      // this removes most of the empty name-safe-band from the top of the canvas.
      var overscale = 1.15;
      hScale = 100 * (srcAspect / faceAspect) * overscale;
      vScale = 100 * overscale;
      topPct = -(vScale - 100) / 2;                     // centre vertically
      leftPct = (100 - hScale) / 2;                     // centre horizontally
    } else {
      if (srcIs1x1) {
        cropTopFrac = 0;
        cropBotFrac = 0;
        cropSideFrac = 0;
      } else if (styleId === 'neon-pop-art') {
        // Neon Pop Art on a square face from the 4:5 master.
        // Scale source to fill 100% of face width (hScale=100). Since
        // source is 4:5 the element must be 125% tall to keep the aspect
        // (100 / 0.8 = 125). Bottom-anchor so the pet's feet land at
        // canvas-face y=100%: topPct = 100 - 125 = -25. The top 25% of
        // the source (name band + upper bg) is clipped by cropWindow;
        // for the named case the 1:1 derivative (srcIs1x1 path) is used
        // instead so this only runs for the no-name fallback.
        cropTopFrac = 0;
        cropBotFrac = 0;
        cropSideFrac = 0;
        hScaleOverride = 100;
        vScaleOverride = 125;
        leftPctOverride = 0;
        topPctOverride = -25;
      } else {
        // Other styles on a square face from the 4:5 master (watercolor
        // / charcoal etc.): cover-crop with bottom anchoring so the
        // pet's chest survives. The empty name-safe-zone paper at the
        // top is what gets trimmed instead. Side / bottom extension via
        // canvas-face bg colour is harder for these styles because the
        // bg has texture (paper grain, wash) — sampling a single hex
        // would look obviously fake.
        cropTopFrac = 0;
        cropBotFrac = 0;
        cropSideFrac = 0;
        // BUT — if the customer has Show Name = Yes, the top of the 4:5
        // master has the composited name. Bottom-anchoring would clip it
        // off and the customer would see a no-name canvas mockup despite
        // the toggle being Yes. Anchor TOP instead so the name survives;
        // the pet's lower chest gets a slight clip but the customer-
        // visible name is preserved. The 1:1 derivative path (srcIs1x1)
        // is the proper fix and renders cleanly when available — this is
        // only a fallback for the brief window after toggling Yes before
        // /add-name's 1:1 derivative lands in session.
        coverPosition = showName ? 'center top' : 'center bottom';
      }
      hScale = 100 / (1 - 2 * cropSideFrac);
      vScale = 100 / (1 - cropTopFrac - cropBotFrac);
      leftPct = -cropSideFrac * hScale;
      topPct = -cropTopFrac * vScale;
      if (hScaleOverride !== undefined) hScale = hScaleOverride;
      if (vScaleOverride !== undefined) vScale = vScaleOverride;
      if (leftPctOverride !== undefined) leftPct = leftPctOverride;
      if (topPctOverride !== undefined) topPct = topPctOverride;
    }

    var cropWindow = document.createElement('div');
    cropWindow.style.cssText = 'position:absolute;inset:0;overflow:hidden;';
    canvasFace.appendChild(cropWindow);

    var portraitImg = document.createElement('img');
    if (sampleNeonBg) portraitImg.crossOrigin = 'anonymous';
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + ' canvas';
    portraitImg.loading = 'lazy';
    portraitImg.style.cssText = 'position:absolute;'
      + 'left:' + leftPct + '%;top:' + topPct + '%;'
      + 'width:' + hScale + '%;height:' + vScale + '%;'
      + 'object-fit:cover;object-position:' + coverPosition + ';display:block;';
    cropWindow.appendChild(portraitImg);

    // Neon Pop Art square: sample the source's top-left corner once
    // the image loads and paint the canvas face that exact saturated
    // hex. The pet sits in the middle 80% × 88% of the face; the side
    // and bottom margins outside that area read as one continuous
    // saturated bg. CORS-safe: requires the CDN to send Access-Control-
    // Allow-Origin (Cloudinary / our preview endpoint already do); if
    // it doesn't, the catch leaves canvas-face transparent and we get
    // the original linen-margin fallback.
    if (sampleNeonBg) {
      var paintFromCorner = function () {
        try {
          var c = document.createElement('canvas');
          c.width = 4; c.height = 4;
          var cx = c.getContext('2d');
          cx.drawImage(portraitImg, 0, 0, 4, 4, 0, 0, 4, 4);
          var d = cx.getImageData(0, 0, 4, 4).data;
          var r = 0, g = 0, b = 0;
          for (var i = 0; i < d.length; i += 4) { r += d[i]; g += d[i+1]; b += d[i+2]; }
          var n = d.length / 4;
          canvasFace.style.background = 'rgb(' + Math.round(r/n) + ',' + Math.round(g/n) + ',' + Math.round(b/n) + ')';
        } catch (e) {
          // CORS / decode error — leave transparent, accept the linen-margin fallback.
        }
      };
      if (portraitImg.complete && portraitImg.naturalWidth > 0) paintFromCorner();
      else portraitImg.addEventListener('load', paintFromCorner, { once: true });
    }

    // Canvas weave texture overlay (SVG noise, multiply blend)
    var weave = document.createElement('div');
    weave.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
      + 'mix-blend-mode:multiply;opacity:0.12;'
      + "background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='w'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0.6 0 0 0 0 0.55 0 0 0 0 0.5 0 0 0 1 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23w)'/%3E%3C/svg%3E\");";
    canvasFace.appendChild(weave);

    // Tiled CSS watermark overlay — applied when the source is the
    // un-watermarked print PNG (i.e. the same file Printful prints from).
    // Mirrors apply_preview_watermark in generate.py: ~28% logo width,
    // -30° rotation, 1.5× spacing. Opacity intentionally floor-set at
    // 1% so the mockup reads as the finished piece, not as a preview
    // sample — visible only on close inspection. The un-watermarked
    // print file is still gated server-side; this overlay is the
    // last-mile reminder, not the IP perimeter.
    if (applyCssWatermark) {
      var wm = document.createElement('div');
      wm.style.cssText = 'position:absolute;'
        // Oversized so the rotated tile fills the canvas face after
        // -30° rotation without revealing un-tiled corners.
        + 'top:-30%;left:-30%;width:160%;height:160%;'
        + 'pointer-events:none;'
        + 'background-image:url(' + _assetBase + 'watermark-logo.png);'
        // 28% × 1.5 = 42% — controls effective spacing between stamps;
        // background-size sets the logo width relative to the wm box.
        + 'background-repeat:repeat;'
        + 'background-size:28% auto;'
        + 'opacity:0.01;'
        + 'mix-blend-mode:multiply;'
        + 'transform:rotate(-30deg);'
        + 'transform-origin:center center;';
      canvasFace.appendChild(wm);
    }

    // Canvas edge highlight — only on the unframed canvas (the wood
    // frame already provides clear edge separation, and the 4-sided
    // 1px highlight reads as a "second inner frame" against dark
    // portraits like a black French Bulldog on a dark wood frame).
    if (!isFramedProduct) {
      var edgeHighlight = document.createElement('div');
      edgeHighlight.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
        + 'box-shadow:inset 0 1px 0 rgba(255,255,255,0.6),'
        +           'inset 0 -1px 0 rgba(0,0,0,0.06),'
        +           'inset 1px 0 0 rgba(255,255,255,0.3),'
        +           'inset -1px 0 0 rgba(0,0,0,0.04);';
      canvasFace.appendChild(edgeHighlight);
    }

    // Size label — glass morphism pill with W × H × D dimensions
    var sizeLabel = document.createElement('div');
    sizeLabel.style.cssText = 'position:absolute;bottom:12px;right:14px;'
      + 'display:flex;align-items:center;gap:8px;'
      + "font-family:'Inter',sans-serif;font-size:var(--text-xs);font-weight:500;letter-spacing:0.04em;"
      + 'color:#3a3530;background:rgba(255,255,255,0.55);padding:6px 12px;border-radius:999px;'
      + 'border:1px solid rgba(255,255,255,0.6);'
      + 'box-shadow:0 2px 8px rgba(0,0,0,0.08),inset 0 1px 0 rgba(255,255,255,0.6);'
      + 'backdrop-filter:blur(10px) saturate(120%);-webkit-backdrop-filter:blur(10px) saturate(120%);z-index:2;';

    // W × H
    var dimMain = document.createElement('span');
    dimMain.innerHTML = '<strong style="font-weight:600;">' + widthIn + '\u2033 \u00D7 ' + heightIn + '\u2033</strong>';
    sizeLabel.appendChild(dimMain);

    // Separator dot
    var sep = document.createElement('span');
    sep.style.cssText = 'color:#a09890;font-size:var(--text-xs);';
    sep.textContent = '\u00B7';
    sizeLabel.appendChild(sep);

    // Depth / frame indicator
    var dimDepth = document.createElement('span');
    dimDepth.style.cssText = 'color:#7a7369;';
    dimDepth.textContent = isFramedProduct ? 'Framed' : '1.25\u2033 deep';
    sizeLabel.appendChild(dimDepth);

    container.appendChild(sizeLabel);

    return container;
  }

  // ── Inject portrait + mockup images into gallery ──────
  var gallery = document.querySelector('.product-gallery__track');
  if (gallery) {
    // Portrait as first slide
    var slide = document.createElement('div');
    slide.className = 'product-gallery__slide';
    slide.setAttribute('role', 'listitem');

    var img = document.createElement('img');
    // Use the watermarked 4:5 preview as the hero so the design-only
    // image carries the Pet Printables watermark. The 1:1 derivative is
    // un-watermarked, so we avoid it here. The gallery slide is locked
    // to 1:1 with object-fit:cover (see base.css); top-anchor the cover
    // crop so the name band (composited at source y≈11%) survives.
    var heroSrc = previewUrl;
    img.src = heroSrc;
    img.alt = petName ? 'Portrait of ' + petName : 'Your custom pet portrait';
    img.loading = 'eager';
    img.style.cssText = 'width:100%;height:100%;display:block;border-radius:16px;'
      + 'object-fit:cover;object-position:center top;';
    slide.appendChild(img);

    // Tap-to-zoom on the hero slide — the gallery's 1:1 cover-crop can
    // trim a 4:5 source if no 1:1 derivative is available, and the
    // customer should always have an escape hatch to see the full
    // composition. Listener sits on the slide because base.css disables
    // pointer-events on the img itself.
    slide.style.cursor = 'zoom-in';
    slide.addEventListener('click', function () {
      openPortraitZoom(heroSrc, petName);
    });

    // Remove generic Shopify product images FIRST (they show wrong dog)
    var existingSlides = Array.from(gallery.querySelectorAll('.product-gallery__slide'));
    existingSlides.forEach(function (s) { gallery.removeChild(s); });

    // Insert portrait slide AFTER clearing old ones
    gallery.insertBefore(slide, gallery.firstChild);

    // Determine which sizes to use for this product
    var sizes = VARIANT_SIZES[productHandle] || VARIANT_SIZES['canvas'] || {};

    // Build mockup slides — every variant renders client-side from the
    // un-watermarked print PNG that Printful actually prints (per-aspect
    // PNG matched to the variant's front-face aspect). The CSS overlay
    // watermark sits on top so the customer can't right-click-save the
    // un-watermarked source. Goal: what the customer sees on PDP IS the
    // front face Printful prints — no Printful mockup-template render in
    // between, so no centre-extraction zoom, no 800-px lossy WebP source,
    // no JPG round-trip.
    //
    // pickPrintSrcForFace: pick the un-watermarked PNG whose aspect
    // matches the variant face. When available, returns that URL +
    // srcMatchesFace=true so createClientMockup renders flush. Falls back
    // to the watermarked 4:5 master + the legacy crop math when a
    // per-aspect URL isn't in session yet (e.g. customer landed on PDP
    // before /add-name finished).
    function pickPrintSrcForFace(faceW, faceH) {
      var isSq = faceW === faceH;
      var isThreeByFour = (faceW === 3 && faceH === 4) || (faceW * 4 === faceH * 3);
      var isFourByFive = (faceW === 4 && faceH === 5) || (faceW * 5 === faceH * 4);
      // Per-aspect PNG (un-watermarked) — best, matches what's printed.
      if (isSq && data.printFileUrl1x1) {
        return { url: data.printFileUrl1x1, matches: true, watermark: true };
      }
      if (isThreeByFour && data.printFileUrl3x4) {
        return { url: data.printFileUrl3x4, matches: true, watermark: true };
      }
      if (isFourByFive && data.printFileUrl) {
        return { url: data.printFileUrl, matches: true, watermark: true };
      }
      // Fallback: 4:5 master with the existing crop math. Source is the
      // already-watermarked WebP — no CSS overlay needed.
      var has1x1Wm = !!data.namedPreviewUrl1x1;
      var useSquareSrc = isSq && data.wantsName !== false && has1x1Wm;
      var fallbackUrl = useSquareSrc ? data.namedPreviewUrl1x1 : previewUrl;
      return { url: fallbackUrl, matches: false, watermark: false, srcIs1x1: useSquareSrc };
    }

    var allSizeKeys = Object.keys(sizes);
    allSizeKeys.forEach(function (sizeKey) {
      var dim = sizes[sizeKey];
      var mockupSlide = document.createElement('div');
      mockupSlide.className = 'product-gallery__slide product-gallery__slide--mockup';
      mockupSlide.setAttribute('role', 'listitem');
      mockupSlide.setAttribute('data-variant-size', sizeKey);

      var pick = pickPrintSrcForFace(dim.w, dim.h);
      var clientMockup = createClientMockup(
        pick.url, dim.w, dim.h, sizeKey,
        !!pick.srcIs1x1, styleId,
        { srcMatchesFace: pick.matches, applyCssWatermark: pick.watermark }
      );
      mockupSlide.appendChild(clientMockup);

      gallery.appendChild(mockupSlide);
    });

    // Watermark disclaimer — sits directly under the gallery so the
    // customer is reassured the Pet Printables marks they see in the
    // preview won't appear on the printed canvas they receive.
    var galleryRoot = gallery.closest('.product-gallery') || gallery.parentNode;
    if (galleryRoot && !galleryRoot.querySelector('.pp-watermark-disclaimer')) {
      var disclaimer = document.createElement('p');
      disclaimer.className = 'pp-watermark-disclaimer';
      disclaimer.style.cssText = 'margin:10px auto 0;text-align:center;'
        + "font-family:'Inter',sans-serif;font-size:var(--text-xs);"
        + 'color:var(--color-muted,#7a7369);letter-spacing:0.02em;'
        + 'line-height:1.4;max-width:520px;';
      disclaimer.textContent = 'Watermark will not appear on your final printed canvas.';
      galleryRoot.appendChild(disclaimer);
    }

    // Store mockup variant→slide index map for variant picker
    // Normalize keys to "NxN" format so they match theme.js lookup
    window._mockupSlideMap = {};
    var allSlides = gallery.querySelectorAll('.product-gallery__slide');
    allSlides.forEach(function (s, i) {
      var varSize = s.getAttribute('data-variant-size');
      if (varSize) {
        var nums = varSize.match(/(\d+)\D+(\d+)/);
        var key = nums ? nums[1] + 'x' + nums[2] : varSize;
        window._mockupSlideMap[key] = i;
      }
    });
  }

  // ── Inject pet name banner above product title ──────────
  if (petName) {
    var title = document.querySelector('.product-info__title');
    if (title) {
      var banner = document.createElement('p');
      banner.style.cssText = "font-family:'Cormorant Garamond',serif;font-style:italic;font-size:var(--text-xl);color:#1C1C1C;margin:0 0 4px;letter-spacing:0.02em;";
      banner.textContent = petName + '\u2019s portrait';
      title.parentNode.insertBefore(banner, title);
    }
  }

  // ── Hide upload widget & pet name input (already completed) ──
  var uploadParent = document.querySelector('.upload-widget');
  if (uploadParent && uploadParent.parentElement) uploadParent.parentElement.style.display = 'none';

  var petNameInput = document.getElementById('PetName');
  if (petNameInput) {
    petNameInput.value = petName;
    petNameInput.disabled = true;
    petNameInput.removeAttribute('name');
    var nameParent = petNameInput.closest('.variant-picker');
    if (nameParent) nameParent.style.display = 'none';
  }

  // ── Inject confirmed portrait strip ───────────────────────
  var styleName = (data.styleId || 'soft-watercolour').replace(/-/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  var strip = document.createElement('div');
  strip.style.cssText = 'display:flex;align-items:center;gap:14px;padding:14px 16px;border:1.5px solid var(--color-border, #e5e0db);border-radius:12px;margin-bottom:16px;background:var(--color-surface, #faf9f7);';

  var thumb = document.createElement('img');
  thumb.src = previewUrl;
  thumb.alt = petName ? petName + ' portrait thumbnail' : 'Portrait thumbnail';
  thumb.style.cssText = 'width:56px;height:56px;object-fit:cover;border-radius:10px;border:1px solid var(--color-border, #e5e0db);flex-shrink:0;';
  strip.appendChild(thumb);

  var info = document.createElement('div');
  info.style.cssText = 'flex:1;min-width:0;';
  var nameLabel = document.createElement('p');
  nameLabel.style.cssText = "margin:0;font-family:'Cormorant Garamond',serif;font-style:italic;font-size:var(--text-lg);color:var(--color-ink, #1C1C1C);line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
  nameLabel.textContent = petName ? petName + '\u2019s Portrait' : 'Your Portrait';
  info.appendChild(nameLabel);
  var styleLabel = document.createElement('p');
  styleLabel.style.cssText = 'margin:2px 0 0;font-size:var(--text-sm);color:var(--color-muted, #8a8580);';
  styleLabel.textContent = styleName;
  info.appendChild(styleLabel);

  strip.appendChild(info);

  var changeLink = document.createElement('a');
  changeLink.href = '#';
  changeLink.textContent = 'Change';
  changeLink.style.cssText = 'font-size:var(--text-sm);color:var(--color-muted, #8a8580);text-decoration:underline;text-underline-offset:2px;white-space:nowrap;flex-shrink:0;';
  changeLink.addEventListener('click', function (e) {
    e.preventDefault();
    try { localStorage.removeItem(LS_KEY); } catch (err) {}
    window.location.reload();
  });
  strip.appendChild(changeLink);

  // ── Build urgency countdown banner ─────────────────────
  function buildUrgencyBanner() {
    var URGENCY_MS = 10 * 60 * 1000;
    var generatedAt = new Date(data.generatedAt).getTime();

    var banner = document.createElement('div');
    banner.setAttribute('role', 'alert');
    banner.style.cssText = 'max-width:100%;margin-bottom:16px;border-radius:12px;padding:14px 18px;'
      + 'text-align:center;transition:all 0.3s ease;';

    var label = document.createElement('p');
    label.style.cssText = "font-family:'Inter',sans-serif;font-size:var(--text-xs);font-weight:700;"
      + 'margin:0 0 8px;letter-spacing:0.10em;text-transform:uppercase;';

    var clock = document.createElement('div');
    clock.style.cssText = "font-family:'Inter',sans-serif;font-weight:700;font-size:var(--text-2xl);"
      + 'line-height:1;margin-bottom:6px;font-variant-numeric:tabular-nums;letter-spacing:0.02em;';

    var msg = document.createElement('p');
    msg.style.cssText = "font-family:'Inter',sans-serif;font-size:var(--text-xs);color:#1C1C1C;"
      + 'margin:0;line-height:1.4;font-weight:500;';

    banner.appendChild(label);
    banner.appendChild(clock);
    banner.appendChild(msg);

    function pad(n) { return String(n).padStart(2, '0'); }

    function tick() {
      var remaining = URGENCY_MS - (Date.now() - generatedAt);
      if (remaining <= 0) {
        banner.style.background = '#FEE2E2';
        banner.style.border = '1.5px solid #DC2626';
        banner.style.boxShadow = 'none';
        banner.style.animation = 'none';
        label.style.color = '#991B1B';
        label.textContent = '\u23F0 Your session has expired';
        clock.style.display = 'none';
        msg.style.color = '#7F1D1D';
        msg.innerHTML = 'Your portrait is still saved for 24 hours \u2014 please start a new session to order.';
        clearInterval(interval);
        return;
      }

      var mins = Math.floor(remaining / 60000);
      var secs = Math.floor((remaining % 60000) / 1000);
      var isUrgent = remaining < 3 * 60 * 1000;

      if (isUrgent) {
        banner.style.background = '#FEE2E2';
        banner.style.border = '1.5px solid #DC2626';
        banner.style.boxShadow = '0 0 0 3px rgba(220,38,38,0.15), 0 4px 12px rgba(220,38,38,0.10)';
        banner.style.animation = 'pf-urgency-pulse 1.2s ease-in-out infinite';
        label.style.color = '#991B1B';
        label.textContent = '\uD83D\uDEA8 HURRY \u2014 EXPIRES VERY SOON';
        clock.style.color = '#991B1B';
      } else {
        banner.style.background = '#FEF3E6';
        banner.style.border = '1.5px solid #D97706';
        banner.style.boxShadow = '0 0 0 3px rgba(217,119,6,0.12)';
        banner.style.animation = 'pf-urgency-pulse 2.5s ease-in-out infinite';
        label.style.color = '#B45309';
        label.textContent = '\u23F1\uFE0F YOUR SESSION EXPIRES IN';
        clock.style.color = '#1C1C1C';
      }

      clock.textContent = pad(mins) + ':' + pad(secs);
      msg.innerHTML = 'This exact portrait is <strong style="font-weight:700;">one-of-a-kind</strong>'
        + ' and can <strong style="font-weight:700;">never be recreated</strong>.';
    }

    tick();
    var interval = setInterval(tick, 1000);
    return banner;
  }

  // Inject pulse keyframe once
  if (!document.getElementById('pdp-urgency-keyframes')) {
    var kf = document.createElement('style');
    kf.id = 'pdp-urgency-keyframes';
    kf.textContent = '@keyframes pf-urgency-pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.015); } }';
    document.head.appendChild(kf);
  }

  // Insert urgency banner + portrait strip directly above the Add-to-Cart
  // submit button (previously anchored to the gift-message input, which has
  // been removed). Falls back to the hidden variant input if needed.
  var atcBtn = document.querySelector('button[name="add"], form[action*="/cart/add"] [type="submit"]');
  var insertTarget = atcBtn
    || document.querySelector('input[name="id"][type="hidden"]');
  if (insertTarget && insertTarget.parentNode) {
    // Countdown timer hidden for now. Uncomment to re-enable:
    // var urgencyBanner = buildUrgencyBanner();
    // insertTarget.parentNode.insertBefore(urgencyBanner, insertTarget);
    insertTarget.parentNode.insertBefore(strip, insertTarget);
  }

  // Print-on-demand override: the ATC button's `disabled` + "Sold Out"
  // label come from Liquid's {% unless product.available %} check. For
  // a POD product fulfilled by Printful that's never truly out of stock,
  // a stale Shopify availability flag (location-fulfillment misconfig
  // or Printful sync lag) shouldn't block a customer who has just spent
  // 2-3 minutes generating a bespoke portrait. Force-enable + relabel.
  if (atcBtn && (atcBtn.disabled || /sold\s*out/i.test(atcBtn.textContent || ''))) {
    atcBtn.disabled = false;
    atcBtn.removeAttribute('disabled');
    if (/sold\s*out/i.test(atcBtn.textContent || '')) {
      atcBtn.textContent = 'Add to Cart';
    }
  }

  // ── "With name / Without name" toggle ─────────────────────
  // withTextUrl is generated on-demand by /add-name when the user clicks Yes.
  // If Step 4 already produced a named preview (data.namedPreviewUrl), we
  // use it directly so the toggle swap is instant.
  var noTextUrl   = previewUrls[0] || previewUrls[1] || previewUrl;
  // CRITICAL: withTextUrl must ONLY be the actual NAMED URL or null.
  // The previous fallback to previewUrls[1] was a no-name watermarked
  // WebP — toggling Yes would treat that no-name URL as the named one
  // and commit it to the cart with _Show Name=Yes. Result: customer
  // sees Yes selected but cart line item points at the no-name file
  // (and Printful prints the no-name version). No spinner fired
  // because the toggle handler thought it already had a named URL.
  var withTextUrl = data.namedPreviewUrl || null;
  // Some styles ship without a name on purpose (saturated neon, moody
  // renaissance, soft aura) — type would break the aesthetic. Force
  // showName off and skip the toggle UI entirely for those.
  var NAMELESS_STYLES = { 'neon-pop-art': 1, 'renaissance-royalty': 1, 'aura-gradient': 1 };
  var styleAllowsName = !NAMELESS_STYLES[styleId];
  // Default the toggle from what the customer picked on Step 4 ("Include name").
  // If they opted in, we land on "Yes" — even if the named preview URL hasn't
  // been persisted yet; we'll fetch it proactively below.
  var showName = wantsName && styleAllowsName;

  if (petName && !styleAllowsName) {
    // Render a brand-voiced explainer in place of the toggle.
    var namelessNote = document.createElement('div');
    namelessNote.style.cssText = 'margin-bottom:16px;padding:14px 16px;'
      + 'border:1.5px solid var(--color-border, #e5e0db);border-radius:12px;'
      + 'background:var(--color-accent-light, #f7f1e8);';
    var copy = styleId === 'neon-pop-art'
      ? "This one runs hot — saturated, electric, edge-to-edge. Type would dim the glow, so we keep this style nameless and let the colour do the talking."
      : styleId === 'renaissance-royalty'
        ? "Old-master portraits never wore a label. We honour the tradition — your pet stands alone in the gallery, the way the masters intended."
        : "Aura portraits live in their soft halo of colour. Adding type would break the spell, so this style ships without a name — pure mood.";
    namelessNote.innerHTML =
      '<p style="font-family:\'Inter\',sans-serif;font-size:var(--text-xs);font-weight:600;letter-spacing:0.06em;text-transform:uppercase;color:var(--color-muted,#8a8580);margin:0 0 6px;">A nameless piece, on purpose</p>'
      + '<p style="font-family:\'Inter\',sans-serif;font-size:var(--text-sm);color:var(--color-ink,#1C1C1C);margin:0;line-height:1.5;">' + copy + '</p>';
    var insertHost = document.querySelector('.product-form, form[action*="/cart/add"]');
    if (insertHost && insertHost.parentNode) insertHost.parentNode.insertBefore(namelessNote, insertHost);
    // Force _Show Name=No in the cart form so the order ships nameless.
    var form = document.querySelector('.product-form, form[action*="/cart/add"]');
    if (form) {
      var sn = form.querySelector('input[name="properties[_Show Name]"]');
      if (sn) sn.value = 'No';
    }
  }

  if (petName && styleAllowsName) {
    // Outer wrapper is a column so the toggle row and the inline name
    // editor stack cleanly. The toggle row itself is its own flex-row
    // container so the label/buttons stay side-by-side regardless of how
    // many other rows the wrapper holds.
    var toggleWrap = document.createElement('div');
    toggleWrap.style.cssText = 'display:flex;flex-direction:column;gap:10px;margin-bottom:16px;padding:12px 16px;'
      + 'border:1.5px solid var(--color-border, #e5e0db);border-radius:12px;background:var(--color-surface, #faf9f7);';

    var toggleRow = document.createElement('div');
    toggleRow.style.cssText = 'display:flex;align-items:center;gap:10px;';
    toggleWrap.appendChild(toggleRow);

    var toggleLabel = document.createElement('span');
    toggleLabel.style.cssText = "font-family:'Inter',sans-serif;font-size:var(--text-sm);font-weight:500;color:var(--color-ink, #1C1C1C);flex:1;min-width:0;display:inline-flex;align-items:center;gap:10px;";
    toggleLabel.innerHTML = '<span>Show name on portrait</span><span data-name-loading style="display:none;font-size:var(--text-xs);color:var(--color-muted, #8a8580);font-weight:500;align-items:center;gap:6px;"></span>';
    toggleRow.appendChild(toggleLabel);

    // Inject spinner keyframe once per page
    if (!document.getElementById('pdp-toggle-spin-kf')) {
      var kf = document.createElement('style');
      kf.id = 'pdp-toggle-spin-kf';
      kf.textContent = '@keyframes pdpToggleSpin{to{transform:rotate(360deg)}}';
      document.head.appendChild(kf);
    }

    // Toggle buttons
    var btnGroup = document.createElement('div');
    btnGroup.style.cssText = 'display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--color-border, #e5e0db);';

    function makeToggleBtn(text, isActive) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = text;
      btn.style.cssText = "font-family:'Inter',sans-serif;font-size:var(--text-xs);font-weight:600;"
        + 'padding:8px 16px;border:none;cursor:pointer;transition:all 0.2s;min-width:52px;'
        + (isActive
          ? 'background:var(--color-ink, #1C1C1C);color:#fff;'
          : 'background:var(--color-surface, #faf9f7);color:var(--color-muted, #8a8580);');
      return btn;
    }

    var yesBtn = makeToggleBtn('Yes', showName);
    var noBtn  = makeToggleBtn('No', !showName);
    btnGroup.appendChild(yesBtn);
    btnGroup.appendChild(noBtn);
    btnGroup.style.flexShrink = '0';
    toggleRow.appendChild(btnGroup);

    var nameLoadingEl = toggleLabel.querySelector('[data-name-loading]');
    var cycler = null;
    function setLoading(on, initialText) {
      if (!nameLoadingEl) return;
      if (on) {
        nameLoadingEl.style.display = 'inline-flex';
        nameLoadingEl.innerHTML = '<span aria-hidden="true" style="width:12px;height:12px;border:2px solid var(--color-border,#e5e0db);'
          + 'border-top-color:var(--color-ink,#1C1C1C);border-radius:50%;animation:pdpToggleSpin 0.9s linear infinite;display:inline-block;"></span>'
          + '<span data-phase>' + (initialText || 'Adding the name\u2026') + '</span>';
        nameLoadingEl.setAttribute('role', 'status');
        nameLoadingEl.setAttribute('aria-live', 'polite');
      } else {
        nameLoadingEl.style.display = 'none';
        nameLoadingEl.innerHTML = '';
        if (cycler) { clearInterval(cycler); cycler = null; }
      }
    }

    function setButtonsDisabled(disabled) {
      [yesBtn, noBtn].forEach(function (b) {
        b.disabled = disabled;
        b.style.opacity = disabled ? '0.6' : '1';
        b.style.cursor = disabled ? 'wait' : 'pointer';
      });
    }

    function renderActiveImage(url) {
      // Pick the 1:1 derivative that matches the current toggle state.
      // Toggling No used to keep showing data.namedPreviewUrl1x1 on square
      // mockups — the name visibly stayed on the canvas after the customer
      // had opted out. Use the no-name 1:1 derivative on No.
      var url1x1 = showName
        ? (data.namedPreviewUrl1x1 || null)
        : (data.previewUrl1x1 || null);
      var heroUrl = url1x1 || url;
      var mainImg = gallery.querySelector('.product-gallery__slide:first-child img');
      if (mainImg) mainImg.src = heroUrl;
      if (thumb) thumb.src = heroUrl;
      // Source-aspect change means the slide's cropping math is wrong
      // for the new src, so always rebuild the slide rather than just
      // swapping <img>.src. Every slide rebuilds as a client mockup
      // sourced from the un-watermarked print PNG (per-aspect) when the
      // toggle state has one available, or from the watermarked WebP
      // fallback when it doesn't.
      var sizesMap = VARIANT_SIZES[productHandle] || VARIANT_SIZES['canvas'] || {};
      gallery.querySelectorAll('.product-gallery__slide--mockup').forEach(function (slide) {
        var sizeKey = slide.getAttribute('data-variant-size') || '';
        var dim = sizesMap[sizeKey];
        if (!dim) return;
        var isSq = dim.w === dim.h;
        var isThreeByFour = (dim.w === 3 && dim.h === 4) || (dim.w * 4 === dim.h * 3);
        var isFourByFive = (dim.w === 4 && dim.h === 5) || (dim.w * 5 === dim.h * 4);

        // Per-aspect un-watermarked PNG, picked to match the active
        // toggle. printFileUrl* fields are overwritten by /add-name on
        // YES; on NO we currently only have the 4:5 no-name PNG, so
        // square / 3:4 NO falls back to the watermarked WebP path.
        var matchedPrintUrl = null;
        if (showName) {
          if (isSq) matchedPrintUrl = data.printFileUrl1x1 || null;
          else if (isThreeByFour) matchedPrintUrl = data.printFileUrl3x4 || null;
          else if (isFourByFive) matchedPrintUrl = data.printFileUrl || null;
        } else {
          // NO state — only the 4:5 has an un-watermarked source today.
          if (isFourByFive) matchedPrintUrl = data.noNamePrintFileUrl || null;
        }

        slide.innerHTML = '';
        if (matchedPrintUrl) {
          var rebuilt = createClientMockup(
            matchedPrintUrl, dim.w, dim.h, sizeKey,
            false, styleId,
            { srcMatchesFace: true, applyCssWatermark: true }
          );
          slide.appendChild(rebuilt);
        } else {
          // Fallback: watermarked WebP + legacy crop math.
          var useSquareSrc = isSq && !!url1x1;
          var srcForSlide = useSquareSrc ? url1x1 : url;
          var rebuiltFallback = createClientMockup(srcForSlide, dim.w, dim.h, sizeKey, useSquareSrc, styleId);
          slide.appendChild(rebuiltFallback);
        }
      });
    }

    function updateHiddenProps(withText, printUrl, portraitUrl) {
      var form = document.querySelector('.product-form, form[action*="/cart/add"]');
      if (!form) return;
      var showNameInput = form.querySelector('input[name="properties[_Show Name]"]');
      if (showNameInput) showNameInput.value = withText ? 'Yes' : 'No';
      if (withText && printUrl) {
        var printInput = form.querySelector('input[name="properties[_Print File URL]"]');
        if (printInput) printInput.value = printUrl;
      }
      if (withText && portraitUrl) {
        var portraitInput = form.querySelector('input[name="properties[_Portrait URL]"]');
        if (portraitInput) portraitInput.value = portraitUrl;
      }
      // Per-aspect URLs — fulfillment._pick_source_url uses these to
      // pick the right per-front-face derivative when the customer's
      // chosen variant isn't 4:5. data.printFileUrl3x4/1x1 were just
      // populated by /add-name; no-op when they're empty.
      if (withText) {
        var printInput3x4 = form.querySelector('input[name="properties[_Print File URL 3x4]"]');
        if (printInput3x4 && data.printFileUrl3x4) printInput3x4.value = data.printFileUrl3x4;
        var printInput1x1 = form.querySelector('input[name="properties[_Print File URL 1x1]"]');
        if (printInput1x1 && data.printFileUrl1x1) printInput1x1.value = data.printFileUrl1x1;
      }
    }

    // Run /add-name for the current petName + style + background. Used by
    // the Yes toggle AND the inline name editor. Resolves when complete so
    // callers can sequence additional updates.
    function fetchNamedPreview() {
      setLoading(true, 'Adding the name\u2026');
      setButtonsDisabled(true);

      var PHRASES = ['Adding the name\u2026', 'Painting the letters\u2026', 'Blending it in\u2026', 'Almost done\u2026'];
      var idx = 0;
      if (cycler) clearInterval(cycler);
      cycler = setInterval(function () {
        idx = Math.min(idx + 1, PHRASES.length - 1);
        var p = nameLoadingEl && nameLoadingEl.querySelector('[data-phase]');
        if (p) p.textContent = PHRASES[idx];
      }, 3500);

      var API_BASE = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
      // CRITICAL: only ever pass a NO-NAME source URL. data.printFileUrl
      // used to be in this fallback chain (commit history) but it points
      // at the NAMED hi-res PNG — sending that to /add-name composites a
      // SECOND name on top of the existing one and produces "ROOGEER" /
      // "JEWILDER" double-name ghosts that no downstream idempotency
      // guard can fully recover from. The chain now ONLY contains
      // confirmed no-name sources:
      //   1. noNamePrintFileUrl — un-watermarked no-name PNG (best)
      //   2. previewUrls[0] / noTextUrl — watermarked no-name WebP
      //      (acceptable; backend strips the watermark by re-rendering
      //      from the source bytes)
      // If neither exists, return early — failing loudly is far better
      // than re-doubling a name onto an already-named file.
      var addNameSource = data.noNamePrintFileUrl || noTextUrl;
      if (!addNameSource) {
        console.error('[PetPrintables] No no-name source available for /add-name — aborting');
        setLoading(false);
        setButtonsDisabled(false);
        return Promise.reject(new Error('NO_NO_NAME_SOURCE'));
      }
      return fetch(API_BASE + '/add-name', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_url: addNameSource,
          pet_name: petName,
          style: data.styleId || '',
          background_mode: backgroundMode,
        }),
      })
      .then(function (r) { return r.ok ? r.json() : r.json().then(function (d) { throw new Error(d.error || 'Failed'); }); })
      .then(function (resp) {
        var newUrl = resp.composited_png_cdn || resp.composited;
        if (!newUrl) throw new Error('No image URL returned');
        withTextUrl = newUrl;
        // Prefer the watermarked per-aspect WebPs (composited_3x4_preview,
        // composited_1x1_preview) for variant display so the diagonal Pet
        // Printables watermark is visible. Fall back to the un-watermarked
        // print PNGs only when the backend hasn't been redeployed yet.
        var newUrl3x4 = resp.composited_3x4_preview
          || resp.composited_png_3x4_cdn
          || null;
        var newUrl1x1 = resp.composited_1x1_preview
          || resp.composited_png_1x1_cdn
          || resp.composited_1x1
          || null;
        // Hi-res un-watermarked print files for Printful + per-variant
        // mockup tasks. These match the front-face aspect of each canvas
        // size so Printful never has to cover-crop a 4:5 source.
        var newPrint3x4 = resp.composited_png_3x4_cdn || null;
        var newPrint1x1 = resp.composited_png_1x1_cdn || null;

        // Persist for future PDP loads so we don't re-fetch on refresh
        try {
          var sess = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
          sess.namedPreviewUrl = newUrl;
          if (newUrl3x4) sess.namedPreviewUrl3x4 = newUrl3x4;
          if (newUrl1x1) sess.namedPreviewUrl1x1 = newUrl1x1;
          if (newPrint3x4) sess.printFileUrl3x4 = newPrint3x4;
          if (newPrint1x1) sess.printFileUrl1x1 = newPrint1x1;
          sess.petName = petName;
          sess.printFileUrl = resp.composited_png_cdn || sess.printFileUrl;
          localStorage.setItem(LS_KEY, JSON.stringify(sess));
        } catch (_) {}
        // Live data refs so the gallery rebuild + cart writer below pick
        // up the new per-aspect URLs without a refresh.
        data.namedPreviewUrl = newUrl;
        data.namedPreviewUrl3x4 = newUrl3x4 || data.namedPreviewUrl3x4;
        data.namedPreviewUrl1x1 = newUrl1x1 || data.namedPreviewUrl1x1;
        data.printFileUrl3x4 = newPrint3x4 || data.printFileUrl3x4;
        data.printFileUrl1x1 = newPrint1x1 || data.printFileUrl1x1;

        if (showName) renderActiveImage(newUrl);
        updateHiddenProps(showName, resp.composited_png_cdn || newUrl, newUrl);
        setLoading(false);
        setButtonsDisabled(false);
      })
      .catch(function (err) {
        setLoading(false);
        setButtonsDisabled(false);
        if (nameLoadingEl) {
          nameLoadingEl.style.display = 'inline-flex';
          nameLoadingEl.style.color = '#B00020';
          nameLoadingEl.textContent = 'Could not add name — try again';
          setTimeout(function () { setLoading(false); nameLoadingEl.style.color = ''; }, 3000);
        }
        // Revert toggle state so the UI is coherent
        updateTextToggle(false);
        throw err;
      });
    }

    function updateTextToggle(withText) {
      showName = withText;
      // Toggle button styles
      yesBtn.style.background = withText ? 'var(--color-ink, #1C1C1C)' : 'var(--color-surface, #faf9f7)';
      yesBtn.style.color      = withText ? '#fff' : 'var(--color-muted, #8a8580)';
      noBtn.style.background  = withText ? 'var(--color-surface, #faf9f7)' : 'var(--color-ink, #1C1C1C)';
      noBtn.style.color       = withText ? 'var(--color-muted, #8a8580)' : '#fff';

      if (!withText) {
        renderActiveImage(noTextUrl);
        updateHiddenProps(false);
        return;
      }

      // User wants name. If we already generated the named version, swap instantly.
      if (withTextUrl) {
        renderActiveImage(withTextUrl);
        updateHiddenProps(true, withTextUrl, withTextUrl);
        return;
      }

      fetchNamedPreview().catch(function () { /* handled inline */ });
    }

    yesBtn.addEventListener('click', function () {
      updateTextToggle(true);
      // Reveal the mockup slide so the customer sees the named portrait on
      // the actual canvas/framed product, not just on the bare portrait.
      var firstMockup = gallery && gallery.querySelector('.product-gallery__slide--mockup');
      if (firstMockup) firstMockup.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
    });
    noBtn.addEventListener('click',  function () { updateTextToggle(false); });

    // ── Inline pet-name editor ────────────────────────────
    // Lets the customer tweak the name right on the PDP. Debounced so we
    // only re-call /add-name after they stop typing, and only when the
    // "Show name" toggle is on (so we don't burn a Gemini call the user
    // won't see).
    var editorRow = document.createElement('div');
    editorRow.style.cssText = 'display:flex;align-items:center;gap:10px;margin-top:10px;';

    var editorLabel = document.createElement('label');
    editorLabel.setAttribute('for', 'PdpPetNameEdit');
    editorLabel.textContent = 'Name';
    editorLabel.style.cssText = "font-family:'Inter',sans-serif;font-size:var(--text-xs);font-weight:600;"
      + 'color:var(--color-muted, #8a8580);text-transform:uppercase;letter-spacing:0.08em;';
    editorRow.appendChild(editorLabel);

    var editorInput = document.createElement('input');
    editorInput.type = 'text';
    editorInput.id = 'PdpPetNameEdit';
    editorInput.value = petName;
    editorInput.maxLength = 20;
    editorInput.setAttribute('aria-label', 'Edit name on portrait');
    editorInput.style.cssText = "flex:1;min-width:0;font-family:'Inter',sans-serif;font-size:var(--text-sm);"
      + 'padding:8px 12px;border:1.5px solid var(--color-border, #e5e0db);border-radius:8px;'
      + 'background:#fff;color:var(--color-ink, #1C1C1C);outline:none;transition:border-color 0.2s;';
    editorInput.addEventListener('focus', function () {
      editorInput.style.borderColor = 'var(--color-ink, #1C1C1C)';
    });
    editorInput.addEventListener('blur', function () {
      editorInput.style.borderColor = 'var(--color-border, #e5e0db)';
    });

    var editorCounter = document.createElement('span');
    editorCounter.style.cssText = "font-family:'Inter',sans-serif;font-size:var(--text-xs);color:var(--color-muted, #8a8580);";
    editorCounter.textContent = petName.length + '/20';
    editorRow.appendChild(editorInput);
    editorRow.appendChild(editorCounter);
    toggleWrap.appendChild(editorRow);

    function applyNameEverywhere(newName) {
      petName = newName;
      // Update the banner above the product title
      var existingBanner = document.querySelector('.product-info__title');
      if (existingBanner && existingBanner.previousElementSibling &&
          existingBanner.previousElementSibling.tagName === 'P') {
        existingBanner.previousElementSibling.textContent = (newName || 'Your') + '\u2019s portrait';
      }
      // Update the confirmed-portrait strip label
      if (nameLabel) {
        nameLabel.textContent = newName ? newName + '\u2019s Portrait' : 'Your Portrait';
      }
      // Keep the (hidden) Shopify form input in sync — Pet Name goes on the cart line
      var petNameFormInput = document.getElementById('PetName');
      if (petNameFormInput) petNameFormInput.value = newName;
      var form = document.querySelector('.product-form, form[action*="/cart/add"]');
      if (form) {
        var nameProp = form.querySelector('input[name="properties[Pet Name]"]');
        if (nameProp) nameProp.value = newName;
      }
      // Persist the new name so reloads pick it up
      try {
        var sess = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
        sess.petName = newName;
        localStorage.setItem(LS_KEY, JSON.stringify(sess));
      } catch (_) {}
    }

    var editDebounce = null;
    editorInput.addEventListener('input', function () {
      var cleaned = sanitizePetName(editorInput.value);
      editorCounter.textContent = cleaned.length + '/20';
      if (cleaned === petName) return;

      // Update banner / strip / hidden inputs immediately — this is what the
      // customer sees in the cart line, regardless of the Gemini rerender.
      applyNameEverywhere(cleaned);

      // The previously generated named preview is now stale.
      withTextUrl = null;

      // If "Show name" is off, don't fire /add-name — the customer hasn't
      // asked to see the name on the art. They'll get a fresh render if
      // they flip the toggle on.
      if (!showName) return;

      // Show "no name" version immediately while we regenerate, so the
      // stale name doesn't linger on screen.
      renderActiveImage(noTextUrl);

      if (editDebounce) clearTimeout(editDebounce);
      editDebounce = setTimeout(function () {
        if (!petName) return; // empty — leave as no-name
        fetchNamedPreview().catch(function () { /* handled inline */ });
      }, 650);
    });

    // Insert after the portrait strip
    if (insertTarget && insertTarget.parentNode) {
      insertTarget.parentNode.insertBefore(toggleWrap, insertTarget);
    }

    // If the customer opted in on Step 4 but we don't have the named
    // preview cached yet (race with Continue click, or they edited from
    // another device), kick off the render so "Yes" actually shows names.
    if (showName && !withTextUrl && petName) {
      fetchNamedPreview().catch(function () { /* handled inline */ });
    }
  }

  // ── Inject hidden line item properties into the product form ──
  var form = document.querySelector('.product-form, form[action*="/cart/add"]');
  if (form) {
    form.querySelectorAll('input[name^="properties["]').forEach(function (el) {
      if (el.type === 'hidden') el.remove();
    });

    var API_BASE = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
    var cdnUrls = data.previewCdnUrls || [];
    var previewUrlForCart = cdnUrls[data.selectedPreviewIndex || 0]
      || cdnUrls[0]
      || (data.imageFilename ? (API_BASE + '/preview/' + data.imageFilename) : '');

    // Hi-res print-ready PNG (3000x3750+ @ 300 DPI) for Printful fulfillment
    // Falls back to preview URL if the hi-res isn't available (shouldn't happen).
    var printFileUrl = data.printFileUrl || previewUrlForCart;

    // Hi-res no-name print PNG for Printful — un-watermarked, used when
    // the customer toggles "Show Name = No" on the cart line. cdnUrls[1]
    // is now the WATERMARKED no-name preview, NOT a print-quality file,
    // so we must use data.noNamePrintFileUrl which is the un-watermarked
    // PNG. Fallback to printFileUrl (with-name PNG) is still a hi-res
    // file even if Gemini hasn't been re-run.
    var noNamePrintFileUrl = data.noNamePrintFileUrl || printFileUrl;

    // Per-aspect print files (front-face-correct, wrap-aware on the
    // server side). Populated by /add-name when the customer adds a
    // name; empty for the no-name path. Fulfillment._pick_source_url
    // falls back to the 4:5 master if the requested aspect is empty.
    var printFileUrl3x4 = data.printFileUrl3x4 || '';
    var printFileUrl1x1 = data.printFileUrl1x1 || '';

    var props = {
      'Pet Name': petName,
      '_Style': data.styleId || '',
      '_Font Size': fontSize,
      '_Show Name': wantsName ? 'Yes' : 'No',
      '_Frame': wantsFrame ? 'Framed' : 'No frame',
      '_Job ID': data.jobId || '',
      '_Portrait URL': previewUrlForCart,                    // watermarked preview for display
      '_Print File URL': printFileUrl,                       // un-watermarked hi-res 4:5 PNG for Printful
      '_No Name URL': noNamePrintFileUrl,                    // un-watermarked hi-res no-name PNG for Printful
      '_Print File URL 3x4': printFileUrl3x4,                // un-watermarked hi-res 3:4 PNG (canvas-12x16)
      '_Print File URL 1x1': printFileUrl1x1,                // un-watermarked hi-res 1:1 PNG (canvas-12x12 / 16x16)
      '_No Name Preview URL': cdnUrls[1] || cdnUrls[0] || '', // watermarked no-name WebP for cart display (e.g. magnet upsell thumbnail)
    };
    Object.keys(props).forEach(function (key) {
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'properties[' + key + ']';
      input.value = props[key];
      form.appendChild(input);
    });

    // ── Auto-select the variant chosen on step 4 ───────────
    // Prefer URL ?variant= match (already handled by Shopify natively),
    // then fall back to matching by size label in the variant picker.
    var urlParams = new URLSearchParams(window.location.search);
    var urlVariantId = urlParams.get('variant');

    function clickMatchingSize() {
      if (!selectedSize) return;
      var targetParts = selectedSize.match(/(\d+)\D+(\d+)/);
      if (!targetParts) return;
      var w = targetParts[1], h = targetParts[2];
      var variantOpts = document.querySelectorAll('.variant-option');
      variantOpts.forEach(function (opt) {
        var txt = (opt.textContent || '').match(/(\d+)\D+(\d+)/);
        if (txt && txt[1] === w && txt[2] === h && !opt.classList.contains('is-selected')) {
          opt.click();
        }
      });
    }

    // Try immediately + after short delay (theme.js might not be ready yet)
    setTimeout(clickMatchingSize, 50);
    setTimeout(clickMatchingSize, 300);

    // Also ensure the hidden form id matches the chosen variant
    if (urlVariantId && form) {
      var hiddenId = form.querySelector('input[name="id"]');
      if (hiddenId) hiddenId.value = urlVariantId;
    }

    // The /add-name call is now handled by the "Show name on portrait"
    // toggle (see updateTextToggle above), so by the time the customer
    // clicks Add to Cart the named preview URL is already present in
    // properties[_Print File URL] and the form can submit cleanly.
    // The ATC button stays "Add to Cart" throughout — no loading hijack.

    // ── Submit-time refresh from the freshest session ─────────
    // The hidden cart props above are populated once at script load from
    // `data` (captured at line 70). If the customer regenerates with a
    // different style elsewhere — e.g. /pages/create → Restart → pick a
    // new style → back to PDP via cached navigation — a stale-data
    // window can leave the form holding the old _Style / _Job ID even
    // though the rest of the page (toggle, image) has caught up.
    //
    // Re-read localStorage in the submit capture phase (before Shopify's
    // /cart/add handler reads the form) and rewrite every session-derived
    // hidden input from the freshest values. The customer's current
    // Show-Name toggle state is read from the form input itself rather
    // than LS, since toggle state isn't persisted to LS today.
    // Flag flips true after we've passed all the guards, so the
    // programmatic .submit() that re-triggers this listener doesn't
    // re-run them in an infinite loop.
    var atcGuardsPassed = false;

    form.addEventListener('submit', function (e) {
      if (atcGuardsPassed) return; // already validated, allow Shopify to handle

      var freshData;
      try {
        var raw = localStorage.getItem(LS_KEY);
        if (raw) {
          var parsed = JSON.parse(raw);
          if (parsed && parsed.version === 1) freshData = parsed;
        }
      } catch (err) { /* keep existing inputs if LS is unreadable */ }
      if (!freshData) return;

      function setProp(key, value) {
        var input = form.querySelector('input[name="properties[' + key + ']"]');
        if (!input) {
          input = document.createElement('input');
          input.type = 'hidden';
          input.name = 'properties[' + key + ']';
          form.appendChild(input);
        }
        input.value = value || '';
      }

      // Customer's current name choice — toggle is the source of truth.
      var showNameInput = form.querySelector('input[name="properties[_Show Name]"]');
      var withName = showNameInput
        ? showNameInput.value === 'Yes'
        : (freshData.wantsName !== false);

      var cdnUrls = freshData.previewCdnUrls || [];
      var defaultPreview = cdnUrls[freshData.selectedPreviewIndex || 0]
        || cdnUrls[0] || '';
      // RACE CONDITION GUARD — if the customer has Yes toggled but
      // namedPreviewUrl hasn't arrived yet (they clicked ATC while
      // /add-name was still in-flight), the fallback would silently
      // commit the no-name URL with _Show Name=Yes. The cart line then
      // shows Yes but renders the no-name image, AND fulfillment ships
      // the no-name print despite the customer's name choice.
      //
      // Block the submit with a clear message and force the customer
      // to wait for the named version to complete.
      if (withName && !freshData.namedPreviewUrl) {
        if (e && e.preventDefault) e.preventDefault();
        if (e && e.stopImmediatePropagation) e.stopImmediatePropagation();
        console.warn(
          '[PetPrintables] BLOCKED ATC — Yes toggled but namedPreviewUrl not yet ready. /add-name still in flight?',
        );
        alert(
          'Please wait — we are still adding the name to your portrait. ' +
          'Try clicking Add to Cart again in 5-10 seconds.'
        );
        // If /add-name isn't already running, kick it off so the
        // customer's next click succeeds.
        try {
          if (typeof fetchNamedPreview === 'function' && !withTextUrl) {
            fetchNamedPreview().catch(function () { /* handled inline */ });
          }
        } catch (_) {}
        return;
      }

      var primaryPreviewUrl = withName
        ? freshData.namedPreviewUrl
        : defaultPreview;
      var primaryPrintFile = withName
        ? (freshData.printFileUrl || primaryPreviewUrl)
        : (freshData.noNamePrintFileUrl || freshData.printFileUrl || primaryPreviewUrl);

      // R2 prefix guard removed — it was over-eager. Different uuid
      // prefixes between noNamePrintFileUrl (from initial generation)
      // and printFileUrl* (from /add-name) are LEGITIMATE and expected:
      // /add-name creates new files with a fresh uuid every time, so
      // the named files always have a different prefix from the no-name
      // source. The guard fired on every legitimate name-toggle flow
      // and produced false-positive "Something went wrong" alerts.
      //
      // The actual class of bugs the prefix guard was trying to catch
      // (sending a named file as a no-name source to /add-name) is now
      // covered by:
      //   - client-side: only no-name URLs in the addNameSource fallback
      //     chain (see fetchNamedPreview above)
      //   - server-side: /add-name rejects URLs containing '_named'
      //     in their path with a 400 error
      // Both checks are stricter than the prefix heuristic and don't
      // false-positive on legitimate flows.

      setProp('Pet Name', freshData.petName || '');
      setProp('_Style', freshData.styleId || '');
      setProp('_Font Size', freshData.fontSize || 'medium');
      setProp('_Show Name', withName ? 'Yes' : 'No');
      setProp('_Job ID', freshData.jobId || '');
      setProp('_Portrait URL', primaryPreviewUrl);
      setProp('_Print File URL', primaryPrintFile);
      setProp('_No Name URL', freshData.noNamePrintFileUrl || primaryPrintFile);
      setProp('_Print File URL 3x4', freshData.printFileUrl3x4 || '');
      setProp('_Print File URL 1x1', freshData.printFileUrl1x1 || '');
      setProp('_No Name Preview URL', cdnUrls[1] || cdnUrls[0] || '');
      // _Frame is a PDP-only UI toggle not persisted to LS — leave the
      // current input untouched.

      // CROSS-CART DUPLICATE GUARD — last line of defense against the
      // "two line items show the same image" regression. The R2 prefix
      // guard above only catches mixed prefixes WITHIN this submission;
      // it can't detect when the entire submission is the OLD portrait's
      // URLs being committed under a new pet name (state staleness path
      // where saveSession partially updated petName but not preview
      // URLs). Fetch the live cart and refuse if our _Portrait URL
      // matches an existing line item whose Pet Name differs.
      //
      // Async — preventDefault now and re-submit programmatically with
      // atcGuardsPassed=true if validation passes.
      if (e && e.preventDefault) e.preventDefault();
      if (e && e.stopImmediatePropagation) e.stopImmediatePropagation();

      fetch('/cart.js', { headers: { 'Accept': 'application/json' } })
        .then(function (r) { return r.ok ? r.json() : { items: [] }; })
        .catch(function () { return { items: [] }; })
        .then(function (cart) {
          var items = (cart && cart.items) || [];
          var conflict = null;
          for (var i = 0; i < items.length; i++) {
            var props = items[i].properties || {};
            var existingPortraitUrl = props['_Portrait URL'] || '';
            var existingPetName = (props['Pet Name'] || '').trim();
            var newPetName = (freshData.petName || '').trim();
            // Same _Portrait URL + DIFFERENT Pet Name = the customer
            // thinks they're adding a new portrait but is committing
            // the previous one. Block with a clear message. Same URL +
            // same name = legitimate "same portrait at different size",
            // allow.
            if (
              existingPortraitUrl &&
              existingPortraitUrl === primaryPreviewUrl &&
              existingPetName.toLowerCase() !== newPetName.toLowerCase()
            ) {
              conflict = { existingPetName: existingPetName, existingTitle: items[i].product_title };
              break;
            }
          }

          if (conflict) {
            console.error(
              '[PetPrintables] BLOCKED ATC — _Portrait URL matches an existing cart item ' +
              '(' + conflict.existingTitle + ' / ' + conflict.existingPetName + ') ' +
              'but Pet Name differs (' + freshData.petName + '). State staleness ' +
              'committed the previous portrait\'s URL under a new name.'
            );
            alert(
              'It looks like this portrait is the same as one already in your cart. ' +
              'To add a different design for ' + (freshData.petName || 'this pet') + ', ' +
              'please go back and generate a new portrait first.'
            );
            return;
          }

          // All guards passed — let Shopify's submit handler run.
          atcGuardsPassed = true;
          form.submit();
        });
    }, true); // capture=true → runs BEFORE Shopify's submit handler
  }

  } // end runInjection
})();
