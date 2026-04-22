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
    'minimal-line-art':     "'Raleway', sans-serif",
    'modern-oil-paint':     "'Playfair Display', serif",
    'neon-pop-art':         "'Bungee', sans-serif",
    'renaissance-royalty':  "'Cinzel', serif",
    'cozy-film-grain':      "'Libre Baskerville', serif",
    'rainbow-bridge':       "'Sacramento', cursive",
    'bold-graphic-poster':  "'Oswald', sans-serif",
    'aura-gradient':        "'Quicksand', sans-serif",
  };
  var FONT_SCALES = { small: 0.7, medium: 1.0, large: 1.35 };
  var nameFontCss = STYLE_FONTS[styleId] || "'Cormorant Garamond', serif";
  var nameFontScale = FONT_SCALES[fontSize] || 1.0;

  // Load Google Font for the style
  var GOOGLE_FONTS = {
    'soft-watercolour':     'Dancing+Script:wght@700',
    'minimal-line-art':     'Raleway:wght@300;600',
    'modern-oil-paint':     'Playfair+Display:ital,wght@0,700;1,700',
    'neon-pop-art':         'Bungee',
    'renaissance-royalty':  'Cinzel:wght@700',
    'cozy-film-grain':      'Libre+Baskerville:ital,wght@0,400;1,400',
    'rainbow-bridge':       'Sacramento',
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
      // 8x10 framed retired 2026-04-22 (no live Shopify variant).
      '12x12': { w: 12, h: 12 },
      '12x16': { w: 12, h: 16 },
      '16x16': { w: 16, h: 16 },
      '16x20': { w: 16, h: 20 },
      '18x24': { w: 18, h: 24 },
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

  // ── Client-side product mockup — CSS-composed canvas ─────
  // Strategy: no pre-photographed canvas background. Instead, build
  // the canvas from scratch in CSS so it always matches the variant
  // aspect ratio exactly (no misalignment). The linen surface + shadow
  // + canvas weave texture sell the realism.
  function createClientMockup(portraitSrc, widthIn, heightIn, label) {
    // Outer container: square 1:1 lifestyle shot
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

    // Canvas wrapper — sized to variant aspect ratio, max 72% of container
    var productAspect = heightIn / widthIn;
    var canvasStyleW, canvasStyleH;
    if (productAspect >= 1) {
      // Portrait/square: constrain by height (max 72% of container height)
      canvasStyleH = 72;
      canvasStyleW = canvasStyleH / productAspect;
    } else {
      // Landscape (unused currently): constrain by width
      canvasStyleW = 72;
      canvasStyleH = canvasStyleW * productAspect;
    }

    var canvasWrap = document.createElement('div');
    canvasWrap.style.cssText = 'position:relative;'
      + 'width:' + canvasStyleW + '%;height:' + canvasStyleH + '%;'
      + 'max-width:72%;max-height:72%;';
    container.appendChild(canvasWrap);

    // Deeper ground shadow if framed (heavier object)
    var groundShadow = document.createElement('div');
    groundShadow.style.cssText = 'position:absolute;inset:0;'
      + (isFramedProduct
        ? 'box-shadow:6px 8px 16px rgba(40,28,18,0.18),'
          +          '12px 18px 40px rgba(40,28,18,0.14),'
          +          '18px 26px 56px rgba(40,28,18,0.10);'
        : 'box-shadow:4px 6px 12px rgba(60,45,30,0.12),'
          +          '8px 12px 32px rgba(60,45,30,0.10),'
          +          '12px 20px 48px rgba(60,45,30,0.06);')
      + 'border-radius:1px;';
    canvasWrap.appendChild(groundShadow);

    // If framed: wood frame border wrapping the canvas face
    // If unframed: just the canvas face
    var canvasFace;
    if (isFramedProduct) {
      // Outer frame (dark walnut wood)
      var frame = document.createElement('div');
      frame.style.cssText = 'position:absolute;inset:0;padding:6%;box-sizing:border-box;'
        // Wood grain gradient — layered for depth
        + 'background:'
        +   'linear-gradient(145deg, rgba(255,255,255,0.08) 0%, transparent 50%),'
        +   'linear-gradient(90deg, #2a1d10 0%, #3a2818 30%, #4a3422 50%, #3a2818 70%, #2a1d10 100%);'
        + 'border-radius:1px;'
        // Inner bevel shadow
        + 'box-shadow:'
        +   'inset 0 1px 0 rgba(255,255,255,0.12),'
        +   'inset 0 -1px 0 rgba(0,0,0,0.25),'
        +   'inset 2px 2px 4px rgba(255,255,255,0.04),'
        +   'inset -2px -2px 4px rgba(0,0,0,0.30);';
      canvasWrap.appendChild(frame);

      // Fine subtle wood grain lines (horizontal)
      var woodGrain = document.createElement('div');
      woodGrain.style.cssText = 'position:absolute;inset:0;pointer-events:none;opacity:0.12;'
        + "background-image:repeating-linear-gradient(90deg, transparent 0 3px, rgba(0,0,0,0.4) 3px 3.5px, transparent 3.5px 8px);";
      frame.appendChild(woodGrain);

      // Inner mat / inset shadow recess where canvas sits
      canvasFace = document.createElement('div');
      canvasFace.style.cssText = 'position:absolute;inset:6%;overflow:hidden;'
        + 'background:#fefdfb;'
        + 'box-shadow:inset 0 2px 6px rgba(0,0,0,0.30),'
        +           'inset 0 -1px 2px rgba(0,0,0,0.15);';
      canvasWrap.appendChild(canvasFace);
    } else {
      // Unframed — canvas face fills the wrap
      canvasFace = document.createElement('div');
      canvasFace.style.cssText = 'position:absolute;inset:0;overflow:hidden;'
        + 'background:#fefdfb;'
        + 'border-radius:1px;';
      canvasWrap.appendChild(canvasFace);
    }

    // Portrait image (the user's pet)
    var portraitImg = document.createElement('img');
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + ' canvas';
    portraitImg.loading = 'lazy';
    // object-fit:cover with center gravity mirrors what Printful does when
     // cropping the 4:5 source down to square (1:1) or other aspect ratios —
     // the mockup and the printed canvas stay visually consistent. The
     // Gemini prompt places the pet's name low enough in the 4:5 source
     // (~18-24% from top) that a center-crop to 1:1 still leaves a visible
     // top margin above the name.
    portraitImg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;'
      + 'object-fit:cover;object-position:center center;display:block;';
    canvasFace.appendChild(portraitImg);

    // Canvas weave texture overlay (SVG noise, multiply blend)
    var weave = document.createElement('div');
    weave.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
      + 'mix-blend-mode:multiply;opacity:0.12;'
      + "background-image:url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='w'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 0.6 0 0 0 0 0.55 0 0 0 0 0.5 0 0 0 1 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23w)'/%3E%3C/svg%3E\");";
    canvasFace.appendChild(weave);

    // Canvas edge highlight (thin line at top where wrap meets face)
    var edgeHighlight = document.createElement('div');
    edgeHighlight.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
      + 'box-shadow:inset 0 1px 0 rgba(255,255,255,0.6),'
      +           'inset 0 -1px 0 rgba(0,0,0,0.06),'
      +           'inset 1px 0 0 rgba(255,255,255,0.3),'
      +           'inset -1px 0 0 rgba(0,0,0,0.04);';
    canvasFace.appendChild(edgeHighlight);

    // Size label — glass morphism pill with W × H × D dimensions
    var sizeLabel = document.createElement('div');
    sizeLabel.style.cssText = 'position:absolute;bottom:12px;right:14px;'
      + 'display:flex;align-items:center;gap:8px;'
      + "font-family:'Inter',sans-serif;font-size:0.72rem;font-weight:500;letter-spacing:0.04em;"
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
    sep.style.cssText = 'color:#a09890;font-size:0.7em;';
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
    img.src = previewUrl;
    img.alt = petName ? 'Portrait of ' + petName : 'Your custom pet portrait';
    img.loading = 'eager';
    img.style.cssText = 'width:100%;display:block;border-radius:16px;';
    slide.appendChild(img);

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

      if (hasAllPrintful) {
        // All Printful mockups ready — use them for consistency
        var mockupImg = document.createElement('img');
        mockupImg.src = printfulByVariant[sizeKey].url;
        mockupImg.alt = (petName || 'Portrait') + ' ' + sizeKey + ' mockup';
        mockupImg.loading = 'lazy';
        mockupImg.style.cssText = 'width:100%;display:block;border-radius:16px;';
        mockupSlide.appendChild(mockupImg);
      } else {
        // Client-side mockup for all variants
        var clientMockup = createClientMockup(previewUrl, dim.w, dim.h, sizeKey);
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
      banner.style.cssText = "font-family:'Cormorant Garamond',serif;font-style:italic;font-size:28px;color:#1C1C1C;margin:0 0 4px;letter-spacing:0.02em;";
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
  nameLabel.style.cssText = "margin:0;font-family:'Cormorant Garamond',serif;font-style:italic;font-size:1.1rem;color:var(--color-ink, #1C1C1C);line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
  nameLabel.textContent = petName ? petName + '\u2019s Portrait' : 'Your Portrait';
  info.appendChild(nameLabel);
  var styleLabel = document.createElement('p');
  styleLabel.style.cssText = 'margin:2px 0 0;font-size:0.8rem;color:var(--color-muted, #8a8580);';
  styleLabel.textContent = styleName;
  info.appendChild(styleLabel);

  strip.appendChild(info);

  var changeLink = document.createElement('a');
  changeLink.href = '#';
  changeLink.textContent = 'Change';
  changeLink.style.cssText = 'font-size:0.82rem;color:var(--color-muted, #8a8580);text-decoration:underline;text-underline-offset:2px;white-space:nowrap;flex-shrink:0;';
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
    label.style.cssText = "font-family:'Inter',sans-serif;font-size:0.72rem;font-weight:700;"
      + 'margin:0 0 8px;letter-spacing:0.10em;text-transform:uppercase;';

    var clock = document.createElement('div');
    clock.style.cssText = "font-family:'Inter',sans-serif;font-weight:700;font-size:36px;"
      + 'line-height:1;margin-bottom:6px;font-variant-numeric:tabular-nums;letter-spacing:0.02em;';

    var msg = document.createElement('p');
    msg.style.cssText = "font-family:'Inter',sans-serif;font-size:12px;color:#1C1C1C;"
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
  // Default the toggle from what the customer picked on Step 4 ("Include name").
  // If they opted in, we land on "Yes" — even if the named preview URL hasn't
  // been persisted yet; we'll fetch it proactively below.
  var showName = wantsName;

  if (petName) {
    var toggleWrap = document.createElement('div');
    toggleWrap.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:12px 16px;'
      + 'border:1.5px solid var(--color-border, #e5e0db);border-radius:12px;background:var(--color-surface, #faf9f7);';

    var toggleLabel = document.createElement('span');
    toggleLabel.style.cssText = "font-family:'Inter',sans-serif;font-size:0.88rem;font-weight:500;color:var(--color-ink, #1C1C1C);flex:1;display:inline-flex;align-items:center;gap:10px;";
    toggleLabel.innerHTML = '<span>Show name on portrait</span><span data-name-loading style="display:none;font-size:0.76rem;color:var(--color-muted, #8a8580);font-weight:500;align-items:center;gap:6px;"></span>';
    toggleWrap.appendChild(toggleLabel);

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
      btn.style.cssText = "font-family:'Inter',sans-serif;font-size:0.78rem;font-weight:600;"
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
    toggleWrap.appendChild(btnGroup);

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
      var mainImg = gallery.querySelector('.product-gallery__slide:first-child img');
      if (mainImg) mainImg.src = url;
      if (thumb) thumb.src = url;
      gallery.querySelectorAll('.product-gallery__slide--mockup img').forEach(function (mImg) {
        mImg.src = url;
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

        // Persist for future PDP loads so we don't re-fetch on refresh
        try {
          var sess = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
          sess.namedPreviewUrl = newUrl;
          sess.petName = petName;
          sess.printFileUrl = resp.composited_png_cdn || sess.printFileUrl;
          localStorage.setItem(LS_KEY, JSON.stringify(sess));
        } catch (_) {}

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

    yesBtn.addEventListener('click', function () { updateTextToggle(true); });
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
    editorLabel.style.cssText = "font-family:'Inter',sans-serif;font-size:0.78rem;font-weight:600;"
      + 'color:var(--color-muted, #8a8580);text-transform:uppercase;letter-spacing:0.08em;';
    editorRow.appendChild(editorLabel);

    var editorInput = document.createElement('input');
    editorInput.type = 'text';
    editorInput.id = 'PdpPetNameEdit';
    editorInput.value = petName;
    editorInput.maxLength = 20;
    editorInput.setAttribute('aria-label', 'Edit name on portrait');
    editorInput.style.cssText = "flex:1;min-width:0;font-family:'Inter',sans-serif;font-size:0.9rem;"
      + 'padding:8px 12px;border:1.5px solid var(--color-border, #e5e0db);border-radius:8px;'
      + 'background:#fff;color:var(--color-ink, #1C1C1C);outline:none;transition:border-color 0.2s;';
    editorInput.addEventListener('focus', function () {
      editorInput.style.borderColor = 'var(--color-ink, #1C1C1C)';
    });
    editorInput.addEventListener('blur', function () {
      editorInput.style.borderColor = 'var(--color-border, #e5e0db)';
    });

    var editorCounter = document.createElement('span');
    editorCounter.style.cssText = "font-family:'Inter',sans-serif;font-size:0.72rem;color:var(--color-muted, #8a8580);";
    editorCounter.textContent = petName.length + '/20';
    editorRow.appendChild(editorInput);
    editorRow.appendChild(editorCounter);
    toggleWrap.appendChild(editorRow);
    // Switch the toggleWrap to a column layout now that we're stacking
    // the toggle row and the editor row inside it.
    toggleWrap.style.flexDirection = 'column';
    toggleWrap.style.alignItems = 'stretch';

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

    var props = {
      'Pet Name': petName,
      '_Style': data.styleId || '',
      '_Font Size': fontSize,
      '_Show Name': wantsName ? 'Yes' : 'No',
      '_Frame': wantsFrame ? 'Framed' : 'No frame',
      '_Job ID': data.jobId || '',
      '_Portrait URL': previewUrlForCart,      // preview for display (with or without name)
      '_Print File URL': printFileUrl,         // hi-res for Printful
      '_No Name URL': (data.previewCdnUrls || [])[0] || '',  // preserved for cart toggle
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
