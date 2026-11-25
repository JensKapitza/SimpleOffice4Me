const assert = require("node:assert/strict");
const test = require("node:test");
const { ReactionGame, VirtualStations } = require("../static/js/kids_reaction_game.js");
const { UsbStations, parsed, Lines } = require("../static/js/kids_reaction_usb.js");
const { keyStation, serialEligible } = require("../static/js/kids_reaction_app.js");

const acknowledge = (g, target) => g.event({ type: "target", ...target });
const hit = (g, target, ms = 300) => g.event({ type: "hit", ...target, ms });
const miss = (g, target) => g.event({ type: "miss", ...target });

test("virtual practice never times out; wrong input and total time are distinct", () => {
  let clock = 1000;
  const game = new ReactionGame();
  const virtual = new VirtualStations((event) => {
    const result = game.event(event);
    if (result?.next) virtual.arm(result.next);
  }, () => clock, () => assert.fail("no timer in practice"));
  virtual.arm(game.start("practice"));
  clock += 100000;
  virtual.press(2);
  virtual.press(1);
  clock += 205;
  virtual.press(2);
  clock += 315;
  virtual.press(3);
  assert.equal(game.phase, "complete");
  assert.equal(game.score.hits, 3);
  assert.equal(game.score.errors, 1);
  assert.equal(game.score.misses, 0);
  assert.equal(game.score.total, 100520);
});

test("duel uses the same six targets and target windows, alternates explicitly and keeps both results", () => {
  const game = new ReactionGame();
  let target = game.start("duel");
  const sequences = [[], []];
  for (let player = 0; player < 2; player++) {
    while (game.phase === "playing") {
      sequences[player].push([target.id, target.window]);
      assert.equal(target.window, 7000);
      assert.equal(acknowledge(game, target).status, "target");
      target = player === 0 && sequences[player].length === 1
        ? miss(game, target).next : hit(game, target, player === 0 ? 12 : 750).next;
    }
    if (player === 0) {
      assert.equal(game.phase, "change");
      assert.equal(game.start("duel"), null);
      target = game.continue();
    }
  }
  assert.deepEqual(sequences[0], sequences[1]);
  assert.equal(sequences[0].length, 6);
  assert.deepEqual(game.results.map((s) => [s.hits, s.misses, s.total]), [[5, 1, 7060], [6, 0, 4500]]);
  assert.equal(game.winner(), 2);
});

test("ordered wrong press or timeout retries same sequence, improves to a gentle finite floor", () => {
  const game = new ReactionGame();
  let target = game.start("ordered");
  acknowledge(game, target);
  assert.equal(game.event({ type: "wrong", ticket: target.ticket, id: 3, ms: 400 }).stop, true);
  assert.equal(game.phase, "retry");
  target = game.continue();
  assert.equal(target.id, 1);
  acknowledge(game, target);
  assert.equal(miss(game, target).stop, true);
  target = game.continue();
  const levelWindows = [];
  for (let level = 0; level < 4; level++) {
    levelWindows.push(target.window);
    for (let i = 0; i < 3; i++) {
      acknowledge(game, target);
      target = hit(game, target).next;
    }
    if (level < 3) {
      assert.equal(game.phase, "level");
      target = game.continue();
    }
  }
  assert.deepEqual(levelWindows, [8000, 6500, 5000, 4000]);
  assert.equal(game.phase, "complete");
  assert.equal(game.continue(), null);
});

test("old, duplicate and unacknowledged game frames do not score", () => {
  const g = new ReactionGame();
  const first = g.start("duel");
  assert.equal(hit(g, first), null);
  assert.ok(acknowledge(g, first));
  assert.equal(acknowledge(g, first), null);
  const wrong = { type: "wrong", ticket: first.ticket, id: 3, ms: 125, seq: 20 };
  assert.ok(g.event(wrong));
  assert.equal(g.event(wrong), null);
  assert.equal(g.score.errors, 1);
  const second = hit(g, first).next;
  assert.equal(hit(g, first), null);
  acknowledge(g, second);
  assert.equal(hit(g, second, -1), null);
  assert.equal(hit(g, second, 7000), null);
  g.stop();
  assert.equal(hit(g, second), null);
  assert.ok(g.start("practice").ticket > second.ticket);
});

test("keyboard only recognizes fresh 1–3 presses in the focused game region", () => {
  const focus = {};
  assert.equal(keyStation({ target: focus, key: "2" }, focus), 2);
  for (const change of [{ target: {} }, { key: "a" }, { repeat: true }, { ctrlKey: true },
    { altKey: true }, { shiftKey: true }, { metaKey: true }, { isComposing: true }]) {
    assert.equal(keyStation({ target: focus, key: "2", ...change }, focus), null);
  }
});

const token = "aabbccddeeff0011";

test("serial decoder handles chunks with bounded ASCII frames and discards banner/noise", () => {
  const out = [];
  const decoder = new Lines((line) => out.push(line));
  for (const part of ["x".repeat(97) + "\n", "REA", `DY ${token} 1\r\n`, "\x00bad\n",
    `HIT ${token} 10 3 015 5\n`, `HIT ${token} 10 3 15 5\n`]) decoder.feed(Buffer.from(part));
  assert.equal(out.length, 3);
  assert.deepEqual(parsed(out[0], token), { type: "ready" });
  assert.equal(parsed(out[1], token), null);
  assert.deepEqual(parsed(out[2], token), { type: "hit", ticket: 10, id: 3, ms: 15, seq: 5 });
  assert.equal(parsed(out[2], "1111111111111111"), null);
});

