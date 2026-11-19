const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/document_transfer_packet.js'), 'utf8');

function fixture() {
  const input = {value: '', listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; }};
  const button = {disabled: true, listeners: {}, addEventListener(type, fn) { this.listeners[type] = fn; }};
  const status = {textContent: ''};
  const urls = [], clicks = [];
  const document = {documentElement: {lang: 'en'}, querySelector(selector) {
    return selector === '[data-transfer-input]' ? input : selector === '[data-transfer-download]' ? button : selector === '[data-transfer-status]' ? status : null;
  }, createElement() { return {click() { clicks.push(this); }}; }};
  vm.runInNewContext(source, {document, window: {setTimeout}, crypto: webcrypto, atob,
    Blob: class Blob { constructor(parts, options) { this.parts = parts; this.options = options; } },
    URL: {createObjectURL(blob) { urls.push(blob); return 'blob:test'; }, revokeObjectURL() {} }});
  return {input, button, status, urls, clicks};
}
const settle = () => new Promise(resolve => setTimeout(resolve, 10));
async function packet(text) {
  const bytes = new TextEncoder().encode(text);
  const hash = await webcrypto.subtle.digest('SHA-256', bytes);
  const hex = [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('');
  const base64 = Buffer.from(bytes).toString('base64');
  return `SO4ME1|hello.txt|${bytes.length}|${hex}|${base64}`;
}

test('valid packet enables download and saves via an explicit click with octet-stream', async () => {
  const f = fixture(); f.input.value = await packet('hello'); f.input.listeners.input(); await settle();
  assert.equal(f.button.disabled, false);
  assert.match(f.status.textContent, /hello.txt/);
  assert.equal(f.clicks.length, 0);
  f.button.listeners.click();
  assert.equal(f.clicks.length, 1);
  assert.equal(f.clicks[0].download, 'hello.txt');
  assert.equal(f.urls[0].options.type, 'application/octet-stream');
});

test('corrupt packets remain disabled', async () => {
  const f = fixture(); f.input.value = 'SO4ME1|x|1|bad|YQ=='; f.input.listeners.input();
  assert.equal(f.button.disabled, true);
  assert.match(f.status.textContent, /No valid/);
});

test('late digest cannot enable a packet after the input was edited', async () => {
  const f = fixture(); f.input.value = await packet('hello'); f.input.listeners.input();
  f.input.value = 'not a packet'; f.input.listeners.input(); await settle();
  assert.equal(f.button.disabled, true);
  assert.equal(f.status.textContent, 'No valid file packet found.');
});
