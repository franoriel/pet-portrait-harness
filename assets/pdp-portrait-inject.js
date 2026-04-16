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
          alert('Could not read your photo. Please try a different image.');
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

  // ── Canvas background photos (AI-generated blank canvases) ──
  // Each has a different aspect ratio and a measured "portrait zone"
  // where we composite the customer's image onto the blank canvas.
  var _assetBase = '';
  var _scriptTag = document.querySelector('script[src*="pdp-portrait-inject"]');
  if (_scriptTag) _assetBase = _scriptTag.src.replace(/pdp-portrait-inject[^/]*$/, '');

  var CANVAS_BACKGROUNDS = {
    // Each config: { img, containerAspect, zone: { top, left, w, h } as % of image }
    square:   { img: 'canvas-bg-square.webp',   aspect: '1/1',    zone: { top: 17, left: 18, w: 64, h: 65 } },
    portrait: { img: 'canvas-bg-portrait.webp', aspect: '800/1192', zone: { top: 16, left: 12, w: 60, h: 60 } },
    tall:     { img: 'canvas-bg-tall.webp',     aspect: '768/1376', zone: { top: 13, left: 12, w: 55, h: 55 } },
  };

  function pickBackground(widthIn, heightIn) {
    var ratio = heightIn / widthIn;
    if (ratio >= 1.8) return CANVAS_BACKGROUNDS.tall;
    if (ratio >= 1.3) return CANVAS_BACKGROUNDS.portrait;
    return CANVAS_BACKGROUNDS.square;
  }

  // ── Client-side product mockup ──────────────────────────
  function createClientMockup(portraitSrc, widthIn, heightIn, label) {
    var bg = pickBackground(widthIn, heightIn);
    var productAspect = heightIn / widthIn;

    // Outer container uses the background image's aspect ratio
    var container = document.createElement('div');
    container.style.cssText = 'width:100%;aspect-ratio:' + bg.aspect + ';border-radius:16px;'
      + 'overflow:hidden;position:relative;background:#f0ece6;';

    // Background canvas photo
    var bgImg = document.createElement('img');
    bgImg.src = _assetBase + bg.img;
    bgImg.alt = '';
    bgImg.setAttribute('aria-hidden', 'true');
    bgImg.loading = 'lazy';
    bgImg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block;';
    container.appendChild(bgImg);

    // Calculate portrait placement within the blank canvas zone,
    // maintaining the variant's exact aspect ratio
    // Convert zone to normalized units (width=100, height scaled by aspect)
    var bgRatio = bg.img === 'canvas-bg-square.webp' ? 1 : (bg.img === 'canvas-bg-portrait.webp' ? 1192/800 : 1376/768);
    var zoneW = bg.zone.w;
    var zoneH = bg.zone.h;  // zone.h is already % of container height
    var zoneAspect = (zoneH * bgRatio) / zoneW;  // height-to-width of zone in actual pixels

    var portraitW, portraitH;
    if (productAspect > zoneAspect) {
      // Variant is taller than zone — fit by height
      portraitH = zoneH;
      portraitW = (portraitH * bgRatio) / productAspect;
    } else {
      // Variant is wider/squarer — fit by width
      portraitW = zoneW;
      portraitH = (portraitW * productAspect) / bgRatio;
    }

    // Center within the zone
    var portraitL = bg.zone.left + (bg.zone.w - portraitW) / 2;
    var portraitT = bg.zone.top + (bg.zone.h - portraitH) / 2;

    // Portrait image — positioned on top of the blank canvas
    var portraitImg = document.createElement('img');
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + ' canvas';
    portraitImg.loading = 'lazy';
    portraitImg.style.cssText = 'position:absolute;'
      + 'top:' + portraitT + '%;'
      + 'left:' + portraitL + '%;'
      + 'width:' + portraitW + '%;'
      + 'height:' + portraitH + '%;'
      + 'object-fit:cover;display:block;'
      // Blend with the canvas lighting — subtle shadow at edges
      + 'box-shadow:inset 0 0 8px rgba(0,0,0,0.04);';
    container.appendChild(portraitImg);

    // Size label — discreet bottom right
    var sizeLabel = document.createElement('div');
    sizeLabel.textContent = widthIn + '\u2033 \u00D7 ' + heightIn + '\u2033';
    sizeLabel.style.cssText = 'position:absolute;bottom:10px;right:14px;'
      + "font-family:'Inter',sans-serif;font-size:0.72rem;font-weight:500;letter-spacing:0.04em;"
      + 'color:#8a8580;background:rgba(255,255,255,0.85);padding:3px 10px;border-radius:12px;'
      + 'backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);';
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

  // Insert strip where upload widget was (before gift message)
  var giftMsg = document.querySelector('input[name="properties[Gift Message]"]');
  var insertTarget = giftMsg ? giftMsg.closest('div[style]') : null;
  if (insertTarget && insertTarget.parentNode) {
    insertTarget.parentNode.insertBefore(strip, insertTarget);
  }

  // ── "With name / Without name" toggle ─────────────────────
  var withTextUrl = previewUrls[0] || previewUrl;
  var noTextUrl = previewUrls[1] || previewUrls[0] || previewUrl;
  var showName = true; // default: with name

  if (previewUrls.length >= 2 && petName) {
    var toggleWrap = document.createElement('div');
    toggleWrap.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:12px 16px;'
      + 'border:1.5px solid var(--color-border, #e5e0db);border-radius:12px;background:var(--color-surface, #faf9f7);';

    var toggleLabel = document.createElement('span');
    toggleLabel.style.cssText = "font-family:'Inter',sans-serif;font-size:0.88rem;font-weight:500;color:var(--color-ink, #1C1C1C);flex:1;";
    toggleLabel.textContent = 'Show name on portrait';
    toggleWrap.appendChild(toggleLabel);

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

    var yesBtn = makeToggleBtn('Yes', true);
    var noBtn = makeToggleBtn('No', false);
    btnGroup.appendChild(yesBtn);
    btnGroup.appendChild(noBtn);
    toggleWrap.appendChild(btnGroup);

    function updateTextToggle(withText) {
      showName = withText;
      var activeUrl = withText ? withTextUrl : noTextUrl;

      // Update toggle button styles
      yesBtn.style.background = withText ? 'var(--color-ink, #1C1C1C)' : 'var(--color-surface, #faf9f7)';
      yesBtn.style.color = withText ? '#fff' : 'var(--color-muted, #8a8580)';
      noBtn.style.background = withText ? 'var(--color-surface, #faf9f7)' : 'var(--color-ink, #1C1C1C)';
      noBtn.style.color = withText ? 'var(--color-muted, #8a8580)' : '#fff';

      // Update main portrait slide
      var mainImg = gallery.querySelector('.product-gallery__slide:first-child img');
      if (mainImg) mainImg.src = activeUrl;

      // Update strip thumbnail
      if (thumb) thumb.src = activeUrl;

      // Update all client mockup images
      gallery.querySelectorAll('.product-gallery__slide--mockup img').forEach(function (mImg) {
        mImg.src = activeUrl;
      });

      // Update hidden form property
      var showNameInput = document.querySelector('input[name="properties[_Show Name]"]');
      if (showNameInput) showNameInput.value = withText ? 'Yes' : 'No';
    }

    yesBtn.addEventListener('click', function () { updateTextToggle(true); });
    noBtn.addEventListener('click', function () { updateTextToggle(false); });

    // Insert after the portrait strip
    if (insertTarget && insertTarget.parentNode) {
      insertTarget.parentNode.insertBefore(toggleWrap, insertTarget);
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
    var portraitUrl = cdnUrls[data.selectedPreviewIndex || 0]
      || cdnUrls[0]
      || (data.imageFilename ? (API_BASE + '/preview/' + data.imageFilename) : '');

    var props = {
      'Pet Name': petName,
      '_Style': data.styleId || '',
      '_Font Size': fontSize,
      '_Show Name': 'Yes',
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

  } // end runInjection
})();
