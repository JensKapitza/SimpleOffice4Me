const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/v3_android_offline.js'), 'utf8');
const operation = id => ({
  operationId: id, targetId: 'task-' + id, baseVersion: 'base-' + id,
  type: 'task_status', status: 'pending', payload: {status: 'completed'},
});
const receipt = (id, status) => ({...operation(id), status});

class Element {
  listeners = new Map();
  addEventListener(key, fn) { this.listeners.set(key, fn); }
  fire(key, value) { return this.listeners.get(key)?.(value); }
}

function native(ids, {badCache, badAck} = {}) {
  const queue = ids.map(operation);
  const calls = [];
  return {
    queue, calls,
    status: () => ({enabled: true, outbox: queue.length}),
    items: () => [],
    outbox: () => queue.slice(),
    cache(id, kind, version) {
      calls.push(['cache', id, kind, version]);
      return id === badCache ? 'io-error' : 'ok';
    },
    ack(id, result) {
      calls.push(['ack', id, result]);
      if (id === badAck) return 'io-error';
      const i = queue.findIndex(op => op.operationId === id);
      if (result === 'synced') queue.splice(i, 1);
      else if (i >= 0) queue[i].status = result;
      return 'ok';
    },
    clear() { calls.push(['clear']); queue.length = 0; return 'ok'; },
    switchAccount() { calls.push(['switch']); return 'ok'; },
  };
}

async function page(bridge, {online = true, network = ''} = {}) {
  const win = new Element();
  win.SimpleOfficeOffline = bridge;
  win.confirm = () => true;
  win.SimpleOfficeI18n = {text: (_de, en) => en};
  const nav = {onLine: online};
  const names = ['message', 'sync', 'switch-account', 'clear', 'cache-task'];
  const dom = Object.fromEntries(names.map(name => [name, new Element()]));
  dom['cache-task'].dataset = {offlineCacheTask: 'task-manual'};
  const dataset = {soNetwork: network};
  const root = {dataset: {enabled: '1', syncApi: '/offline/sync', taskApiPrefix: '/offline/task/'},
    querySelector: selector => dom[selector.slice(14, -1)] || null,
    querySelectorAll: selector => selector === '[data-offline-cache-task]' ? [dom['cache-task']] : []};
  const requests = [];
  const timers = new Map();
  let serial = 0;
  win.setTimeout = (fn, delay) => { timers.set(++serial, {fn, delay}); return serial; };
  win.clearTimeout = id => timers.delete(id);
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const fetch = (url, options) => new Promise((resolve, reject) => {
    requests.push({url, options, resolve, reject});
    options.signal?.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
  });
  vm.runInNewContext(script, {
    document: {querySelector: selector => selector === '[data-android-offline-app]'
      ? root : {content: 'fixture-csrf'}, documentElement: {dataset}},
    navigator: nav, window: win, fetch, AbortController,
  });
  await tick();
  return {win, nav, dom, dataset, requests, tick, timers,
    async reply(index, payload) {
      requests[index].resolve({ok: true, json: async () => payload});
      await tick();
    }};
}

test('manual, browser and native online signals share one running sync and protect local clear', async () => {
  const bridge = native(['a']);
  const p = await page(bridge);
  p.dom.sync.fire('click');
  p.dom.clear.fire('click');
  p.dom['switch-account'].fire('click');
  p.win.fire('online');
  p.win.fire('simpleoffice:network', {detail: {online: true}});
  p.win.fire('simpleoffice:native-ready');
  await p.tick();
  assert.equal(p.requests.length, 1);
  assert.equal(p.requests[0].options.headers['X-CSRF-Token'], 'fixture-csrf');
  assert.equal(p.dom.sync.disabled, true);
  assert.deepEqual(bridge.calls, []);
  await p.reply(0, {results: [receipt('a', 'synced')]});
  assert.equal(p.requests[1].url, '/offline/task/task-a');
  assert.equal(p.dom.sync.disabled, true);
  assert.deepEqual(bridge.calls, []);
  await p.reply(1, {id: 'task-a', version: 'new'});
  assert.deepEqual(bridge.calls, [['cache', 'task-a', 'task', 'new'], ['ack', 'a', 'synced']]);
  assert.match(p.dom.message.textContent, /^Synced: 1 .* Still pending: 0$/);
  assert.match(p.dom.message.className, /success$/);
  assert.equal(p.dom.sync.disabled, false);
});

