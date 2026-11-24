import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../static/js/federation_token_scan.js', import.meta.url), 'utf8');
const TOKEN = 'A'.repeat(64);

function harness({ detected = null, imageDecoded = '', secure = true, media = true } = {}) {
  class Element {
    constructor() { this.value = ''; this.textContent = ''; this.disabled = false; this.hidden = false; this.listeners = {}; this.srcObject = null; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    click() { return this.listeners.click?.(); }
    dispatchEvent(event) { this.listeners[event.type]?.(event); }
    pause() {}
    play() { return Promise.resolve(); }
  }
  const nodes = { button: new Element(), imageButton: new Element(), imageInput: new Element(), close: new Element(), preview: new Element(), panel: new Element(), output: new Element(), status: new Element() };
  nodes.panel.hidden = true;
  const document = {
    documentElement: { lang: 'en' },
    createElement: () => ({
      getContext: () => ({ drawImage() {}, getImageData: () => ({ data: [], width: 2, height: 2 }) }),
    }),
    getElementById: (id) => ({
      'token-qr-camera-button': nodes.button,
      'token-qr-image-button': nodes.imageButton,
      'token-qr-image-file': nodes.imageInput,
      'token-qr-camera-close': nodes.close,
      'token-qr-camera-preview': nodes.preview,
      'token-qr-camera-panel': nodes.panel,
      'peer-token': nodes.output,
      'token-qr-status': nodes.status,
    })[id] || null,
    addEventListener(name, handler) { if (name === 'DOMContentLoaded') queueMicrotask(handler); },
  };
  let tracksStopped = 0;
  const stream = { getTracks: () => [{ stop() { tracksStopped++; } }] };
  const navigator = { mediaDevices: media ? { async getUserMedia() { return stream; } } : undefined };
  class FakeDetector {
    async detect() { return detected === null ? [] : [{ rawValue: detected }]; }
  }
  const window = {
    BarcodeDetector: FakeDetector,
    isSecureContext: secure,
    createImageBitmap: async () => ({ width: 2, height: 2, close() {} }),
    jsQR: () => ({ data: imageDecoded }),
    SimpleOfficeCamera: { async openCameraStream() { return stream; } },
    addEventListener() {},
  };
  const BarcodeDetector = FakeDetector;
  const timers = [];
  const setTimeoutShim = (fn) => { timers.push(fn); return timers.length; };
  vm.runInNewContext(source, { window, document, navigator, console, Event, BarcodeDetector, setTimeout: setTimeoutShim, clearTimeout: () => {} });
  return { nodes, window, timers, tracksStopped: () => tracksStopped };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test('accepts strict token shapes only', async () => {
  const h = harness();
  await tick();
  const check = h.window.FederationTokenScan.isTokenPayload;
  assert.equal(check(TOKEN), true);
  assert.equal(check('a-_0'.repeat(16)), true);
  assert.equal(check(`  ${TOKEN}  `), true);
  for (const value of ['', 'short', 'A'.repeat(31), 'A'.repeat(129), 'A'.repeat(63) + '!', 'http://192.168.1.1:8080', 'sofp://peer/abc']) {
    assert.equal(check(value), false, JSON.stringify(value));
  }
});

test('valid QR token fills the peer field and stops the camera', async () => {
  const h = harness({ detected: TOKEN });
  await tick();
  await h.nodes.button.click();
  await tick();
  for (const timer of h.timers.splice(0)) await timer();
  await tick();
  assert.equal(h.nodes.output.value, TOKEN);
  assert.equal(h.nodes.status.textContent, 'Token recognized and adopted.');
  assert.equal(h.tracksStopped(), 1);
  assert.equal(h.nodes.panel.hidden, true);
});

test('invalid QR payload keeps scanning without touching the field', async () => {
  const h = harness({ detected: 'not-a-token!' });
  await tick();
  await h.nodes.button.click();
  await tick();
  for (const timer of h.timers.splice(0)) await timer();
  await tick();
  assert.equal(h.nodes.output.value, '');
  assert.equal(h.nodes.status.textContent, 'The QR code does not contain a valid token.');
  assert.equal(h.tracksStopped(), 0);
});

test('insecure LAN origin gets a clear HTTPS hint and image fallback', async () => {
  const h = harness({ secure: false, media: false });
  await tick();
  let imagePickerOpened = false;
  h.nodes.imageInput.click = () => { imagePickerOpened = true; };
  await h.nodes.button.click();
  assert.match(h.nodes.status.textContent, /needs HTTPS/i);
  await h.nodes.imageButton.click();
  assert.equal(imagePickerOpened, true);
});

test('QR photo is decoded locally and fills the peer token', async () => {
  const h = harness({ imageDecoded: TOKEN });
  await tick();
  h.nodes.imageInput.files = [{ type: 'image/jpeg' }];
  h.nodes.imageInput.listeners.change();
  await tick();
  assert.equal(h.nodes.output.value, TOKEN);
  assert.match(h.nodes.status.textContent, /recognized and adopted/i);
});
