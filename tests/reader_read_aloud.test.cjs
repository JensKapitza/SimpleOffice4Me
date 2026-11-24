const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/reader_read_aloud.js'), 'utf8');
const base = 'https://office.invalid';
const endpoint = '/reader/book/speech';
const ocrEndpoint = '/reader/book/speech/ocr';

class Element {
  constructor() { this.handlers = new Map(); }
  addEventListener(name, handler) { this.handlers.set(name, handler); }
  fire(name, event = {}) { return this.handlers.get(name)?.(event); }
}

function wav() {
  const data = Buffer.alloc(46);
  data.write('RIFF', 0);
  data.writeUInt32LE(38, 4);
  data.write('WAVEfmt ', 8);
  data.writeUInt32LE(16, 16);
  data.writeUInt16LE(1, 20);
  data.writeUInt16LE(1, 22);
  data.writeUInt32LE(8000, 24);
  data.writeUInt32LE(16000, 28);
  data.writeUInt16LE(2, 32);
  data.writeUInt16LE(16, 34);
  data.write('data', 36);
  data.writeUInt32LE(2, 40);
  return data;
}

function response(bytes = wav(), type = 'audio/wav', status = 200) {
  return new Response(bytes, {status, headers: {'Content-Type': type}});
}

function fixture({
  url = endpoint, ocrUrl, token = 'fixture-csrf', page = '4', hasAudio = true, playResults = [],
} = {}) {
  const root = new Element();
  root.dataset = {
    speechUrl: url, speechOcrUrl: ocrUrl, speechLoading: 'Lädt', speechPaused: 'Pausiert', speechPlaying: 'Liest',
    speechOcrLoading: 'OCR lädt', speechOcrUnavailable: 'OCR fehlt', speechOcrNoText: 'OCR leer',
    speechOcrTimeout: 'OCR Zeitlimit', speechPdfTextUnavailable: 'PDF nicht lesbar',
    speechUnavailable: 'Nicht verfügbar', speechPauseLabel: 'Pause', speechResumeLabel: 'Weiter',
    speechPlayRequired: 'Bereit', speechError: 'Fehler', speechNoText: 'Kein Text', speechStale: 'Neu laden',
    speechTooLarge: 'Zu groß',
  };
  const reader = new Element();
  reader.dataset = {documentVersion: 'version-2'};
  const position = {value: page};
  reader.querySelector = selector => selector === '[data-reader-position]' ? position : null;
  root.closest = () => reader;
  const controls = Object.fromEntries(['start', 'pause', 'stop', 'status'].map(key => [key, new Element()]));
  if (ocrUrl !== undefined) controls.ocr = new Element();
  root.querySelector = selector => controls[selector.slice(13, -1)] ?? null;
  const window = new Element();
  window.location = new URL(base + '/reader/book');
  const requests = [], audios = [], created = [], revoked = [];
  const fetch = (url, opts) => new Promise(resolve => requests.push({url, opts, resolve}));

  class BrowserUrl extends URL {
    static createObjectURL(blob) {
      const id = 'blob:' + base + '/' + (created.length + 1);
      created.push({id, blob});
      return id;
    }
    static revokeObjectURL(id) { revoked.push(id); }
  }
  class Audio extends Element {
    constructor() {
      super();
      this.playCount = this.pauseCount = this.loadCount = 0;
      audios.push(this);
    }
    play() {
      this.playCount++;
      return (playResults.shift() ?? (() => Promise.resolve()))();
    }
    pause() { this.pauseCount++; }
    removeAttribute(name) { delete this[name]; }
    load() { this.loadCount++; }
  }

  vm.runInNewContext(script, {
    document: {
      querySelectorAll: selector => selector === '[data-reader-read-aloud]' ? [root] : [],
      querySelector: selector => selector === 'meta[name="csrf-token"]' ? {content: token} : null,
    },
    window, URL: BrowserUrl, fetch, AbortController, Audio: hasAudio ? Audio : undefined,
  });
  return {
    ...controls, window, requests, audios, created, revoked,
    goTo: value => {
      position.value = String(value);
      reader.fire('reader:page-changed', {detail: {page: value}});
    },
  };
}

