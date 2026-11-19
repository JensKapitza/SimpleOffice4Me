import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

const source = await readFile(new URL('../static/js/chat_room.js', import.meta.url), 'utf8');

function messageIds(html) {
  return [...html.matchAll(/<article\b[^>]*\bid=["'](message-[^"']+)["']/g)].map((match) => ({ id: match[1] }));
}

function harness(fetchImpl, initialHtml = '<p>initial</p>', { messageTops = {} } = {}) {
  let poll;
  const target = {
    innerHTML: initialHtml,
    querySelectorAll(selector) {
      assert.equal(selector, 'article[id^="message-"]');
      return messageIds(this.innerHTML).map(({ id }) => ({
        id,
        getBoundingClientRect() {
          const configured = messageTops[id];
          const top = typeof configured === 'function' ? configured(target.innerHTML) : (configured ?? 120);
          return { top, bottom: top + 40 };
        },
      }));
    },
  };
  const scroll = {
    scrollHeight: 1000, scrollTop: 800, clientHeight: 200,
    getBoundingClientRect() { return { top: 100, bottom: 300 }; },
  };
  let scrollListener;
  scroll.addEventListener = (name, callback) => { if (name === 'scroll') scrollListener = callback; };
  const buttonClasses = new Set(['d-none']);
  const button = {
    classList: {
      add: name => buttonClasses.add(name),
      remove: name => buttonClasses.delete(name),
      contains: name => buttonClasses.has(name),
    },
  };
  button.addEventListener = () => {};
  const root = { dataset: { chatFragmentUrl: '/chat/fragment' } };
  const elements = {
    'main-content': root,
    'chat-messages': target,
    'chat-scroll': scroll,
    'chat-new-messages': button,
  };
  const document = {
    documentElement: { lang: 'en' },
    hidden: false,
    getElementById: id => elements[id] || target.querySelectorAll('article[id^="message-"]').find(message => message.id === id) || null,
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
  return {
    target, scroll, button,
    runPoll: () => poll(),
    scrollEvent: () => scrollListener?.(),
  };
}

test('anchor falls through to the next visible message when the first is removed', async () => {
  const initial = '<article id="message-one">one</article><article id="message-two">two</article>';
  const tops = {
    'message-one': 120,
    'message-two': html => html.includes('message-one') ? 160 : 190,
  };
  const app = harness(async () => ({
    ok: true,
    text: async () => '<article id="message-two">two updated</article><article id="message-three">three</article>',
  }), initial, { messageTops: tops });

  app.scroll.scrollTop = 400;
  await app.runPoll();

  assert.equal(app.scroll.scrollTop, 430, 'the surviving visible message should preserve its viewport position');
});

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

test('new messages show a notice only when the user is scrolled up', async () => {
  const oldMessage = '<article id="message-one">hello</article>';
  const app = harness(async () => ({
    ok: true,
    text: async () => '<article id="message-one">hello</article><article id="message-two">new</article>',
  }), oldMessage);
  assert.equal(app.button.classList.contains('d-none'), true, 'notice starts hidden');
  app.scroll.scrollTop = 400;
  await app.runPoll();
  assert.equal(app.button.classList.contains('d-none'), false, 'incoming message IDs reveal the notice while reading older messages');
  assert.equal(app.scroll.scrollTop, 400, 'polling must leave the reader at their current position');
  app.scroll.scrollTop = 900;
  app.scrollEvent();
  assert.equal(app.button.classList.contains('d-none'), true, 'manually scrolling back to the latest messages dismisses the notice');
});

test('reaction-only changes do not show a notice and new messages at bottom remain hidden', async () => {
  let fragment = '<article id="message-one">hello</article>';
  const app = harness(async () => ({ ok: true, text: async () => fragment }), fragment);
  assert.equal(app.button.classList.contains('d-none'), true, 'notice starts hidden');

  app.scroll.scrollTop = 400;
  fragment = '<article id="message-one">hello <span>👍 1</span></article>';
  await app.runPoll();
  assert.equal(app.button.classList.contains('d-none'), true, 'updated reaction markup keeps existing message IDs');

  app.scroll.scrollTop = app.scroll.scrollHeight;
  fragment = '<article id="message-one">hello <span>👍 1</span></article><article id="message-two">new</article>';
  await app.runPoll();
  assert.equal(app.button.classList.contains('d-none'), true, 'a new message at bottom is followed automatically without a notice');
  assert.equal(app.scroll.scrollTop, app.scroll.scrollHeight);
});
