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

`_layouts/post.html` includes it **twice**, and CSS shows exactly one:

| Width | Where it appears |
|---|---|
| >= 1200px | sticky rail in the left column (`.post-toc-side`), following the scroll |
| < 1200px | card in the flow above the article |

The rail fills the two columns the article already left empty on its left, so the article's own
position is unchanged at every width. The hidden copy is `display: none`, so screen readers never
encounter both.

Two constraints worth knowing before touching this:

- `position: sticky` is inert inside an ancestor whose `overflow` is not `visible`, and the theme
  sets `overflow: hidden` on `article`. `less/post-toc.less` releases that above 1200px; putting it
  back will silently stop the rail from sticking.
- The rail is only ~137px wide. `.post-row` must stay `display: flex` there, because a floated
  Bootstrap column is only as tall as its own content and would give the rail nothing to travel along.

The theme also ships its own JS side catalog (`page.catalog`, right-hand side, `less/side-catalog.less`).
It is unused — no post sets `catalog: true` — and it bypasses the skip rules below, so prefer this one.

The TOC is skipped when any of these hold:

| Condition | Rationale |
|---|---|
| Body contains `**目录**` or `**文章目录**` | the ~87 older posts hand-rolled their own |
| Body contains a kramdown `{:toc}` (emits `id="markdown-toc"`) | avoids two TOCs |
| Fewer than 3 headings in `h2`..`h4` | a TOC of one or two links is just noise |
| `toc: false` in front matter | manual opt-out |
| `multilingual: true` | content holds two languages, headings would interleave |

Anchor slugs follow GitHub's rules, identical to what the old Markdown All in One plugin produced,
so links in the hand-written TOCs of older posts remain valid:

| Heading | Anchor |
|---|---|
| `#### 部署 metallb` | `#部署-metallb` |
| `#### rke 可以直接移除两个 master 节点吗？` | `#rke-可以直接移除两个-master-节点吗` |

To change the depth range, edit the `'2' or '3' or '4'` level tests in `_includes/post-toc.html`
and add a matching `.post-toc-d3` rule. Note that kramdown's own `{:toc}` is *not* used here;
`{:toc levels="..."}` does not work as an IAL — it leaks through as an HTML attribute — and the
global `kramdown.toc_levels` option would only apply to `{:toc}`, not to this include.

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
