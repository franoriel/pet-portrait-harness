/* ─────────────────────────────────────────────────────────────
   PDP Portrait Injection
   Reads the saved portrait from localStorage and injects it
   as the hero image on product pages. Also saves portrait
   context into hidden line item property inputs.
   ───────────────────────────────────────────────────────────── */
(function () {
  var LS_KEY = 'petPrintables_session';
  var raw;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { return; }
  if (!raw) return;

  var data;
  try { data = JSON.parse(raw); } catch (e) { return; }
  if (!data || data.version !== 1 || !data.previewDataUrls || !data.previewDataUrls.length) return;

  // Check expiry (7 days)
  var age = Date.now() - new Date(data.generatedAt).getTime();
  if (age > 7 * 24 * 60 * 60 * 1000) { try { localStorage.removeItem(LS_KEY); } catch (e) {} return; }

  var previewUrl = data.previewDataUrls[data.selectedPreviewIndex || 0];
  var petName = data.petName || '';

  // ── Detect product type from URL ────────────────────────
  var pathParts = window.location.pathname.split('/');
  var productHandle = pathParts[pathParts.indexOf('products') + 1] || '';
  var mockups = data.mockups && data.mockups[productHandle] ? data.mockups[productHandle] : [];

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
    // Must remove (not hide) so theme.js slide indices stay correct
    var existingSlides = Array.from(gallery.querySelectorAll('.product-gallery__slide'));
    existingSlides.forEach(function (s) { gallery.removeChild(s); });

    // Insert portrait slide AFTER clearing old ones
    gallery.insertBefore(slide, gallery.firstChild);

    if (mockups.length > 0) {
      // We have real mockups — inject them after the portrait

      var insertedMockupVariants = {};
      mockups.forEach(function (mockup) {
        if (mockup.placement !== 'default' || insertedMockupVariants[mockup.variant]) return;
        insertedMockupVariants[mockup.variant] = true;

        var mockupSlide = document.createElement('div');
        mockupSlide.className = 'product-gallery__slide product-gallery__slide--mockup';
        mockupSlide.setAttribute('role', 'listitem');
        mockupSlide.setAttribute('data-variant-size', mockup.variant);

        var mockupImg = document.createElement('img');
        mockupImg.src = mockup.url;
        mockupImg.alt = petName ? petName + ' ' + mockup.variant + ' mockup' : mockup.variant + ' mockup';
        mockupImg.loading = 'lazy';
        mockupImg.style.cssText = 'width:100%;display:block;border-radius:16px;';
        mockupSlide.appendChild(mockupImg);

        gallery.appendChild(mockupSlide);
      });
    } else {
      // No mockups yet — only portrait slide remains
      // If we have a filename, trigger mockup generation in background
      var imageFilename = data.imageFilename;
      if (imageFilename) {
        var API_BASE = (window.petPrintables && window.petPrintables.previewApi) || 'https://web-production-a392e.up.railway.app';
        fetch(API_BASE + '/mockups', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ image_filename: imageFilename, product_type: productHandle }),
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (resp) {
          if (!resp || !resp.mockups || !resp.mockups.length) return;
          try {
            var session = JSON.parse(localStorage.getItem(LS_KEY) || '{}');
            if (!session.mockups) session.mockups = {};
            session.mockups[productHandle] = resp.mockups;
            localStorage.setItem(LS_KEY, JSON.stringify(session));
            // Reload to show the new mockups
            window.location.reload();
          } catch (e) {}
        })
        .catch(function () {});
      }
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
    petNameInput.disabled = true; // Prevent duplicate submission
    petNameInput.removeAttribute('name'); // Remove form name so it won't submit
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
    // Remove any existing hidden properties to avoid duplicates
    form.querySelectorAll('input[name^="properties["]').forEach(function (el) {
      if (el.type === 'hidden') el.remove();
    });

    // Use permanent R2 CDN URL if available, otherwise fall back to Railway preview
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
