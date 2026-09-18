const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/audio_output_remote.js'), 'utf8');
function fixture(responses) {
  const nodes = new Map(), calls = [], events = [];
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, {value: '', dataset: {}, listeners: {}, options: [],
      addEventListener(type, fn) { this.listeners[type] = fn; },
      setAttribute() {}, removeAttribute() {}, replaceChildren(...items) { this.options = items; },
      add(option) { this.options.push(option); }, querySelector() { return get('submit'); }});
    return nodes.get(id);
  };
  get('announcement-remote').dataset.scanUrl = '/scan';
  get('audio-announcements').dataset.api = '/audio';
  vm.runInNewContext(source, {document: {getElementById: get, querySelector: () => ({content: 'csrf'})},
    window: {dispatchEvent: event => events.push(event.type)}, Event: class {constructor(type) {this.type = type;}},
    Option: class {constructor(text, value) {this.text = text; this.value = value;}},
    AbortController, setTimeout, clearTimeout, Date,
    fetch: async (url, options) => {calls.push({url, options}); return responses.shift();}});
  return {get, calls, events, settle: () => new Promise(resolve => setImmediate(resolve))};
}
test('discovery fills manual fields without sending; save binds explicit target with CSRF', async () => {
  const f = fixture([{ok: true, json: async () => ({targets: [{id: '192.168.1.2:5004', host: '192.168.1.2', port: 5004, label: '<b>Speaker</b>'}], updated_at: 1})},
    {ok: true, json: async () => ({name: 'Speaker'})}]);
  f.get('announcement-remote-scan').listeners.click(); await f.settle();
  assert.match(f.get('announcement-remote-status').textContent, /1 aktive/);
  f.get('announcement-remote-select').value = '0'; f.get('announcement-remote-select').listeners.change();
  assert.equal(f.get('announcement-remote-host').value, '192.168.1.2');
  assert.equal(f.calls.length, 1);
  f.get('announcement-remote-volume').value = '40';
  f.get('announcement-remote-form').listeners.submit({preventDefault() {}}); await f.settle();
  assert.equal(f.calls[1].url, '/audio/outputs');
  assert.equal(f.calls[1].options.headers['X-CSRF-Token'], 'csrf');
  const payload = JSON.parse(f.calls[1].options.body);
  assert.deepEqual(payload.transport, {kind: 'rtp-udp', host: '192.168.1.2', port: 5004});
  assert.equal(payload.volume, 40);
  assert.deepEqual(f.events, ['simpleoffice:audio-output-saved']);
  assert.match(f.get('announcement-remote-status').textContent, /nicht bestätigt/);
});
test('failed scan restores controls and shows error instead of successful empty result', async () => {
  const f = fixture([{ok: false, json: async () => ({error: 'Netzwerk nicht verfügbar'})}]);
  f.get('announcement-remote-scan').listeners.click(); await f.settle();
  assert.equal(f.get('announcement-remote-status').textContent, 'Netzwerk nicht verfügbar');
  assert.equal(f.get('announcement-remote-scan').disabled, false);
  assert.equal(f.get('submit').disabled, false);
  assert.equal(f.events.length, 0);
});
