const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/document_preview.js'), 'utf8');
let previewIntl = Intl;

class Element {
  constructor() {
    this.listeners = new Map();
    this.classes = new Set();
    this.classList = {contains: name => this.classes.has(name),
      toggle: (name, present) => present ? this.classes.add(name) : this.classes.delete(name)};
    this.value = '';
    this.textContent = '';
  }
  addEventListener(name, listener) { this.listeners.set(name, listener); }
  fire(name, modifiers = {}) {
    const event = {...modifiers, preventDefault() { this.prevented = true; }};
    this.listeners.get(name)?.(event);
    return event;
  }
  setAttribute() {}
  scrollIntoView() { this.scrolled = true; }
}

function preview(original) {
  const parts = Object.fromEntries([
    'body', 'search', 'count', 'feedback', 'prev', 'next', 'copy', 'copy-selection',
  ].map(name => [name, new Element()]));
  const body = parts.body;
  const text = value => ({textContent: value, parentNode: body});
  body.nodes = [text(original)];
  Object.defineProperty(body, 'textContent', {
    get: () => body.nodes.map(node => node.textContent).join(''),
    set: value => { body.nodes = [text(value)]; },
  });
  body.querySelectorAll = () => body.nodes.filter(node => node instanceof Element);
  body.replaceChildren = frag => { body.nodes = frag.children; };
  // The old script walked and replaced text nodes. Support its path as well so
  // the regressions demonstrate incorrect behavior, not a missing test stub.
  body.replaceChild = (frag, node) => body.nodes.splice(body.nodes.indexOf(node), 1, ...frag.children);
  const root = {querySelector: sel => parts[sel.slice(19, -1)] || null,
    dataset: {msgCopied: 'Copied.', msgEmpty: 'No selection.'}};
  const timers = new Map();
  let serial = 0;
  const clipboard = [];
  vm.runInNewContext(script, {
    Intl: previewIntl,
    document: {querySelector: () => root,
      createElement: () => new Element(), createTextNode: text,
      createDocumentFragment: () => ({children: [], append(node) { this.children.push(node); }}),
      createTreeWalker: () => {
        const nodes = body.nodes.values();
        return {nextNode() { this.currentNode = nodes.next().value; return this.currentNode; }};
      }},
    NodeFilter: {SHOW_TEXT: 4},
    navigator: {clipboard: {writeText: async value => clipboard.push(value)}},
    window: {setTimeout: fn => { timers.set(++serial, fn); return serial; },
      clearTimeout: id => timers.delete(id)},
  });
  return {
    parts, clipboard,
    edit(value) { parts.search.value = value; parts.search.fire('input'); },
    flush() { for (const fn of timers.values()) fn(); timers.clear(); },
    marks() { return body.querySelectorAll().map(mark => mark.textContent); },
  };
}

test('Unicode case folding preserves source offsets and copy retains the original text', async () => {
  const original = 'İ prefix: FOO foo <b>visible characters</b>';
  const p = preview(original);
  p.edit('foo');
  p.flush();
  assert.deepEqual(p.marks(), ['FOO', 'foo']);
  assert.equal(p.parts.count.textContent, '1 / 2');
  assert.equal(p.parts.body.textContent, original);
  p.parts.copy.fire('click');
  assert.deepEqual(p.clipboard, [original]);
});

test('canonical OCR spellings match in both directions, retain accents and copy the original text', async () => {
  const canonical = 'Überprüfung';
  const decomposed = canonical.normalize('NFD');
  const original = '🧾 İ: ' + decomposed + ' / ' + canonical + ' / Uberprufung';
  const p = preview(original);
  for (const needle of [canonical, decomposed]) {
    p.edit(needle);
    p.flush();
    assert.deepEqual(p.marks(), [decomposed, canonical]);
    assert.equal(p.parts.count.textContent, '1 / 2');
    assert.equal(p.parts.body.textContent, original);
    p.parts.copy.fire('click');
  }
  assert.deepEqual(p.clipboard, [original, original]);
  p.edit('Uberprufung');
  p.flush();
  assert.deepEqual(p.marks(), ['Uberprufung']);
});

