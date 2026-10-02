const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/admin/osm_address_index.html'), 'utf8');
const start = template.indexOf('  async function updateSize()');
const end = template.indexOf("  select?.addEventListener('change', updateSize);", start);
assert.ok(start >= 0 && end > start);

function createLookup(fetch) {
  const context = {
    select: {value: ''}, size: {textContent: ''}, button: {textContent: 'Download + Index'},
    english: false, endpoint: '/admin/osm-addresses/region-info.json', fetch,
  };
  vm.createContext(context);
  vm.runInContext('let serial = 0;\n' + template.slice(start, end), context);
  return context;
}

test('unselected region makes no request and shows selection hint', async () => {
  const context = createLookup(() => assert.fail('empty region must not be requested'));
  await context.updateSize();
  assert.equal(context.size.textContent, 'Region auswählen, um die Downloadgröße zu ermitteln.');
});

test('selected region requests metadata and updates size', async () => {
  const context = createLookup(async url => {
    assert.equal(url, '/admin/osm-addresses/region-info.json?region=germany');
    return {ok: true, json: async () => ({region: {bytes: 1024, size: '1 KiB'}})};
  });
  context.select.value = 'germany';
  await context.updateSize();
  assert.equal(context.size.textContent, 'Erwartete Downloadgröße: 1 KiB');
  assert.equal(context.button.textContent, 'Download 1 KiB + Index');
});

test('deselecting invalidates an outstanding metadata response', async () => {
  let resolve;
  const context = createLookup(() => new Promise(done => {resolve = done;}));
  context.select.value = 'germany';
  const pending = context.updateSize();
  context.select.value = '';
  await context.updateSize();
  resolve({ok: true, json: async () => ({region: {bytes: 1024, size: '1 KiB'}})});
  await pending;
  assert.equal(context.size.textContent, 'Region auswählen, um die Downloadgröße zu ermitteln.');
});

test('metadata failure keeps a usable page with a diagnostic hint', async () => {
  const context = createLookup(async () => {throw new Error('offline');});
  context.select.value = 'germany';
  await context.updateSize();
  assert.equal(context.size.textContent, 'Downloadgröße konnte vorab nicht bestimmt werden.');
});
