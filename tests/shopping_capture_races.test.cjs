const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');

const entry = readFileSync(require.resolve('../static/js/shopping.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let finish; const pending = new Promise(resolve => { finish = resolve; }); return {pending, finish}; };

function ui() {
  function element() {
    const handlers = {};
    return {value: '', hidden: false, files: [], readyState: 2, srcObject: null,
      addEventListener: (name, fn) => { handlers[name] = fn; },
      fire(name) { return handlers[name]?.({target: this}); },
      focus() {}, play() {}, pause() {}, removeAttribute() {}};
  }
  const e = Object.fromEntries(['shopping-app', 'shopping-barcode', 'shopping-name', 'shopping-barcode-scan',
    'shopping-barcode-check', 'shopping-camera-stop', 'shopping-camera', 'shopping-camera-box',
    'shopping-scan-status', 'shopping-product-photo', 'shopping-photo-preview', 'shopping-photo-clear']
    .map(id => [id, element()]));
  e['shopping-app'].dataset = {barcodeUrl: '/local-lookup'};
  const photos = [], detections = [], frames = [], requests = [];
  let stopped = 0;
  class BarcodeDetector { detect() { const n = deferred(); detections.push(n); return n.pending; } }
  const window = {BarcodeDetector, requestAnimationFrame: cb => frames.push(cb), addEventListener() {},
    SimpleOfficeCamera: {openCameraStream: async () => ({getTracks: () => [{stop() { stopped += 1; }}]})}};
  vm.runInNewContext(entry, {window, document: {getElementById: id => e[id], documentElement: {lang: 'en'}},
    navigator: {mediaDevices: {getUserMedia() {}}},
    createImageBitmap: async file => { const b = {closed: false, close() { this.closed = true; }}; photos.push([file, b]); return b; },
    fetch: url => { const n = {...deferred(), url}; requests.push(n); return n.pending; }});
  return {e, photos, detections, frames, requests, stopped: () => stopped};
}

test('a stopped camera does not accept a detector result that arrives later', async () => {
  const t = ui(), {e} = t;
  await e['shopping-barcode-scan'].fire('click');
  t.frames.shift()(500);
  e['shopping-camera-stop'].fire('click');
  t.detections[0].finish([{rawValue: '9783960091905'}]);
  await tick();
  assert.equal(t.stopped(), 1);
  assert.equal(e['shopping-barcode'].value, '');
  assert.equal(e['shopping-camera'].srcObject, null);
  assert.equal(t.requests.length, 0);
});

test('only the latest photo can supply an empty barcode; every bitmap closes', async () => {
  const t = ui(), {e} = t, photo = e['shopping-product-photo'];
  photo.files = [{name: 'old.png', size: 100}]; photo.fire('change'); await tick();
  photo.files = [{name: 'new.png', size: 100}]; photo.fire('change'); await tick();
  t.detections[0].finish([{rawValue: '9783960091905'}]); await tick();
  t.detections[1].finish([{rawValue: '9783960106289'}]); await tick();
  assert.deepEqual(t.requests.map(x => x.url), ['/local-lookup?code=9783960106289']);
  t.requests[0].finish({ok: true, json: async () => ({barcode: '9783960106289'})}); await tick();
  assert.equal(e['shopping-barcode'].value, '9783960106289');
  assert(t.photos.every(([, b]) => b.closed));
});

test('typing a new barcode wins over an older lookup and its product fields', async () => {
  const t = ui(), {e} = t, code = e['shopping-barcode'];
  code.value = '9783960091905'; code.fire('change');
  code.value = '9783960106289'; code.fire('input'); code.fire('change');
  t.requests[1].finish({ok: true, json: async () => ({known: true, barcode: code.value, item: {name: 'New'}})});
  await tick();
  t.requests[0].finish({ok: true, json: async () => ({known: true, barcode: '9783960091905', item: {name: 'Old'}})});
  await tick();
  assert.equal(code.value, '9783960106289');
  assert.equal(e['shopping-name'].value, 'New');
});
