// Highlights the entry matching the section being read in the sticky TOC rail
// built by _includes/post-toc.html. Styling lives in less/post-toc.less.
//
// The theme's own catalog highlights via jquery.nav.js, but that is wired to the
// `page.catalog` sidebar, which no post enables.

(function () {
    'use strict';

    var rail = document.querySelector('.post-toc-side .post-toc');
    if (!rail) return;

    // Reading the headings back out of the rail's own links keeps the level rules
    // in post-toc.html as the single source of truth: whatever it chose to list is
    // exactly what can light up.
    var entries = [];
    var links = rail.querySelectorAll('.post-toc-list a');
    for (var i = 0; i < links.length; i++) {
        var id = decodeURIComponent((links[i].getAttribute('href') || '').slice(1));
        var heading = id ? document.getElementById(id) : null;
        if (heading) entries.push({ item: links[i].parentNode, heading: heading });
    }
    if (!entries.length) return;

    // Matches @toc-rail-top in less/post-toc.less. Headings above this viewport
    // line count as read; kept a little below the rail's own top offset.
    var LINE = 100;

    var current = null;
    var queued = false;
    var tops = [];

    // Below 1200px the rail is display:none and the in-flow card is shown instead.
    var wide = window.matchMedia('(min-width: 1200px)');

    function measureTops() {
        // Document Y of each heading — refreshed on load/resize/font settle only.
        // scroll + getBoundingClientRect() every frame was forcing layout and made
        // the highlight (and the rail's internal scroll) hunt section boundaries.
        tops = entries.map(function (entry) {
            return entry.heading.getBoundingClientRect().top + window.pageYOffset;
        });
    }

    function activeEntry() {
        var y = window.pageYOffset + LINE;
        var found = null;
        for (var i = 0; i < entries.length; i++) {
            if (tops[i] > y) break;
            found = entries[i];
        }
        var atBottom = window.innerHeight + window.pageYOffset >=
            document.documentElement.scrollHeight - 4;
        return atBottom ? entries[entries.length - 1] : found;
    }

    function update() {
        queued = false;
        var next = activeEntry();
        if (next === current) return;
        if (current) current.item.classList.remove('active');
        current = next;
        if (!current) return;
        current.item.classList.add('active');
        // Do not auto-scroll the rail during page scroll — changing scrollTop here
        // was the twitch readers saw as the TOC "wobbling" in the gutter.
    }

    function schedule() {
        if (queued) return;
        queued = true;
        window.requestAnimationFrame(update);
    }

    function onResize() {
        measureTops();
        schedule();
    }

    function sync() {
        if (wide.matches) {
            window.addEventListener('scroll', schedule, { passive: true });
            window.addEventListener('resize', onResize);
            measureTops();
            update();
        } else {
            window.removeEventListener('scroll', schedule);
            window.removeEventListener('resize', onResize);
            if (current) {
                current.item.classList.remove('active');
                current = null;
            }
        }
    }

    function onMqChange() {
        sync();
    }

    if (wide.addEventListener) wide.addEventListener('change', onMqChange);
    else wide.addListener(onMqChange);

    sync();
    window.addEventListener('load', onResize);
})();
