const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/documents_bulk.js'), 'utf8');

class Element {
  constructor(value = '') {
    this.value = value;
    this.listeners = new Map();
    this.attributes = new Map();
    this.hidden = true;
    this.disabled = false;
    this.style = {};
  }
  setAttribute(key, value) { this.attributes.set(key, value); }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  fire(name = 'click') { return this.listeners.get(name)?.({stopPropagation() {}}); }
  focus() { this.focused = true; }
}

function page(size, language = 'en') {
  const parts = Object.fromEntries([
    'count', 'status', 'progress', 'progress-bar', 'reload', 'tags', 'folder', 'tags-apply', 'move-apply',
  ].map(name => [name, new Element()]));
  parts.tags.value = 'selected-tag';
  parts.folder.value = 'archive/2026';
  const boxes = Array.from({length: size}, (_, i) => {
    const input = new Element('doc-' + i);
    input.checked = true;
    return input;
  });
  const bar = new Element();
  bar.querySelector = selector => parts[selector.slice(11, -1)] || null;
  bar.dataset = {bulkLanguage: language, bulkTagsUrl: '/documents/bulk-tags',
    bulkMoveUrl: '/documents/bulk-move'};
  const requests = [];
  const timers = [];
  let reloaded = 0;
  const fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({url, redirect: options.redirect, data: JSON.parse(options.body), resolve, reject});
  });
  vm.runInNewContext(script, {
    document: {querySelector: () => bar, querySelectorAll: () => boxes},
    window: {location: {reload: () => reloaded++}, setTimeout: fn => timers.push(fn)},
    fetch,
  });
  const tick = () => new Promise(resolve => setImmediate(resolve));
  return {
    parts, bar, boxes, requests, timers, reloads: () => reloaded,
    async reply(index, updated, failed) {
      requests[index].resolve({ok: true, json: async () => ({updated, failed})});
      await tick();
    },
    async drop(index) {
      requests[index].reject(new Error('network lost'));
      await tick();
    },
  };
}

test('confirmed partial result plus lost chunk leaves all outcomes visible and later documents untouched', async () => {
  const p = page(51);
  p.parts['tags-apply'].fire();
  p.parts['move-apply'].fire();
  p.parts['tags-apply'].fire();
  assert.equal(p.requests.length, 1);
  assert.equal(p.requests[0].url, '/documents/bulk-tags');
  assert.equal(p.requests[0].redirect, 'error');
  assert.equal(p.requests[0].data.tags, 'selected-tag');
  assert.equal(p.parts.status.textContent, 'Processing…');
  assert(p.boxes.every(box => box.disabled));
  assert(p.parts.tags.disabled && p.parts.folder.disabled && p.parts['move-apply'].disabled);
  const first = p.requests[0].data.ids;
  await p.reply(0, first.slice(0, 24), [{id: first[24], error: 'private backend detail'}]);
  assert.equal(p.requests.length, 2);
  await p.drop(1);
  assert.equal(p.requests.length, 2);
  assert.match(p.parts.status.textContent, /^24\/51 updated · 1 failed · 25 unconfirmed · 1 not sent/);
  assert.doesNotMatch(p.parts.status.textContent, /private backend detail/);
  assert.equal(p.parts.progress.attributes.get('aria-valuenow'), '98');
  assert.equal(p.parts.reload.hidden, false);
  assert.equal(p.timers.length, 0);
  p.parts['move-apply'].fire();
  assert.equal(p.requests.length, 2);
  p.parts.reload.fire();
  assert.equal(p.reloads(), 1);
});

test('only full confirmation refreshes automatically and keeps second actions locked while it waits', async () => {
  const p = page(2, 'de');
  p.parts['move-apply'].fire();
  await p.reply(0, p.requests[0].data.ids, []);
  assert.equal(p.parts.status.textContent, '2/2 aktualisiert.');
  assert.equal(p.parts.reload.hidden, true);
  p.parts['tags-apply'].fire();
  assert.equal(p.requests.length, 1);
  p.timers[0]();
  assert.equal(p.reloads(), 1);
});

test('a contradictory response cannot report or reload as success', async () => {
  const p = page(26);
  p.parts['move-apply'].fire();
  const ids = p.requests[0].data.ids;
  await p.reply(0, ids, [{id: ids[0]}]);
  assert.match(p.parts.status.textContent, /^0\/26 updated · 25 unconfirmed · 1 not sent/);
  assert.equal(p.requests.length, 1);
  assert.equal(p.timers.length, 0);
  assert.equal(p.parts.reload.hidden, false);
});

test('blank tags and blank folder are correctable without reloading or writing', async () => {
  const p = page(1, 'de');
  p.parts.tags.value = ' , , ';
  p.parts.folder.value = '  ';
  p.parts['tags-apply'].fire();
  assert.match(p.parts.status.textContent, /mindestens ein Tag/);
  assert(p.parts.tags.focused);
  p.parts['move-apply'].fire();
  assert.match(p.parts.status.textContent, /einen Zielordner/);
  assert(p.parts.folder.focused);
  assert(!p.parts['move-apply'].disabled && !p.boxes[0].disabled);
  assert.equal(p.requests.length, 0);
  p.parts.folder.value = 'archiv';
  p.parts['move-apply'].fire();
  await p.reply(0, p.requests[0].data.ids, []);
  assert.equal(p.requests[0].data.folder, 'archiv');
});

test('explicit per-document rejection remains visible without stopping later independent chunks', async () => {
  const p = page(26);
  p.parts['tags-apply'].fire();
  const ids = p.requests[0].data.ids;
  await p.reply(0, ids.slice(0, 24), [{id: ids[24]}]);
  await p.reply(1, p.requests[1].data.ids, []);
  assert.match(p.parts.status.textContent, /^25\/26 updated · 1 failed/);
  assert.doesNotMatch(p.parts.status.textContent, /unconfirmed|not sent/);
  assert.equal(p.parts.reload.hidden, false);
  assert.equal(p.timers.length, 0);
});
