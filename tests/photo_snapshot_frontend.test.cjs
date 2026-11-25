const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {test} = require('node:test');
const vm = require('node:vm');

const template = readFileSync(path.join(__dirname, '../templates/documents/_photo_snapshot.html'), 'utf8');
const script = template.split('<script>')[1].split('</script>')[0]
  .replace("{{ 'true' if g.language == 'en' else 'false' }}", 'false')
  .replaceAll('{{ url_for("documents.images") }}', '/documents/images');

function fixture(payload, wantsOcr) {
  const nodes = new Map();
  function get(id) {
    if (!nodes.has(id)) nodes.set(id, {disabled: false, handlers: {}, addEventListener(type, fn) { this.handlers[type] = fn; }});
    return nodes.get(id);
  }
  const form = get('photo-snapshot-form');
  form.action = 'https://office.example/documents/photos/upload';
  form.dataset = {photoDetailsBase: '/documents/'};
  const file = {name:'camera.jpg', size:100, lastModified:0, type:'image/jpeg'};
  get('photo-snapshot-file').files = [file];
  get('photo-snapshot-ocr').checked = wantsOcr;
  get('photo-snapshot-csrf').value = 'fixed-csrf';
  const window = {
    location: {href: 'https://office.example/documents/images', origin: 'https://office.example'},
    listeners: {},
    addEventListener(type, fn) { this.listeners[type] = fn; },
  };
  const requests = [];
  const fetch = async (url, options) => {
    requests.push({url, options});
    return {ok:payload.ok, status:payload.ok ? 200 : 415, json:async () => payload};
  };
  class FormData extends Map {
    constructor() {
      super();
      if (get('photo-snapshot-ocr').checked && !get('photo-snapshot-ocr').disabled) this.set('run_ocr', '1');
    }
  }
  vm.runInNewContext(script, {document:{getElementById:get}, window, URL, FormData, fetch});
  return {form, get, window, requests, submit: () => form.handlers.submit({preventDefault() {}})};
}

test('opt-in camera OCR submits once and opens exactly the photo returned by the server', async () => {
  const f = fixture({ok:true, document_id:'exact-photo', detail_url:'/documents/exact-photo'}, true);
  assert.match(template, /name="run_ocr" value="1"/);
  const first = f.submit();
  const second = f.submit();
  await Promise.all([first,second]);
  assert.equal(f.requests.length, 1);
  assert.equal(f.requests[0].options.body.get('run_ocr'), '1');
  assert.equal(f.requests[0].options.headers['X-CSRF-Token'], 'fixed-csrf');
  assert.equal(f.window.location.href, 'https://office.example/documents/exact-photo');
  assert.match(f.get('photo-snapshot-status').textContent, /Fotodetails/);
  assert.equal(f.get('photo-snapshot-submit').disabled, true);
  f.window.listeners.pageshow({persisted:true});
  for (const id of ['photo-snapshot-file', 'photo-snapshot-submit', 'photo-snapshot-ocr']) {
    assert.equal(f.get(id).disabled, false);
  }
});

test('ordinary snapshot retains gallery return, while opted duplicate opens the existing details without promising OCR success', async () => {
  const payload = {ok:true, duplicate:true, document_id:'exact-photo', detail_url:'/documents/exact-photo'};
  const ordinary = fixture(payload, false);
  await ordinary.submit();
  assert.equal(ordinary.requests[0].options.body.get('run_ocr'), undefined);
  assert.equal(ordinary.window.location.href, '/documents/images');
  const opted = fixture(payload, true);
  await opted.submit();
  assert.equal(opted.window.location.href, 'https://office.example/documents/exact-photo');
  assert.match(opted.get('photo-snapshot-status').textContent, /bereits gespeichert/);
  assert.doesNotMatch(opted.get('photo-snapshot-status').textContent, /Texterkennung abgeschlossen/);
});

test('wrong destination falls back to the gallery and a refused photo permits retry', async () => {
  for (const detail_url of ['https://wrong.example/documents/exact-photo', '/documents/other-photo', undefined]) {
    const f = fixture({ok:true, document_id:'exact-photo', detail_url}, true);
    await f.submit();
    assert.equal(f.window.location.href, '/documents/images');
  }
  const f = fixture({ok:false, error:'Bitte normale Datei auswählen'}, true);
  await f.submit();
  assert.equal(f.window.location.href, 'https://office.example/documents/images');
  assert.match(f.get('photo-snapshot-status').textContent, /normale Datei/);
  for (const id of ['photo-snapshot-file', 'photo-snapshot-submit', 'photo-snapshot-ocr']) {
    assert.equal(f.get(id).disabled, false);
  }
});