test("USB port mock: requires handshake and matching target ACK, deduplicates and disconnects without restart", async () => {
  let nextRead;
  const commands = [];
  let disconnected;
  const port = {
    async open(options) { assert.equal(options.baudRate, 115200); },
    async close() {},
    addEventListener(type, cb) { assert.equal(type, "disconnect"); disconnected = cb; },
    removeEventListener() {},
    readable: { getReader: () => ({
      read: () => new Promise((resolve) => { nextRead = resolve; }),
      async cancel() { nextRead?.({ done: true }); },
      releaseLock() {}
    }) },
    writable: { getWriter: () => ({
      async write(bytes) { commands.push(Buffer.from(bytes).toString().trim()); },
      async abort() {},
      releaseLock() {}
    }) }
  };
  let ticks = 0;
  const events = [], status = [];
  const adapter = new UsbStations(port, (event) => events.push(event), (state) => status.push(state),
    { epoch: token, now: () => ticks, interval: () => 42, clear: (i) => assert.equal(i, 42) });
  await adapter.open();
  assert.deepEqual(commands, [`HELLO ${token}`]);
  const push = async (line) => {
    nextRead({ done: false, value: Buffer.from(line + "\n") });
    await new Promise((resolve) => setImmediate(resolve));
  };
  await push(`READY ${token} 1`);
  assert.deepEqual(status, ["ready"]);
  await push(`READY ${token} 1`);
  await push(`PONG ${token} 1`);
  adapter.arm({ ticket: 1, id: 2, window: 7000 });
  await push(`HIT ${token} 1 2 150 1`);
  await push(`TARGET ${token} 1 1 7000`);
  await push(`TARGET ${token} 1 2 7000`);
  await push(`WRONG ${token} 1 3 200 1`);
  await push(`WRONG ${token} 1 3 200 1`);
  await push(`HIT ${token} 1 2 7000 2`);
  await push(`HIT ${token} 1 2 500 2`);
  await push(`HIT ${token} 1 2 500 2`);
  assert.deepEqual(events.map((e) => [e.type, e.ms]), [["target", undefined], ["wrong", 200], ["hit", 500]]);
  adapter.arm({ ticket: 2, id: 1, window: 7000 });
  disconnected();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(adapter.closed, true);
  assert.deepEqual(status, ["ready", "lost"]);
  ticks += 6000;
  adapter.tick();
  assert.equal(commands.filter((line) => line.startsWith("ARM")).length, 2);
});

test("shipped page app uses only the focused keyboard area and renders an explicit visual reward", () => {
  const vm = require("node:vm");
  const fs = require("node:fs");
  function element() {
    return {
      handlers: {}, textContent: "", dataset: {}, classes: new Set(),
      addEventListener(event, cb) { this.handlers[event] = cb; },
      replaceChildren(...children) { this.children = children; },
      append(child) { (this.children ??= []).push(child); },
      focus() { this.focused = true; },
      get classList() { return { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name) }; }
    };
  }
  const names = "keyboard play-state start mode stop continue target window sequence progress reward connect disconnect connection score results compare".split(" ");
  const elements = Object.fromEntries(names.map((name) => [name, element()]));
  elements.mode.value = "practice";
  const buttons = [1, 2, 3].map((id) => {
    const button = element();
    button.dataset.station = String(id);
    return button;
  });
  const page = {
    dataset: { en: "1", usb: "0" },
    querySelector: (selector) => elements[selector.slice(6, -1)],
    querySelectorAll: () => buttons
  };
  let now = 100;
  const doc = { querySelector: () => page, createElement: element, ...element() };
  // Keep querySelector and createElement native-shaped after adding event methods.
  doc.querySelector = () => page;
  doc.createElement = element;
  const environment = { document: doc, performance: { now: () => now }, setTimeout, clearTimeout,
    addEventListener: () => {}, KidsReactionGame: { ReactionGame, VirtualStations }, KidsReactionUsb: { UsbStations } };
  // Inject the clock into the same class that the actual browser boot receives.
  environment.KidsReactionGame.VirtualStations = class extends VirtualStations {
    constructor(emit) { super(emit, () => now); }
  };
  vm.runInNewContext(fs.readFileSync(require.resolve("../static/js/kids_reaction_app.js"), "utf8"), environment);
  const area = elements.keyboard;
  elements.start.handlers.click();
  assert.equal(area.focused, true);
  assert.equal(elements.target.textContent, "Station 1 · Key 1");
  assert.equal(elements.connect.disabled, true);
  assert.equal(doc.handlers.keydown, undefined);
  area.handlers.keydown({ key: "1", target: buttons[0], preventDefault: () => assert.fail("wrong focus") });
  area.handlers.keydown({ key: "1", target: area, repeat: true, preventDefault: () => assert.fail("repeated key") });
  assert.match(elements.score.textContent, /Hits: 0/);
  for (const key of ["1", "2", "3"]) {
    now += 123;
    area.handlers.keydown({ key, target: area, preventDefault: () => {} });
  }
  assert.match(elements.reward.textContent, /Well played/);
  assert.match(elements.score.textContent, /Hits: 3.*369 ms/);
  assert.equal(elements.results.children.length, 3);
});

test("serial requires the actual browser to be top-level secure loopback even when page permits USB", () => {
  const view = {
    location: { hostname: "127.0.0.1" }, isSecureContext: true,
    navigator: { serial: { requestPort() {} } }
  };
  view.top = view;
  const page = { dataset: { usb: "1" } };
  assert.equal(serialEligible(view, page), true);
  view.location.hostname = "office.example.invalid";
  assert.equal(serialEligible(view, page), false);
  view.location.hostname = "localhost";
  assert.equal(serialEligible(view, page), true);
  page.dataset.usb = "0";
  assert.equal(serialEligible(view, page), false);
  page.dataset.usb = "1";
  view.top = {};
  assert.equal(serialEligible(view, page), false);
});
