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

  // ── Build a room-scene canvas mockup ─────────────────────
  // Renders a realistic wall scene with the portrait as a
  // hanging canvas, including shadow, depth, and furniture hints.

  // Alternate between two wall/room styles for variety
  var _mockupSceneIndex = 0;
  var ROOM_SCENES = [
    {
      // Warm living room — cream wall, wooden shelf
      wall: 'linear-gradient(180deg, #EDE8E0 0%, #E5DFD5 60%, #DDD6CA 100%)',
      shelfColor: '#8B7355',
      shelfTop: '78%',
      accentItem: 'plant',   // small plant on shelf
      accentColor: '#6B8E5A',
    },
    {
      // Modern minimal — light gray wall, floating shelf
      wall: 'linear-gradient(180deg, #EDEDED 0%, #E4E4E4 60%, #DCDCDC 100%)',
      shelfColor: '#C4B9A8',
      shelfTop: '80%',
      accentItem: 'book',
      accentColor: '#A89880',
    },
  ];

  function createClientMockup(portraitSrc, widthIn, heightIn, label) {
    var scene = ROOM_SCENES[_mockupSceneIndex % ROOM_SCENES.length];
    _mockupSceneIndex++;

    // Outer container — the "room" viewport
    var room = document.createElement('div');
    room.style.cssText = 'width:100%;aspect-ratio:4/5;border-radius:16px;overflow:hidden;position:relative;'
      + 'background:' + scene.wall + ';';

    // Subtle wall texture
    var texture = document.createElement('div');
    texture.style.cssText = 'position:absolute;inset:0;opacity:0.03;'
      + "background-image:url(\"data:image/svg+xml,%3Csvg width='4' height='4' xmlns='http://www.w3.org/2000/svg'%3E%3Crect width='1' height='1' fill='%23000'/%3E%3C/svg%3E\");";
    room.appendChild(texture);

    // Canvas frame — centered on the wall, sized proportionally
    // Canvas takes ~50-65% of room width depending on aspect ratio
    var isSquare = widthIn === heightIn;
    var isTall = heightIn > widthIn * 1.5;
    var canvasWidthPct = isSquare ? 50 : isTall ? 38 : 55;

    var canvas = document.createElement('div');
    canvas.style.cssText = 'position:absolute;left:50%;transform:translateX(-50%);'
      + 'top:8%;width:' + canvasWidthPct + '%;aspect-ratio:' + widthIn + '/' + heightIn + ';'
      + 'border-radius:2px;overflow:hidden;'
      // Realistic canvas depth + shadow
      + 'box-shadow:'
      +   '0 2px 4px rgba(0,0,0,0.08),'      // tight shadow
      +   '0 8px 24px rgba(0,0,0,0.12),'      // medium spread
      +   '0 20px 40px rgba(0,0,0,0.08),'     // ambient
      +   '4px 4px 0 0 rgba(0,0,0,0.03);'     // right/bottom edge depth
      // White canvas wrap edge
      + 'border:3px solid #fff;';

    var portraitImg = document.createElement('img');
    portraitImg.src = portraitSrc;
    portraitImg.alt = (petName || 'Portrait') + ' on ' + label + ' canvas';
    portraitImg.loading = 'lazy';
    portraitImg.style.cssText = 'width:100%;height:100%;object-fit:cover;object-position:top;display:block;';
    canvas.appendChild(portraitImg);
    room.appendChild(canvas);

    // Floating shelf
    var shelf = document.createElement('div');
    shelf.style.cssText = 'position:absolute;left:10%;right:10%;top:' + scene.shelfTop + ';height:6px;'
      + 'background:' + scene.shelfColor + ';border-radius:2px;'
      + 'box-shadow:0 2px 8px rgba(0,0,0,0.1);';
    room.appendChild(shelf);

    // Accent item on shelf
    if (scene.accentItem === 'plant') {
      var plant = document.createElement('div');
      plant.style.cssText = 'position:absolute;right:15%;top:calc(' + scene.shelfTop + ' - 28px);'
        + 'width:20px;height:28px;';
      // Pot
      var pot = document.createElement('div');
      pot.style.cssText = 'position:absolute;bottom:0;left:3px;width:14px;height:10px;'
        + 'background:#C4A882;border-radius:1px 1px 3px 3px;';
      plant.appendChild(pot);
      // Leaves (simple circles)
      var leaf1 = document.createElement('div');
      leaf1.style.cssText = 'position:absolute;bottom:8px;left:2px;width:8px;height:12px;'
        + 'background:' + scene.accentColor + ';border-radius:50% 50% 50% 0;transform:rotate(-15deg);';
      plant.appendChild(leaf1);
      var leaf2 = document.createElement('div');
      leaf2.style.cssText = 'position:absolute;bottom:10px;left:8px;width:8px;height:14px;'
        + 'background:' + scene.accentColor + ';border-radius:50% 50% 0 50%;transform:rotate(10deg);opacity:0.85;';
      plant.appendChild(leaf2);
      room.appendChild(plant);
    } else {
      // Books
      var books = document.createElement('div');
      books.style.cssText = 'position:absolute;left:16%;top:calc(' + scene.shelfTop + ' - 18px);'
        + 'display:flex;gap:2px;align-items:flex-end;';
      ['16px', '20px', '14px'].forEach(function(h, i) {
        var book = document.createElement('div');
        var colors = ['#B8A088', '#9E8E7E', '#C4B09A'];
        book.style.cssText = 'width:6px;height:' + h + ';background:' + colors[i] + ';border-radius:1px;';
        books.appendChild(book);
      });
      room.appendChild(books);
    }

    // Size label — elegant pill
    var sizeLabel = document.createElement('div');
    sizeLabel.textContent = widthIn + '" × ' + heightIn + '"';
    sizeLabel.style.cssText = 'position:absolute;bottom:10px;left:50%;transform:translateX(-50%);'
      + "font-family:'Inter',sans-serif;font-size:0.7rem;font-weight:500;letter-spacing:0.05em;"
      + 'color:#6B6560;background:rgba(255,255,255,0.9);padding:4px 12px;border-radius:20px;'
      + 'backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px);';
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
