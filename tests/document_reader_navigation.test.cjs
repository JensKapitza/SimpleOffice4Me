const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/document_reader.js'), 'utf8');

class Element {
  constructor(dataset = {}) {
    this.dataset = dataset;
    this.listeners = new Map();
    this.classes = new Set(['d-none']);
    this.classList = this.classes;
    this.classList.remove = name => this.classes.delete(name);
    this.style = {};
    this.offsetTop = 400;
    this.value = '';
    this.innerHTML = '';
  }
  addEventListener(name, fn) { this.listeners.set(name, fn); }
  fire(name = 'click') { return this.listeners.get(name)?.(); }
}

function reader(saved = {}) {
  const root = new Element({format: 'epub', documentVersion: 'version-1',
    savedLocator: JSON.stringify(saved), epubUrl: '/chapters', progressApi: '/progress',
    annotationApi: '/notes'});
  const names = ['status', 'position', 'error', 'epub-body', 'prev', 'next', 'note', 'add-note'];
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  const chapters = ['a', 'b', 'c'].map((id, index) => new Element({
    readerChapter: String(index), readerChapterId: id,
  }));
  root.querySelector = selector => elements[selector.slice(13, -1)] || null;
  root.querySelectorAll = selector => selector === '[data-reader-chapter]' ? chapters : [];
  const gets = [];
  const writes = [];
  const timers = new Map();
  const frames = [];
  const scrolls = [];
  let serial = 0;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const response = (body, ok = true) => ({ok, status: ok ? 200 : 503, json: async () => body});
  const fetch = (url, options) => {
    if (options.method === 'POST') {
      writes.push({url, data: JSON.parse(options.body)});
      return Promise.resolve(response({}));
    }
    return new Promise(resolve => gets.push({url, resolve}));
  };
  const win = new Element();
  win.location = {reload() {}};
  win.scrollTo = opts => scrolls.push(opts.top);
  vm.runInNewContext(script, {
    document: {getElementById: id => id === 'reader-root' ? root : null, querySelector: () => null},
    window: win, Headers, fetch, requestAnimationFrame: fn => frames.push(fn),
    setTimeout: (fn, delay) => { timers.set(++serial, {fn, delay}); return serial; },
    clearTimeout: id => timers.delete(id),
  });
  return {
    elements, chapters, gets, writes, frames, scrolls, win,
    async finish(index, id, ok = true) {
      const data = ok ? {id, html: '<p>' + id + '</p>'} : {error: id};
      gets[index].resolve(response(data, ok));
      await tick();
    },
    async runTimers(delay) {
      for (const [id, entry] of [...timers]) {
        if (entry.delay !== delay || !timers.has(id)) continue;
        timers.delete(id);
        await entry.fn();
      }
    },
  };
}

const locator = (chapter, chapter_index, offset = 0) =>
  ({chapter, chapter_index, anchor: '', cfi: '', offset});

test('rapid Next actions still request both chapters; the late result cannot replace the visible or saved chapter', async () => {
  const r = reader();
  await r.finish(0, 'a');
  r.win.scrollY = 570;
  r.win.fire('scroll');
  r.elements.next.fire();
  r.elements.next.fire();
  assert.deepEqual(r.gets.map(g => g.url), ['/chapters/0', '/chapters/1', '/chapters/2']);
  await r.finish(2, 'c');
  await r.finish(1, 'b');
  await r.runTimers(250);
  await r.runTimers(350);
  assert.equal(r.elements['epub-body'].innerHTML, '<p>c</p>');
  assert.equal(r.elements.position.value, '3');
  assert.equal(r.elements.status.textContent, 'Kapitel 3 / 3 · 100%');
  assert.deepEqual(r.writes, [{url: '/progress', data: {
    locator: locator('c', 2), percent: 100, document_version: 'version-1',
  }}]);
});

test('annotations and debounced progress use the displayed chapter during a pending request', async () => {
  const r = reader();
  assert.equal(r.elements['add-note'].disabled, true);
  r.win.fire('scroll');
  await r.runTimers(250);
  await r.runTimers(350);
  assert.deepEqual(r.writes, []);
  await r.finish(0, 'a');
  assert.equal(r.elements['add-note'].disabled, false);
  r.elements.next.fire();
  r.elements.note.value = 'visible chapter';
  await r.elements['add-note'].fire();
  await r.runTimers(350);
  assert.equal(r.writes.length, 2);
  assert.deepEqual(r.writes.map(x => [x.url, x.data.locator]), [
    ['/notes', locator('a', 0)],
    ['/progress', locator('a', 0)],
  ]);
});

test('old errors stay hidden; the latest failed selection returns controls to the displayed chapter', async () => {
  const r = reader();
  await r.finish(0, 'a');
  r.chapters[1].fire();
  r.chapters[2].fire();
  await r.finish(2, 'c');
  await r.finish(1, 'old failure', false);
  assert.equal(r.elements.error.classes.has('d-none'), true);
  r.elements.position.value = '1';
  r.elements.position.fire('change');
  await r.finish(3, 'latest failure', false);
  assert.equal(r.elements.error.textContent, 'latest failure');
  assert.equal(r.elements['epub-body'].innerHTML, '<p>c</p>');
  assert.equal(r.elements.position.value, '3');
  r.elements.prev.fire();
  assert.equal(r.gets[4].url, '/chapters/1');
  await r.finish(4, 'b');
  assert.equal(r.elements.error.classes.has('d-none'), true);
});

test('saved stable chapter ID restores its offset even after an index change; a stale frame cannot scroll a newer view', async () => {
  const r = reader({chapter: 'b', chapter_index: 0, offset: 75});
  assert.equal(r.gets[0].url, '/chapters/1');
  await r.finish(0, 'b');
  r.frames.shift()();
  assert.deepEqual(r.scrolls, [475]);
  const s = reader({chapter: 'a', chapter_index: 0, offset: 80});
  await s.finish(0, 'a');
  s.elements.next.fire();
  await s.finish(1, 'b');
  s.frames.shift()();
  assert.deepEqual(s.scrolls, [400]);
  assert.equal(s.elements.position.value, '2');
});

test('initial failed resume does not jump back to the stale saved chapter index', async () => {
  const r = reader({chapter: 'b', chapter_index: 0, offset: 75});
  await r.finish(0, 'unavailable', false);
  assert.equal(r.elements.position.value, '2');
  assert.equal(r.elements['add-note'].disabled, true);
  r.elements.next.fire();
  assert.equal(r.gets[1].url, '/chapters/2');
  await r.finish(1, 'c');
  assert.equal(r.elements['epub-body'].innerHTML, '<p>c</p>');
});