test('conflict and rejection counts are distinct; incomplete or unconfirmed operations stay pending', async () => {
  const bridge = native(['a', 'b', 'c', 'd', 'e', 'f', 'g'], {badCache: 'task-d', badAck: 'e'});
  const p = await page(bridge);
  await p.reply(0, {results: [
    receipt('a', 'conflict'), receipt('b', 'rejected'), receipt('d', 'synced'), receipt('e', 'conflict'),
    {...receipt('f', 'synced'), baseVersion: 'different batch'}, receipt('g', 'synced'), receipt('g', 'rejected'),
  ]});
  assert.equal(p.requests[1].url, '/offline/task/task-d');
  await p.reply(1, {id: 'task-d', version: 'new'});
  assert.equal(p.requests.length, 2);
  assert.deepEqual(bridge.calls, [
    ['ack', 'a', 'conflict'], ['ack', 'b', 'rejected'],
    ['cache', 'task-d', 'task', 'new'], ['ack', 'e', 'conflict'],
  ]);
  assert.match(p.dom.message.textContent, /^Synced: 0 · Conflicts: 1 · Rejected: 1 · Still pending: 5$/);
  assert.match(p.dom.message.className, /warning$/);
});

test('a delayed task refresh from a replaced native bridge cannot update either workspace', async () => {
  const old = native(['a']);
  const p = await page(old);
  await p.reply(0, {results: [receipt('a', 'synced')]});
  const next = native(['b']);
  p.win.SimpleOfficeOffline = next;
  p.win.fire('simpleoffice:native-ready');
  await p.tick();
  await p.reply(1, {id: 'task-a', version: 'new'});
  assert.deepEqual(old.calls, []);
  assert.deepEqual(next.calls, []);
  assert.equal(p.dom.sync.disabled, false);
  p.dom.sync.fire('click');
  assert.equal(JSON.parse(p.requests[2].options.body).operations[0].operationId, 'b');
});

test('manual offline and transport failure leave queued work and allow explicit retry', async () => {
  const bridge = native(['a']);
  const p = await page(bridge, {network: 'offline'});
  p.dom.sync.fire('click');
  assert.equal(p.requests.length, 0);
  assert.match(p.dom.message.textContent, /Offline.*outbox/);
  p.dataset.soNetwork = 'online';
  p.win.fire('simpleoffice:network', {detail: {online: true}});
  p.requests[0].reject(new Error('server diagnostic not for the page'));
  await p.tick();
  assert.match(p.dom.message.textContent, /Could not synchronize.*outbox/);
  assert.doesNotMatch(p.dom.message.textContent, /server diagnostic/);
  assert.deepEqual(bridge.calls, []);
  p.dom.sync.fire('click');
  assert.equal(p.requests.length, 2);
});


test('clearing local data while a manual cache read is pending prevents late re-caching', async () => {
  const bridge = native([]);
  const p = await page(bridge);
  p.dom['cache-task'].fire('click');
  assert.equal(p.requests[0].url, '/offline/task/task-manual');
  await p.dom.clear.fire('click');
  await p.reply(0, {id: 'task-manual', version: 'new'});
  assert.deepEqual(bridge.calls, [['clear']]);
  assert.equal(p.dom.message.textContent, 'Local offline data was deleted.');
});


test('one deadline covers POST and read-back; a hung refresh leaves the operation pending and unlocks', async () => {
  const bridge = native(['a']);
  const p = await page(bridge);
  await p.reply(0, {results: [receipt('a', 'synced')]});
  assert.equal(p.dom.sync.disabled, true);
  assert.equal(p.requests[1].options.signal, p.requests[0].options.signal);
  const [timer] = p.timers.values();
  assert.equal(timer.delay, 30000);
  timer.fn();
  await p.tick();
  assert.deepEqual(bridge.calls, []);
  assert.equal(p.dom.sync.disabled, false);
  assert.equal(p.dom.clear.disabled, false);
  assert.match(p.dom.message.textContent, /Still pending: 1/);
  p.dom.sync.fire('click');
  assert.equal(JSON.parse(p.requests[2].options.body).operations[0].operationId, 'a');
});