test('explicit start sends same-origin no-store WAV request, pause/resume keeps audio, and stop frees playback', async () => {
  const f = fixture();
  assert.equal(f.requests.length, 0);
  assert.equal(f.stop.disabled, true);
  const reading = f.start.fire('click');
  assert.equal(f.status.textContent, 'Lädt');
  const [{url, opts, resolve}] = f.requests;
  assert.equal(url, base + endpoint);
  assert.equal(opts.method, 'POST');
  assert.equal(opts.credentials, 'same-origin');
  assert.equal(opts.cache, 'no-store');
  assert.equal(opts.redirect, 'error');
  assert.equal(opts.headers['X-CSRF-Token'], 'fixture-csrf');
  assert.equal(opts.headers.Accept, 'audio/wav');
  assert.deepEqual(JSON.parse(opts.body), {page: 4, document_version: 'version-2'});
  await f.start.fire('click');
  assert.equal(f.requests.length, 1, 'loading must not duplicate synthesis');
  resolve(response());
  await reading;
  const [audio] = f.audios;
  assert.equal(audio.playCount, 1);
  assert.equal(f.created[0].blob.type, 'audio/wav');
  assert.equal(f.status.textContent, 'Liest');
  await f.pause.fire('click');
  assert.equal(f.status.textContent, 'Pausiert');
  assert.equal(f.pause.textContent, 'Weiter');
  await f.pause.fire('click');
  assert.equal(audio.playCount, 2);
  assert.equal(f.requests.length, 1);
  f.stop.fire('click');
  assert.equal(f.start.disabled, false);
  assert.equal(audio.src, undefined);
  assert.equal(audio.loadCount, 1);
  assert.deepEqual(f.revoked, [f.created[0].id]);
  assert.equal(opts.signal.aborted, true);
});

test('page changes abort active requests and out-of-order audio cannot supersede the new page', async () => {
  const f = fixture({page: ''});
  const first = f.start.fire('click');
  assert.equal(JSON.parse(f.requests[0].opts.body).page, 1);
  f.goTo(7);
  assert.equal(f.requests[0].opts.signal.aborted, true);
  const second = f.start.fire('click');
  assert.equal(JSON.parse(f.requests[1].opts.body).page, 7);
  f.requests[1].resolve(response());
  await second;
  assert.equal(f.audios.length, 1);
  f.requests[0].resolve(response());
  await first;
  assert.equal(f.audios.length, 1);
  assert.equal(f.status.textContent, 'Liest');
  f.goTo(7);
  assert.equal(f.revoked.length, 0, 'same page (e.g. zoom) does not stop reading');
  f.goTo(8);
  assert.equal(f.audios[0].pauseCount, 1);
  assert.deepEqual(f.revoked, [f.created[0].id]);
});

test('stop during undecoded response and pagehide dispose of in-flight playback', async () => {
  const f = fixture();
  const first = f.start.fire('click');
  let finishBlob;
  const pendingBlob = new Promise(resolve => { finishBlob = resolve; });
  f.requests[0].resolve({ok: true, headers: new Headers({'Content-Type': 'audio/wav'}), blob: () => pendingBlob});
  await Promise.resolve();
  f.stop.fire('click');
  assert.equal(f.requests[0].opts.signal.aborted, true);
  finishBlob(new Blob([wav()], {type: 'audio/wav'}));
  await first;
  assert.equal(f.created.length, 0);
  const second = f.start.fire('click');
  f.window.fire('pagehide');
  assert.equal(f.requests[1].opts.signal.aborted, true);
  f.requests[1].resolve(response());
  await second;
  assert.equal(f.audios.length, 0);
});

