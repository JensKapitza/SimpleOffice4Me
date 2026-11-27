const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const test = require('node:test');
const {runInNewContext} = require('node:vm');

const script = readFileSync('static/js/inventory_intake.js', 'utf8');

function harness({scope, initialChoice} = {}) {
  let request;
  const timers = new Map();
  let timerId = 0;
  function node(base = {}) {
    const handlers = {};
    return {handlers, addEventListener: (name, callback) => { handlers[name] = callback; }, ...base};
  }
  const status = node();
  const submit = node({textContent: 'Privaten Entwurf anlegen'});
  const scan = node();
  const barcode = node({value: ''});
  const codeLabel = node();
  const code = node({hidden: true, querySelector: () => codeLabel});
  const openSaved = node({hidden: true});
  let dirty = false;
  const picture = node({hidden: true, children: [], replaceChildren(...children) { this.children = children; }});
  let choice = initialChoice ?? null;
  const batch = scope ? node({value: '', disabled: true, dataset: {intakeBatchScope: scope}, options: ['', 'one', 'two']
    .map(value => ({value, remove() {batch.options = batch.options.filter(row => row !== this);}})),
    dispatchEvent(event) { this.handlers[event.type]?.(); }}) : null;
  const form = node({action: '/inventory/capture/intake',
    querySelector: key => key === '#intake-barcode' ? barcode : key.includes('native-scan') ? scan : submit,
    requestSubmit() {}});
  const input = node({files: [{size: 12, type: 'image/png'}]});
  const pending = node({hidden: true, querySelector: key => key.includes('pending-photo') ? picture
    : key.includes('pending-barcode') ? code : key.includes('open-saved') ? openSaved : status});
  const map = {'#intake-batch-location': batch, '#intake-capture': form, '#intake-photo': input, '#intake-pending': pending};
  const location = {reloads: 0, reload() { this.reloads++; }};
  const calls = [];
  let decodedWidth = 640;
  let decodedHeight = 480;
  let decode = async () => ({width: decodedWidth, height: decodedHeight, close() {}});
  const window = node({
    location, sessionStorage: {getItem: () => choice, setItem: (_, value) => { choice = value; },
      removeItem: () => { choice = null; }}, createImageBitmap: photo => decode(photo), fetch: (path, options) => {
      calls.push([path, options]);
      return new Promise(resolve => { request = resolve; });
    },
  });
  const ctx = {window, AbortController, Event,
    setTimeout(callback, delay) { const id = ++timerId; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
    FormData: class {
      constructor(actual) { assert.equal(actual, form); this.location = batch?.value; this.token = 'stable-token'; }
      get(key) { return key === 'location_id' ? this.location : this.token; }
      set(key, value) { assert.equal(key, 'location_id'); this.location = value; }
    },
    document: {querySelector: key => key.includes('data-so-dirty') ? dirty : map[key], querySelectorAll: () => [], createElement: tag => {
      const context = {draws: [], drawImage(...args) { this.draws.push(args); }};
      return node({tag, context, attributes: {}, setAttribute(key, value) { this.attributes[key] = value; },
        getContext: () => context});
    }}};
  runInNewContext(script, ctx);
  return {input, form, pending, picture, status, calls, location, barcode, code, codeLabel, openSaved, scan, submit, timers,
    batch, getChoice: () => choice, setDirty() { dirty = true; }, ack: data => request(data),
    setBitmapSize(width, height) { decodedWidth = width; decodedHeight = height; },
    setDecode(callback) { decode = callback; }};
}

test('local raster canvas preview is bounded; stored state only after server acknowledgment', async () => {
  const ui = harness();
  ui.setBitmapSize(4000, 2000);
  await ui.input.handlers.change();
  assert.equal(ui.pending.hidden, false);
  assert.equal(ui.picture.children.length, 1);
  assert.equal(ui.picture.children[0].tag, 'canvas');
  assert.equal(ui.picture.children[0].attributes['aria-label'], 'Vorschau des neuen Fotos');
  assert.equal(ui.picture.children[0].width, 1200);
  assert.equal(ui.picture.children[0].height, 600);
  assert.equal(ui.picture.children[0].context.draws.length, 1);
  assert.match(ui.status.textContent, /Noch nicht gespeichert/);
  assert.equal(ui.calls.length, 0);
  const saving = ui.form.handlers.submit({preventDefault() {}});
  assert.equal(ui.calls[0][0], '/inventory/capture/intake');
  assert.equal(ui.calls[0][1].credentials, 'same-origin');
  assert.equal(ui.location.reloads, 0);
  await ui.form.handlers.submit({preventDefault() {}});
  assert.equal(ui.calls.length, 1);
  ui.ack({ok: true, json: async () => ({ok: true, draft: {id: 'a'.repeat(32)}})});
  await saving;
  assert.equal(ui.location.hash, 'draft-' + 'a'.repeat(32));
  assert.equal(ui.location.reloads, 1);
});

test('late decode from a previous selection cannot replace the latest preview', async () => {
  const ui = harness();
  let finishFirst;
  ui.setDecode(photo => photo.id === 'first'
    ? new Promise(resolve => { finishFirst = resolve; })
    : Promise.resolve({width: 320, height: 240, close() {}}));
  ui.input.files = [{id: 'first', size: 20 * 1024 * 1024, type: 'image/png'}];
  const first = ui.input.handlers.change();
  ui.input.files = [{id: 'second', size: 20 * 1024 * 1024, type: 'image/png'}];
  await ui.input.handlers.change();
  finishFirst({width: 900, height: 900, close() {}});
  await first;
  assert.equal(ui.picture.children.length, 1);
  assert.equal(ui.picture.children[0].width, 320);
  assert.equal(ui.picture.children[0].height, 240);
});

test('unconfirmed upload leaves photo selected and can be retried', async () => {
  const ui = harness();
  await ui.input.handlers.change();
  const attempt = ui.form.handlers.submit({preventDefault() {}});
  ui.ack({ok: false, json: async () => ({ok: false})});
  await attempt;
  assert.match(ui.status.textContent, /nicht bestätigt/);
  assert.equal(ui.input.disabled, false);
  assert.equal(ui.input.files.length, 1);
  assert.equal(ui.location.reloads, 0);
});

test('barcode-only capture waits with text, never fabricates image', async () => {
  const ui = harness();
  ui.input.files = [];
  ui.barcode.value = '9780140328721';
  const saving = ui.form.handlers.submit({preventDefault() {}});
  assert.equal(ui.pending.hidden, false);
  assert.equal(ui.picture.hidden, true);
  assert.equal(ui.picture.children.length, 0);
  assert.equal(ui.code.hidden, false);
  assert.equal(ui.codeLabel.textContent, '9780140328721');
  assert.equal(ui.calls.length, 1);
  ui.ack({ok: true, json: async () => ({ok: true, draft: {id: 'b'.repeat(32)}})});
  await saving;
  assert.equal(ui.location.hash, 'draft-' + 'b'.repeat(32));
});

test('native capture submits once and does not reload away another draft edit', async () => {
  const ui = harness();
  ui.input.files = [];
  ui.barcode.value = '9780140328721';
  ui.setDirty();
  let attempt;
  ui.form.requestSubmit = () => { attempt = ui.form.handlers.submit({preventDefault() {}}); };
  ui.form.handlers['intake:native-scan']();
  assert.equal(ui.calls.length, 1);
  assert.equal(ui.barcode.readOnly, true);
  ui.form.handlers['intake:native-scan']();
  assert.equal(ui.calls.length, 1);
  ui.ack({ok: true, json: async () => ({ok: true, draft: {id: 'c'.repeat(32)}})});
  await attempt;
  assert.equal(ui.location.reloads, 0);
  assert.equal(ui.openSaved.hidden, false);
  ui.openSaved.handlers.click();
  assert.equal(ui.location.reloads, 1);
});

test('clearing a selected photo removes the previous canvas', async () => {
  const ui = harness();
  await ui.input.handlers.change();
  assert.equal(ui.picture.children.length, 1);
  ui.input.files = [];
  await ui.input.handlers.change();
  assert.equal(ui.pending.hidden, true);
  assert.equal(ui.picture.children.length, 0);
});

test('a stalled native save stops waiting and can only retry the original payload', async () => {
  const ui = harness();
  ui.input.files = [];
  ui.barcode.value = '036000291452';
  let attempt;
  ui.form.requestSubmit = () => { attempt = ui.form.handlers.submit({preventDefault() {}}); };
  ui.form.handlers['intake:native-scan']();
  const sent = ui.calls[0][1];
  ui.ack({ok: true, json: () => new Promise(() => {})}); // Headers arrived, body did not.
  await Promise.resolve();
  const [{callback, delay}] = ui.timers.values();
  assert.equal(delay, 8000);
  assert.equal(ui.submit.disabled, true);
  callback();
  await attempt;
  assert.equal(sent.signal.aborted, true);
  assert.equal(ui.timers.size, 0);
  assert.match(ui.status.textContent, /denselben Scan/);
  assert.equal(ui.scan.disabled, true);
  assert.equal(ui.input.disabled, true);
  assert.equal(ui.barcode.readOnly, true);
  assert.equal(ui.submit.disabled, false);
  assert.equal(ui.submit.textContent, 'Scan erneut speichern');
  ui.form.handlers['intake:native-scan']();
  assert.equal(ui.calls.length, 1);
  const retry = ui.form.handlers.submit({preventDefault() {}});
  assert.equal(ui.calls[1][1].body, sent.body);
  ui.ack({ok: true, json: async () => ({ok: true, draft: {id: 'd'.repeat(32)}})});
  await retry;
  assert.equal(ui.location.hash, 'draft-' + 'd'.repeat(32));
  assert.equal(ui.timers.size, 0);
});

test('batch destination is restored only in the same session and uses existing options', () => {
  const ui = harness({scope: 'session-a'});
  assert.equal(ui.batch.disabled, false);
  ui.batch.value = 'one';
  ui.batch.handlers.change();
  const initialChoice = ui.getChoice();
  assert.equal(harness({scope: 'session-a', initialChoice}).batch.value, 'one');
  assert.equal(harness({scope: 'session-b', initialChoice}).batch.value, '');
  assert.equal(harness({scope: 'session-a', initialChoice: '{"scope":"session-a","location":"missing"}'}).batch.value, '');
  ui.batch.value = '';
  ui.batch.handlers.change();
  assert.equal(ui.getChoice(), null);
});

test('a definitely rejected native destination can be changed with the same scan and token', async () => {
  const ui = harness({scope: 'session-a'});
  ui.batch.value = 'one';
  ui.input.files = [];
  ui.barcode.value = '9780140328721';
  let attempt;
  ui.form.requestSubmit = () => { attempt = ui.form.handlers.submit({preventDefault() {}}); };
  ui.form.handlers['intake:native-scan']();
  const first = ui.calls[0][1].body;
  assert.equal(first.get('location_id'), 'one');
  assert.equal(ui.batch.disabled, true);
  ui.ack({status: 400, ok: false, json: async () => ({ok: false, error_kind: 'location_unavailable', error: 'Regal fehlt'})});
  await attempt;
  assert.equal(ui.batch.value, '');
  assert.equal(ui.batch.options.some(row => row.value === 'one'), false);
  assert.equal(ui.batch.disabled, false);
  assert.equal(ui.barcode.readOnly, true);
  ui.batch.value = 'two';
  ui.batch.handlers.change();
  const retry = ui.form.handlers.submit({preventDefault() {}});
  const second = ui.calls[1][1].body;
  assert.equal(second, first);
  assert.equal(second.get('location_id'), 'two');
  assert.equal(second.get('capture_token'), 'stable-token');
  ui.ack({status: 201, ok: true, json: async () => ({ok: true, draft: {id: 'f'.repeat(32)}})});
  await retry;
  assert.equal(ui.location.reloads, 1);
});