test('multi-unit canonical decompositions are valid and compatibility forms stay distinct', () => {
  const greek = preview('ᾂ');
  greek.edit('ᾂ'.normalize('NFD'));
  greek.flush();
  assert.deepEqual(greek.marks(), ['ᾂ']);
  const hangul = preview('가 / 가');
  hangul.edit('가');
  hangul.flush();
  assert.deepEqual(hangul.marks(), ['가', '가']);
  const ligature = preview('ﬀ / ff');
  ligature.edit('ff');
  ligature.flush();
  assert.deepEqual(ligature.marks(), ['ff']);
});

test('rejected partial graphemes cannot swallow a later valid whole match', () => {
  const p = preview('👩‍👩👩👩');
  p.edit('👩👩');
  p.flush();
  assert.deepEqual(p.marks(), ['👩👩']);
  assert.equal(p.parts.body.nodes[0].textContent, '👩‍👩');
});

test('a browser without grapheme segmentation keeps literal matching and copy', () => {
  previewIntl = {};
  try {
    const original = 'U\u0308berpru\u0308fung';
    const p = preview(original);
    p.edit('Überprüfung');
    p.flush();
    assert.deepEqual(p.marks(), []);
    p.edit(original);
    p.flush();
    assert.deepEqual(p.marks(), [original]);
    p.parts.copy.fire('click');
    assert.deepEqual(p.clipboard, [original]);
  } finally {
    previewIntl = Intl;
  }
});

test('quoted punctuation is found literally and not interpreted as a pattern', () => {
  const needle = '[a-b].*\\';
  const original = 'İ before ' + needle + ' then AB or a-b.';
  const p = preview(original);
  p.edit(needle);
  p.flush();
  assert.deepEqual(p.marks(), [needle]);
  assert.equal(p.parts.count.textContent, '1 / 1');
  assert.equal(p.parts.body.textContent, original);
});

test('clicking before debounce applies the new query and chooses first or last immediately', () => {
  const p = preview('Alpha Beta beta Beta Alpha');
  p.edit('Alpha');
  p.flush();
  p.edit('Beta');
  p.parts.next.fire('click');
  assert.deepEqual(p.marks(), ['Beta', 'beta', 'Beta']);
  assert.equal(p.parts.count.textContent, '1 / 3');
  p.parts.next.fire('click');
  p.flush();
  assert.equal(p.parts.count.textContent, '2 / 3');
  p.edit('Alpha');
  p.parts.prev.fire('click');
  p.flush();
  assert.equal(p.parts.count.textContent, '2 / 2');
  p.edit('');
  p.parts.next.fire('click');
  assert.deepEqual(p.marks(), []);
  assert.equal(p.parts.count.textContent, '');
});

test('Enter and Shift+Enter wrap without intercepting IME or modified shortcuts', () => {
  const p = preview('Alpha Alpha Alpha');
  p.edit('Alpha');
  const composing = p.parts.search.fire('keydown', {key: 'Enter', isComposing: true});
  const modified = p.parts.search.fire('keydown', {key: 'Enter', ctrlKey: true});
  assert.equal(composing.prevented, undefined);
  assert.equal(modified.prevented, undefined);
  assert.deepEqual(p.marks(), []);
  const reverse = p.parts.search.fire('keydown', {key: 'Enter', shiftKey: true});
  assert.equal(reverse.prevented, true);
  assert.equal(p.parts.count.textContent, '3 / 3');
  const advance = p.parts.search.fire('keydown', {key: 'Enter'});
  assert.equal(advance.prevented, true);
  assert.equal(p.parts.count.textContent, '1 / 3');
});

test('a pasted query longer than the bounded preview clears stale matches without compiling it', () => {
  const p = preview('Alpha');
  p.edit('Alpha');
  p.flush();
  p.edit('['.repeat(100000));
  p.parts.next.fire('click');
  p.flush();
  assert.deepEqual(p.marks(), []);
  assert.equal(p.parts.body.textContent, 'Alpha');
  assert.equal(p.parts.count.textContent, '0 / 0');
});
