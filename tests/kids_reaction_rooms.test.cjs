"use strict";
const { keyStation, elapsedMs } = require("../static/js/kids_reaction_game.js");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const { test } = require("node:test");
const assert = require("node:assert/strict");

const script = readFileSync(new URL("../static/js/kids_reaction_rooms.js", `file://${__filename}`), "utf8");
const drain = () => new Promise(setImmediate);

function mockElement() {
  const listeners = new Map();
  return {
    dataset: {}, hidden: false, textContent: "", disabled: false, value: "", focused: false, children: [],
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    removeAttribute(name) { delete this.attributes[name]; },
    getAttribute(name) { return this.attributes[name] ?? null; },
    addEventListener: (event, handle) => listeners.set(event, handle),
    focus() { this.focused = true; },
    replaceChildren(...items) { this.children = items; },
    dispatch(type, fields = {}) {
      const event = { target: this, prevented: false, preventDefault() { this.prevented = true; }, ...fields };
      listeners.get(type)?.(event);
      return event;
    },
  };
}

function harness(token = "fictive-station-token", search = "") {
  const elements = new Map();
  const get = (name) => {
    if (!elements.has(name)) elements.set(name, mockElement());
    return elements.get(name);
  };
  const calls = [];
  const responses = [];
  const jobs = new Map();
  const frames = [];
  const document = {
    ...mockElement(), hidden: false,
    createElement: mockElement,
    querySelector: () => ({
      dataset: { api: "/rooms/api", csrf: "fictive-csrf-token", en: "1", qr: "/rooms/qr" },
      querySelector: (selector) => get(selector.match(/^\[data-room-([a-z-]+)\]$/)[1]),
    }),
  };
  let now = 100;
  let timerId = 0;
  const window = {
    ...mockElement(),
    location: { search },
    setTimeout(fn, delay) { jobs.set(++timerId, { fn, delay }); return timerId; },
    clearTimeout(id) { jobs.delete(id); },
    requestAnimationFrame(fn) { frames.push(fn); },
  };
  const snapshot = (overrides = {}) => ({
    station: 2, role: "station", join_code: null, phase: "lobby", mode: "practice",
    target: null, stations: [1, 2], turn: 1, rows: [], scores: [], winner: null, ...overrides,
  });
  const respond = (next) => responses.push(next);
  async function cycle(delay = 500) {
    const timer = [...jobs].find(([, item]) => item.delay === delay);
    assert.ok(timer, `No timer for ${delay} ms`);
    jobs.delete(timer[0]);
    now += delay;
    timer[1].fn();
    await drain();
  }
  runInNewContext(script, {
    document, window, AbortController, URLSearchParams, KidsReactionGame: { keyStation, elapsedMs: (start) => elapsedMs(start, now) },
    performance: { now: () => now },
    async fetch(url, options) {
      calls.push({ url, body: JSON.parse(options.body), headers: options.headers, keepalive: options.keepalive });
      const payload = responses.shift();
      assert.ok(payload, "Expected a fake response for this HTTP request");
      return { json: async () => payload };
    },
  });
  async function join() {
    respond(snapshot({ token }));
    get("code").value = "aaab2222";
    get("join").dispatch("submit");
    await drain();
    assert.equal(calls[0].body.code, "AAAB2222");
    assert.equal(calls[0].url, "/rooms/api");
    assert.equal(get("code").value, "");
    assert.ok(get("keyboard").focused);
  }
  return { get, calls, document, window, frames, snapshot, respond, cycle, join, clock: (ms) => { now += ms; } };
}

