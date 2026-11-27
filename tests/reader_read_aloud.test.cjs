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
  url = endpoint, ocrUrl, token = 'fixture-csrf', page = '4', hasAudio = true, playResults = [], pageCount,
  epub = false,
} = {}) {
  const root = new Element();
  root.dataset = {
    speechUrl: url, speechOcrUrl: ocrUrl, speechLoading: 'Lädt', speechPaused: 'Pausiert', speechPlaying: 'Liest',
    speechOcrLoading: 'OCR lädt', speechOcrUnavailable: 'OCR fehlt', speechOcrNoText: 'OCR leer',
    speechOcrTimeout: 'OCR Zeitlimit', speechPdfTextUnavailable: 'PDF nicht lesbar',
    speechUnavailable: 'Nicht verfügbar', speechPauseLabel: 'Pause', speechResumeLabel: 'Weiter',
    speechPlayRequired: 'Bereit', speechError: 'Fehler', speechNoText: 'Kein Text', speechStale: 'Neu laden',
    speechTooLarge: 'Zu groß', speechPdfTooLarge: 'PDF teilen (64 MiB)',
    speechBusy: 'Belegt. Warte kurz und versuche es erneut.',
    speechTimeout: 'Vorlesen dauerte zu lange. Warte kurz und versuche es erneut.',
    speechPdfSourceTimeout: 'PDF Vorbereitung Zeitlimit', speechPdfStageUnavailable: 'Temporärer Speicher',
  };
  const reader = new Element();
  reader.dataset = {documentVersion: 'version-2', pageCount, format: epub ? 'epub' : 'pdf'};
  if (epub) {
    reader.dataset.speechChapter = '0';
    reader.dataset.speechChapterSegments = '0';
  }
  const position = {value: page, dispatchEvent: event => {
    if (event.type === 'change') reader.fire('reader:page-changed', {detail: {page: Number(position.value)}});
  }};
  reader.querySelector = selector => selector === '[data-reader-position]' ? position : null;
  root.closest = () => reader;
  const controls = Object.fromEntries(['start', 'pause', 'stop', 'status'].map(key => [key, new Element()]));
  if (ocrUrl !== undefined) controls.ocr = new Element();
  if (pageCount !== undefined) {
    controls.continuous = new Element();
    controls.jump = new Element();
  }
  if (epub) for (const key of ['prev-segment', 'next-segment', 'part']) controls[key] = new Element();
  root.querySelector = selector => controls[selector.slice(13, -1)] ?? null;
  const window = new Element();
  window.location = new URL(base + '/reader/book');
  const requests = [], audios = [], created = [], revoked = [];
  const fetch = (url, opts) => new Promise(resolve => requests.push({url, opts, resolve}));

  if (ocrUrl !== undefined) {
    controls.rotation = new Element();
    controls.rotation.value = '0';
  }

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
    window, URL: BrowserUrl, fetch, AbortController, Event, Audio: hasAudio ? Audio : undefined,
  });
  return {
    ...controls, window, requests, audios, created, revoked, position,
    typePage: value => { position.value = String(value); },
    commitChapter: (chapter, parts) => {
      reader.dataset.speechChapter = String(chapter);
      reader.dataset.speechChapterSegments = String(parts);
      reader.fire('reader:chapter-displayed');
    },
    goTo: value => {
      position.value = String(value);
      reader.fire('reader:page-changed', {detail: {page: value}});
    },
  };
}

