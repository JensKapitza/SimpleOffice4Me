const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const test = require('node:test');
const {runInNewContext} = require('node:vm');

const script = readFileSync('static/js/inventory_nfc.js', 'utf8');

function setup(adapter) {
  const window = new EventTarget();
  if (adapter) window.SimpleOfficeNativeNfc = adapter;
  const readers = [];
  const reading = (serialNumber, message) => Object.assign(new Event('reading'), {serialNumber, message});
  window.NDEFReader = class extends EventTarget {
    constructor() {
      super();
      readers.push(this);
    }
    scan({signal}) {
      this.signal = signal;
    }
  };
  runInNewContext(script, {window, AbortController});
  return {window, readers, reading, helper: window.SimpleOfficeInventoryNfc};
}

test('normalizes only hex bytes and rejects URLs, arbitrary text and oversized UIDs', () => {
  const {helper} = setup();
  assert.equal(helper.normalizeUid('04:a2:00:fd'), '04A200FD');
  assert.equal(helper.normalizeUid('00-03-00-FF'), '000300FF');
  assert.equal(helper.normalizeUid('ab'.repeat(32)), 'AB'.repeat(32));
  for (const value of ['A', 'AA:', 'uid:03abc', '04AA|https://test.invalid', 'javascript:1',
    'https://test.invalid', '04a', 'ab'.repeat(33), '', null, 412]) {
    assert.equal(helper.normalizeUid(value), '', String(value));
  }
});

test('reads one browser UID, ignores all NDEF records, and stops polling even if scan is not settled', async () => {
  const {helper, readers, reading} = setup();
  const result = helper.scan({signal: new AbortController().signal});
  let inspected = false;
  const payload = {get records() { inspected = true; return [{data: 'https://test.invalid'}]; }};
  const reader = readers[0];
  reader.dispatchEvent(reading('04:5e:2a:ff', payload));
  reader.dispatchEvent(reading('DEADBEEF', payload));
  assert.equal(await result, '045E2AFF');
  assert.equal(inspected, false);
  assert.equal(reader.signal.aborted, true);
});

test('a browser tag without an ID is rejected and an explicit abort stops browser polling', async () => {
  const {helper, readers, reading} = setup();
  const result = helper.scan({signal: new AbortController().signal});
  readers[0].dispatchEvent(reading('', {records: [{data: 'ABCD'}]}));
  await assert.rejects(result, {code: 'no-id'});
  assert.equal(readers[0].signal.aborted, true);

  const controller = new AbortController();
  const cancelled = helper.scan({signal: controller.signal});
  controller.abort();
  await assert.rejects(cancelled, {code: 'abort'});
  assert.equal(readers[1].signal.aborted, true);
});

test('native scan wins over Web NFC and only its initialized request and first result are accepted', async () => {
  const started = [];
  const stopped = [];
  const {window, readers, helper} = setup({
    start(id) { started.push(id); return 'ok'; },
    stop(id) { stopped.push(id); },
  });
  const send = (requestId, serial, state = 'ok') =>
    window.dispatchEvent(new CustomEvent('simpleoffice:nfc', {detail: {requestId, state, serial}}));
  const one = new AbortController();
  const first = helper.scan({signal: one.signal});
  one.abort();
  await assert.rejects(first, {code: 'abort'});
  const next = helper.scan({signal: new AbortController().signal});
  send(started[0], '1234');
  send(started[1], '04AB');
  send(started[1], 'CDEF');
  assert.equal(await next, '04AB');
  assert.equal(readers.length, 0);
  assert.deepEqual(stopped, started);
});

test('native disabled and browser unsupported return separate failure reasons', async () => {
  const {window, helper} = setup({start() { return 'disabled'; }, stop() {}});
  const signal = new AbortController().signal;
  await assert.rejects(helper.scan({signal}), {code: 'disabled'});
  delete window.SimpleOfficeNativeNfc;
  delete window.NDEFReader;
  await assert.rejects(helper.scan({signal}), {code: 'unavailable'});
});

test('inventory USB field consumes Enter, submits no form and never uses a scanned URL', async () => {
  const {window} = setup();
  const nfc = new EventTarget();
  nfc.value = '';
  nfc.focus = () => { nfc.focused = true; };
  nfc.select = () => {};
  const usb = new EventTarget();
  const status = {};
  const form = new EventTarget();
  form.dataset = {};
  const nodes = {'inventory-app': {dataset: {findUrl: '/inventory/find'}}, 'nfc-id': nfc,
    'nfc-keyboard': usb, 'scan-status': status, 'inventory-form': form};
  const document = new EventTarget();
  Object.assign(document, {documentElement: {lang: 'de'}, getElementById: id => nodes[id],
    querySelectorAll: () => [], querySelector: () => null});
  const calls = [];
  let submits = 0;
  form.addEventListener('submit', () => { submits++; });
  window.SimpleOfficeInventoryNavigation = {};
  runInNewContext(readFileSync('static/js/inventory.js', 'utf8'), {
    window, document, Event, setTimeout, clearTimeout, AbortController,
    fetch(url) { calls.push(url); return Promise.resolve({ok: true, json: async () => ({found: false})}); },
  });
  const enter = () => {
    const event = Object.assign(new Event('keydown', {cancelable: true}), {key: 'Enter'});
    nfc.dispatchEvent(event);
    return event.defaultPrevented;
  };
  usb.dispatchEvent(new Event('click'));
  assert.equal(nfc.focused, true);
  nfc.value = '00-aa-bb-cd';
  assert.equal(enter(), true);
  assert.equal(nfc.value, '00AABBCD');
  assert.deepEqual(calls, ['/inventory/find?identifier=&nfc=00AABBCD']);
  assert.equal(submits, 0);
  usb.dispatchEvent(new Event('click'));
  nfc.value = 'https://example.invalid/payload';
  assert.equal(enter(), true);
  assert.equal(nfc.value, '00AABBCD');
  assert.equal(calls.length, 1);
  assert.match(status.textContent, /Ungültige ID/);
  assert.equal(submits, 0);
});
