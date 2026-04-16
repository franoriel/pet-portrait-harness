/* ─────────────────────────────────────────────────────────────
   PDP Portrait Injection
   Reads the saved portrait from localStorage and injects it
   as the hero image on product pages. Generates instant
   client-side canvas mockups per variant, with Printful
   mockups replacing them when available.
   ───────────────────────────────────────────────────────────── */
(function () {
  var LS_KEY = 'petPrintables_session';
  var raw;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { return; }
  if (!raw) return;

  var data;
  try { data = JSON.parse(raw); } catch (e) { return; }
  if (!data || data.version !== 1) return;

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
  if (age > 7 * 24 * 60 * 60 * 1000) { try { localStorage.removeItem(LS_KEY); } catch (e) {} return; }

  var previewUrl = previewUrls[data.selectedPreviewIndex || 0] || previewUrls[0];
  var petName = data.petName || '';
  var styleId = data.styleId || 'soft-watercolour';
  var fontSize = data.fontSize || 'medium';

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

  // ── Canvas variant sizes (inches) ───────────────────────
  var VARIANT_SIZES = {
    'canvas': {
      '10x10': { w: 10, h: 10 },
      '10x20': { w: 10, h: 20 },
      '12x18': { w: 12, h: 18 },
      '12x24': { w: 12, h: 24 },
    },
    'poster': {
      '12x18': { w: 12, h: 18 },
    },
  };

  // ── Build a photorealistic room-scene mockup ──────────────
  // Uses real room photography as background with the portrait
  // composited as a canvas on the wall via CSS positioning.

  var _mockupSceneIndex = 0;

  // Room scene configs — positions define the blank wall zone where
  // the canvas fits (percentage of image dimensions, measured from
  // the actual room photographs)
  var ROOM_SCENES = [
    {
      // Warm scandinavian — credenza + olive tree, blank wall
      image: 'mockup-room-1.webp',
      // Wall zone above the credenza (percentages of image)
      zoneTop: 5,
      zoneLeft: 22,
      zoneW: 55,
      zoneH: 48,
    },
    {
      // Modern living room — gray sofa + side table, blank wall
      image: 'mockup-room-2.webp',
      zoneTop: 5,
      zoneLeft: 12,
      zoneW: 55,
      zoneH: 45,
    },
  ];

  // Resolve Shopify asset URLs for room scene images
  var _assetBase = '';
  var _scriptTag = document.querySelector('script[src*="pdp-portrait-inject"]');
  if (_scriptTag) {
    _assetBase = _scriptTag.src.replace(/pdp-portrait-inject[^/]*$/, '');
  }

  function createClientMockup(portraitSrc, widthIn, heightIn, label) {
    var scene = ROOM_SCENES[_mockupSceneIndex % ROOM_SCENES.length];
    _mockupSceneIndex++;

    // Outer container — room photo viewport
    var room = document.createElement('div');
    room.style.cssText = 'width:100%;border-radius:16px;overflow:hidden;position:relative;';

    // Room background photo
    var bgImg = document.createElement('img');
    bgImg.src = _assetBase + scene.image;
    bgImg.alt = '';
    bgImg.setAttribute('aria-hidden', 'true');
    bgImg.loading = 'lazy';
    bgImg.style.cssText = 'width:100%;display:block;';
    room.appendChild(bgImg);

    // Calculate canvas dimensions to fit within the wall zone
    // while maintaining the product's aspect ratio.
    // The zone is defined in % of the image. We need to fit the
    // canvas (widthIn:heightIn) inside the zone (zoneW:zoneH).
    // Image aspect is ~4:5 (800x993), so zoneH in px is ~1.24x zoneH%
    var imgAspect = 993 / 800; // height/width of room photos
    var productAspect = heightIn / widthIn;

    // Zone in "normalized" units (width=100)
    var zW = scene.zoneW;
    var zH = scene.zoneH * imgAspect; // convert height % to same scale as width %

    // Fit canvas inside zone
    var canvasW, canvasH;
    if (productAspect > zH / zW) {
      // Product is taller than zone — constrain by height
      canvasH = scene.zoneH;
      canvasW = canvasH * imgAspect / productAspect;
    } else {
      // Product is wider/squarer — constrain by width
      canvasW = zW;
      canvasH = canvasW * productAspect / imgAspect;
    }

    // Shrink slightly (90%) to leave breathing room within the zone
    canvasW *= 0.88;
    canvasH *= 0.88;

    // Center within the zone
    var canvasL = scene.zoneLeft + (scene.zoneW - canvasW) / 2;
    var canvasT = scene.zoneTop + (scene.zoneH - canvasH) / 2;

    // ── Product mockup on wall ─────────────────────────────
    var isCanvas = productHandle !== 'poster';

    var productEl = document.createElement('div');
    productEl.style.cssText = 'position:absolute;'
      + 'top:' + canvasT + '%;'
      + 'left:' + canvasL + '%;'
      + 'width:' + canvasW + '%;'
      + 'height:' + canvasH + '%;'
      + 'overflow:hidden;'
      + (isCanvas
        // Canvas: thick gallery wrap, warm directional shadow
        ? 'box-shadow:'
          +   '2px 3px 2px hsla(30,20%,15%,0.08),'    // contact
          +   '3px 5px 8px hsla(30,20%,15%,0.10),'    // near
          +   '5px 10px 20px hsla(30,20%,15%,0.10),'  // mid
          +   '8px 16px 36px hsla(30,20%,15%,0.08);'  // ambient
        // Poster: thin flat print, softer centered shadow
        : 'box-shadow:'
          +   '0 1px 3px hsla(220,10%,20%,0.08),'
          +   '0 4px 10px hsla(220,10%,20%,0.08),'
          +   '0 10px 24px hsla(220,10%,20%,0.06);'
      );
    room.appendChild(productEl);

    var portraitImg = document.createElement('img');
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + (isCanvas ? ' canvas' : ' print');
    portraitImg.loading = 'lazy';
    portraitImg.style.cssText = 'width:100%;height:100%;object-fit:cover;object-position:center;display:block;';
    productEl.appendChild(portraitImg);

    // Inset shadow — subtle edge darkening where print meets wall
    var inset = document.createElement('div');
    inset.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:2;'
      + 'box-shadow:inset 0 0 0 1px rgba(0,0,0,0.06);';
    productEl.appendChild(inset);

    // Size label
    var sizeLabel = document.createElement('div');
    sizeLabel.textContent = widthIn + '" × ' + heightIn + '"';
    sizeLabel.style.cssText = 'position:absolute;bottom:10px;left:50%;transform:translateX(-50%);'
      + "font-family:'Inter',sans-serif;font-size:0.7rem;font-weight:500;letter-spacing:0.05em;"
      + 'color:#6B6560;background:rgba(255,255,255,0.92);padding:4px 12px;border-radius:20px;'
      + 'box-shadow:0 1px 4px rgba(0,0,0,0.08);';
    room.appendChild(sizeLabel);

    return room;
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

    // Build mockup slides — use Printful mockups if available, otherwise client-side
    var printfulByVariant = {};
    mockups.forEach(function (m) {
      if (m.placement !== 'default') return;
      var nums = m.variant.match(/(\d+)\D+(\d+)/);
      var key = nums ? nums[1] + 'x' + nums[2] : m.variant;
      if (!printfulByVariant[key]) printfulByVariant[key] = m;
    });

    Object.keys(sizes).forEach(function (sizeKey) {
      var dim = sizes[sizeKey];
      var mockupSlide = document.createElement('div');
      mockupSlide.className = 'product-gallery__slide product-gallery__slide--mockup';
      mockupSlide.setAttribute('role', 'listitem');
      mockupSlide.setAttribute('data-variant-size', sizeKey);

      if (printfulByVariant[sizeKey]) {
        // Use real Printful mockup
        var mockupImg = document.createElement('img');
        mockupImg.src = printfulByVariant[sizeKey].url;
        mockupImg.alt = (petName || 'Portrait') + ' ' + sizeKey + ' mockup';
        mockupImg.loading = 'lazy';
        mockupImg.style.cssText = 'width:100%;display:block;border-radius:16px;';
        mockupSlide.appendChild(mockupImg);
      } else {
        // Client-side canvas mockup
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

  // Insert strip where upload widget was (before gift message)
  var giftMsg = document.querySelector('input[name="properties[Gift Message]"]');
  var insertTarget = giftMsg ? giftMsg.closest('div[style]') : null;
  if (insertTarget && insertTarget.parentNode) {
    insertTarget.parentNode.insertBefore(strip, insertTarget);
  }

  // ── Inject hidden line item properties into the product form ──
  var form = document.querySelector('.product-form, form[action*="/cart/add"]');
  if (form) {
    form.querySelectorAll('input[name^="properties["]').forEach(function (el) {
      if (el.type === 'hidden') el.remove();
    });

    var API_BASE = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
    var cdnUrls = data.previewCdnUrls || [];
    var portraitUrl = cdnUrls[data.selectedPreviewIndex || 0]
      || cdnUrls[0]
      || (data.imageFilename ? (API_BASE + '/preview/' + data.imageFilename) : '');

    var props = {
      'Pet Name': petName,
      '_Style': data.styleId || '',
      '_Font Size': fontSize,
      '_Job ID': data.jobId || '',
      '_Portrait URL': portraitUrl,
    };
    Object.keys(props).forEach(function (key) {
      var input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'properties[' + key + ']';
      input.value = props[key];
      form.appendChild(input);
    });
  }
})();