test("only the assigned station receives the visible touch/key cue; valid local presses are sent exactly once", async () => {
  const h = harness();
  await h.join();
  h.respond(h.snapshot({ phase: "playing", target: { station: 1, ticket: 1, window: 7000 } }));
  await h.cycle();
  assert.equal(h.get("target").textContent, "Active station: 1");
  assert.equal(h.get("touch").dataset.active, "no");
  assert.equal(h.calls.filter((x) => x.body.action === "ack").length, 0);
  const own = h.snapshot({ phase: "playing", target: { station: 2, ticket: 2, window: 7000 } });
  h.respond(own);
  h.respond(own);
  await h.cycle();
  assert.equal(h.calls.at(-1).body.action, "ack");
  assert.equal(h.get("touch").disabled, true);
  h.frames.shift()();
  assert.equal(h.get("touch").textContent, "Touch me · Key 2");
  assert.equal(h.get("prompt").textContent, "Touch me · Key 2");
  assert.equal(h.get("touch").disabled, false);

  const keyboard = h.get("keyboard");
  const touch = h.get("touch");
  assert.equal(keyboard.dispatch("keydown", { key: "1" }).prevented, false);
  assert.equal(keyboard.dispatch("keydown", { key: "2", repeat: true }).prevented, false);
  assert.equal(keyboard.dispatch("keydown", { key: "2", ctrlKey: true }).prevented, false);
  assert.equal(keyboard.dispatch("keydown", { key: "2", target: h.get("leave") }).prevented, false);
  h.document.dispatch("keydown", { key: "2" });
  assert.equal(h.calls.filter((x) => x.body.action === "press").length, 0);
  h.clock(241);
  h.respond(h.snapshot({ phase: "playing", target: { station: 1, ticket: 3, window: 7000 } }));
  assert.equal(keyboard.dispatch("keydown", { key: "2" }).prevented, true);
  touch.dispatch("click");
  await drain();
  assert.equal(h.calls.filter((x) => x.body.action === "press").length, 1);
  assert.deepEqual(h.calls.at(-1).body, { action: "press", ticket: 2, ms: 241 });
  assert.equal(h.calls.at(-1).headers["X-CSRF-Token"], "fictive-csrf-token");
  assert.equal(h.calls.at(-1).headers["X-Reaction-Station"], "fictive-station-token");
  assert.equal(h.get("touch").disabled, true);
});

test("visibility loss clears the cue, releases the room and never automatically reconnects", async () => {
  const h = harness();
  await h.join();
  const own = h.snapshot({ phase: "playing", target: { station: 2, ticket: 1, window: 7000 } });
  h.respond(own);
  h.respond(own);
  await h.cycle();
  assert.equal(h.frames.length, 1);
  h.respond({ phase: "closed" });
  h.document.hidden = true;
  h.document.dispatch("visibilitychange");
  h.frames.shift()();
  assert.equal(h.get("touch").disabled, true);
  assert.equal(h.get("play").hidden, true);
  assert.equal(h.get("entry").hidden, false);
  h.document.hidden = false;
  h.document.dispatch("visibilitychange");
  await drain();
  assert.equal(h.calls.at(-1).body.action, "leave");
  assert.equal(h.calls.at(-1).keepalive, true);
});

test("host stop releases everyone and clears the visible code", async () => {
  const h = harness();
  h.respond(h.snapshot({ station: 1, role: "host", token: "host-token", join_code: "ABCDEFGH" }));
  h.get("host").dispatch("click");
  await drain();
  assert.equal(h.get("invite").hidden, false);
  assert.match(h.get("invite").textContent, /ABCDEFGH/);
  assert.equal(h.get("leave").textContent, "Stop room for everyone");
  h.respond({ phase: "closed" });
  h.get("leave").dispatch("click");
  await drain();
  assert.equal(h.calls.at(-1).body.action, "stop");
  assert.equal(h.get("play").hidden, true);
  assert.equal(h.get("active").hidden, true);
  assert.equal(h.get("invite").textContent, "");
});

test("host invite shows a QR code that disappears when the room closes", async () => {
  const h = harness();
  h.respond(h.snapshot({ station: 1, role: "host", token: "host-token", join_code: "ABCDEFGH" }));
  h.get("host").dispatch("click");
  await drain();
  assert.equal(h.get("qr-wrap").hidden, false);
  assert.equal(h.get("qr").getAttribute("src"), "/rooms/qr?code=ABCDEFGH");
  h.respond({ phase: "closed" });
  h.get("leave").dispatch("click");
  await drain();
  assert.equal(h.get("qr-wrap").hidden, true);
  assert.equal(h.get("qr").getAttribute("src"), null);
});

test("shared room code from the URL is prefilled, invalid codes are ignored", async () => {
  const good = harness("fictive-station-token", "?code=abcdefgh");
  assert.equal(good.get("code").value, "ABCDEFGH");
  const bad = harness("fictive-station-token", "?code=evil!!!");
  assert.equal(bad.get("code").value, "");
  const none = harness();
  assert.equal(none.get("code").value, "");
});
