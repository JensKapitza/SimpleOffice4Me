const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture() {
  const elements = new Map(), timers = new Map(), posts = [], applied = [], requests = [];
  const holds = new Map(), rejected = new Set(), listeners = {};
  let nextTimer = 0, rejectGet = false;
  const node = () => ({value: '', textContent: '', children: [], handlers: {}, disabled: false,
    classList: {hidden: true, toggle(name, value) {this.hidden = value;}},
    addEventListener(type, callback) {this.handlers[type] = callback;},
    appendChild(child) {this.children.push(child);}, append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this.children = children;}, focus() {}});
  const el = id => {if (!elements.has(id)) elements.set(id, node()); return elements.get(id);};
  const data = {state: {permission: 'none', generation: ''}, events: [], fields: [], commands: [], epoch: '1'};
  const context = {document: {getElementById: id => el(id.replace('screen-support-', '')), querySelector: () => ({content: 'token'}), createElement: node},
    window: {setTimeout(fn, ms) {timers.set(++nextTimer, {fn, ms}); return nextTimer;}, clearTimeout(id) {timers.delete(id);}, addEventListener(name, callback) {listeners[name] = callback;},
      SimpleOfficeScreenWorkspace: {fields: () => ({epoch: '1', fields: []}), apply(command) {applied.push(command); return true;}}},
    localStorage: {getItem: () => null, setItem() {}}, AbortController,
    fetch: async (url, options) => {
      const kind = options.method === 'POST' ? JSON.parse(options.body).type : 'GET';
      requests.push({url, kind, body: options.body && JSON.parse(options.body)});
      if (rejected.has(kind)) throw new Error(`${kind} network failure`);
      if (options.method === 'POST') {
        const body = JSON.parse(options.body); posts.push(body);
        if (body.type === 'grant') data.state = {permission: 'granted', generation: 'nonce'};
        if (['deny', 'revoke', 'leave', 'join'].includes(body.type)) data.state = {permission: 'none', generation: ''};
      } else if (rejectGet) throw new Error('offline');
      const snapshot = JSON.parse(JSON.stringify(data));
      const hold = holds.get(kind);
      if (hold) { holds.delete(kind); await hold.promise; }
      return {ok: true, json: async () => snapshot};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/screen_support.js'), 'utf8'), context);
  const tick = async () => {
    const entry = [...timers.entries()].find(([, timer]) => timer.ms === 900);
    assert.ok(entry, 'support poll scheduled'); timers.delete(entry[0]); await entry[1].fn();
  };
  return {api: context.window.SimpleOfficeScreenSupport, el, posts, data, applied, tick, requests,
    hold(kind) {
      let release, reject;
      const promise = new Promise((resolve, fail) => {release = resolve; reject = fail;});
      holds.set(kind, {promise}); return {release, reject};
    },
    fail(kind) {rejected.add(kind);},
    workspaceChanged() {listeners['screen-workspace-change']?.();},
    offline() {rejectGet = true;}};
}

test('Notices show text safely only to opposite role; acknowledgments refer to notice', async () => {
  const f = fixture(); await f.api.start({session_id: 'sid'}, 'sender', 'CODE');
  f.data.events = [{seq: 1, type: 'notice', from: 'sender', at: 1, text: 'own'}, {seq: 2, type: 'notice', from: 'receiver', at: 2, text: '<script>unsafe</script>'}];
  await f.tick();
  assert.equal(f.el('popup-text').textContent, '<script>unsafe</script>');
  assert.equal(f.el('popup').classList.hidden, false);
  assert.equal(f.el('history').children.length, 2);
  await f.el('ack').handlers.click();
  assert.equal(f.posts.at(-1).type, 'ack'); assert.equal(f.posts.at(-1).event_seq, 2);
  assert.equal(f.el('popup').classList.hidden, true);
});

test('Explicit approval enables matching generation commands; revoke blocks stale commands', async () => {
  const f = fixture(); await f.api.start({session_id: 'sid'}, 'sender', 'CODE');
  f.data.state.permission = 'requested'; await f.tick();
  assert.equal(f.el('permission-popup').classList.hidden, false);
  await f.el('popup-grant').handlers.click();
  f.data.commands = [{seq: 1, generation: 'old', field_id: 'f1'}, {seq: 2, generation: 'nonce', field_id: 'f1'}];
  await f.tick();
  assert.equal(f.applied.length, 1);
  assert.equal(f.posts.at(-1).type, 'input-result');
  assert.equal(f.posts.at(-1).outcome, 'applied');
  await f.el('banner-revoke').handlers.click();
  f.data.commands.push({seq: 3, generation: 'nonce', field_id: 'f1'}); await f.tick();
  assert.equal(f.applied.length, 1);
});

test('Receiver joins before polling, sends leave on stop and cannot locally grant', async () => {
  const f = fixture(); await f.api.start({session_id: 'sid'}, 'receiver', 'CODE');
  assert.equal(f.posts[0].type, 'join');
  assert.equal(f.el('grant').disabled, true);
  f.api.stop(); await Promise.resolve();
  assert.equal(f.posts.at(-1).type, 'leave');
  assert.equal(f.el('send').disabled, true);
});

test('Permission channel failure blocks input locally and requests server revocation', async () => {
  const f = fixture(); await f.api.start({session_id: 'sid'}, 'sender', 'CODE');
  f.data.state.permission = 'requested'; await f.tick(); await f.el('grant').handlers.click();
  f.offline(); await f.tick();
  assert.equal(f.posts.at(-1).type, 'revoke');
  assert.match(f.el('error').textContent, /offline/);
});


const settle = () => new Promise(resolve => setImmediate(resolve));

test('Delayed grant cannot undo local revoke or disconnect when server revocation fails', async () => {
  for (const veto of ['revoke', 'disconnect']) {
    const f = fixture(); await f.api.start({session_id: 'sid'}, 'sender', 'CODE');
    f.data.state.permission = 'requested'; await f.tick();
    const delayed = f.hold('grant');
    const granting = f.el('grant').handlers.click(); await settle();
    await f.tick(); // Poll sees server grant before the grant response arrives.
    f.fail('revoke');
    if (veto === 'revoke') await f.el('revoke').handlers.click();
    else f.api.disconnected();
    f.data.commands = [{seq: 1, generation: 'nonce', field_id: 'f1', value: 'after veto'}];
    delayed.release(); await granting; await f.tick();
    assert.equal(f.applied.length, 0, veto);
    assert.equal(f.el('active-banner').classList.hidden, true);
  }
});

test('Old grant and old poll cannot activate or acknowledge commands in a replacement session', async () => {
  const f = fixture(); await f.api.start({session_id: 'old'}, 'sender', 'CODE');
  f.data.state.permission = 'requested'; await f.tick();
  const delayedGrant = f.hold('grant'), granting = f.el('grant').handlers.click(); await settle();
  f.api.stop(); await settle();
  await f.api.start({session_id: 'new'}, 'sender', 'NEW');
  f.data.state = {permission: 'granted', generation: 'nonce'};
  f.data.commands = [{seq: 1, generation: 'nonce', field_id: 'f1'}];
  delayedGrant.release(); await granting; await f.tick();
  assert.equal(f.applied.length, 0);
  assert.equal(f.el('active-banner').classList.hidden, true);
});

test('A poll awaiting field publication cannot issue a GET for a replacement session', async () => {
  const f = fixture(), delayed = f.hold('fields');
  const starting = f.api.start({session_id: 'old'}, 'sender', 'CODE'); await settle();
  f.api.stop(); await settle();
  await f.api.start({session_id: 'new'}, 'sender', 'NEW');
  const before = f.requests.length;
  delayed.release(); await starting;
  assert.equal(f.requests.length, before);
});

test('An old command batch awaiting receipt cannot edit or advance a replacement session cursor', async () => {
  const f = fixture(); await f.api.start({session_id: 'old'}, 'sender', 'CODE');
  f.data.state.permission = 'requested'; await f.tick(); await f.el('grant').handlers.click();
  f.data.commands = [{seq: 10, generation: 'nonce', field_id: 'f1'}, {seq: 11, generation: 'nonce', field_id: 'f2'}];
  const delayed = f.hold('input-result'), polling = f.tick(); await settle();
  assert.equal(f.applied.length, 1);
  f.api.stop(); await settle();
  f.data.commands = [];
  await f.api.start({session_id: 'new'}, 'sender', 'NEW');
  f.data.state.permission = 'requested'; await f.tick(); await f.el('grant').handlers.click();
  delayed.release(); await polling;
  assert.equal(f.applied.length, 1);
  await f.tick();
  assert.match(f.requests.at(-1).url, /sessions\/new\/support.*after_command=0/);
  assert.equal(f.requests.filter(request => request.kind === 'input-result').length, 1);
});

test('Unchanged workspace notifications do not republish and discard pending field input', async () => {
  const f = fixture(); await f.api.start({session_id: 'sid'}, 'sender', 'CODE');
  const publications = f.posts.filter(body => body.type === 'fields').length;
  f.workspaceChanged(); await f.tick();
  assert.equal(f.posts.filter(body => body.type === 'fields').length, publications);
});
