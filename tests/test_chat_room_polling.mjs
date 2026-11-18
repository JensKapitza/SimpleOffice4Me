import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../static/js/chat_room.js', import.meta.url), 'utf8');

function harness(fetchImpl) {
  let poll;
  const target = { innerHTML: '<p>initial</p>' };
  const scroll = { scrollHeight: 1000, scrollTop: 800, clientHeight: 200 };
  const root = { dataset: { chatFragmentUrl: '/chat/fragment' } };
  const elements = {
    'main-content': root,
    'chat-messages': target,
    'chat-scroll': scroll,
  };
  const document = {
    documentElement: { lang: 'en' },
    hidden: false,
    getElementById: id => elements[id] || null,
    querySelectorAll: () => [],
  };
  const context = {
    document,
    navigator: { userAgent: '' },
    URL,
    fetch: fetchImpl,
    setInterval(callback) { poll = callback; },
  };
  vm.runInNewContext(source, context);
  return { target, scroll, runPoll: () => poll() };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test('polling permits only one in-flight request and respects scrolling during fetch', async () => {
  const pending = deferred();
  let calls = 0;
  const app = harness(() => { calls += 1; return pending.promise; });

  const first = app.runPoll();
  await app.runPoll();
  assert.equal(calls, 1, 'overlapping interval tick must be skipped');

  app.scroll.scrollTop = 400; // User reads older messages while fetch is pending.
  pending.resolve({ ok: true, text: async () => '<p>updated</p>' });
  await first;
  assert.equal(app.target.innerHTML, '<p>updated</p>');
  assert.equal(app.scroll.scrollTop, 400, 'poll update must not pull the user back to bottom');
});

test('a failed poll releases the guard and a later interval retries', async () => {
  let calls = 0;
  const app = harness(async () => {
    calls += 1;
    if (calls === 1) throw new Error('temporary network failure');
    return { ok: true, text: async () => '<p>retried</p>' };
  });

  await app.runPoll();
  assert.equal(app.target.innerHTML, '<p>initial</p>');
  await app.runPoll();
  assert.equal(calls, 2);
  assert.equal(app.target.innerHTML, '<p>retried</p>');
});

test('bottom position is checked after the response arrives', async () => {
  const pending = deferred();
  const app = harness(() => pending.promise);
  app.scroll.scrollTop = 0;
  const request = app.runPoll();
  app.scroll.scrollTop = 850; // User returns to the latest messages during the request.
  pending.resolve({ ok: true, text: async () => '<p>new message</p>' });
  await request;
  assert.equal(app.scroll.scrollTop, 1000, 'poll update should keep a user who reached the bottom at the bottom');
});
