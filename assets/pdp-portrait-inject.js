/* ─────────────────────────────────────────────────────────────
   PDP Portrait Injection
   Reads the saved portrait from localStorage and injects it
   as the hero image on product pages. Generates instant
   client-side canvas mockups per variant, with Printful
   mockups replacing them when available.
   ───────────────────────────────────────────────────────────── */
(function () {
  console.log('[PetPrintables] PDP inject script loaded v3');
  var LS_KEY = 'petPrintables_session';
  var PENDING_KEY = 'petPrintables_pending';
  var raw;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { /* no storage access */ }

  // Function definitions (hoisted, but defining explicitly for clarity)
  function setupPdpPreGenFlow() {
    console.log('[PetPrintables] Setting up pre-gen PDP flow');
    function init() {
      var uploadInput = document.getElementById('PetPhotoUpload');
      var nameInput = document.getElementById('PetName');
      var atcBtn = document.querySelector('.atc-btn');
      var form = document.querySelector('.product-form, form[action*="/cart/add"]');

      console.log('[PetPrintables] Pre-gen init found elements:', {
        uploadInput: !!uploadInput, nameInput: !!nameInput, atcBtn: !!atcBtn, form: !!form
      });

      if (!uploadInput || !nameInput || !atcBtn || !form) return;

      // Rename the ATC button to lead into the flow
      atcBtn.textContent = 'CONTINUE \u2192 PICK YOUR STYLE';
      atcBtn.setAttribute('type', 'button');
      atcBtn.removeAttribute('name');
      atcBtn.setAttribute('data-pregen', 'true');  // flag so theme.js skips updating it

      // Watch for other scripts overwriting the button text and restore it
      var preGenText = 'CONTINUE \u2192 PICK YOUR STYLE';
      var observer = new MutationObserver(function () {
        if (atcBtn.textContent.trim() !== preGenText) {
          atcBtn.textContent = preGenText;
        }
      });
      observer.observe(atcBtn, { childList: true, characterData: true, subtree: true });

      // Also intercept the FORM submission (belt-and-suspenders)
      form.addEventListener('submit', function (e) {
        e.preventDefault();
        e.stopPropagation();
        handleContinue();
      }, true);

      // Intercept button click
      atcBtn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        handleContinue();
      }, true);

      function handleContinue() {
        var file = uploadInput.files && uploadInput.files[0];
        var petName = (nameInput.value || '').trim();

        if (!file) {
          alert('Please upload your pet\u2019s photo to continue.');
          uploadInput.click();
          return;
        }
        if (!petName) {
          alert('Please enter your pet\u2019s name.');
          nameInput.focus();
          return;
        }

        // Validate the file — mirrors portrait-flow.js so users learn why early.
        var fileName = (file.name || '').toLowerCase();
        var fileType = (file.type || '').toLowerCase();
        var ACCEPTED = ['image/jpeg', 'image/png', 'image/webp'];
        var MAX_SIZE = 15 * 1024 * 1024;
        var isHeic = fileType === 'image/heic' || fileType === 'image/heif'
          || fileName.endsWith('.heic') || fileName.endsWith('.heif');
        if (isHeic) {
          alert(
            'HEIC photos from iPhone aren\u2019t supported yet.\n\n' +
            '\u2022 On iPhone: open the photo, tap Share \u2192 Mail \u2014 iOS converts it to JPG.\n' +
            '\u2022 Or change Settings \u2192 Camera \u2192 Formats \u2192 Most Compatible, then retake.'
          );
          return;
        }
        if (ACCEPTED.indexOf(fileType) === -1) {
          alert(
            'Please upload a JPG, PNG, or WebP file.\n\n' +
            '\u2022 Most photo apps can export as JPG or PNG \u2014 look for \u201cShare\u201d or \u201cExport As\u201d.'
          );
          return;
        }
        if (file.size > MAX_SIZE) {
          alert(
            'This file is over 15 MB. Please use a smaller photo.\n\n' +
            '\u2022 On iPhone, when emailing, choose \u201cMedium\u201d size.\n' +
            '\u2022 Or take a screenshot of the photo to shrink the file.'
          );
          return;
        }

        var reader = new FileReader();
        reader.onload = function () {
          try {
            var pending = {
              version: 1,
              petName: petName,
              photoDataUrl: reader.result,
              photoName: file.name,
              photoType: file.type,
              createdAt: new Date().toISOString(),
            };
            localStorage.setItem(PENDING_KEY, JSON.stringify(pending));
          } catch (err) { /* storage quota — proceed anyway */ }
          window.location.href = '/pages/create';
        };
        reader.onerror = function () {
          alert(
            'We couldn\u2019t open this photo. The file may be damaged.\n\n' +
            '\u2022 Try opening it in your Photos app, re-saving, and uploading again.\n' +
            '\u2022 Or choose a different photo.'
          );
        };
        reader.readAsDataURL(file);
      }
    }

    // Run immediately if DOM is ready, otherwise wait
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
  var mockups = data.mockups && data.mockups[productHandle] ? data.mockups[productHandle] : [];

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
  // so square mockups read as the same product family as the tall
  // Printful mockups. The canvas-face has no fill colour; the portrait
  // covers it edge to edge. That way any style's own background is
  // what sells the canvas face — never a flat white rectangle that the
  // portrait sits on top of.
  function createClientMockup(portraitSrc, widthIn, heightIn, label, srcIs1x1) {
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
    var MAX_PCT = 84;        // container % for the largest canvas
    var MIN_PCT = 70;        // container % for the smallest canvas (12")
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
      // Solid walnut frame. The previous gradient stack (5-stop wood
      // gradient + diagonal highlight + grain stripes + inset bevel)
      // produced visible "second frame" striping on the right edge
      // when paired with dark portraits — the gradient stops compressed
      // into thin stripes that read as concentric frames. A single
      // solid colour reads cleanly behind any portrait.
      var frame = document.createElement('div');
      frame.style.cssText = 'position:absolute;inset:0;padding:6%;box-sizing:border-box;'
        + 'background:#3a2818;border-radius:1px;'
        + 'box-shadow:'
        +   'inset 0 1px 0 rgba(255,255,255,0.10),'
        +   'inset 0 -1px 0 rgba(0,0,0,0.30);';
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
    // When the source is the 1:1 derivative (already composed for a
    // square face — pet, chest cut, and name band all in their final
    // positions) no cropping is needed. Otherwise the source is the
    // 4:5 master and we trim the small bg padding ring.
    var cropTopFrac, cropBotFrac, cropSideFrac;
    if (srcIs1x1) {
      cropTopFrac = 0;
      cropBotFrac = 0;
      cropSideFrac = 0;
    } else if (isSquare) {
      cropTopFrac = 0;
      cropBotFrac = 0;
      cropSideFrac = 0.05;
    } else {
      cropTopFrac = 0.05;
      cropBotFrac = 0.10;
      cropSideFrac = 0;
    }
    // Always top-anchor so the name (composited at source y≈11%) lands inside
    // the visible region on every face aspect, rather than being centre-cropped
    // out the top by object-fit:cover.
    var coverPosition = 'center top';
    var hScale = 100 / (1 - 2 * cropSideFrac);                     // e.g. 125
    var vScale = 100 / (1 - cropTopFrac - cropBotFrac);             // e.g. 161.3 (named) or 122 (no-name)
    var leftPct = -cropSideFrac * hScale;                          // e.g. -12.5
    var topPct = -cropTopFrac * vScale;                            // e.g. -48.4 (named) or -12.2 (no-name)

    var cropWindow = document.createElement('div');
    cropWindow.style.cssText = 'position:absolute;inset:0;overflow:hidden;';
    canvasFace.appendChild(cropWindow);

    var portraitImg = document.createElement('img');
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + ' canvas';
    portraitImg.loading = 'lazy';
    portraitImg.style.cssText = 'position:absolute;'
      + 'left:' + leftPct + '%;top:' + topPct + '%;'
      + 'width:' + hScale + '%;height:' + vScale + '%;'
      + 'object-fit:cover;object-position:' + coverPosition + ';display:block;';
    cropWindow.appendChild(portraitImg);

    // Canvas weave texture overlay (SVG noise, multiply blend)
    var weave = document.createElement('div');
    weave.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
      + 'mix-blend-mode:multiply;opacity:0.12;'
      + "background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='w'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0.6 0 0 0 0 0.55 0 0 0 0 0.5 0 0 0 1 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23w)'/%3E%3C/svg%3E\");";
    canvasFace.appendChild(weave);

    // Brand watermark overlay — uses the actual Pet Printables logo
    // asset tiled across the OUTER mockup container (linen + canvas)
    // so it spans the entire visible preview and can't be cropped by
    // the source-image zoom inside the canvas-face.
    var watermark = document.createElement('div');
    watermark.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:3;'
      + 'opacity:0.02;'
      + "background-image:url(" + _assetBase + "watermark-logo.png);"
      + 'background-repeat:repeat;background-size:160px auto;';
    container.appendChild(watermark);

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

    // Build mockup slides — always use consistent client-side mockups
    // Printful mockups only replace ALL at once (via background fetch)
    // to avoid a mix of styles looking inconsistent
    var printfulByVariant = {};
    mockups.forEach(function (m) {
      if (m.placement !== 'default') return;
      var nums = m.variant.match(/(\d+)\D+(\d+)/);
      var key = nums ? nums[1] + 'x' + nums[2] : m.variant;
      if (!printfulByVariant[key]) printfulByVariant[key] = m;
    });
    var allSizeKeys = Object.keys(sizes);
    var hasAllPrintful = allSizeKeys.length > 0 && allSizeKeys.every(function (k) { return !!printfulByVariant[k]; });

    allSizeKeys.forEach(function (sizeKey) {
      var dim = sizes[sizeKey];
      var mockupSlide = document.createElement('div');
      mockupSlide.className = 'product-gallery__slide product-gallery__slide--mockup';
      mockupSlide.setAttribute('role', 'listitem');
      mockupSlide.setAttribute('data-variant-size', sizeKey);

      // Tall sizes prefer Printful's product photo when available.
      // Square sizes use the client-side linen+canvas mockup. We feed
      // it the 1:1 derivative (composed for a square face — pet, name
      // and chest cut all sit at their final positions) when we have
      // it, so the artwork doesn't read as floating with empty bg
      // padding below the pet. The 1:1 derivative is un-watermarked,
      // so createClientMockup tiles a CSS Pet Printables watermark on
      // top when we pass srcIs1x1=true. Falls back to the 4:5 master
      // when no 1:1 derivative exists yet (no name / unnamed style).
      var isSquareVariant = dim.w === dim.h;
      var has1x1 = !!data.namedPreviewUrl1x1;
      var useSquareSrc = isSquareVariant && data.wantsName !== false && has1x1;
      if (hasAllPrintful && !isSquareVariant) {
        var mockupImg = document.createElement('img');
        mockupImg.src = printfulByVariant[sizeKey].url;
        mockupImg.alt = (petName || 'Portrait') + ' ' + sizeKey + ' mockup';
        mockupImg.loading = 'lazy';
        mockupImg.style.cssText = 'width:100%;display:block;border-radius:16px;';
        mockupSlide.appendChild(mockupImg);
      } else {
        var srcForVariant = useSquareSrc ? data.namedPreviewUrl1x1 : previewUrl;
        var clientMockup = createClientMockup(srcForVariant, dim.w, dim.h, sizeKey, useSquareSrc);
        mockupSlide.appendChild(clientMockup);
      }

      gallery.appendChild(mockupSlide);
    });

    // Fire background Printful mockup generation if we don't have all of them yet
    var hasPrintful = Object.keys(printfulByVariant).length;
    var totalSizes = Object.keys(sizes).length;
    if (hasPrintful < totalSizes && data.imageFilename) {
      var API_BASE = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
      fetch(API_BASE + '/mockups', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_filename: data.imageFilename, product_type: productHandle }),
      })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (resp) {
        if (!resp || !resp.mockups || !resp.mockups.length) return;
        try {
          var session = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
          if (!session.mockups) session.mockups = {};
          session.mockups[productHandle] = resp.mockups;
          localStorage.setItem(LS_KEY, JSON.stringify(session));
          // Replace client mockups with Printful mockups (no full reload)
          resp.mockups.forEach(function (m) {
            if (m.placement !== 'default') return;
            var nums = m.variant.match(/(\d+)\D+(\d+)/);
            var key = nums ? nums[1] + 'x' + nums[2] : m.variant;
            // Square variants: keep the client-side linen+canvas mockup.
            // Printful renders the 4:5 master onto the square face and
            // centre-crops it, clipping the name band off the top — so
            // we never let Printful replace square slides.
            var isSq = nums && nums[1] === nums[2];
            if (isSq) return;
            var slideEl = gallery.querySelector('[data-variant-size="' + key + '"]');
            if (slideEl) {
              slideEl.innerHTML = '';
              var newImg = document.createElement('img');
              newImg.src = m.url;
              newImg.alt = (petName || 'Portrait') + ' ' + key + ' mockup';
              newImg.loading = 'lazy';
              newImg.style.cssText = 'width:100%;display:block;border-radius:16px;';
              slideEl.appendChild(newImg);
            }
          });
        } catch (e) {}
      })
      .catch(function () {});
    }

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

  // ── "With name / Without name" toggle ─────────────────────
  // withTextUrl is generated on-demand by /add-name when the user clicks Yes.
  // If Step 4 already produced a named preview (data.namedPreviewUrl), we
  // use it directly so the toggle swap is instant.
  var noTextUrl   = previewUrls[0] || previewUrls[1] || previewUrl;
  var withTextUrl = data.namedPreviewUrl || previewUrls[1] || null; // may be null until Yes is clicked
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
      // Hero slide is locked to a 1:1 aspect ratio — feed it the 1:1
      // derivative when we have one so the name band isn't cropped.
      var url1x1 = data.namedPreviewUrl1x1 || null;
      var heroUrl = (showName && url1x1) ? url1x1 : url;
      var mainImg = gallery.querySelector('.product-gallery__slide:first-child img');
      if (mainImg) mainImg.src = heroUrl;
      if (thumb) thumb.src = heroUrl;
      // Square variants need the 1:1 derivative — cover-cropping the
      // 4:5 master onto a square face clips the name band off the top.
      // Source-aspect change means the slide's cropping math is wrong
      // for the new src, so rebuild client mockup slides instead of
      // just swapping the src on the inner <img>.
      var sizesMap = VARIANT_SIZES[productHandle] || VARIANT_SIZES['canvas'] || {};
      gallery.querySelectorAll('.product-gallery__slide--mockup').forEach(function (slide) {
        var sizeKey = slide.getAttribute('data-variant-size') || '';
        var dim = sizesMap[sizeKey];
        var isSq = dim ? (dim.w === dim.h) : false;
        var useSquareSrc = isSq && !!url1x1;
        var srcForSlide = useSquareSrc ? url1x1 : url;
        // Printful mockup slides have a single bare <img>; client-side
        // ones have a nested canvas-wrap with a portrait <img> inside.
        var isClientMockup = !!slide.querySelector('[style*="aspect-ratio"]');
        if (isClientMockup && dim) {
          slide.innerHTML = '';
          var rebuilt = createClientMockup(srcForSlide, dim.w, dim.h, sizeKey, useSquareSrc);
          slide.appendChild(rebuilt);
        } else {
          var slideImg = slide.querySelector('img');
          if (slideImg) slideImg.src = srcForSlide;
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
      return fetch(API_BASE + '/add-name', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_url: noTextUrl,
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
        var newUrl1x1 = resp.composited_png_1x1_cdn || resp.composited_1x1 || null;

        // Persist for future PDP loads so we don't re-fetch on refresh
        try {
          var sess = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
          sess.namedPreviewUrl = newUrl;
          if (newUrl1x1) sess.namedPreviewUrl1x1 = newUrl1x1;
          sess.petName = petName;
          sess.printFileUrl = resp.composited_png_cdn || sess.printFileUrl;
          localStorage.setItem(LS_KEY, JSON.stringify(sess));
        } catch (_) {}
        // Live data ref so the gallery rebuild below picks up the new URL
        data.namedPreviewUrl1x1 = newUrl1x1 || data.namedPreviewUrl1x1;

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

    var props = {
      'Pet Name': petName,
      '_Style': data.styleId || '',
      '_Font Size': fontSize,
      '_Show Name': wantsName ? 'Yes' : 'No',
      '_Frame': wantsFrame ? 'Framed' : 'No frame',
      '_Job ID': data.jobId || '',
      '_Portrait URL': previewUrlForCart,                    // watermarked preview for display
      '_Print File URL': printFileUrl,                       // un-watermarked hi-res PNG for Printful
      '_No Name URL': noNamePrintFileUrl,                    // un-watermarked hi-res no-name PNG for Printful
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
  }

  } // end runInjection
})();
