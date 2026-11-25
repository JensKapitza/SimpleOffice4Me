const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync("static/js/optional-setup-progress.js", "utf8");

function setup(initial, returned) {
  const events = new Map(), delays = [], fetched = [];
  let reloaded = 0;
  const document = {
    hidden:false,
    querySelector: () => ({dataset:{progress:"/setup/optional/progress"}}),
    getElementById: (name) => name === "kids-hand" ? {
      querySelector: () => ({dataset:{optionalStatus:initial}}),
    } : null,
  };
  const window = {
    addEventListener: (name, listener) => events.set(name, listener),
    setTimeout: (callback) => delays.push(callback),
    location:{reload: () => { reloaded += 1; }},
  };
  vm.runInNewContext(code, {window, document, AbortSignal, fetch: async (path, options) => {
    fetched.push([path, options]);
    return {ok:true, json:async () => ({"kids-hand":returned})};
  }});
  return {document, events, delays, fetched, reloaded:() => reloaded};
}

test("no polling when a fresh or ready page has no queued job", () => {
  for (const state of ["missing", "ready", "failed"]) assert.equal(setup(state, state).delays.length, 0);
});

test("pending page refreshes after a changed authenticated status only", async () => {
  const fixture = setup("queued", "downloading");
  await fixture.delays.shift()();
  assert.equal(fixture.reloaded(), 1);
  const [path, options] = fixture.fetched[0];
  assert.equal(path, "/setup/optional/progress");
  assert.equal(options.credentials, "same-origin");
  assert.equal(options.cache, "no-store");
});

test("hidden tab makes no requests and page leave stops an outstanding refresh", async () => {
  const fixture = setup("downloading", "failed");
  fixture.document.hidden = true;
  await fixture.delays.shift()();
  assert.equal(fixture.fetched.length, 0);
  assert.equal(fixture.delays.length, 1);
  fixture.events.get("pagehide")();
  await fixture.delays.shift()();
  assert.equal(fixture.fetched.length, 0);
  assert.equal(fixture.delays.length, 0);
  assert.equal(fixture.reloaded(), 0);
});
