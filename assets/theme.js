/* ============================================================
   PET PRINTABLES — THEME JS
   Vanilla JS only. No jQuery.
   ============================================================ */

(function () {
  'use strict';

  /* ── Lazy loading ──────────────────────────────────────── */
  function initLazyLoad() {
    const imgs = document.querySelectorAll('[data-lazy]');
    if (!imgs.length) return;
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        if (el.dataset.src) el.src = el.dataset.src;
        if (el.dataset.srcset) el.srcset = el.dataset.srcset;
        el.classList.add('is-loaded');
        observer.unobserve(el);
      });
    }, { rootMargin: '200px' });
    imgs.forEach(img => observer.observe(img));
  }

  /* ── Nav drawer ────────────────────────────────────────── */
  function initNavDrawer() {
    const drawer   = document.getElementById('NavDrawer');
    const openBtn  = document.getElementById('NavOpen');
    const closeBtn = document.getElementById('NavClose');
    const overlay  = drawer && drawer.querySelector('.nav-drawer__overlay');
    if (!drawer) return;
    const open  = () => { drawer.classList.add('is-open'); document.body.style.overflow = 'hidden'; openBtn?.setAttribute('aria-expanded','true'); };
    const close = () => { drawer.classList.remove('is-open'); document.body.style.overflow = ''; openBtn?.setAttribute('aria-expanded','false'); };
    openBtn?.addEventListener('click', open);
    closeBtn?.addEventListener('click', close);
    overlay?.addEventListener('click', close);
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  }

  /* ── Desktop nav dropdowns ────────────────────────────── */
  function initNavDropdowns() {
    document.querySelectorAll('.nav-dropdown').forEach(dropdown => {
      const trigger = dropdown.querySelector('.nav-dropdown__trigger');
      const menu = dropdown.querySelector('.nav-dropdown__menu');
      if (!trigger || !menu) return;

      trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        const open = trigger.getAttribute('aria-expanded') === 'true';
        closeAllDropdowns();
        if (!open) {
          trigger.setAttribute('aria-expanded', 'true');
          menu.classList.add('is-open');
        }
      });
    });

    function closeAllDropdowns() {
      document.querySelectorAll('.nav-dropdown__trigger').forEach(t => {
        t.setAttribute('aria-expanded', 'false');
        t.nextElementSibling?.classList.remove('is-open');
      });
    }

    document.addEventListener('click', closeAllDropdowns);
  }

  /* ── Mobile nav accordion groups ─────────────────────── */
  function initMobileNavGroups() {
    document.querySelectorAll('.nav-drawer__group-toggle').forEach(btn => {
      btn.addEventListener('click', () => {
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        btn.setAttribute('aria-expanded', expanded ? 'false' : 'true');
        btn.nextElementSibling?.classList.toggle('is-open');
      });
    });
  }

  /* ── FAQ accordion ─────────────────────────────────────── */
  function initFAQ() {
    document.querySelectorAll('.faq__question').forEach(btn => {
      btn.addEventListener('click', () => {
        const expanded = btn.getAttribute('aria-expanded') === 'true';
        document.querySelectorAll('.faq__question').forEach(b => {
          b.setAttribute('aria-expanded','false');
          b.nextElementSibling?.classList.remove('is-open');
        });
        if (!expanded) {
          btn.setAttribute('aria-expanded','true');
          btn.nextElementSibling?.classList.add('is-open');
        }
      });
    });
  }

  /* ── Before/after slider ───────────────────────────────── */
  function initBeforeAfter() {
    const slider = document.querySelector('.before-after__slider');
    if (!slider) return;
    const afterImg = slider.querySelector('.before-after__after');
    const divider  = slider.querySelector('.before-after__divider');
    const handle   = slider.querySelector('.before-after__handle');
    let dragging   = false;
    const setPos = (pct) => {
      pct = Math.max(5, Math.min(95, pct));
      const p = pct + '%';
      afterImg.style.clipPath = `inset(0 ${100 - pct}% 0 0)`;
      divider.style.left = p;
      handle.style.left  = p;
    };
    const getPercent = (x) => ((x - slider.getBoundingClientRect().left) / slider.offsetWidth) * 100;
    slider.addEventListener('mousedown',  e => { dragging = true; setPos(getPercent(e.clientX)); });
    slider.addEventListener('touchstart', e => { dragging = true; setPos(getPercent(e.touches[0].clientX)); }, { passive: true });
    window.addEventListener('mousemove',  e => { if (dragging) setPos(getPercent(e.clientX)); });
    window.addEventListener('touchmove',  e => { if (dragging) setPos(getPercent(e.touches[0].clientX)); }, { passive: true });
    window.addEventListener('mouseup',   () => { dragging = false; });
    window.addEventListener('touchend',  () => { dragging = false; });
    setPos(50);
  }

  /* ── Gallery filter ────────────────────────────────────── */
  function initGalleryFilter() {
    const filterBtns = document.querySelectorAll('.filter-btn');
    const items      = document.querySelectorAll('.gallery-item');
    if (!filterBtns.length) return;
    filterBtns.forEach(btn => {
      btn.addEventListener('click', () => {
        filterBtns.forEach(b => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        const filter = btn.dataset.filter;
        items.forEach(item => { item.style.display = (filter === 'all' || item.dataset.category === filter) ? '' : 'none'; });
      });
    });
  }

  /* ── Product image gallery (swipe) ─────────────────────── */
  function initProductGallery() {
    const track = document.querySelector('.product-gallery__track');
    if (!track) return;
    const slides = track.querySelectorAll('.product-gallery__slide');
    const dotsWrap = document.querySelector('.product-gallery__dots');
    let dots = document.querySelectorAll('.product-gallery__dot');

    // Rebuild dots to match actual slide count (inject script may have added slides)
    if (dotsWrap) {
      dotsWrap.innerHTML = '';
      slides.forEach((_, i) => {
        const dot = document.createElement('span');
        dot.className = 'product-gallery__dot' + (i === 0 ? ' is-active' : '');
        dot.setAttribute('role', 'button');
        dot.setAttribute('aria-label', 'Go to image ' + (i + 1));
        dot.style.cursor = 'pointer';
        dot.addEventListener('click', () => scrollToSlide(i));
        dotsWrap.appendChild(dot);
      });
      dots = dotsWrap.querySelectorAll('.product-gallery__dot');
    } else if (slides.length > 1) {
      // Create dots container if missing
      const newDots = document.createElement('div');
      newDots.className = 'product-gallery__dots';
      newDots.setAttribute('aria-hidden', 'true');
      slides.forEach((_, i) => {
        const dot = document.createElement('span');
        dot.className = 'product-gallery__dot' + (i === 0 ? ' is-active' : '');
        dot.style.cursor = 'pointer';
        dot.addEventListener('click', () => scrollToSlide(i));
        newDots.appendChild(dot);
      });
      track.parentNode.appendChild(newDots);
      dots = newDots.querySelectorAll('.product-gallery__dot');
    }

    function scrollToSlide(idx) {
      if (!slides[idx]) return;
      slides[idx].scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' });
    }

    const updateDots = (idx) => dots.forEach((d, i) => d.classList.toggle('is-active', i === idx));

    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const idx = Array.from(slides).indexOf(entry.target);
        if (idx !== -1) updateDots(idx);
      });
    }, { root: track, threshold: 0.5 });
    slides.forEach(s => observer.observe(s));

    // Expose scrollToSlide globally for variant picker
    window._galleryScrollTo = scrollToSlide;
  }

  /* ── Upload widget (product page) ─────────────────────── */
  function initUploadWidget() {
    const widget  = document.querySelector('.upload-widget');
    const input   = document.getElementById('PetPhotoUpload');
    const preview = widget?.querySelector('.upload-widget__preview');
    const img     = preview?.querySelector('img');
    if (!widget || !input) return;
    widget.addEventListener('click', () => input.click());
    widget.addEventListener('dragover', e => { e.preventDefault(); widget.classList.add('drag-over'); });
    widget.addEventListener('dragleave', () => widget.classList.remove('drag-over'));
    widget.addEventListener('drop', e => {
      e.preventDefault(); widget.classList.remove('drag-over');
      const file = e.dataTransfer.files[0];
      if (file) showPreview(file);
    });
    input.addEventListener('change', () => { if (input.files[0]) showPreview(input.files[0]); });
    function showPreview(file) {
      if (!file.type.startsWith('image/')) return;
      const reader = new FileReader();
      reader.onload = e => {
        if (img) img.src = e.target.result;
        if (preview) preview.style.display = 'block';
        widget.querySelector('.upload-widget__sub').textContent = file.name;
      };
      reader.readAsDataURL(file);
    }
  }

  /* ── Variant picker ────────────────────────────────────── */
  function initVariantPicker() {
    const variantDataEl = document.getElementById('ProductVariantData');
    const imageDataEl = document.getElementById('ProductImageData');
    if (!variantDataEl) return;

    let variants, imageMap;
    try {
      variants = JSON.parse(variantDataEl.textContent);
      imageMap = imageDataEl ? JSON.parse(imageDataEl.textContent) : [];
    } catch (e) { return; }

    const hiddenId = document.querySelector('.product-form input[name="id"]');
    const priceEl = document.querySelector('.product-info__price');
    const atcBtn = document.querySelector('.atc-btn');

    function getSelectedOptions() {
      const opts = [];
      document.querySelectorAll('.variant-picker__options').forEach(group => {
        const checked = group.querySelector('input:checked');
        if (checked) opts.push(checked.value);
      });
      return opts;
    }

    function findVariant(opts) {
      return variants.find(v =>
        v.options.length === opts.length && v.options.every((o, i) => o === opts[i])
      );
    }

    function updateVariant() {
      const opts = getSelectedOptions();
      const variant = findVariant(opts);
      if (!variant) return;

      // Update hidden form ID
      if (hiddenId) hiddenId.value = variant.id;

      // Update displayed price
      if (priceEl) {
        const priceMeta = priceEl.querySelector('meta[itemprop="price"]');
        if (priceMeta) priceMeta.content = (variant.price / 100).toFixed(2);
        const priceText = priceEl.childNodes;
        for (const node of priceText) {
          if (node.nodeType === 3 && node.textContent.trim().startsWith('$')) {
            node.textContent = variant.priceFormatted + '\n      ';
            break;
          }
        }
      }

      // Update ATC button
      if (atcBtn) {
        if (variant.available) {
          atcBtn.disabled = false;
          atcBtn.textContent = 'Add to Cart \u2014 ' + variant.priceFormatted + ' CAD';
        } else {
          atcBtn.disabled = true;
          atcBtn.textContent = 'Sold Out';
        }
      }

      // Scroll gallery to mockup slide for this variant (if available)
      if (window._galleryScrollTo) {
        const variantTitle = variant.title || '';
        // Extract size like "10×10" from variant title → normalize to "10x10"
        const sizeMatch = variantTitle.match(/(\d+)\D+(\d+)/);
        const sizeKey = sizeMatch ? sizeMatch[1] + 'x' + sizeMatch[2] : null;
        const mockupMap = window._mockupSlideMap || {};

        if (sizeKey && mockupMap[sizeKey] !== undefined) {
          window._galleryScrollTo(mockupMap[sizeKey]);
        } else {
          // Fallback: scroll to portrait slide (index 0)
          window._galleryScrollTo(0);
        }
      }

      // Update sticky price
      const stickyPrice = document.getElementById('StickyPrice');
      if (stickyPrice) stickyPrice.textContent = variant.priceFormatted + ' CAD';
    }

    document.querySelectorAll('.variant-option').forEach(opt => {
      opt.addEventListener('click', () => {
        const group = opt.closest('.variant-picker__options');
        group.querySelectorAll('.variant-option').forEach(o => o.classList.remove('is-selected'));
        opt.classList.add('is-selected');
        updateVariant();
      });
    });

    // Set initial state
    updateVariant();
  }

  /* ── Sticky CTA bar ────────────────────────────────────── */
  function initStickyCta() {
    const bar = document.getElementById('StickyCta');
    if (!bar) return;
    const sentinel = document.querySelector('.hero, .product-layout');
    if (!sentinel) return;
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        bar.classList.toggle('is-visible', !entry.isIntersecting);
        bar.setAttribute('aria-hidden', entry.isIntersecting ? 'true' : 'false');
      });
    }, { threshold: 0 });
    observer.observe(sentinel);
    const stickyAtc = document.getElementById('StickyAddToCart');
    const mainForm  = document.querySelector('.product-form');
    if (stickyAtc && mainForm) {
      stickyAtc.addEventListener('click', () => {
        mainForm.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => mainForm.querySelector('[type="submit"]')?.click(), 600);
      });
    }
  }

  /* ── Scroll-reveal animations ──────────────────────────── */
  function initScrollReveal() {
    const els = document.querySelectorAll('[data-reveal]');
    if (!els.length) return;
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('is-revealed');
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.08, rootMargin: '0px 0px -32px 0px' });
    els.forEach(el => observer.observe(el));
  }

  /* ── Cart count ────────────────────────────────────────── */
  function refreshCartCount() {
    fetch('/cart.js')
      .then(r => r.json())
      .then(cart => {
        document.querySelectorAll('[data-cart-count]').forEach(el => {
          el.textContent = cart.item_count;
          el.style.display = cart.item_count > 0 ? '' : 'none';
        });
        document.querySelectorAll('.cart-count').forEach(el => {
          el.textContent = cart.item_count;
          el.style.display = cart.item_count > 0 ? '' : 'none';
        });
      })
      .catch(err => console.error('[CartCount]', err));
  }

  /* ── Cart Drawer ───────────────────────────────────────── */
  class CartDrawer {
    constructor() {
      this.drawer = document.getElementById('CartDrawer');
      if (!this.drawer) return;
      this._cartData = null;
      this.init();
    }
    init() {
      document.querySelectorAll('[data-open-cart]').forEach(btn => {
        btn.addEventListener('click', e => { e.preventDefault(); this.open(); });
      });
      this.drawer.querySelector('[data-cart-close]')?.addEventListener('click', () => this.close());
      this.drawer.querySelector('[data-cart-overlay]')?.addEventListener('click', () => this.close());
      document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && this.drawer.classList.contains('is-open')) this.close();
      });
      document.addEventListener('cart:refresh', () => this.refresh());
    }
    async open() {
      await this.refresh();
      this.drawer.classList.add('is-open');
      document.body.style.overflow = 'hidden';
    }
    close() {
      this.drawer.classList.remove('is-open');
      document.body.style.overflow = '';
    }
    async refresh() {
      try {
        const resp = await fetch('/cart.json');
        this._cartData = await resp.json();
        this.render(this._cartData);
        refreshCartCount();
      } catch (err) { console.error('[CartDrawer]', err); }
    }
    fmt(price) { return '$' + (price / 100).toFixed(2); }
    render(cart) {
      const body   = this.drawer.querySelector('[data-cart-body]');
      const footer = this.drawer.querySelector('[data-cart-footer]');
      if (!body) return;

      if (cart.items.length === 0) {
        body.innerHTML = `
          <div class="cart-empty-state">
            <p class="cart-empty-state__icon">🛒</p>
            <p class="cart-empty-state__title">Your cart is empty</p>
            <p class="cart-empty-state__sub">Create your first pet portrait!</p>
            <a href="/collections/all" class="btn btn--primary" data-cart-close>Shop Now</a>
          </div>`;
        if (footer) footer.innerHTML = '';
        body.querySelector('[data-cart-close]')?.addEventListener('click', () => this.close());
        return;
      }

      body.innerHTML = cart.items.map(item => `
        <div class="cart-item" data-item-key="${item.key}">
          <img src="${item.image || ''}" alt="${item.title}" class="cart-item__img" width="72" height="72" loading="lazy">
          <div class="cart-item__info">
            <p class="cart-item__title">${item.product_title}</p>
            ${item.variant_title !== 'Default Title' ? `<p class="cart-item__variant">${item.variant_title}</p>` : ''}
            <div class="cart-item__footer">
              <div class="cart-item__qty">
                <button class="qty-btn" data-key="${item.key}" data-qty="${item.quantity - 1}" aria-label="Decrease">−</button>
                <span class="cart-item__qty-num">${item.quantity}</span>
                <button class="qty-btn" data-key="${item.key}" data-qty="${item.quantity + 1}" aria-label="Increase">+</button>
              </div>
              <span class="cart-item__price">${this.fmt(item.final_line_price)} CAD</span>
            </div>
          </div>
        </div>`).join('');

      if (footer) {
        footer.innerHTML = `
          <div class="cart-subtotal">
            <span>Subtotal</span>
            <span>${this.fmt(cart.total_price)} CAD</span>
          </div>
          <p class="cart-drawer__shipping">Taxes & shipping calculated at checkout</p>
          <a href="/checkout" class="btn btn--primary btn--full btn--large">Checkout →</a>
          <a href="/cart" class="btn btn--ghost btn--full cart-drawer__links">View Full Cart</a>`;
      }

      // Qty buttons
      body.querySelectorAll('.qty-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
          const key = btn.dataset.key;
          const qty = Math.max(0, parseInt(btn.dataset.qty));
          try {
            await fetch('/cart/change.js', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ id: key, quantity: qty })
            });
            this.refresh();
          } catch (err) { console.error('[CartQty]', err); }
        });
      });
    }
  }

  /* Legacy PortraitPreview class removed — replaced by React portrait-flow widget */

  /* ── Init ──────────────────────────────────────────────── */
  document.addEventListener('DOMContentLoaded', () => {
    initLazyLoad();
    initNavDrawer();
    initNavDropdowns();
    initMobileNavGroups();
    initFAQ();
    initBeforeAfter();
    initGalleryFilter();
    initProductGallery();
    initUploadWidget();
    initVariantPicker();
    initStickyCta();
    initScrollReveal();
    refreshCartCount();
    new CartDrawer();

    // Disable right-click save on portrait/product images
    document.addEventListener('contextmenu', function(e) {
      if (e.target.tagName === 'IMG' && (
        e.target.closest('.product-gallery') ||
        e.target.closest('.product-gallery__slide') ||
        e.target.closest('#portrait-flow-root') ||
        e.target.closest('.cart-item')
      )) {
        e.preventDefault();
      }
    });
    // PortraitPreview removed — React portrait-flow widget handles this
  });

})();
