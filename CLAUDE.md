# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Jekyll-based technical blog hosted on GitHub Pages, primarily covering Kubernetes and Go programming topics. Based on the Hux Blog theme.

## Common Commands

```bash
# Local development (requires Jekyll installed)
jekyll serve --watch

# If jekyll-paginate plugin is missing
gem install jekyll-paginate

# Build assets (LESS compilation, JS minification)
npm install
grunt           # Build once
grunt watch     # Watch for changes

# Alternative preview methods from package.json
npm run py3view  # Python 3 HTTP server on port 8020
```

## Creating Blog Posts

Posts go in `_posts/` with filename format: `YYYY-MM-DD-title.markdown`

Required front matter:
```yaml
---
layout:     post
title:      "Post Title"
subtitle:   "Optional subtitle"
date:       YYYY-MM-DD HH:MM:SS
author:     "weak old dog"
header-img-credit: false
tags:
    - TagName
---
```

### Table of Contents

**Write nothing.** `_includes/post-toc.html` builds the TOC at build time. No plugin, no
JavaScript, no front matter.

It scans the *rendered* HTML for `h2`..`h4`, reusing the ids that kramdown's `auto_ids` already
emits, and outputs a flat list whose depth classes (`post-toc-d0/d1/d2`) are indented via CSS in
`less/post-toc.less`. Depth is relative to the shallowest heading in the post, so a post starting
at `###` gets a flush-left first level just like one starting at `##`.

`_layouts/post.html` renders it once into `_toc_html` and emits that **twice**; CSS shows exactly one:

| Width | Where it appears |
|---|---|
| >= 1370px | fixed in the viewport's left gutter (outside the centred container) |
| 1200–1369px | sticky rail in the left column (`.post-toc-side`), following the scroll |
| < 1200px | card in the flow above the article |

From 1370px up the rail sits in the margin the centred container leaves on the left — no longer
flush against the article. `js/post-toc.js` sets its `top` from `.intro-header`'s live bottom edge
so it never overlaps the hero; once the hero scrolls away it settles to navbar clearance (76px). Below
1370px the gutter is too narrow and the rail falls back to the in-column sticky layout. The article
keeps a `col-lg-offset-2` start so body text lines up with the post title; see `less/typography.less`
for the 80% width override on `.post-container.col-lg-9`.

`js/post-toc.js` highlights the entry being read, loaded from `footer.html` for `layout: post` only.
It reads the headings back out of the rail's own links, so the level rules in `post-toc.html` stay the
single source of truth; it scrolls the rail internally when a long TOC outgrows the viewport, and it
switches itself off below 1200px. The highlight changes colour but deliberately not weight — re-bolding
a wrapped CJK heading reflows it and the rail visibly twitches. Unminified on purpose: it is outside
the Grunt uglify pipeline, where a stale `.min` would go unnoticed.

Two constraints worth knowing before touching this:

- From 1370px up the rail is `position: fixed` in the viewport gutter; below that, through 1200px,
  it uses `position: sticky` inside `.post-toc-side`. Both need `article { overflow: visible }` above
  1200px — the theme sets `overflow: hidden` on `article`, which makes sticky inert.
- The rail is only ~150px wide at most. `.post-row` must stay `display: flex` through 1369px, because
  a floated column is only as tall as its own content and would give the sticky rail nothing to travel
  along; above 1370px the column is zero-width and the fixed rail no longer depends on it.

The theme also ships its own JS side catalog (`page.catalog`, right-hand side, `less/side-catalog.less`).
It is unused — no post sets `catalog: true` — and it bypasses the skip rules below, so prefer this one.

### The 87 posts with a hand-written TOC

Those posts open with a `**目录**` marker and a link list, left behind by the Markdown All in One
plugin. They are **not** skipped: their anchors are the same ids kramdown derives from the same
headings (verified: 708 of 709 match), so the rail reproduces them. `post.html` flags the container
with `has-legacy-toc` and `less/post-toc.less` hides that opening `<p>` plus the `<ul>` after it.

