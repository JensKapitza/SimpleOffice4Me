const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/quick_search_workspace.js'), 'utf8');

class Element {
  constructor() {
    this.children = [];
    this.listeners = new Map();
    this.nodes = new Map();
    this.dataset = {};
    this.attributes = {};
    this.value = '';
    this.textContent = '';
    this.hidden = true;
  }
  querySelector(selector) { return this.nodes.get(selector) || null; }
  querySelectorAll() { return []; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(kind, fn) { this.listeners.set(kind, fn); }
  fire(kind) { return this.listeners.get(kind)?.({stopPropagation() {}}); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren() { this.children = []; }
  byAttribute(name) {
    if (Object.hasOwn(this.attributes, name)) return this;
    return this.children.map(child => child.byAttribute(name)).find(Boolean);
  }
  focus() { this.focused = true; }
  select() { this.selected = true; }
}

function page({clipboard, lang = 'de'} = {}) {
  const root = new Element();
  const input = new Element();
  const list = new Element();
  const preview = new Element();
  root.dataset = {quickApi: '/search', quickPreview: '/preview', quickLang: lang};
  root.nodes.set('[data-quick-list]', list);
  root.nodes.set('[data-quick-preview]', preview);
  for (const name of ['title', 'kind', 'rows', 'actions', 'manual', 'feedback', 'close']) {
    preview.nodes.set(`[data-quick-preview-${name}]`, new Element());
  }
  // The close button is looked up from the workspace, like a normal DOM descendant.
  const lookup = root.querySelector.bind(root);
  root.querySelector = selector => lookup(selector) || preview.querySelector(selector);
  const requests = new Map();
  const pendingSearch = [];
  let searchTimer;
  const fetch = (url, options) => {
    const parsed = new URL(url, 'https://office.example');
    if (parsed.pathname === '/search') return Promise.resolve({
      ok: true, json: async () => ({matches: pendingSearch}),
    });
    // Simulate an abort that cannot stop an already resolving response.
    return new Promise(resolve => requests.set(parsed.searchParams.get('id'), {resolve, signal: options.signal}));
  };
  vm.runInNewContext(script, {
    AbortController, URL, URLSearchParams, fetch,
    navigator: {clipboard}, window: {location: {origin: 'https://office.example'},
      setTimeout: fn => { searchTimer = fn; return 1; }, clearTimeout() {}},
    document: {createElement: () => new Element(), querySelector: () => root,
      getElementById: id => id === 'quick-search-query' ? input : null},
  });
  const part = name => preview.querySelector(`[data-quick-preview-${name}]`);
  const tick = () => new Promise(resolve => setImmediate(resolve));
  return {
    preview, part, requests,
    async search(matches) {
      pendingSearch.splice(0, pendingSearch.length, ...matches);
      input.value = 'query';
      input.fire('input');
      searchTimer();
      await tick();
    },
    open(position) { return list.children[position].byAttribute('data-quick-preview-open').fire('click'); },
    finish(id, rows, ok = true, extra = {}) {
      requests.get(id).resolve({ok, json: async () => ({rows, url: `/record/${id}`, ...extra})});
      return tick();
    },
    action(label) { return part('actions').children.find(n => n.textContent === label); },
  };
}

const examples = [
  {kind: 'task', ref: 'first', label: 'Erster Treffer', url: '/record/first'},
  {kind: 'appointment', ref: 'second', label: 'Zweiter Treffer', url: '/record/second'},
];
const rows = [['Beschreibung', '<script>literal</script>'], ['Leer', ''], ['Termin', '2026-11-24']];
const formatted = 'Zweiter Treffer\nBeschreibung: <script>literal</script>\nTermin: 2026-11-24';

test('copies just the visible fields of the newest loaded preview, even if an older HTTP result arrives last', async () => {
  const copies = [];
  const p = page({clipboard: {writeText: async value => { copies.push(value); }}});
  await p.search(examples);
  p.open(0);
  p.open(1);
  assert.equal(p.requests.get('first').signal.aborted, true);
  assert.equal(p.action('Vorschau kopieren'), undefined);
  await p.finish('second', rows);
  await p.finish('first', [['Nicht mehr angezeigt', 'ignore me']]);
  assert.equal(p.part('title').textContent, 'Zweiter Treffer');
  assert.deepEqual(p.part('rows').children.map(n => n.textContent),
    ['Beschreibung', '<script>literal</script>', 'Termin', '2026-11-24']);
  await p.action('Vorschau kopieren').fire('click');
  assert.deepEqual(copies, [formatted]);
  assert.equal(p.part('feedback').textContent, 'Vorschau kopiert.');
  assert.equal(p.part('manual').hidden, true);
});

test('LAN fallback exposes selected plain text only on explicit click, clears it on close', async () => {
  const p = page({lang: 'en'});
  await p.search(examples);
  p.open(1);
  await p.finish('second', rows);
  assert.equal(p.part('manual').hidden, true);
  await p.action('Copy preview').fire('click');
  assert.equal(p.part('manual').value, formatted);
  assert.equal(p.part('manual').selected, true);
  assert.equal(p.part('manual').focused, true);
  assert.match(p.part('feedback').textContent, /select and copy the text in the field/);
  p.part('close').fire('click');
  assert.equal(p.preview.hidden, true);
  assert.equal(p.part('manual').hidden, true);
  assert.equal(p.part('manual').value, '');
});

test('clipboard denial falls back, but cannot publish old text after closing or switching previews', async () => {
  let reject;
  const p = page({clipboard: {writeText: () => new Promise((_, fail) => { reject = fail; })}});
  await p.search(examples);
  p.open(0);
  await p.finish('first', rows);
  const copy = p.action('Vorschau kopieren');
  const failed = copy.fire('click');
  reject(new Error('Denied'));
  await failed;
  assert.equal(p.part('manual').hidden, false);
  const superseded = copy.fire('click');
  p.open(1);
  reject(new Error('Denied'));
  await superseded;
  assert.equal(p.part('manual').hidden, true);
  assert.equal(p.part('manual').value, '');
  p.part('close').fire('click');
  await p.finish('second', rows);
  assert.equal(p.action('Vorschau kopieren'), undefined);
  assert.equal(p.part('rows').children.length, 0);
});

test('no preview-copy action for missing, inaccessible, or empty preview fields', async () => {
  const p = page();
  await p.search(examples);
  p.open(0);
  await p.finish('first', [], false);
  assert.match(p.part('rows').children[0].textContent, /nicht verfügbar/);
  assert.equal(p.action('Vorschau kopieren'), undefined);
  p.open(1);
  await p.finish('second', [['Leer', '']]);
  assert.equal(p.action('Vorschau kopieren'), undefined);
  assert.ok(p.action('Öffnen'));
});


test('document preview links only to its current same-origin Reader without exposing a page claim', async () => {
  const p = page({lang: 'en'});
  const docs = [
    {kind: 'document', ref: 'pdf', label: 'Scan', url: '/documents/pdf'},
    {kind: 'document', ref: 'second', label: 'Other', url: '/documents/second'},
    examples[1],
  ];
  await p.search(docs);
  p.open(0);
  p.open(1);
  await p.finish('second', [], true, {reader_url: '/documents/reader/second'});
  await p.finish('pdf', [], true, {reader_url: '/documents/reader/pdf'});
  assert.equal(p.action('Open').attributes.href, '/record/second');
  assert.equal(p.action('Open in Reader').attributes.href, 'https://office.example/documents/reader/second');
  p.open(0);
  await p.finish('pdf', [], true, {reader_url: '//different.example/reader'});
  assert.equal(p.action('Open in Reader'), undefined);
  p.open(2);
  await p.finish('second', [], true, {reader_url: '/documents/reader/second'});
  assert.equal(p.action('Open in Reader'), undefined);
});
