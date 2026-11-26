const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const script = fs.readFileSync("static/js/android-speech-setup.js", "utf8");

function target(props = {}) {
  const listeners = new Map();
  return Object.assign({
    addEventListener(name, listener) { listeners.set(name, listener); },
    emit(name, event = {}) { return listeners.get(name)?.(event); },
  }, props);
}

function fixture({action = "/setup/optional/android-speech/test", playError = false} = {}) {
  const requests = [], created = [], revoked = [], shared = [], timers = new Map();
  const start = target({disabled: true}), stop = target({hidden: true}), status = target({textContent: ""});
  const audio = target({
    src: "", hidden: true, ended: false, error: null, pauses: 0, plays: 0, loads: 0,
    pause() { this.pauses++; },
    async play() {
      this.plays++;
      if (playError) throw new Error("autoplay blocked");
      this.emit("playing");
    },
    removeAttribute(name) { assert.equal(name, "src"); this.src = ""; },
    load() { this.loads++; },
  });
  const elements = {start, stop, status, audio};
  const form = target({
    action,
    fields: [["_csrf_token", "test-csrf"], ["scope", "kids"], ["language", "en"]],
    dataset: Object.fromEntries(["loading", "ready", "playing", "failed", "unsupported", "stopped", "ended"]
      .map(name => [name, name.toUpperCase()])),
    querySelector(selector) { return elements[selector.match(/trial-([a-z]+)\]/)[1]]; },
  });
  const document = target({querySelector: () => form, hidden: false});
  const window = target({
    location: new URL("https://app.test/setup/optional"),
    dispatchEvent(event) { shared.push(event); return this.emit(event.type, event); },
    setTimeout(callback, duration) { const key = Symbol(); timers.set(key, {callback, duration}); return key; },
    clearTimeout(key) { timers.delete(key); },
  });
  class Url extends URL {
    static createObjectURL(value) { const id = `blob:audio${created.length}`; created.push([id, value]); return id; }
    static revokeObjectURL(value) { revoked.push(value); }
  }
  vm.runInNewContext(script, {window, document, URL: Url, URLSearchParams, AbortController,
    CustomEvent: class { constructor(type, {detail}) { this.type = type; this.detail = detail; } },
    FormData: function (node) { return node.fields; },
    fetch(url, options) { return new Promise(resolve => requests.push({url, options, resolve})); },
  });
  return {form, audio, start, stop, status, requests, created, revoked, shared, window, document, timers,
    submit: () => form.emit("submit", {preventDefault() {}})};
}

function wavResponse(options = {}) {
  return {ok: true, headers: new Headers({"Content-Type": "audio/wav"}),
    blob: async () => new Blob(["wave fixture"], {type: "audio/wav"}), ...options};
}

test("only an explicit form submit requests a private, URL-encoded sample for the chosen area and language", async () => {
  const f = fixture();
  assert.equal(f.requests.length, 0);
  assert.equal(f.start.disabled, false);
  const submitted = f.submit();
  assert.equal(f.status.textContent, "LOADING");
  const [{url, options, resolve}] = f.requests;
  assert.equal(url, "https://app.test/setup/optional/android-speech/test");
  assert.equal(options.method, "POST");
  assert.equal(options.body.toString(), "_csrf_token=test-csrf&scope=kids&language=en");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.cache, "no-store");
  assert.equal(options.redirect, "error");
  assert.equal(options.headers.Accept, "audio/wav");
  resolve(wavResponse());
  await submitted;
  assert.equal(f.status.textContent, "PLAYING");
  assert.equal(f.shared[0].type, "simpleoffice:speech-start");
  assert.equal(f.shared[0].detail, f.form);
  assert.equal(f.audio.hidden, false);
  f.audio.ended = true;
  f.audio.emit("ended");
  assert.equal(f.status.textContent, "ENDED");
  assert.equal(f.audio.hidden, true);
  assert.deepEqual(f.revoked, ["blob:audio0"]);
});

test("HTTP errors and non-WAV responses are not read, displayed, or played", async () => {
  for (const response of [wavResponse({ok: false}), wavResponse({headers: new Headers({"Content-Type": "text/html"})})]) {
    const f = fixture();
    const submitted = f.submit();
    let read = 0;
    f.requests[0].resolve({...response, blob: async () => { read++; }});
    await submitted;
    assert.equal(read, 0);
    assert.equal(f.audio.plays, 0);
    assert.equal(f.created.length, 0);
    assert.equal(f.status.textContent, "FAILED");
  }
  const external = fixture({action: "https://outside.test/trial"});
  assert.equal(external.start.disabled, true);
  assert.equal(external.status.textContent, "UNSUPPORTED");
  assert.equal(external.requests.length, 0);
});

test("abort on newer submit, timeout, hidden tab, shared owner, and page leave without reviving stale sound", async () => {
  const f = fixture();
  const first = f.submit();
  const second = f.submit();
  assert.equal(f.requests[0].options.signal.aborted, true);
  f.requests[0].resolve(wavResponse());
  await first;
  assert.equal(f.created.length, 0);
  f.requests[1].resolve(wavResponse());
  await second;
  f.window.emit("simpleoffice:speech-start", {detail: {}});
  assert.equal(f.status.textContent, "STOPPED");
  assert.deepEqual(f.revoked, ["blob:audio0"]);

  for (const [interrupt, message] of [
    [() => f.timers.values().next().value.callback(), "FAILED"],
    [() => f.window.emit("simpleoffice:speech-stop"), "STOPPED"],
    [() => { f.document.hidden = true; f.document.emit("visibilitychange"); }, "STOPPED"],
    [() => f.window.emit("pagehide"), ""],
  ]) {
    const submitted = f.submit();
    const req = f.requests.at(-1);
    interrupt();
    assert.equal(req.options.signal.aborted, true);
    req.resolve(wavResponse());
    await submitted;
    assert.equal(f.status.textContent, message);
    assert.equal(f.timers.size, 0);
    assert.equal(f.created.length, 1);
  }
});

test("native background stop releases a playing sample even when visibility is unchanged", async () => {
  const f = fixture();
  const pending = f.submit();
  f.requests[0].resolve(wavResponse());
  await pending;
  assert.equal(f.document.hidden, false);
  assert.equal(f.status.textContent, "PLAYING");
  f.window.emit("simpleoffice:speech-stop");
  assert.equal(f.status.textContent, "STOPPED");
  assert.equal(f.audio.pauses, 1);
  assert.deepEqual(f.revoked, ["blob:audio0"]);
});

test("browser autoplay rejection leaves a playable control until explicit stop revokes the audio", async () => {
  const f = fixture({playError: true});
  const pending = f.submit();
  f.requests[0].resolve(wavResponse());
  await pending;
  assert.equal(f.status.textContent, "READY");
  assert.equal(f.audio.hidden, false);
  assert.equal(f.shared.length, 0);
  f.stop.emit("click");
  assert.equal(f.status.textContent, "STOPPED");
  assert.equal(f.audio.hidden, true);
  assert.deepEqual(f.revoked, ["blob:audio0"]);
});
