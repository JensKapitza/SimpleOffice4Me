const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const test = require('node:test');
const {runInNewContext} = require('node:vm');

const code = readFileSync('static/js/inventory_intake.js', 'utf8');

function harness() {
  let request;
  function node(base = {}) {
    const handlers = {};
    return {handlers, addEventListener: (name, callback) => { handlers[name] = callback; }, ...base};
  }
  const image = node({removeAttribute() {}});
  const status = node();
  const submit = node();
  const picture = node({hidden: true});
  const form = node({action: '/inventory/capture/intake', querySelector: () => submit, requestSubmit() {}});
  const input = node({files: [{size: 12, type: 'image/png'}]});
  const pending = node({hidden: true, querySelector: key => key === 'img' ? image : key.includes('pending-photo') ? picture : status});
  const map = {'#intake-capture': form, '#intake-photo': input, '#intake-pending': pending};
  const location = {reloads: 0, reload() { this.reloads++; }};
  const calls = [];
  const url = {createObjectURL: () => 'blob:synthetic-photo', revokeObjectURL() {}};
  const window = node({
    URL: url, location, fetch: (path, options) => {
      calls.push([path, options]);
      return new Promise(resolve => { request = resolve; });
    },
  });
  const ctx = {window, URL: url, FormData: class {constructor(actual) { assert.equal(actual, form); }},
    document: {querySelector: key => map[key], querySelectorAll: () => []}};
  runInNewContext(code, ctx);
  return {input, form, pending, picture, image, status, calls, location, ack: data => request(data)};
}

test('immediate local square preview; stored state only after server acknowledgment', async () => {
  const ui = harness();
  ui.input.handlers.change();
  assert.equal(ui.pending.hidden, false);
  assert.equal(ui.image.src, 'blob:synthetic-photo');
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

test('unconfirmed upload leaves photo selected and can be retried', async () => {
  const ui = harness();
  ui.input.handlers.change();
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
  const saving = ui.form.handlers.submit({preventDefault() {}});
  assert.equal(ui.pending.hidden, false);
  assert.equal(ui.picture.hidden, true);
  assert.equal(ui.image.src, undefined);
  assert.equal(ui.calls.length, 1);
  ui.ack({ok: true, json: async () => ({ok: true, draft: {id: 'b'.repeat(32)}})});
  await saving;
  assert.equal(ui.location.hash, 'draft-' + 'b'.repeat(32));
});
