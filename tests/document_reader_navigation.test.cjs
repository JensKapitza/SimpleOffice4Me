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
  fire(name = 'click', event) { return this.listeners.get(name)?.(event); }
  dispatchEvent(event) { this.fire(event.type, event); }
}

function reader(saved = {}, {stale = false, format = 'epub'} = {}) {
  const root = new Element({format, documentVersion: 'version-1',
    savedLocator: JSON.stringify(saved), epubUrl: '/chapters', progressApi: '/progress',
    annotationApi: '/notes', pdfUrl: '/book.pdf', pageCount: '5'});
  const names = ['status', 'position', 'error', 'epub-body', 'pdf-frame', 'prev', 'next', 'note', 'add-note'];
  if (stale) names.push('stale-progress', 'save-new-progress', 'save-new-status');
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  if (stale) elements['save-new-progress'].disabled = true;
  const chapters = ['a', 'b', 'c'].map((id, index) => new Element({
    readerChapter: String(index), readerChapterId: id,
  }));
  const listedIndexes = arguments[1]?.listedIndexes ?? [0, 1, 2];
  root.dataset.chapterCount = String(chapters.length);
  root.querySelector = selector => elements[selector.slice(13, -1)] || null;
  root.querySelectorAll = selector => selector === '[data-reader-chapter]' ? listedIndexes.map(i => chapters[i]) : [];
  const gets = [];
  const writes = [];
  const timers = new Map();
  const frames = [];
  const scrolls = [];
  const progressReplies = [];
  let serial = 0;
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const response = (body, ok = true) => ({ok, status: ok ? 200 : 503, json: async () => body});
  const fetch = (url, options) => {
    if (options.method === 'POST') {
      writes.push({url, data: JSON.parse(options.body)});
      const next = url === '/progress' && progressReplies.shift();
      if (next?.defer) return next.defer.then(() => response(next.body, next.ok));
      return Promise.resolve(next ? response(next.body, next.ok) : response({document_version: 'version-1', stale: false}));
    }
    return new Promise(resolve => gets.push({url, resolve}));
  };
  const win = new Element();
  win.location = new URL('http://localhost/reader/book');
  win.scrollTo = opts => scrolls.push(opts.top);
  vm.runInNewContext(script, {
    document: {getElementById: id => id === 'reader-root' ? root : null, querySelector: () => null},
    window: win, Headers, URL, fetch, requestAnimationFrame: fn => frames.push(fn),
    setTimeout: (fn, delay) => { timers.set(++serial, {fn, delay}); return serial; },
    clearTimeout: id => timers.delete(id),
    CustomEvent: class {
      constructor(type, {detail} = {}) { this.type = type; this.detail = detail; }
    },
  });
  return {
    elements, chapters, gets, writes, frames, scrolls, win, progressReplies,
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

test('partial publisher tables of contents do not change the reading order or completed percentage', async () => {
  const r = reader({}, {listedIndexes: [0]});
  await r.finish(0, 'a');
  assert.equal(r.elements.status.textContent, 'Kapitel 1 / 3 · 33%');
  r.elements.next.fire();
  await r.finish(1, 'b');
  assert.equal(r.elements.status.textContent, 'Kapitel 2 / 3 · 67%');
  r.elements.next.fire();
  await r.finish(2, 'c');
  await r.runTimers(350);
  assert.equal(r.elements.status.textContent, 'Kapitel 3 / 3 · 100%');
  assert.deepEqual(r.gets.map(g => g.url), ['/chapters/0', '/chapters/1', '/chapters/2']);
  assert.deepEqual(r.writes[0].data.locator, locator('c', 2));

  const s = reader({chapter: 'b', chapter_index: 1, offset: 75}, {listedIndexes: [2]});
  assert.equal(s.gets[0].url, '/chapters/1');
  await s.finish(0, 'b');
  s.frames.shift()();
  assert.deepEqual(s.scrolls, [475]);
  s.chapters[2].fire();
  assert.equal(s.gets[1].url, '/chapters/2');
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

test('opening or browsing a replaced EPUB does not replace the old position without consent', async () => {
  const r = reader({}, {stale: true});
  await r.finish(0, 'a');
  r.elements.next.fire();
  await r.finish(1, 'b');
  r.win.scrollY = 487;
  r.win.fire('scroll');
  await r.runTimers(250);
  await r.runTimers(350);
  assert.deepEqual(r.writes, []);
  assert.equal(r.elements.position.value, '2');
  assert.equal(r.elements['stale-progress'].hidden, undefined);
  await r.elements['save-new-progress'].fire();
  await r.runTimers(350);
  assert.equal(r.elements['stale-progress'].hidden, true);
  assert.deepEqual(r.writes.map(x => x.data.locator), [locator('b', 1, 87)]);
  r.elements.next.fire();
  await r.finish(2, 'c');
  await r.runTimers(350);
  assert.deepEqual(r.writes.map(x => x.data.locator), [locator('b', 1, 87), locator('c', 2)]);
});

test('replaced PDF navigation remains usable and writes only after explicit acceptance', async () => {
  const r = reader({}, {stale: true, format: 'pdf'});
  await r.runTimers(350);
  r.elements.next.fire();
  await r.runTimers(350);
  assert.equal(new URL(r.elements['pdf-frame'].src).hash, '#page=2');
  assert.deepEqual(r.writes, []);
  await r.elements['save-new-progress'].fire();
  await r.runTimers(350);
  assert.deepEqual(r.writes, [{url: '/progress', data: {
    locator: {page: 2}, percent: 40, document_version: 'version-1',
  }}]);
  r.elements.next.fire();
  await r.runTimers(350);
  assert.equal(r.writes[1].data.locator.page, 3);
});

test('a failed first EPUB chapter has not enabled starting a new saved position', async () => {
  const r = reader({}, {stale: true});
  await r.finish(0, 'missing', false);
  assert.equal(r.elements['save-new-progress'].disabled, true);
  await r.elements['save-new-progress'].fire();
  await r.runTimers(350);
  assert.deepEqual(r.writes, []);
});

test('an unconfirmed write keeps the warning and does not enable autosave', async () => {
  const r = reader({}, {stale: true, format: 'pdf'});
  r.progressReplies.push({ok: false, body: {error: 'Das Dokument wurde geändert.'}}, {ok: true, body: {}});
  await r.elements['save-new-progress'].fire();
  assert.match(r.elements['save-new-status'].textContent, /Dokument wurde geändert/);
  assert.equal(r.elements['stale-progress'].hidden, undefined);
  r.elements.next.fire();
  await r.runTimers(350);
  assert.equal(r.writes.length, 1);
  await r.elements['save-new-progress'].fire();
  assert.match(r.elements['save-new-status'].textContent, /nicht bestätigt/);
  assert.equal(r.elements['stale-progress'].hidden, undefined);
  r.elements.next.fire();
  await r.runTimers(350);
  assert.equal(r.writes.length, 2);
  await r.elements['save-new-progress'].fire();
  assert.equal(r.writes[2].data.locator.page, 3);
  assert.equal(r.elements['stale-progress'].hidden, true);
});

test('the acknowledged position is snapshotted before later navigation resumes autosave', async () => {
  const r = reader({}, {stale: true, format: 'pdf'});
  let complete;
  const defer = new Promise(resolve => { complete = resolve; });
  r.progressReplies.push({defer, ok: true, body: {document_version: 'version-1', stale: false}});
  const save = r.elements['save-new-progress'].fire();
  r.elements.next.fire();
  await r.elements['save-new-progress'].fire();
  await r.runTimers(350);
  assert.deepEqual(r.writes.map(x => x.data.locator.page), [1]);
  complete();
  await save;
  await r.runTimers(350);
  assert.deepEqual(r.writes.map(x => x.data.locator.page), [1, 2]);
});
