const assert = require("node:assert/strict");
const {test} = require("node:test");
const {install, safePeerUrl} = require("../static/js/federation_browser_diagnostics.js");

function harness({fetchImpl, confirmResult = true, setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout} = {}) {
  const listeners = new Map();
  const status = {dataset: {peerBrowserStatus: "peer-a"}, textContent: "Client: ungeprüft"};
  const buttons = [
    {dataset: {peerOpen: "peer-a", peerUrl: "https://peer.example/base"}},
    {dataset: {peerBrowserCheck: "peer-a", peerUrl: "https://peer.example/base"}},
    {dataset: {peerManualConfirm: "peer-a"}},
  ].map(button => {
    const element = {...button};
    element.addEventListener = (event, listener) => listeners.set(element, listener);
    return element;
  });
  const document = {
    querySelectorAll(selector) {
      if (selector === "[data-peer-browser-status]") return [status];
      if (selector === "[data-peer-open]") return [buttons[0]];
      if (selector === "[data-peer-browser-check]") return [buttons[1]];
      if (selector === "[data-peer-manual-confirm]") return [buttons[2]];
      return [];
    },
  };
  const opened = [];
  const confirms = [];
  const window = {
    URL,
    Date,
    AbortController,
    setTimeout: setTimeoutImpl,
    clearTimeout: clearTimeoutImpl,
    fetch: fetchImpl || (async () => ({type: "opaque", status: 0})),
    open: (...args) => opened.push(args),
    confirm: message => { confirms.push(message); return confirmResult; },
  };
  install(document, window);
  return {buttons, listeners, status, opened, confirms};
}

test("only credential-free HTTP(S) peer URLs are eligible for browser actions", () => {
  assert.equal(safePeerUrl("javascript:alert(1)", URL), null);
  assert.equal(safePeerUrl("https://user:secret@peer.example", URL), null);
  assert.equal(safePeerUrl("https://peer.example/?token=secret", URL), null);
  assert.equal(safePeerUrl("https://peer.example/#secret", URL), null);
  assert.equal(safePeerUrl("https://peer.example/base", URL).href, "https://peer.example/base");
});

test("opening a peer requires a user click and opens without credentials/referrer", () => {
  const page = harness();
  page.listeners.get(page.buttons[0])();
  assert.deepEqual(page.opened, [["https://peer.example/base", "_blank", "noopener,noreferrer"]]);
  assert.match(page.status.textContent, /bitte selbst prüfen/);
});

test("browser probe omits credentials and never calls an opaque response verified success", async () => {
  const requests = [];
  const page = harness({fetchImpl: async (...args) => { requests.push(args); return {type: "opaque", status: 0}; }});
  await page.listeners.get(page.buttons[1])();
  assert.equal(requests.length, 1);
  assert.equal(requests[0][0], "https://peer.example/base/auth/login");
  assert.equal(requests[0][1].method, "GET");
  assert.equal(requests[0][1].mode, "no-cors");
  assert.equal(requests[0][1].credentials, "omit");
  assert.equal(requests[0][1].cache, "no-store");
  assert.equal(requests[0][1].signal instanceof AbortSignal, true);
  assert.match(page.status.textContent, /Antwort nicht verifizierbar/);
  assert.doesNotMatch(page.status.textContent, /erfolgreich|erreichbar/);
});

test("network errors are indeterminate, not an offline verdict", async () => {
  const page = harness({fetchImpl: async () => { throw new Error("blocked"); }});
  await page.listeners.get(page.buttons[1])();
  assert.match(page.status.textContent, /nicht auswertbar/);
  assert.match(page.status.textContent, /nicht zwingend offline/);
});

test("probe timeout is bounded and remains an indeterminate result", async () => {
  const scheduled = [];
  const page = harness({
    setTimeoutImpl: (callback, delay) => { scheduled.push({callback, delay}); return 1; },
    clearTimeoutImpl: () => {},
    fetchImpl: (_url, options) => new Promise((resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(new Error("aborted")), {once: true});
    }),
  });
  const pending = page.listeners.get(page.buttons[1])();
  assert.equal(scheduled[0].delay, 5000);
  scheduled[0].callback();
  await pending;
  assert.match(page.status.textContent, /Timeout/);
  assert.match(page.status.textContent, /Peer nicht zwingend offline/);
});

test("manual confirmation is explicit and marked unverified", () => {
  const page = harness();
  page.listeners.get(page.buttons[2])();
  assert.equal(page.confirms.length, 1);
  assert.match(page.status.textContent, /manuell bestätigt/);
  assert.match(page.status.textContent, /nicht technisch verifiziert/);
  assert.match(page.status.textContent, /nur diese Browser-Sitzung/);
});

test("declining manual confirmation leaves the state unchanged", () => {
  const page = harness({confirmResult: false});
  page.listeners.get(page.buttons[2])();
  assert.equal(page.status.textContent, "Client: ungeprüft");
});
