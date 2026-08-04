#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { parseHTML } = require('linkedom');

function run() {
  const root = path.join(__dirname, '..');
  const html = `<!DOCTYPE html><body>
    <div class="post-container">
      <img src="/pics/son_1.jpg" alt="sample" width="320">
      <img width="0" height="0" src="data:," alt="track" id="zero-img">
      <a href="https://example.com"><img src="/pics/B-tree.svg" alt="linked" width="120"></a>
    </div>
  </body>`;

  const { window } = parseHTML(html);
  const { document } = window;
  global.window = window;
  global.document = document;

  function keydown(key) {
    const event = new window.Event('keydown', { bubbles: true });
    event.key = key;
    document.dispatchEvent(event);
  }

  const script = fs.readFileSync(path.join(root, 'js/post-lightbox.js'), 'utf8');
  // eslint-disable-next-line no-eval
  eval(script);

  const failures = [];
  function assert(name, ok) {
    if (!ok) failures.push(name);
  }

  function click(el) {
    if (typeof el.click === 'function') {
      el.click();
      return;
    }
    el.dispatchEvent(new window.Event('click', { bubbles: true, cancelable: true }));
  }

  const overlay = document.querySelector('.post-lightbox');
  const full = document.querySelector('.post-lightbox-img');
  const sample = document.querySelector('.post-container img[width="320"]');
  const zero = document.getElementById('zero-img');
  const linked = document.querySelector('.post-container a img');

  assert('overlay created', !!overlay && !!full);
  assert('initially hidden', overlay.hidden === true);

  click(sample);
  assert('opens on image click', overlay.hidden === false);
  assert('locks body scroll', document.body.classList.contains('post-lightbox-open'));
  assert('uses image src', full.src.indexOf('son_1.jpg') !== -1);

  click(zero);
  assert('skips zero-size tracking pixel', overlay.hidden === false);

  keydown('Escape');
  assert('closes on Escape', overlay.hidden === true);
  assert('restores body scroll', !document.body.classList.contains('post-lightbox-open'));

  click(linked);
  assert('opens linked image instead of navigating', overlay.hidden === false);
  assert('linked image src', full.src.indexOf('B-tree.svg') !== -1);

  click(document.querySelector('.post-lightbox-close'));
  assert('closes on close button', overlay.hidden === true);

  click(sample);
  click(overlay);
  assert('closes on backdrop click', overlay.hidden === true);

  if (failures.length) {
    console.error('FAIL:', failures.join(', '));
    process.exit(1);
  }

  console.log('PASS: post-lightbox integration (' + (9) + ' checks)');
}

run();
