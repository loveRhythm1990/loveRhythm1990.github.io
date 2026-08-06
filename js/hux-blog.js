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