Nothing was deleted from the sources; reverting means dropping that one CSS rule and restoring a
skip condition. Two guards keep the flag honest, and removing either one loses TOCs silently:

- it matches the **first 80 characters** of the rendered body, so a post that merely writes **目录**
  further down keeps its opening paragraph;
- it requires a TOC to have actually been generated. Four posts (`pagecache`, `k8s-hostgw`, `btree`,
  `mmap`) have only two headings, fall under the minimum, and therefore keep their own list visible.

### Skip conditions

| Condition | Rationale |
|---|---|
| Fewer than 3 listed entries | a TOC of one or two links is just noise |
| Body contains a kramdown `{:toc}` (emits `id="markdown-toc"`) | avoids two TOCs |
| `toc: false` in front matter | manual opt-out |
| `multilingual: true` | content holds two languages, headings would interleave |

Anchor slugs follow GitHub's rules, identical to what the old plugin produced:

| Heading | Anchor |
|---|---|
| `#### 部署 metallb` | `#部署-metallb` |
| `#### rke 可以直接移除两个 master 节点吗？` | `#rke-可以直接移除两个-master-节点吗` |

### Depth

`h2`..`h6` are scanned, but only the shallowest three levels present are listed, so a post written
entirely in `h5`/`h6` (a couple of the older ones are) still gets a TOC while one spanning `h2`..`h6`
lists `h2`..`h4` — five levels of indentation would not survive a 137px rail. Widening that means
raising the `_depth <= 2` test and adding `.post-toc-d3`. kramdown's own `{:toc}` is deliberately
unused: `{:toc levels="..."}` is not a valid IAL — it leaks through as an HTML attribute — and the
global `kramdown.toc_levels` option applies only to `{:toc}`, not to this include.

## Archive page

`archive.html` renders a timeline grouped by year, styled in `less/archive.less`. `js/archive.js`
filters it by toggling `.d-none` — which carries `!important` — on each year `<section>` and on
`.item`, so those two hooks and the `data-tags` attribute have to survive any restyling. The rail is
drawn per item rather than once per section, so it stays continuous when the filter hides rows.

Tag colours come from `_config.yml`'s `tag-colors`, the same map the post header uses, so a tag reads
the same in both places. The template passes the colour in as an inline `--tag` custom property and
`color-mix()` derives the pill and node tints from it; unmapped tags arrive as grey.

`jquery.tagcloud.js` is deliberately no longer loaded — it tinted each tag inline by post count, and an
inline colour cannot be overridden from a stylesheet. The file remains in `js/` but is unreferenced.

## Post header (hero)

Flat-colour headers (`.hero-band`) keep the title on the tinted hero with compact padding.
Posts, Home, Archive, and About all use this class; the old full-bleed photo headers for pages
are gone. Posts may pass a tag colour as `--hero`; pages fall back to the site default teal.

The date shares the title's line — `.post-heading` is a baseline-aligned flex row.
The title is `flex: 0 1 auto; min-width: 0` so it wraps on narrow screens instead
of pushing the date off-screen; it must not grow or the date drifts to the column's
right edge, which lines up with nothing (header is `col-lg-8`, article `col-lg-9`).

Padding lives in `less/variables.less` as `@hero-compact-pad-*`. Top must clear
the overlaying navbar (~56px). `less/responsive.less` adds mobile overrides.

Note when screenshotting: headless Chrome clamps `--window-size` to a 500px minimum
width; use a 390px-wide `<iframe>` for real narrow layouts.

## Architecture

- `_config.yml` - Site configuration (title, SEO, analytics, sidebar, featured tags)
- `_posts/` - Markdown blog posts
- `_layouts/` - Page templates (post.html, page.html, keynote.html)
- `_includes/` - Reusable HTML components (nav, footer, header)
- `less/` - LESS stylesheets (compiled to CSS via Grunt)
- `js/` - JavaScript files
- `img/`, `pics/` - Images for posts and site

## Key Configuration

- `future: true` in _config.yml allows posts with future dates to be published
- Uses kramdown with GitHub Flavored Markdown
- Rouge for syntax highlighting
- Pagination set to 10 posts per page
