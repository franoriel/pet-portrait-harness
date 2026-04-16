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

  // ── Build a client-side canvas mockup ───────────────────
  function createClientMockup(portraitSrc, widthIn, heightIn, label) {
    var container = document.createElement('div');
    container.style.cssText = 'width:100%;display:flex;align-items:center;justify-content:center;padding:24px;background:#f5f0eb;border-radius:16px;position:relative;';

    // Outer frame — subtle shadow to look like a real canvas
    var frame = document.createElement('div');
    var aspect = heightIn / widthIn;
    // Size the frame: max 80% of container width, maintain aspect ratio
    frame.style.cssText = 'position:relative;width:70%;max-width:320px;background:#fff;border-radius:4px;'
      + 'box-shadow:0 4px 24px rgba(0,0,0,0.12),0 1px 4px rgba(0,0,0,0.08);'
      + 'padding:0;overflow:hidden;aspect-ratio:' + widthIn + '/' + heightIn + ';';

    var portraitImg = document.createElement('img');
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + ' canvas';
    portraitImg.loading = 'lazy';
    portraitImg.style.cssText = 'width:100%;height:100%;object-fit:cover;display:block;';
    frame.appendChild(portraitImg);

    container.appendChild(frame);

    // Size label
    var sizeLabel = document.createElement('span');
    sizeLabel.textContent = widthIn + '" × ' + heightIn + '"';
    sizeLabel.style.cssText = 'position:absolute;bottom:10px;right:14px;font-size:0.75rem;color:#8a8580;'
      + "font-family:'Inter',sans-serif;background:rgba(255,255,255,0.85);padding:3px 8px;border-radius:6px;";
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
