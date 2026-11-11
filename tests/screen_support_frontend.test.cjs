const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function fixture() {
  const elements = new Map(), timers = new Map(), posts = [], applied = [];
  let nextTimer = 0, rejectGet = false;
  const node = () => ({value: '', textContent: '', children: [], handlers: {}, disabled: false,
    classList: {hidden: true, toggle(name, value) {this.hidden = value;}},
    addEventListener(type, callback) {this.handlers[type] = callback;},
    appendChild(child) {this.children.push(child);}, append(...children) {this.children.push(...children);},
    replaceChildren(...children) {this.children = children;}, focus() {}});
  const el = id => {if (!elements.has(id)) elements.set(id, node()); return elements.get(id);};
  const data = {state: {permission: 'none', generation: ''}, events: [], fields: [], commands: [], epoch: '1'};
  const context = {document: {getElementById: id => el(id.replace('screen-support-', '')), querySelector: () => ({content: 'token'}), createElement: node},
    window: {setTimeout(fn, ms) {timers.set(++nextTimer, {fn, ms}); return nextTimer;}, clearTimeout(id) {timers.delete(id);}, addEventListener() {},
      SimpleOfficeScreenWorkspace: {fields: () => ({epoch: '1', fields: []}), apply(command) {applied.push(command); return true;}}},
    localStorage: {getItem: () => null, setItem() {}}, AbortController,
    fetch: async (url, options) => {
      if (options.method === 'POST') {
        const body = JSON.parse(options.body); posts.push(body);
        if (body.type === 'grant') data.state = {permission: 'granted', generation: 'nonce'};
        if (['deny', 'revoke', 'leave', 'join'].includes(body.type)) data.state = {permission: 'none', generation: ''};
      } else if (rejectGet) throw new Error('offline');
      return {ok: true, json: async () => JSON.parse(JSON.stringify(data))};
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/js/screen_support.js'), 'utf8'), context);
  const tick = async () => {
    const entry = [...timers.entries()].find(([, timer]) => timer.ms === 900);
    assert.ok(entry, 'support poll scheduled'); timers.delete(entry[0]); await entry[1].fn();
  };
  return {api: context.window.SimpleOfficeScreenSupport, el, posts, data, applied, tick, offline() {rejectGet = true;}};
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
