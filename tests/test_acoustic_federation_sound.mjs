import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../static/js/federation_sound.js', import.meta.url), 'utf8');

function harness({ decoded = null } = {}) {
  class Element {
    constructor(value = '') {
      this.value = value; this.textContent = ''; this.disabled = false;
      this.listeners = {}; this.attributes = {}; this.events = [];
    }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    click() { return this.listeners.click?.(); }
    dispatchEvent(event) { this.events.push(event.type); this.listeners[event.type]?.(event); }
    getAttribute(name) { return this.attributes[name] ?? null; }
  }
  const announce = new Element();
  announce.attributes['data-endpoint'] = 'http://192.168.1.23:8080';
  const nodes = { announce, listen: new Element(), stop: new Element(), status: new Element(), band: new Element('ultrasound') };
  const target = new Element();
  const root = { querySelector: (selector) => ({
    '[data-fed-sound-listen]': nodes.listen,
    '[data-fed-sound-stop]': nodes.stop,
    '[data-fed-sound-status]': nodes.status,
    '[data-fed-sound-band]': nodes.band,
  })[selector] || null,
  querySelectorAll: (selector) => (selector === '[data-fed-sound-announce]' ? [nodes.announce] : []),
  };
  let micRequests = 0;
  let tracksStopped = 0;
  let encoded = null;
  let measured = null;
  const contexts = [];
  class FakeContext {
    constructor() { this.sampleRate = 48000; this.state = 'running'; this.destination = {}; contexts.push(this); }
    resume() { return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createBuffer(_channels, length) { return { copyToChannel: () => {}, getChannelData: () => new Float32Array(length) }; }
    createBufferSource() { const source = { connect() {}, disconnect() {}, start() { queueMicrotask(() => source.onended?.()); }, stop() {} }; return source; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createScriptProcessor() {
      const processor = { disconnect() {}, onaudioprocess: null };
      processor.connect = () => queueMicrotask(() => processor.onaudioprocess?.({ inputBuffer: { getChannelData: () => new Float32Array(4096) } }));
      return processor;
    }
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
  }
  const timers = new Map();
  let timerId = 0;
  const window = {
    AudioContext: FakeContext,
    AcousticCodec: {
      measure: (text, fec) => { measured = { text, fec }; return { fits: true }; },
      encode(text, options) { encoded = { text, options }; return new Float32Array(8); },
      Decoder: class { push() { return decoded; } },
    },
    FederationSound: undefined,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener() {},
  };
  const document = {
    documentElement: { lang: 'en' },
    querySelectorAll: () => [root],
    getElementById: (id) => (id === 'direct-endpoint' ? target : null),
  };
  const navigator = { mediaDevices: { async getUserMedia() {
    micRequests++;
    return { getTracks: () => [{ stop() { tracksStopped++; } }] };
  } } };
  vm.runInNewContext(source, { window, document, navigator, console, Event, Float32Array, Promise, setTimeout, clearTimeout });
  return { nodes, target, window, contexts, encoded: () => encoded, measured: () => measured,
    micRequests: () => micRequests, tracksStopped: () => tracksStopped };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test('accepts local endpoint announcements', () => {
  const h = harness();
  const check = h.window.FederationSound.isAnnounceEndpoint;
  for (const value of ['http://192.168.1.23:8080', 'https://10.0.0.5:443', 'http://100.110.89.7:8765', 'http://127.0.0.1:8080', '  http://192.168.1.1:80  ']) {
    assert.equal(check(value), true, value);
  }
});

test('rejects non-local and malformed announcements', () => {
  const h = harness();
  const check = h.window.FederationSound.isAnnounceEndpoint;
  for (const value of ['', 'hello', 'http://8.8.8.8:8080', 'http://224.0.0.1:8080', 'http://192.168.1.23', 'http://192.168.1.23:99999', 'http://192.168.1.23:0', 'http://user@192.168.1.23:8080', 'http://192.168.1.23:8080/path', 'http://192.168.1.23:8080?x=1', 'https://office.example.de:443', 'ftp://192.168.1.23:21', 'sofp://peer/abc', 'http://999.1.1.1:8080']) {
    assert.equal(check(value), false, value);
  }
});

test('announce plays the endpoint and reports completion', async () => {
  const h = harness();
  await h.nodes.announce.click();
  await tick();
  assert.equal(h.encoded().text, 'http://192.168.1.23:8080');
  assert.equal(h.encoded().options.band, 'ultrasound');
  assert.equal(h.nodes.status.textContent, 'Announcement sent.');
  assert.equal(h.contexts[0].state, 'closed');
});

test('listen fills only valid announcements into the direct form', async () => {
  const h = harness({ decoded: 'http://192.168.178.44:8080' });
  assert.equal(h.micRequests(), 0);
  await h.nodes.listen.click();
  await tick();
  assert.equal(h.micRequests(), 1);
  assert.equal(h.target.value, 'http://192.168.178.44:8080');
  assert.deepEqual(h.target.events, ['input']);
  assert.equal(h.nodes.status.textContent, 'Sound announcement detected and filled into the form.');
  assert.equal(h.tracksStopped(), 1);
});

test('listen rejects invalid decodes without touching the form', async () => {
  const h = harness({ decoded: 'http://8.8.8.8:8080' });
  await h.nodes.listen.click();
  await tick();
  assert.equal(h.target.value, '');
  assert.equal(h.nodes.status.textContent, 'Sound detected, but not a valid device endpoint.');
  assert.equal(h.tracksStopped(), 1);
});

test('stop ends listening and closes microphone tracks', async () => {
  const h = harness();
  await h.nodes.listen.click();
  await h.nodes.stop.click();
  assert.equal(h.nodes.status.textContent, 'Stopped.');
  assert.equal(h.tracksStopped(), 1);
  assert.equal(h.contexts[0].state, 'closed');
});
