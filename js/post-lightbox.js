// Click-to-zoom for images in the article body (.post-container).
// Styling lives in less/post-lightbox.less.

(function () {
    'use strict';

    var container = document.querySelector('.post-container');
    if (!container) return;

    var overlay = document.createElement('div');
    overlay.className = 'post-lightbox';
    overlay.hidden = true;
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '放大图片');

    var closeBtn = document.createElement('button');
    closeBtn.type = 'button';
    closeBtn.className = 'post-lightbox-close';
    closeBtn.setAttribute('aria-label', '关闭');
    closeBtn.innerHTML = '&times;';

    var stage = document.createElement('div');
    stage.className = 'post-lightbox-stage';

    var full = document.createElement('img');
    full.className = 'post-lightbox-img';
    full.alt = '';

    stage.appendChild(full);
    overlay.appendChild(closeBtn);
    overlay.appendChild(stage);
    document.body.appendChild(overlay);

    var open = false;
    var lastFocus = null;

    function isZoomable(img) {
        if (!img || img.tagName !== 'IMG') return false;
        if (img.classList.contains('no-lightbox')) return false;
        if (img.closest('.no-lightbox')) return false;
        if (img.getAttribute('width') === '0' || img.getAttribute('height') === '0') {
            return false;
        }
        return true;
    }

    function show(img) {
        var src = img.currentSrc || img.src;
        if (!src) return;

        lastFocus = document.activeElement;
        full.src = src;
        full.alt = img.alt || '';
        overlay.hidden = false;
        document.body.classList.add('post-lightbox-open');
        open = true;
        closeBtn.focus();
    }

    function hide() {
        if (!open) return;
        overlay.hidden = true;
        document.body.classList.remove('post-lightbox-open');
        full.removeAttribute('src');
        open = false;
        if (lastFocus && lastFocus.focus) lastFocus.focus();
        lastFocus = null;
    }

    container.addEventListener('click', function (event) {
        var img = event.target.closest('img');
        if (!img || !container.contains(img) || !isZoomable(img)) return;

        event.preventDefault();
        event.stopPropagation();
        show(img);
    });

    closeBtn.addEventListener('click', function (event) {
        event.preventDefault();
        hide();
    });

    overlay.addEventListener('click', function (event) {
        if (event.target === overlay || event.target === stage) hide();
    });

    document.addEventListener('keydown', function (event) {
        if (open && event.key === 'Escape') {
            event.preventDefault();
            hide();
        }
    });
})();
