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

    // A heading counts as current once it reaches this line, kept just below the
    // rail's own sticky offset so the switch happens as the heading meets the top
    // of the rail rather than while it is still mid-screen.
    var LINE = 96;

    var current = null;
    var queued = false;

    function activeEntry() {
        // Measured live rather than cached: images and web fonts settle after this
        // script runs, and every cached offset would be stale by then.
        var found = null;
        for (var i = 0; i < entries.length; i++) {
            if (entries[i].heading.getBoundingClientRect().top > LINE) break;
            found = entries[i];
        }
        // The last section is usually too short to ever reach the line, so anchor
        // it to the bottom of the document instead.
        var atBottom = window.innerHeight + window.pageYOffset >=
            document.documentElement.scrollHeight - 4;
        return atBottom ? entries[entries.length - 1] : found;
    }

    // The rail scrolls internally once a TOC outgrows the viewport. scrollTop is
    // set directly because scrollIntoView would scroll the window along with it.
    function reveal(item) {
        if (rail.scrollHeight <= rail.clientHeight) return;
        var top = item.offsetTop;
        var bottom = top + item.offsetHeight;
        if (top < rail.scrollTop) {
            rail.scrollTop = top - 8;
        } else if (bottom > rail.scrollTop + rail.clientHeight) {
            rail.scrollTop = bottom - rail.clientHeight + 8;
        }
    }

    function update() {
        queued = false;
        var next = activeEntry();
        if (next === current) return;
        if (current) current.item.classList.remove('active');
        current = next;
        if (!current) return;
        current.item.classList.add('active');
        reveal(current.item);
    }

    function schedule() {
        if (queued) return;
        queued = true;
        window.requestAnimationFrame(update);
    }

    // Below 1200px the rail is display:none and the in-flow card is shown instead,
    // where a highlight would scroll out of sight and mean nothing.
    var wide = window.matchMedia('(min-width: 1200px)');

    function sync() {
        if (wide.matches) {
            window.addEventListener('scroll', schedule, { passive: true });
            window.addEventListener('resize', schedule);
            update();
        } else {
            window.removeEventListener('scroll', schedule);
            window.removeEventListener('resize', schedule);
            if (current) {
                current.item.classList.remove('active');
                current = null;
            }
        }
    }

    if (wide.addEventListener) wide.addEventListener('change', sync);
    else wide.addListener(sync);

    sync();
    // Late-loading images shift every heading; re-run once the page settles.
    window.addEventListener('load', schedule);
})();
