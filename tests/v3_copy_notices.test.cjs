const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const test = require('node:test');
const {runInNewContext} = require('node:vm');

const src = readFileSync('static/js/v3_copy_notices.js', 'utf8');
const eventId = 'a'.repeat(64);
const row = {id: eventId, kind: 'job', at: '2026-11-27T17:00:00Z'};

function setup(initial = []) {
  const constructors = () => ({dataset: {}, textContent: '', append(...items) { this.items = items; }});
  const list = {rows: initial, querySelectorAll() { return this.rows; }, replaceChildren(...rows) { this.rows = rows; }};
  const status = constructors();
  const pane = {
    dataset: {endpoint: '/automation-v3/copy-notices', jobLabel: 'Job complete', browserLabel: 'Document copied',
      emptyLabel: 'No notifications', newLabel: 'New notification'},
    querySelector(sel) { return sel === '[data-copy-notice-list]' ? list : status; },
  };
  const document = Object.assign(new EventTarget(), {hidden: false, querySelector: () => pane, createElement: constructors});
  const window = new EventTarget();
  const navigator = {onLine: true};
  const timers = new Map(), pending = [];
  let serial = 0;
  runInNewContext(src, {
    window, document, navigator, AbortController,
    setTimeout(fn, delay) { timers.set(++serial, {fn, delay}); return serial; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) { return new Promise((resolve, reject) => pending.push({url, options, resolve, reject})); },
  });
  const tick = async (delay) => {
    const [id, task] = [...timers].find(([, t]) => t.delay === delay);
    timers.delete(id);
    return task.fn();
  };
  const start = delay => {
    const [id, task] = [...timers].find(([, t]) => t.delay === delay);
    timers.delete(id);
    return task.fn();
  };
  const answer = async (response, promise) => {
    pending.shift().resolve(response);
    await promise;
  };
  return {list, status, timers, pending, document, window, navigator, tick, start, answer};
}

const good = (...notices) => ({ok: true, status: 200, json: async () => ({notices})});

test('refresh renders a new job locally and announces it once without overlapping requests', async () => {
  const c = setup();
  const first = c.start(5000);
  const request = c.pending[0];
  assert.equal(request.url, '/automation-v3/copy-notices');
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.credentials, 'same-origin');
  c.document.dispatchEvent(new Event('visibilitychange'));
  await c.tick(0);
  assert.equal(c.pending.length, 1);
  await c.answer(good(row), first);
  assert.equal(c.list.rows[0].dataset.noticeId, eventId);
  assert.equal(c.list.rows[0].items[0], 'Job complete');
  assert.equal(c.status.textContent, 'New notification');
  const previous = c.list.rows;
  const second = c.start(5000);
  await c.answer(good(row), second);
  assert.equal(c.list.rows, previous);
});

test('hidden tabs cancel, ignore late responses, and resume when visible', async () => {
  const c = setup();
  const first = c.start(5000);
  c.document.hidden = true;
  c.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(c.pending[0].options.signal.aborted, true);
  await c.answer(good(row), first);
  assert.equal(c.list.rows.length, 0);
  assert.equal(c.timers.size, 0);
  c.document.hidden = false;
  c.document.dispatchEvent(new Event('visibilitychange'));
  await c.answer(good(row), c.start(0));
  assert.equal(c.list.rows.length, 1);
});

test('temporary failure keeps the last notice; revoked access clears it and stops', async () => {
  const old = {dataset: {noticeId: eventId}};
  const c = setup([old]);
  await c.answer({ok: false, status: 503}, c.start(5000));
  assert.equal(c.list.rows[0], old);
  await c.answer({ok: false, status: 0, type: 'opaqueredirect'}, c.start(30000));
  assert.equal(c.list.rows[0].textContent, 'No notifications');
  assert.equal(c.timers.size, 0);
});

test('stalled feed is aborted and retried with a backoff', async () => {
  const c = setup();
  const request = c.start(5000);
  await c.tick(10000);
  assert.equal(c.pending[0].options.signal.aborted, true);
  c.pending.shift().reject(new Error('Aborted'));
  await request;
  assert.equal([...c.timers.values()][0].delay, 30000);
});