test('autoplay denial enables a user-initiated resume and ended releases its object URL', async () => {
  const f = fixture({playResults: [() => Promise.reject(new DOMException('blocked', 'NotAllowedError'))]});
  const reading = f.start.fire('click');
  f.requests[0].resolve(response());
  await reading;
  assert.equal(f.status.textContent, 'Bereit');
  assert.equal(f.pause.textContent, 'Weiter');
  assert.equal(f.pause.disabled, false);
  await f.pause.fire('click');
  assert.equal(f.status.textContent, 'Liest');
  f.audios[0].fire('ended');
  assert.equal(f.stop.disabled, true);
  assert.deepEqual(f.revoked, [f.created[0].id]);
});

test('wrong MIME, invalid WAV and server refusal produce only localized status, never playback', async () => {
  for (const [reply, message] of [
    [response('<html>Sensitive debug</html>', 'text/html'), 'Fehler'],
    [response(wav(), 'audio/wav; charset=utf-8'), 'Fehler'],
    [response(Buffer.alloc(48)), 'Fehler'],
    [response('{"error":"no_text"}', 'application/json', 422), 'Kein Text'],
    [response('{"error":"Sensitive debug"}', 'application/json', 422), 'Fehler'],
    [response('{"error":"pdf_text_unavailable"}', 'application/json', 422), 'PDF nicht lesbar'],
    [response('{}', 'application/json', 409), 'Neu laden'],
    [response('{}', 'application/json', 413), 'Zu groß'],
    [response('{}', 'application/json', 503), 'Nicht verfügbar'],
  ]) {
    const f = fixture();
    const reading = f.start.fire('click');
    f.requests[0].resolve(reply);
    await reading;
    assert.equal(f.status.textContent, message);
    assert.equal(f.start.disabled, false);
    assert.equal(f.audios.length, 0);
  }
});

test('unsafe endpoint, missing CSRF or missing playback support disables the feature without networking', async () => {
  for (const options of [
    {url: ''}, {url: '//other.invalid/speech'}, {url: 'blob:' + base + '/speech'},
    {url: 'https://user:password@office.invalid/speech'}, {url: '/speech#fragment'},
    {token: ''}, {hasAudio: false},
  ]) {
    const f = fixture(options);
    await f.start.fire('click');
    assert.equal(f.status.textContent, 'Nicht verfügbar');
    assert.equal(f.start.disabled, true);
    assert.equal(f.requests.length, 0);
  }
});

test('WAV size is checked before downloading a declared oversized body and again after blob reads', async () => {
  const maximum = 8 * 1024 * 1024;
  const oversized = new Blob([wav(), new Uint8Array(maximum)], {type: 'audio/wav'});
  for (const [declared, blobReads] of [[String(maximum + 1), 0], ['44', 1], ['invalid', 1], [null, 1]]) {
    const f = fixture();
    const reading = f.start.fire('click');
    const headers = new Headers({'Content-Type': 'audio/wav'});
    if (declared !== null) headers.set('Content-Length', declared);
    let reads = 0;
    f.requests[0].resolve({ok: true, headers, blob: async () => { reads++; return oversized; }});
    await reading;
    assert.equal(reads, blobReads);
    assert.equal(f.created.length, 0);
    assert.equal(f.status.textContent, 'Fehler');
  }
});


