const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { webcrypto } = require("node:crypto");
const { runInNewContext } = require("node:vm");
const { setTimeout: delay } = require("node:timers/promises");

class Target {
  constructor() { this.handlers = new Map(); }
  addEventListener(name, listener) {
    this.handlers.set(name, [...this.handlers.get(name) || [], listener]);
  }
  emit(name, data = {}) {
    for (const callback of this.handlers.get(name) || []) callback(data);
  }
  dispatchEvent(event) { this.emit(event.type, event); }
}

function fixture(t, source) {
  const window = new Target();
  window.top = window;
  window.navigation = new Target();
  const location = new URL("http://127.0.0.1:8765/inventory/capture/intake");
  const form = new Target(), field = new Target(), button = new Target(), help = {}, status = {};
  button.hidden = help.hidden = true;
  field.value = "";
  field.form = form;
  field.isConnected = true;
  let current = form;
  let effects = 0;
  form.elements = { namedItem: (name) => name === "barcode" ? field : null };
  form.querySelector = (selector) => ({
    "#intake-barcode": field,
    "[data-intake-native-scan]": button,
    "[data-intake-native-scan-status]": status,
    "[data-intake-native-scan-help]": help,
  })[selector];
  const document = { querySelector: () => current };
  runInNewContext(source, { window, document, location, Event, crypto: webcrypto, fetch: () => { effects++; } });
  const channels = [];
  t.after(() => channels.forEach((pair) => { pair.port1.close(); pair.port2.close(); }));
  const transfer = (origin = "null", source = null) => {
    const pair = new MessageChannel();
    channels.push(pair);
    const calls = [], cancellations = [];
    pair.port1.onmessage = ({ data }) => (data.startsWith("cancel:") ? cancellations : calls).push(data);
    window.emit("message", { origin, source, data:"simpleoffice:intake-barcode/v1", ports:[pair.port2] });
    return { calls, cancellations, reply: (id, value, state = "ok") => pair.port1.postMessage(id + ":" + state + ":" + value) };
  };
  return { window, location, form, field, button, help, status, transfer, effects: () => effects,
    replace: () => { current = {}; field.isConnected = false; } };
}

async function until(predicate) {
  const deadline = Date.now() + 2000;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await delay(5);
  }
  assert.fail("condition not reached");
}

module.exports = { fixture, until };

// Contract helper receives the exact authenticated page's served script on stdin.
if (require.main === module) {
  void (async () => {
    const after = [];
    try {
      const f = fixture({after: (cleanup) => after.push(cleanup)}, readFileSync(0, "utf8"));
      const channel = f.transfer();
      f.button.emit("click", {isTrusted: true});
      await until(() => channel.calls.length === 1);
      const code = "036000291452";
      channel.reply(channel.calls[0].slice(5), code);
      await until(() => f.field.value === code);
      console.log(JSON.stringify({staged: f.field.value, effects: f.effects()}));
    } finally {
      after.forEach((cleanup) => cleanup());
    }
  })().catch((error) => { console.error(error); process.exitCode = 1; });
}
