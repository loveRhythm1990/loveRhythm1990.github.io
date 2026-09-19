/*!
 * Clean Blog v1.0.0 (http://startbootstrap.com)
 * Copyright 2015 Start Bootstrap
 * Licensed under Apache 2.0 (https://github.com/IronSummitMedia/startbootstrap/blob/gh-pages/LICENSE)
 */

 /*!
 * Hux Blog v1.6.0 (http://startbootstrap.com)
 * Copyright 2016 @huxpro
 * Licensed under Apache 2.0 
 */

// Give every tag a stable, readable colour without requiring a config entry.
// An inline --tag or --hero value from _config.yml remains the explicit override.
(function(window, document) {
    'use strict';

    var palette = [
        '#1565C0', '#00695C', '#6A1B9A', '#AD3F00',
        '#283593', '#2E6B35', '#7B1F4A', '#455A64',
        '#8A4B08', '#005B96', '#5D4037', '#006D77',
        '#8E244D', '#37474F', '#3F51B5', '#7A3E00'
    ];

    function colorForTag(tag) {
        var hash = 2166136261;
        var value = String(tag || '');
        var i;

        for (i = 0; i < value.length; i++) {
            hash ^= value.charCodeAt(i);
            hash += (hash << 1) + (hash << 4) + (hash << 7) +
                    (hash << 8) + (hash << 24);
        }

        return palette[(hash >>> 0) % palette.length];
    }

    function applyTagColors(root) {
        var elements = (root || document).querySelectorAll('[data-tag-color]');
        var i;

        for (i = 0; i < elements.length; i++) {
            var element = elements[i];
            var property = element.getAttribute('data-tag-color-property') || '--tag';

            if (!element.style.getPropertyValue(property)) {
                element.style.setProperty(property, colorForTag(element.getAttribute('data-tag-color')));
            }
        }
    }

    window.TagColors = {
        colorFor: colorForTag,
        apply: applyTagColors
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() {
            applyTagColors(document);
        });
    } else {
        applyTagColors(document);
    }
})(window, document);

// Tooltip Init
// Unuse by Hux since V1.6: Titles now display by default so there is no need for tooltip
// $(function() {
//     $("[data-toggle='tooltip']").tooltip();
// });


// make all images responsive
/* 
 * Unuse by Hux
 * actually only Portfolio-Pages can't use it and only post-img need it.
 * so I modify the _layout/post and CSS to make post-img responsive!
 */
// $(function() {
//  $("img").addClass("img-responsive");
// });

// responsive tables
$(document).ready(function() {
    $("table").wrap("<div class='table-responsive'></div>");
    $("table").addClass("table");
});

// responsive embed videos
$(document).ready(function() {
    $('iframe[src*="youtube.com"]').wrap('<div class="embed-responsive embed-responsive-16by9"></div>');
    $('iframe[src*="youtube.com"]').addClass('embed-responsive-item');
    $('iframe[src*="vimeo.com"]').wrap('<div class="embed-responsive embed-responsive-16by9"></div>');
    $('iframe[src*="vimeo.com"]').addClass('embed-responsive-item');
});

// Navigation: pin the bar after the hero scrolls away (desktop only).
jQuery(document).ready(function($) {
    var MQL = 1170;

    if ($(window).width() > MQL) {
        var bannerHeight = $('.intro-header .container').height();

        function introHeaderClear() {
            var el = document.querySelector('.intro-header');
            if (!el) return true;
            return el.getBoundingClientRect().bottom <= 0;
        }

        $(window).on('scroll', function() {
            var currentTop = $(window).scrollTop(),
                $catalog = $('.side-catalog'),
                $nav = $('.navbar-custom');

            if (introHeaderClear()) {
                $nav.addClass('is-fixed');
            } else {
                $nav.removeClass('is-fixed');
            }

            $catalog.show();
            if (currentTop > (bannerHeight + 41)) {
                $catalog.addClass('fixed');
            } else {
                $catalog.removeClass('fixed');
            }
        });

        $(window).trigger('scroll');
    }
});