test('explicit turn is sent only in the OCR locator and resets with the page', async () => {
  const f = fixture({ocrUrl: ocrEndpoint});
  f.rotation.value = '90';
  assert.equal(f.rotation.disabled, false);
  assert.equal(f.requests.length, 0, 'choosing rotation alone does not start OCR');
  const native = f.start.fire('click');
  assert.equal(f.requests[0].url, base + endpoint);
  assert.equal(JSON.parse(f.requests[0].opts.body).ocr_clockwise, undefined);
  f.requests.shift().resolve(response());
  await native;
  await f.stop.fire('click');
  const pending = f.ocr.fire('click');
  assert.equal(f.rotation.disabled, true);
  const first = f.requests[0];
  assert.equal(first.url, base + ocrEndpoint);
  assert.deepEqual(JSON.parse(first.opts.body), {page: 4, document_version: 'version-2', ocr_clockwise: 90});
  f.goTo(5);
  assert.equal(first.opts.signal.aborted, true);
  assert.equal(f.rotation.value, '0');
  first.resolve(response());
  await pending;
  assert.equal(f.audios.length, 1, 'stale OCR never becomes a second audio');
  const same = f.ocr.fire('click');
  assert.equal(f.requests[1].url, base + ocrEndpoint);
  assert.equal(JSON.parse(f.requests[1].opts.body).ocr_clockwise, undefined);
  f.requests[1].resolve(response());
  await same;
  await f.stop.fire('click');
  f.rotation.value = '270';
  const left = f.ocr.fire('click');
  assert.equal(JSON.parse(f.requests[2].opts.body).ocr_clockwise, 270);
  f.requests[2].resolve(response());
  await left;
});

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

test('busy and synthesis timeout use fixed retry guidance without automatically retrying', async () => {
  for (const [control, code, message] of [
    ['start', 'read_aloud_busy', 'Belegt. Warte kurz und versuche es erneut.'],
    ['ocr', 'read_aloud_timeout', 'Vorlesen dauerte zu lange. Warte kurz und versuche es erneut.'],
  ]) {
    const f = fixture({ocrUrl: ocrEndpoint});
    const request = f[control].fire('click');
    const failure = {error: code, details: 'internal fixture details'};
    f.requests[0].resolve(response(JSON.stringify(failure), 'application/json', 503));
    await request;
    assert.equal(f.status.textContent, message);
    assert.equal(f[control].disabled, false);
    assert.equal(f.requests.length, 1);
    assert.equal(f.audios.length, 0);
    const retry = f[control].fire('click');
    assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1].url, f.requests[0].url);
    assert.equal(f.requests[1].opts.body, f.requests[0].opts.body);
    f.requests[1].resolve(response());
    await retry;
    assert.equal(f.status.textContent, 'Liest');
    assert.equal(f.audios.length, 1);
  }
});

