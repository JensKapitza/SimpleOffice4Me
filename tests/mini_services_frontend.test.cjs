const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function fixture(scan) {
  const nodes = [];
  function node(tag) {
    const value = {tag, children: [], attributes: {}, handlers: {}, textContent: '',
      append(...items) { this.children.push(...items); },
      appendChild(item) { this.children.push(item); },
      setAttribute(key, value) { this.attributes[key] = value; },
      removeAttribute(key) { delete this.attributes[key]; },
      addEventListener(key, fn) { this.handlers[key] = fn; },
      querySelectorAll() { return nodes.filter(item => item.tag === 'button'); }};
    Object.defineProperty(value, 'innerHTML', {set() { throw Error('Unsafe HTML rendering'); }});
    nodes.push(value); return value;
  }
  const elements = Object.fromEntries(['mini-service-control', 'mini-feedback', 'mini-control-cards', 'mini-refresh'].map(id => [id, node('div')]));
  elements['mini-service-control'].dataset = {api: '/api/mini-services'};
  const service = {id: 'audio-sender', name: 'Audio', state: 'stopped', capabilities: ['scan'], settings: {}, scan,
    owner: 'web', requires: ['web'], optional_requires: [], provides: ['audio-sender'], version: 'test'};
  let postFailure = false;
  const context = {Headers, AbortController,
    setTimeout: () => 1, clearTimeout() {},
    document: {hidden: false, getElementById: id => elements[id], createElement: node, querySelector: () => ({content: 'csrf'})},
    fetch: async (url, options) => {
      if (options.method === 'POST' && postFailure) {
        service.scan = {state: 'failed', error: {message: 'Audio-Gerät fehlt.', action: 'Erneut suchen.'}};
        return {ok: false, json: async () => ({error: {message: 'Audio-Gerät fehlt.'}})};
      }
      return {ok: true, json: async () => ({services: [service]})};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/mini_services.js'), 'utf8'), context);
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, elements, service, failPost() { postFailure = true; },
    scanNode: () => nodes.find(item => item.attributes.role === 'status')};
}

test('Scan states distinguish progress, failure, empty results and unsupported hardware checks', async () => {
  for (const [scan, expected] of [
    [undefined, /Noch keine Suche/],
    [{state: 'scanning', count: 0}, /Suche läuft/],
    [{state: 'failed', error: {message: '<Mikrofon fehlt>', action: 'Neu suchen.'}}, /Suche fehlgeschlagen.*<Mikrofon fehlt>.*Neu suchen/],
    [{state: 'completed', count: 0}, /Keine Treffer/],
    [{state: 'completed', count: 1, scope: 'Hardware nicht geprüft'}, /1 Treffer.*Hardware nicht geprüft/]
  ]) {
    const f = await fixture(scan);
    assert.match(f.scanNode().textContent, expected);
    assert.equal(f.scanNode().attributes['aria-live'], 'polite');
    const diagnosis = JSON.parse(f.nodes.find(item => item.tag === 'pre').textContent);
    assert.deepEqual(diagnosis.requires, ['web']);
    assert.equal(diagnosis.owner, 'web');
  }
});

test('Failed scan refreshes card and restores buttons while keeping the action error', async () => {
  const f = await fixture({state: 'completed', count: 3});
  f.failPost();
  const button = f.nodes.find(item => item.tag === 'button' && item.textContent === 'Suchen');
  await button.handlers.click();
  assert.match(f.scanNode().textContent, /Suche fehlgeschlagen/);
  assert.equal(f.elements['mini-feedback'].textContent, 'Audio-Gerät fehlt.');
  assert.equal(button.disabled, false);
  assert.equal(f.elements['mini-service-control'].attributes['aria-busy'], undefined);
});
