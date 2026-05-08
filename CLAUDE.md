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
