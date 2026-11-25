const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const html = readFileSync(path.join(__dirname, '../templates/documents/images.html'), 'utf8');
const script = html.split('<script>').at(-1).split('</script>')[0]
  .replace("{{ 'true' if g.language == 'en' else 'false' }}", 'false');

function gallery(fetch) {
  function element() {
    const listeners = {};
    return {
      disabled: false, checked: false, style: {}, classList: { remove() {} },
      addEventListener(type, callback) { listeners[type] = callback; },
      setAttribute() {}, children: [],
      append(child) { this.children.push(child); },
      prepend(child) { this.children.unshift(child); },
      set innerHTML(value) { assert.equal(value, ''); this.children = []; },
      submit() { return listeners.submit({ preventDefault() {} }); },
    };
  }
  const elements = new Map();
  function get(id) {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  }
  get('photo-bulk-form').action = '/documents/photos/upload';
  get('photo-csrf').value = 'test-csrf';
  get('photo-files').files = ['first.jpg', 'second.jpg'].map(name => ({
    name, size: 2048, type: 'image/jpeg',
  }));
  vm.runInNewContext(script, {
    document: { getElementById: get, createElement: element }, FormData: class extends Map {
      append(key, value) { this.set(key, value); }
    },
    fetch,
  });
  return { form: get('photo-bulk-form'), get };
}

test('mobile photo import applies the starting settings to every photo, including after a failed upload', async () => {
  let failFirst;
  const requests = [];
  const { form, get } = gallery((url, { body, headers }) => {
    assert.equal(url, '/documents/photos/upload');
    assert.equal(headers['X-CSRF-Token'], 'test-csrf');
    requests.push(body);
    if (requests.length === 1) return new Promise(resolve => { failFirst = resolve; });
    return Promise.resolve({ ok: true, json: async () => ({ ok: true, detail_url: '/documents/detail/photo' }) });
  });
  const archive = get('photo-archive');
  const ocr = get('photo-ocr');
  archive.checked = true;
  const pending = form.submit();
  const optionsWereDisabled = archive.disabled && ocr.disabled;
  archive.checked = false;
  ocr.checked = true;
  failFirst({ ok: false, status: 415, json: async () => ({ error: 'Dieses Fotoformat ist ungültig' }) });
  await pending;
  assert.deepEqual(requests.map(data => [data.get('file').name, data.get('archive'), data.get('run_ocr')]),
    [['first.jpg', '1', '0'], ['second.jpg', '1', '0']]);
  assert.ok(optionsWereDisabled);
  assert.equal(requests[0].get('batch_id'), requests[1].get('batch_id'));
  assert.match(get('photo-errors').children[0].textContent, /first\.jpg/);
  assert.equal(get('photo-results').children.length, 1);
  assert.equal(get('photo-progress-percent').textContent, '100 %');
  for (const id of ['photo-start', 'photo-files', 'photo-archive', 'photo-ocr']) assert.equal(get(id).disabled, false);
});

test('another submit cannot start a second import before the current batch completes', async () => {
  const requests = [];
  let release;
  const success = { ok: true, json: async () => ({ ok: true, detail_url: '/documents/detail/photo' }) };
  const { form, get } = gallery((_url, { body }) => {
    requests.push(body);
    if (requests.length === 1) return new Promise(resolve => { release = resolve; });
    return Promise.resolve(success);
  });
  const first = form.submit();
  const overlapping = form.submit();
  release(success);
  await Promise.all([first, overlapping]);
  assert.deepEqual(requests.map(data => data.get('file').name), ['first.jpg', 'second.jpg']);
  get('photo-archive').checked = true;
  get('photo-ocr').checked = true;
  await form.submit();
  assert.deepEqual(requests.slice(2).map(data => [data.get('archive'), data.get('run_ocr')]), [['1', '1'], ['1', '1']]);
});
