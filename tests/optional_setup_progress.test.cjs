const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync("static/js/optional-setup-progress.js", "utf8");

function setup(initial, returned, feature = "kids-hand", hidden = false) {
  const events = new Map(), delays = [], fetched = [], deadlines = new Set();
  let reloaded = 0;
  const badge = {dataset:{optionalStatus:initial}};
  const classes = new Set();
  const fill = {style:{width:""}, classList:{add:(...names) => names.forEach(name => classes.add(name)),
    remove:(...names) => names.forEach(name => classes.delete(name))}};
  const barAttributes = new Map();
  const bar = {
    setAttribute:(name, value) => barAttributes.set(name, value),
    removeAttribute:(name) => barAttributes.delete(name),
  };
  const message = {textContent:""};
  const container = {
    hidden:true,
    querySelector:(selector) => selector === ".progress" ? bar
      : selector === ".progress-bar" ? fill
        : selector === "[data-progress-message]" ? message : null,
  };
  const page = {dataset:{progress:"/setup/optional/progress", progressDetails:"/setup/optional/progress/details"}};
  const document = {
    hidden,
    documentElement:{lang:"de"},
    querySelector:(selector) => selector === "[data-optional-setup]" ? page
      : selector === `[data-component-progress="${feature}"]` ? container : null,
    getElementById:(name) => name === feature ? {querySelector:() => badge} : null,
  };
  const window = {
    addEventListener:(name, listener) => events.set(name, listener),
    setTimeout:(callback, ms) => {
      if (ms === 3500) deadlines.add(callback);
      else delays.push(callback);
      return callback;
    },
    clearTimeout:(callback) => deadlines.delete(callback),
    location:{reload:() => {reloaded += 1;}},
  };
  vm.runInNewContext(code, {window, document, AbortController, fetch:async (path, options) => {
    fetched.push([path, options]);
    return {ok:true, json:async () => ({[feature]:returned})};
  }});
  return {document, events, delays, deadlines, fetched, barAttributes, fill, classes, container, message, reloaded:() => reloaded};
}

test("no polling when a fresh or ready page has no queued job", () => {
  for (const state of ["missing", "ready", "failed"]) assert.equal(setup(state, {state}).delays.length, 0);
});

test("authenticated polling displays measured byte progress and accessible percent", async () => {
  const fixture = setup("downloading", {
    state:"downloading", file:"hand_landmarker.task", received:4096, total:8192, percent:50,
  });
  await fixture.delays.shift()();
  assert.equal(fixture.reloaded(), 0);
  assert.equal(fixture.container.hidden, false);
  assert.equal(fixture.fill.style.width, "50%");
  assert.equal(fixture.barAttributes.get("aria-valuenow"), "50");
  assert.match(fixture.message.textContent, /hand_landmarker\.task · 50% \(4\.0 KiB \/ 8\.0 KiB\)/);
  const [path, options] = fixture.fetched[0];
  assert.equal(path, "/setup/optional/progress/details");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.cache, "no-store");
});

test("hidden tab makes no requests and page leave stops an outstanding refresh", async () => {
  const fixture = setup("downloading", {state:"failed"}, "kids-hand", true);
  await fixture.delays.shift()();
  assert.equal(fixture.fetched.length, 0);
  assert.equal(fixture.delays.length, 1);
  fixture.events.get("pagehide")();
  await fixture.delays.shift()();
  assert.equal(fixture.fetched.length, 0);
  assert.equal(fixture.delays.length, 0);
  assert.equal(fixture.reloaded(), 0);
});

test("terminal cancellation is announced before refreshing the voice package card", async () => {
  const fixture = setup("cancelling", {state:"cancelled"}, "voice-package");
  await fixture.delays.shift()();
  assert.equal(fixture.reloaded(), 0);
  assert.match(fixture.message.textContent, /Einrichtung abgebrochen/);
  fixture.delays.shift()();
  assert.equal(fixture.reloaded(), 1);
});

test("English voice installation has independent progress and terminal refresh", async () => {
  const fixture = setup("downloading", {
    state:"downloading", file:"en_US-libritts-high.onnx", received:1048576, total:136693974, percent:0,
  }, "english-voice");
  await fixture.delays.shift()();
  assert.equal(fixture.reloaded(), 0);
  assert.equal(fixture.container.hidden, false);
  assert.match(fixture.message.textContent, /en_US-libritts-high\.onnx · 0%/);
});

test("WebViews without AbortSignal.timeout poll queued downloads and allow cancelling", async () => {
  const fixture = setup("queued", {state:"queued", file:"", percent:null}, "english-voice");
  await fixture.delays.shift()();
  assert.equal(fixture.deadlines.size, 0);
  assert.equal(fixture.fetched[0][1].signal.aborted, false);
  assert.equal(fixture.container.hidden, false);
  assert.match(fixture.message.textContent, /Wartet auf den Downloadstart/);
});

test("managed Piper runtime exposes download progress and refreshes on completion", async () => {
  const fixture = setup("downloading", {
    state:"downloading", file:"piper_tts-1.8.0.whl", received:4096, total:8192, percent:50,
  }, "piper-engine");
  await fixture.delays.shift()();
  assert.equal(fixture.reloaded(), 0);
  assert.equal(fixture.container.hidden, false);
  assert.match(fixture.message.textContent, /piper_tts-1\.8\.0\.whl · 50%/);
  const finished = setup("downloading", {state:"ready"}, "piper-engine");
  await finished.delays.shift()();
  assert.equal(finished.reloaded(), 1);
});

test("Piper preparation, install and check phases stay indeterminate after wheel downloads", async () => {
  for (const [phase, expected] of [["prepare", /wird vorbereitet/], ["install", /wird installiert/], ["check", /wird geprüft/]]) {
    const fixture = setup("downloading", {
      state:"downloading", phase, file:"Installing Piper runtime", received:0, total:0, percent:null,
    }, "piper-engine");
    await fixture.delays.shift()();
    assert.equal(fixture.barAttributes.has("aria-valuenow"), false);
    assert.ok(fixture.classes.has("progress-bar-animated"));
    assert.match(fixture.message.textContent, expected);
  }
});
