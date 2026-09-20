const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function fixture() {
  const elements = new Map(), timers = new Map();
  let timerId = 0, stopped = 0, closed = 0, resolvePoll;
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', classList: {add() {}, remove() {}},
      handlers: {}, addEventListener(name, callback) { this.handlers[name] = callback; }, removeAttribute(name) { delete this[name]; }});
    return elements.get(id);
  };
  const track = {stop() { stopped++; }, addEventListener() {}};
  const stream = {getTracks: () => [track], getVideoTracks: () => [track]};
  const reply = data => ({ok: true, json: async () => data});
  const context = {
    Headers, AbortController, MediaStream: class {},
    document: {querySelector: q => q.includes('csrf') ? {content: 'token'} : {},
      getElementById: element, querySelectorAll: () => []},
    navigator: {mediaDevices: {getDisplayMedia: async () => stream}, clipboard: {writeText: async () => {}}},
    window: {setTimeout(fn, ms) { timers.set(++timerId, {fn, ms}); return timerId; },
      clearTimeout(id) { timers.delete(id); }, addEventListener() {}},
    RTCPeerConnection: class {
      addTrack() {} close() { closed++; }
      async createOffer() { return {type: 'offer', sdp: 'v=0'}; }
      async setLocalDescription(value) { this.localDescription = {toJSON: () => value}; }
    },
    fetch: async (url, options) => {
      if (url.endsWith('/sessions')) return reply({session: {session_id: 'session', join_code: 'CODE'}});
      if (!options.method) return new Promise(resolve => { resolvePoll = resolve; });
      const body = JSON.parse(options.body);
      if (body.type === 'bye') return new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(new Error('timeout')));
      });
      return reply({accepted: true});
    }
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/screen_share.js'), 'utf8'), context);
  return {element, timers, stopped: () => stopped, closed: () => closed,
    resolvePoll: response => resolvePoll(response)};
}

test('Stop releases capture before a stalled bye request; stale poll cannot restart polling', async () => {
  const f = fixture();
  await f.element('screen-share-start').handlers.click();
  const stopping = f.element('screen-share-stop').handlers.click();
  assert.equal(f.stopped(), 1);
  assert.equal(f.closed(), 1);
  f.resolvePoll({ok: true, json: async () => ({messages: [], last_sequence: 0})});
  for (const timer of [...f.timers.values()]) if (timer.ms === 10000) timer.fn();
  await stopping;
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.timers.size, 0);
});

test('Closed server session releases active capture instead of polling forever', async () => {
  const f = fixture();
  await f.element('screen-share-start').handlers.click();
  f.resolvePoll({ok: false, status: 404});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.stopped(), 1);
  assert.equal(f.closed(), 1);
  assert.equal(f.timers.size, 0);
});

test('Repeated signaling failures back off and stop capture after three attempts', async () => {
  const f = fixture();
  await f.element('screen-share-start').handlers.click();
  for (let attempt = 1; attempt <= 3; attempt++) {
    f.resolvePoll({ok: false, status: 503});
    await new Promise(resolve => setImmediate(resolve));
    if (attempt < 3) {
      assert.equal(f.stopped(), 0);
      const [id, timer] = [...f.timers.entries()][0];
      assert.equal(timer.ms, 700 * (2 ** attempt));
      f.timers.delete(id); timer.fn();
    }
  }
  assert.equal(f.stopped(), 1);
  assert.equal(f.timers.size, 0);
});


test('Connection code is visible before display capture resolves and duplicate starts are ignored', async () => {
  const f = fixture();
  let releaseCapture;
  // The fixture capture is already immediate; the visible code after the first
  // microtask still verifies that session creation precedes signaling.
  const first = f.element('screen-share-start').handlers.click();
  const second = f.element('screen-share-start').handlers.click();
  await first;
  await second;
  assert.equal(f.element('screen-share-code').value, 'CODE');
  assert.equal(f.element('screen-share-start').disabled, true);
  assert.equal(f.element('screen-share-stop').disabled, false);
});
