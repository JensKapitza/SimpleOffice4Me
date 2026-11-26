const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {test} = require('node:test');
const vm = require('node:vm');

const entry = readFileSync(require.resolve('../static/js/site_visits.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let finish; const pending = new Promise(resolve => { finish = resolve; }); return {pending, finish}; };

function ui() {
  class Element {
    constructor() { this.handlers = {}; this.children = []; this.style = {}; this.value = ''; this.files = []; this.isConnected = true; }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    fire(name) { return this.handlers[name]?.({preventDefault() {}}); }
    setAttribute() {}
    append(...nodes) { this.children.push(...nodes); }
    remove() { this.isConnected = false; }
  }
  class Video extends Element { constructor() { super(); this.readyState = 2; } play() {} pause() {} }
  const body = new Element(), scanner = new Element(), target = new Element();
  const root = {querySelectorAll: () => [], querySelector: key => ({'[data-barcode-scan]': scanner, '[data-barcode-target]': target}[key] ?? null)};
  const detections = [];
  let stopped = 0;
  class BarcodeDetector { detect(source) { const n = {...deferred(), source}; detections.push(n); return n.pending; } }
  const window = {BarcodeDetector, addEventListener() {}, SimpleOfficeCamera: {
    openCameraStream: async () => ({getTracks: () => [{stop() { stopped += 1; }}]})}};
  const document = {body, querySelector: key => key === '[data-site-visit]' ? root : null,
    createElement: tag => tag === 'video' ? new Video() : new Element()};
  vm.runInNewContext(entry, {window, document, navigator: {mediaDevices: {getUserMedia() {}}}, BarcodeDetector,
    HTMLVideoElement: Video, location: {pathname: '/site-visits/record'}, URL, requestAnimationFrame() {},
    createImageBitmap: async file => ({file, closed: false, close() { this.closed = true; }})});
  return {scanner, target, body, detections, stopped: () => stopped};
}

test('choosing a photo takes precedence over an in-progress camera frame', async () => {
  const t = ui(); await t.scanner.fire('click');
  const file = t.body.children[0].children[2].children[0].children[0];
  file.files = [{name: 'replacement.png'}]; const photo = file.fire('change'); await tick();
  t.detections[0].finish([{rawValue: 'old live code'}]); await tick();
  assert.equal(t.target.value, '');
  assert.equal(t.stopped(), 1);
  const fresh = t.detections[1]; fresh.finish([{rawValue: 'selected photo code'}]); await photo;
  assert.equal(t.target.value, 'selected photo code');
  assert.equal(fresh.source.closed, true);
});

test('the latest selected photo wins, and a discarded bitmap is still closed', async () => {
  const t = ui(); await t.scanner.fire('click');
  const file = t.body.children[0].children[2].children[0].children[0];
  file.files = [{name: 'first.png'}]; const first = file.fire('change'); await tick();
  file.files = [{name: 'second.png'}]; const second = file.fire('change'); await tick();
  t.detections[1].finish([{rawValue: 'old photo'}]); await first;
  assert.equal(t.target.value, '');
  assert.equal(t.detections[1].source.closed, true);
  t.detections[2].finish([{rawValue: 'new photo'}]); await second;
  assert.equal(t.target.value, 'new photo');
  t.detections[0].finish([]); await tick();
});
