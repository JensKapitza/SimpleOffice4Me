const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const test = require('node:test');
const {runInNewContext} = require('node:vm');

const src = readFileSync('static/js/v3_copy_notices.js', 'utf8');
const eventId = 'a'.repeat(64);
const row = {id: eventId, kind: 'job', at: '2026-11-27T17:00:00Z'};

function setup(initial = [], enableAudio = false) {
  const constructors = () => ({dataset: {}, textContent: '', append(...items) { this.items = items; }});
  const list = {rows: initial, querySelectorAll() { return this.rows; }, replaceChildren(...rows) { this.rows = rows; }};
  const status = constructors();
  const soundStatus = constructors();
  const soundButton = Object.assign(new EventTarget(), {
    attributes: {'aria-pressed': 'false'},
    setAttribute(key, value) { this.attributes[key] = value; },
  });
  const audioControls = {hidden: true};
  const sourceSelect = Object.assign(new EventTarget(), {value: 'peer'});
  const testSound = Object.assign(new EventTarget(), {disabled: true});
  const pane = {
    dataset: {endpoint: '/automation-v3/copy-notices', jobLabel: 'Job complete', browserLabel: 'Document copied',
      peerLabel: 'Paired notice', emptyLabel: 'No notifications', newLabel: 'New notification',
      soundOnLabel: 'On', soundOffLabel: 'Off', soundUnavailableLabel: 'Unavailable'},
    querySelector(sel) {
      return {'[data-copy-notice-list]': list, '[data-copy-notice-announcement]': status,
        '[data-copy-notice-audio]': audioControls, '[data-copy-notice-sound-source]': sourceSelect,
        '[data-copy-notice-test-sound]': testSound, '[data-copy-notice-sound]': soundButton, '[data-copy-notice-sound-status]': soundStatus}[sel];
    },
  };
  const document = Object.assign(new EventTarget(), {hidden: false, querySelector: () => pane, createElement: constructors});
  const window = new EventTarget();
  const sounds = [];
  let closed = 0;
  if (enableAudio) {
    window.AudioContext = class {
      currentTime = 0;
      destination = {};
      async resume() { this.state = 'running'; }
      async close() { this.state = 'closed'; closed++; }
      createGain() {
        return {gain: {setValueAtTime() {}, linearRampToValueAtTime() {}}, connect() {}, disconnect() {}};
      }
      createOscillator() {
        return {frequency: {}, connect() {}, disconnect() {}, start() { sounds.push(this.frequency.value); this.onended(); }, stop() {}};
      }
    };
  }
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
  return {list, status, timers, pending, document, window, navigator, tick, start, answer, sounds, soundButton,
    soundStatus, audioControls, sourceSelect, testSound, closeCount: () => closed};
}

const good = (...notices) => ({ok: true, status: 200, json: async () => ({enabled: true, notices})});

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

test('disabling the capability clears the old list without trying again', async () => {
  const c = setup([{dataset: {noticeId: eventId}}]);
  await c.answer({ok: true, status: 200, json: async () => ({enabled: false, notices: []})}, c.start(5000));
  assert.equal(c.list.rows[0].textContent, 'No notifications');
  assert.equal(c.timers.size, 0);
});

const peer = (letter, time = new Date()) => ({id: letter.repeat(64), kind: 'peer', at: time.toISOString()});

test('fresh paired notice sounds once after explicit opt-in and not for old or local results', async () => {
  const c = setup([], true);
  const old = peer('b', new Date(Date.now() - 60_000));
  await c.answer(good(old), c.start(5000));
  assert.deepEqual(c.sounds, []);
  c.soundButton.dispatchEvent(new Event('click'));
  await new Promise(setImmediate);
  assert.equal(c.soundButton.attributes['aria-pressed'], 'true');
  const recent = peer('c');
  await c.answer(good(recent, peer('d'), {...row, at: new Date().toISOString()}, old), c.start(5000));
  assert.deepEqual(c.sounds, [660]);
  await c.answer(good(recent, peer('d'), {...row, at: new Date().toISOString()}, old), c.start(5000));
  assert.deepEqual(c.sounds, [660]);
  await c.answer(good({...row, id: 'e'.repeat(64)}, peer('f', new Date(Date.now() - 30_000)), recent), c.start(5000));
  assert.deepEqual(c.sounds, [660]);
  c.soundButton.dispatchEvent(new Event('click'));
  assert.equal(c.closeCount(), 1);
  assert.equal(c.soundStatus.textContent, 'Off');
});

test('switching tabs or revoking access closes audio and never plays catch-up automatically', async () => {
  const c = setup([], true);
  c.soundButton.dispatchEvent(new Event('click'));
  await new Promise(setImmediate);
  const pending = c.start(5000);
  c.document.hidden = true;
  c.document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(c.closeCount(), 1);
  assert.equal(c.soundButton.attributes['aria-pressed'], 'false');
  await c.answer(good(peer('b')), pending);
  c.document.hidden = false;
  c.document.dispatchEvent(new Event('visibilitychange'));
  await c.answer(good(peer('c')), c.start(0));
  assert.deepEqual(c.sounds, []);
  c.soundButton.dispatchEvent(new Event('click'));
  await new Promise(setImmediate);
  await c.answer({ok: false, status: 403}, c.start(5000));
  assert.equal(c.closeCount(), 2);
  assert.equal(c.audioControls.hidden, true);
});

test('local and combined selection only sound for unread matching events after a fresh opt-in', async () => {
  const c = setup([], true);
  assert.equal(c.audioControls.hidden, false);
  c.sourceSelect.value = 'local';
  c.sourceSelect.dispatchEvent(new Event('change'));
  c.soundButton.dispatchEvent(new Event('click'));
  await new Promise(setImmediate);
  assert.equal(c.testSound.disabled, false);
  c.testSound.dispatchEvent(new Event('click'));
  const stale = {...row, at: new Date(Date.now() - 5000).toISOString()};
  await c.answer(good(stale, peer('b')), c.start(5000));
  assert.deepEqual(c.sounds, [660]); // the intentional test tone only
  const local = {...row, id: 'c'.repeat(64), at: new Date().toISOString()};
  await c.answer(good(stale, peer('b'), local), c.start(5000));
  assert.deepEqual(c.sounds, [660, 660]);
  c.sourceSelect.value = 'all';
  c.sourceSelect.dispatchEvent(new Event('change'));
  assert.equal(c.testSound.disabled, true);
  await c.answer(good(local, peer('d')), c.start(5000));
  assert.deepEqual(c.sounds, [660, 660]);
  c.soundButton.dispatchEvent(new Event('click'));
  await new Promise(setImmediate);
  await c.answer(good(local, peer('d'), peer('e')), c.start(5000));
  assert.deepEqual(c.sounds, [660, 660, 660]);
});
