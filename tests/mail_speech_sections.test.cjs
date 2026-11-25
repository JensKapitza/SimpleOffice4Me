const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Element {
  handlers = new Map();
  addEventListener(type, handler) { this.handlers.set(type, handler); }
  fire(type, event) { return this.handlers.get(type)?.(event); }
  dispatchEvent(event) { return this.fire(event.type, event); }
}

function player() {
  const page = new Element();
  const reader = new Element();
  const selector = new Element();
  const controls = Object.fromEntries(['start', 'stop', 'pause', 'status'].map(name => [name, new Element()]));
  selector.value = '1';
  selector.options = {length: 7};
  selector.disabled = true;
  selector.closest = () => reader;
  page.closest = () => reader;
  page.dataset = {speechUrl: '/mail/speech/archive/id?parts=1'};
  page.querySelector = tag => controls[tag.slice(13, -1)];
  reader.dataset = {documentVersion: 'sha-fixture'};
  reader.querySelector = tag => tag === '[data-reader-position]' ? selector : page.querySelector(tag);
  const audio = [];
  const revoked = [];
  class Audio extends Element {
    constructor() { super(); audio.push(this); }
    play() { return Promise.resolve(); }
    pause() { this.paused = true; }
    load() {}
    removeAttribute(name) { delete this[name]; }
  }
  class LocalUrl extends URL {
    static createObjectURL() { return 'blob:fixture'; }
    static revokeObjectURL(value) { revoked.push(value); }
  }
  const window = new Element();
  window.location = new URL('https://test.invalid/mail');
  const requests = [];
  const context = vm.createContext({
    window, URL: LocalUrl, Audio, AbortController,
    CustomEvent: class { constructor(type, {detail}) { this.type = type; this.detail = detail; } },
    document: {
      querySelectorAll: tag => tag === '[data-mail-speech-section]' ? [selector] : [page],
      querySelector: () => ({content: 'fixture-csrf'}),
    },
    fetch: (url, options) => new Promise(resolve => requests.push({url, options, resolve})),
  });
  for (const name of ['reader_read_aloud.js', 'mail_speech_sections.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/', name), 'utf8'), context);
  }
  return {selector, controls, requests, audio, revoked, choose: value => {
    selector.value = String(value);
    selector.fire('change');
  }};
}

function fixtureWav() {
  const bytes = Buffer.alloc(46);
  bytes.write('RIFF', 0);
  bytes.write('WAVE', 8);
  return new Response(bytes, {headers: {'Content-Type': 'audio/wav'}});
}

test('manual section selection cancels only, never reloads, fetches or advances by itself', async () => {
  const f = player();
  assert.equal(f.selector.disabled, false);
  assert.equal(f.requests.length, 0);
  f.choose(2);
  assert.equal(f.requests.length, 0);
  const first = f.controls.start.fire('click');
  assert.deepEqual(JSON.parse(f.requests[0].options.body), {page: 2, document_version: 'sha-fixture'});
  assert.equal(f.requests[0].options.method, 'POST');
  f.choose(7);
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.requests[0].resolve(fixtureWav());
  await first;
  assert.equal(f.audio.length, 0);
  assert.equal(f.requests.length, 1);

  const final = f.controls.start.fire('click');
  assert.equal(f.requests[1].url, 'https://test.invalid/mail/speech/archive/id?parts=1');
  assert.deepEqual(JSON.parse(f.requests[1].options.body), {page: 7, document_version: 'sha-fixture'});
  f.requests[1].resolve(fixtureWav());
  await final;
  assert.equal(f.audio.length, 1);
  f.audio[0].fire('ended');
  assert.equal(f.requests.length, 2);
  assert.deepEqual(f.revoked, ['blob:fixture']);

  f.choose(1);
  assert.equal(f.requests.length, 2);
  const whole = f.controls.start.fire('click');
  assert.deepEqual(JSON.parse(f.requests[2].options.body), {page: 1, document_version: 'sha-fixture'});
  f.requests[2].resolve(new Response('{"error":"too_large"}', {
    status: 413, headers: {'Content-Type': 'application/json'},
  }));
  await whole;
  assert.equal(f.audio.length, 1);
  assert.equal(f.requests.length, 3, 'no automatic retry or fallback request');
});
