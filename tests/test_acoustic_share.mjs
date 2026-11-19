import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../static/js/acoustic_share.js', import.meta.url), 'utf8');

function harness({ decoded = null, permission = null } = {}) {
  class Element {
    constructor(value = '') { this.value = value; this.textContent = ''; this.disabled = false; this.listeners = {}; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    click() { return this.listeners.click?.(); }
    focus() { this.focused = true; }
  }
  const nodes = Object.fromEntries(['send', 'receive', 'stop', 'status', 'text', 'result', 'band', 'fec'].map((key) => [key, new Element()]));
  nodes.band.value = 'high';
  nodes.fec.value = 'hamming';
  const root = { querySelector: (selector) => ({
    '[data-acoustic-send]': nodes.send,
    '[data-acoustic-receive]': nodes.receive,
    '[data-acoustic-stop]': nodes.stop,
    '[data-acoustic-status]': nodes.status,
    '[data-acoustic-text]': nodes.text,
    '[data-acoustic-result]': nodes.result,
    '[data-acoustic-band]': nodes.band,
    '[data-acoustic-fec]': nodes.fec,
  })[selector] || null };
  let micRequests = 0;
  let lastOptions;
  let tracksStopped = 0;
  const contexts = [];
  class FakeContext {
    constructor() { this.sampleRate = 48000; this.state = 'running'; this.destination = {}; contexts.push(this); }
    resume() { return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createBuffer(_channels, length) { const channel = new Float32Array(length); return { copyToChannel: (value) => channel.set(value), getChannelData: () => channel }; }
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
      encode(text, options) { lastOptions = options; return new Float32Array(text.length); },
      Decoder: class { constructor(options) { lastOptions = options; } push() { return decoded; } },
    },
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener() {},
  };
  const document = {
    documentElement: { lang: 'en' },
    querySelectorAll: () => [root],
    getElementById: () => null,
  };
  const navigator = { mediaDevices: { async getUserMedia() {
    micRequests++;
    if (permission) return permission;
    return { getTracks: () => [{ stop() { tracksStopped++; } }] };
  } } };
  vm.runInNewContext(source, { window, document, navigator, console, Float32Array, Uint8Array, Promise, setTimeout, clearTimeout });
  return { nodes, window, timers, contexts, micRequests: () => micRequests, tracksStopped: () => tracksStopped, lastOptions: () => lastOptions };
}

test('plays encoded PCM from the acoustic textarea and reports completion', async () => {
  const h = harness();
  h.nodes.text.value = 'draft text';
  await h.nodes.send.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(JSON.stringify(h.lastOptions()), JSON.stringify({ sampleRate: 48000, band: 'ultrasound', fec: 'hamming' }));
  assert.equal(h.nodes.status.textContent, 'Sending complete.');
  assert.equal(h.contexts[0].state, 'closed');
});

test('requests microphone only after receive click and keeps received text separate', async () => {
  const h = harness({ decoded: 'received text' });
  h.nodes.text.value = 'original draft';
  assert.equal(h.micRequests(), 0);
  await h.nodes.receive.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.micRequests(), 1);
  assert.equal(h.nodes.text.value, 'original draft');
  assert.equal(h.nodes.result.value, 'received text');
  assert.equal(h.nodes.status.textContent, 'Transfer received.');
  assert.equal(h.tracksStopped(), 1);
});

test('stop ends listening and closes microphone tracks', async () => {
  const h = harness();
  await h.nodes.receive.click();
  await h.nodes.stop.click();
  assert.equal(h.nodes.status.textContent, 'Transfer stopped.');
  assert.equal(h.tracksStopped(), 1);
  assert.equal(h.contexts[0].state, 'closed');
});

test('late permission denial after stop leaves the stopped state intact', async () => {
  let deny;
  const permission = new Promise((resolve, reject) => { deny = reject; });
  const h = harness({ permission });
  const pending = h.nodes.receive.click();
  assert.equal(h.nodes.fec.disabled, true);
  await h.nodes.stop.click();
  deny(new Error('permission denied'));
  await pending;
  assert.equal(h.nodes.status.textContent, 'Transfer stopped.');
  assert.equal(h.nodes.fec.disabled, false);
});
