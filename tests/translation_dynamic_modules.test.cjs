const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {test} = require("node:test");

async function shoppingHarness(language) {
  const nodes = new Map();
  class Node {
    constructor(id = "") { this.id = id; this.value = id === "shopping-barcode" ? "4012345678901" : ""; this.dataset = {}; this.listeners = {}; this.files = []; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    removeAttribute() {}
    focus() {}
  }
  const app = new Node("shopping-app");
  app.dataset.barcodeUrl = "/shopping/barcode";
  const document = {
    documentElement: {lang: language},
    getElementById(id) {
      if (id === "shopping-app") return app;
      if (!nodes.has(id)) nodes.set(id, new Node(id));
      return nodes.get(id);
    },
  };
  const window = {
    SimpleOfficeI18n: {text: (de, en) => language === "de" ? de : en},
    addEventListener() {}, location: {href: "/shopping/"},
    setTimeout: (callback) => { queueMicrotask(callback); return 1; },
  };
  const context = {
    document, window, navigator: {onLine: true}, URL, URLSearchParams, FormData,
    fetch: async () => ({ok: true, json: async () => ({known: false, barcode: "4012345678901"})}),
    encodeURIComponent, setTimeout, clearTimeout,
  };
  const source = fs.readFileSync(path.join(__dirname, "../static/js/shopping.js"), "utf8");
  vm.runInNewContext(source, context);
  await nodes.get("shopping-barcode-check").listeners.click();
  return nodes.get("shopping-scan-status").textContent;
}

test("shopping barcode feedback follows the selected language", async () => {
  assert.match(await shoppingHarness("en"), /valid but not known locally/);
  assert.match(await shoppingHarness("de"), /lokal noch unbekannt/);
});