test('document cap guidance is distinct from OCR limits and staging errors', async () => {
  for (const [status, code, expected] of [
    [413, 'document_too_large', 'PDF teilen (64 MiB)'],
    [413, 'ocr_raster_too_large', 'Zu groß'],
    [413, 'too_much_text', 'Zu groß'],
    [503, 'pdf_source_timeout', 'PDF Vorbereitung Zeitlimit'],
    [503, 'pdf_stage_unavailable', 'Temporärer Speicher'],
  ]) {
    const f = fixture({ocrUrl: '/recognize'});
    const reading = f.ocr.fire('click');
    f.requests[0].resolve(response(JSON.stringify({error: code}), 'application/json', status));
    await reading;
    assert.equal(f.status.textContent, expected);
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


const pdfRun = options => fixture({ocrUrl: ocrEndpoint, pageCount: '14', ...options});
const bodyPage = request => JSON.parse(request.opts.body).page;

async function respond(f, reading, reply = response()) {
  f.requests.at(-1).resolve(reply);
  await reading;
}

test('PDF continuation is explicit; ordinary and OCR buttons still read only one page', async () => {
  for (const button of ['start', 'ocr']) {
    const f = pdfRun();
    assert.equal(f.requests.length, 0);
    assert.equal(f.continuous.disabled, false);
    assert.equal(f.jump.hidden, true);
    await respond(f, f[button].fire('click'));
    f.audios[0].fire('ended');
    assert.equal(f.requests.length, 1);
    assert.equal(f.stop.disabled, true);
  }
  for (const pageCount of ['NaN', '4', '3.4', '10001']) {
    const f = pdfRun({pageCount});
    assert.equal(f.continuous.disabled, true);
    await f.continuous.fire('click');
    assert.equal(f.requests.length, 0);
  }
});

test('five native pages are requested serially, each prior audio is released and no view auto-jumps', async () => {
  const f = pdfRun();
  await respond(f, f.continuous.fire('click'));
  assert.equal(f.status.textContent, 'Reading page 4.');
  await f.pause.fire('click');
  f.audios[0].fire('ended');
  assert.equal(f.requests.length, 1);
  await f.pause.fire('click');
  for (let page = 5; page <= 8; page++) {
    const previousAudio = f.audios.at(-1);
    const pending = previousAudio.fire('ended');
    previousAudio.fire('ended'); // duplicate or queued callback from a disposed clip
    assert.equal(f.requests.length, page - 3);
    assert.deepEqual(JSON.parse(f.requests.at(-1).opts.body), {page, document_version: 'version-2'});
    assert.equal(f.requests.at(-1).url, base + endpoint);
    assert.equal(f.revoked.length, page - 4);
    assert.equal(f.position.value, '4');
    await respond(f, pending);
    assert.equal(f.status.textContent, `Reading page ${page}.`);
  }
  f.audios.at(-1).fire('ended');
  assert.deepEqual(f.requests.map(bodyPage), [4, 5, 6, 7, 8]);
  assert.equal(f.revoked.length, 5);
  assert.equal(f.jump.textContent, 'Go to page 9');
  f.jump.fire('click');
  assert.equal(f.position.value, '9');
  assert.equal(f.requests.length, 5);
  assert.equal(f.jump.hidden, true);
});

test('no-text page stops conclusively and offers navigation but never starts OCR or skips ahead', async () => {
  const f = pdfRun();
  await respond(f, f.continuous.fire('click'));
  const reading = f.audios[0].fire('ended');
  await respond(f, reading, response('{"error":"no_text"}', 'application/json', 422));
  assert.equal(f.requests.length, 2);
  assert.equal(f.requests[1].url, base + endpoint);
  assert.equal(f.position.value, '4');
  assert.match(f.status.textContent, /Stopped at page 5/);
  assert.equal(f.jump.hidden, false);
  f.jump.fire('click');
  assert.equal(f.position.value, '5');
  assert.equal(f.requests.length, 2);
  await respond(f, f.ocr.fire('click'));
  assert.equal(f.requests.length, 3);
  assert.equal(f.requests[2].url, base + ocrEndpoint);
  assert.equal(bodyPage(f.requests[2]), 5);
  f.audios.at(-1).fire('ended');
  assert.equal(f.requests.length, 3);
});

test('temporary failure on a later page offers a manual jump without advancing or retrying', async () => {
  const f = pdfRun();
  await respond(f, f.continuous.fire('click'));
  const next = f.audios[0].fire('ended');
  await respond(f, next, response('{"error":"read_aloud_busy"}', 'application/json', 503));
  assert.equal(f.status.textContent, 'Belegt. Warte kurz und versuche es erneut.');
  assert.equal(f.requests.length, 2);
  assert.equal(f.position.value, '4');
  assert.equal(f.jump.hidden, false);
  f.jump.fire('click');
  assert.equal(f.position.value, '5');
  assert.equal(f.requests.length, 2);
  await respond(f, f.continuous.fire('click'));
  assert.equal(bodyPage(f.requests[2]), 5);
  assert.equal(f.requests[2].url, base + endpoint);
});

test('known final page ends a shorter run and offers only a manual jump to the last page', async () => {
  const f = pdfRun({pageCount: '5'});
  await respond(f, f.continuous.fire('click'));
  await respond(f, f.audios.at(-1).fire('ended'));
  f.audios.at(-1).fire('ended');
  assert.deepEqual(f.requests.map(bodyPage), [4, 5]);
  assert.equal(f.status.textContent, 'End of PDF.');
  assert.equal(f.position.value, '4');
  assert.equal(f.jump.hidden, false);
  assert.equal(f.jump.textContent, 'Go to page 5');
  f.jump.fire('click');
  assert.equal(f.position.value, '5');
  assert.equal(f.jump.hidden, true);
  assert.deepEqual(f.requests.map(bodyPage), [4, 5]);
});

test('APK background signal cancels a pending read without playing the late body', async () => {
  const f = fixture();
  const request = f.start.fire('click');
  f.window.fire('simpleoffice:speech-stop');
  assert.equal(f.requests[0].opts.signal.aborted, true);
  f.requests[0].resolve(response());
  await request;
  assert.equal(f.audios.length, 0);
});

test('manual navigation, stop, pagehide and stale permission/version end subsequent in-flight playback', async () => {
  for (const cancel of [f => f.goTo(7), f => f.stop.fire('click'), f => f.window.fire('pagehide')]) {
    const f = pdfRun();
    await respond(f, f.continuous.fire('click'));
    const pending = f.audios.at(-1).fire('ended');
    cancel(f);
    assert.equal(f.requests[1].opts.signal.aborted, true);
    await respond(f, pending);
    assert.equal(f.created.length, 1);
    assert.equal(f.requests.length, 2);
    assert.equal(f.stop.disabled, true);
  }
  for (const [code, message] of [[404, 'Fehler'], [409, 'Neu laden']]) {
    const f = pdfRun();
    await respond(f, f.continuous.fire('click'));
    await respond(f, f.audios.at(-1).fire('ended'), response('{}', 'application/json', code));
    assert.equal(f.status.textContent, message);
    assert.equal(f.requests.length, 2);
    assert.equal(f.stop.disabled, true);
  }
});

test('subsequent playback blocked by browser waits for the same audio to be manually resumed', async () => {
  const f = pdfRun({playResults: [
    () => Promise.resolve(),
    () => Promise.reject(new DOMException('blocked', 'NotAllowedError')),
  ]});
  await respond(f, f.continuous.fire('click'));
  await respond(f, f.audios.at(-1).fire('ended'));
  const second = f.audios.at(-1);
  assert.equal(f.status.textContent, 'Bereit');
  assert.equal(f.requests.length, 2);
  assert.equal(f.pause.disabled, false);
  await f.pause.fire('click');
  assert.equal(second.playCount, 2);
  assert.equal(f.status.textContent, 'Reading page 5.');
  f.stop.fire('click');
  second.fire('ended');
  assert.equal(f.requests.length, 2);
  assert.equal(f.revoked.length, 2);
});

test('EPUB reads only the selected segment of a committed chapter, without prefetch or stale audio', async () => {
  const f = fixture({epub: true});
  assert.equal(f.start.disabled, true);
  f.commitChapter(2, 3);
  assert.equal(f.start.disabled, false);
  f.typePage(5);
  f['next-segment'].fire('click');
  assert.match(f.part.textContent, /2.*3/);
  assert.equal(f.requests.length, 0);
  const old = f.start.fire('click');
  assert.deepEqual(JSON.parse(f.requests[0].opts.body), {page: 2, document_version: 'version-2', segment: 2});
  f.commitChapter(1, 1);
  assert.equal(f.requests[0].opts.signal.aborted, true);
  f.requests[0].resolve(response());
  await old;
  assert.equal(f.audios.length, 0);
  assert.match(f.part.textContent, /1.*1/);
  const current = f.start.fire('click');
  assert.deepEqual(JSON.parse(f.requests[1].opts.body), {page: 1, document_version: 'version-2', segment: 1});
  f.requests[1].resolve(response());
  await current;
  f.audios[0].fire('ended');
  assert.equal(f.requests.length, 2);
  assert.equal(f.revoked.length, 1);
  f.commitChapter(3, 0);
  assert.equal(f.start.disabled, true);
  assert.equal(f.part.textContent, 'Kein Text');
});