test('OCR is an explicit same-origin request with the same page/version and playback lifecycle', async () => {
  const f = fixture({ocrUrl: ocrEndpoint});
  assert.equal(f.requests.length, 0);
  const nativeReading = f.start.fire('click');
  f.requests[0].resolve(response('{"error":"no_text"}', 'application/json', 422));
  await nativeReading;
  assert.equal(f.status.textContent, 'Kein Text');
  assert.equal(f.ocr.disabled, false);
  assert.equal(f.requests.length, 1, 'empty native text must not run OCR automatically');

  const ocrReading = f.ocr.fire('click');
  assert.equal(f.status.textContent, 'OCR lädt');
  assert.equal(f.start.disabled, true);
  assert.equal(f.ocr.disabled, true);
  await f.ocr.fire('click');
  await f.start.fire('click');
  assert.equal(f.requests.length, 2);
  const request = f.requests[1];
  assert.equal(request.url, base + ocrEndpoint);
  assert.equal(request.opts.method, 'POST');
  assert.equal(request.opts.credentials, 'same-origin');
  assert.equal(request.opts.cache, 'no-store');
  assert.equal(request.opts.redirect, 'error');
  assert.equal(request.opts.headers['X-CSRF-Token'], 'fixture-csrf');
  assert.equal(request.opts.headers.Accept, 'audio/wav');
  assert.deepEqual(JSON.parse(request.opts.body), {page: 4, document_version: 'version-2'});
  request.resolve(response());
  await ocrReading;
  assert.equal(f.status.textContent, 'Liest');
  assert.equal(f.audios[0].playCount, 1);
  f.stop.fire('click');
  assert.equal(f.ocr.disabled, false);
  assert.deepEqual(f.revoked, [f.created[0].id]);
});

test('OCR error codes render fixed messages, never subprocess or server details', async () => {
  for (const [status, error, message] of [
    [422, 'ocr_no_text', 'OCR leer'],
    [422, 'no_text', 'OCR leer'],
    [422, 'pdf_text_unavailable', 'PDF nicht lesbar'],
    [503, 'ocr_unavailable', 'OCR fehlt'],
    [503, 'ocr_timeout', 'OCR Zeitlimit'],
    [422, 'runtime secret', 'Fehler'],
    [503, 'runtime secret', 'Nicht verfügbar'],
  ]) {
    const f = fixture({ocrUrl: ocrEndpoint});
    const reading = f.ocr.fire('click');
    f.requests[0].resolve(response(JSON.stringify({error, detail: 'runtime secret'}), 'application/json', status));
    await reading;
    assert.equal(f.status.textContent, message);
    assert.equal(f.ocr.disabled, false);
    assert.equal(f.created.length, 0);
    f.goTo(5);
    assert.equal(f.status.textContent, '');
  }
});

test('navigation cancels pending OCR and stale success/error cannot replace playback on the new page', async () => {
  const f = fixture({ocrUrl: ocrEndpoint});
  const staleAudio = f.ocr.fire('click');
  f.goTo(5);
  assert.equal(f.requests[0].opts.signal.aborted, true);
  const staleError = f.ocr.fire('click');
  let resolveProblem;
  const problem = new Promise(resolve => { resolveProblem = resolve; });
  f.requests[1].resolve({ok: false, status: 503, json: () => problem});
  await Promise.resolve();
  f.goTo(6);
  assert.equal(f.requests[1].opts.signal.aborted, true);
  const current = f.start.fire('click');
  f.requests[2].resolve(response());
  await current;
  f.requests[0].resolve(response());
  resolveProblem({error: 'ocr_unavailable'});
  await Promise.all([staleAudio, staleError]);
  assert.equal(f.status.textContent, 'Liest');
  assert.equal(f.audios.length, 1);
  assert.equal(f.revoked.length, 0);
  f.window.fire('pagehide');
  assert.deepEqual(f.revoked, [f.created[0].id]);
});

test('an absent or unsafe OCR URL cannot be requested and leaves native speech unchanged', async () => {
  const text = fixture();
  assert.equal(text.ocr, undefined);
  for (const ocrUrl of ['', 'https://elsewhere.invalid/ocr', '/ocr#fragment']) {
    const f = fixture({ocrUrl});
    assert.equal(f.ocr.disabled, true);
    assert.equal(f.start.disabled, false);
    await f.ocr.fire('click');
    assert.equal(f.requests.length, 0);
  }
});
