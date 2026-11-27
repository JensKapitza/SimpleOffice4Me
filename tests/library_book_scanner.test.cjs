const assert = require("node:assert/strict");
const {readFileSync} = require("node:fs");
const {join} = require("node:path");
const {test} = require("node:test");
const vm = require("node:vm");

const script = readFileSync(join(__dirname, "../static/js/library_book_scanner.js"), "utf8");

function scanner(fetch, canShare = false) {
  const downloads = [];
  const revoked = [];
  function element() {
    const callbacks = new Map();
    return {
      children: [], style: {}, disabled: false, focus() {}, remove() {},
      addEventListener(name, callback) { callbacks.set(name, callback); },
      event(name, detail = {}) { return callbacks.get(name)?.({preventDefault() {}, ...detail}); },
      reset() { this.resetCount = (this.resetCount || 0) + 1; },
      click() { if (this.download) downloads.push(this.download); return this.event("click"); },
      append(...children) { this.children.push(...children); },
      replaceChildren() { this.children = []; },
      querySelector(selector) {
        if (selector === "button:not([disabled])") return this.children.at(-1).children.find(c => !c.disabled);
        return elements[selector];
      },
      querySelectorAll() { return [picker]; },
    };
  }
  const picker = element();
  const form = element();
  form.action = "/library/book-scan";
  const controls = element();
  const submit = element();
  const status = element();
  const list = element();
  const root = element();
  const share = element();
  const shareControls = element();
  share.hidden = true;
  shareControls.disabled = true;
  const window = element();
  window.setTimeout = (callback) => callback();
  const elements = {
    "[data-book-scanner]": root, "[data-scan-export]": form, "[data-scan-controls]": controls,
    "[data-scan-download]": submit, "[data-scan-pages]": list, "[data-scan-message]": status,
    "[data-scan-share]": share, "[data-scan-share-controls]": shareControls,
  };
  root.dataset = {maxPages: "4", maxPageBytes: "5", maxTotalBytes: "12", lang: "en"};
  vm.runInNewContext(script, {
    document: {querySelector: root.querySelector, createElement: element},
    URL: {createObjectURL: (file) => "blob:" + file.name, revokeObjectURL: (url) => revoked.push(url)},
    window,
    FormDataEvent: canShare ? function FormDataEvent() {} : undefined,
    FormData: class extends Map {
      constructor() { super(); this.set("_csrf_token", "fixture-csrf"); }
      append(key, value) { this.set(key, [...(this.get(key) || []), value]); }
    },
    fetch,
  });
  function add(...files) {
    picker.files = files.map(file => typeof file === "string" ? {name: file, size: 1} : file);
    picker.event("change");
  }
  return {add, controls, submit, form, share, shareControls, window, list, status, downloads, revoked};
}

test("review stays in the tab; explicit export sends the current order and rotation only once", async () => {
  const requests = [];
  let release;
  const ui = scanner((url, options) => {
    requests.push({url, options});
    return new Promise(resolve => { release = resolve; });
  });
  ui.add("first.jpg", "sideways.png", "exclude.png");
  ui.list.children[2].children[2].children[3].click();
  ui.list.children[1].children[2].children[0].click();
  ui.list.children[0].children[2].children[2].click();
  assert.match(ui.list.children[0].children[1].style.transform, /90deg/);
  assert.equal(requests.length, 0);
  const pending = ui.form.event("submit");
  const second = ui.form.event("submit");
  assert.equal(requests.length, 1);
  assert.equal(ui.controls.disabled, true);
  const {url, options} = requests[0];
  assert.equal(url, "/library/book-scan");
  assert.equal(options.method, "POST");
  assert.equal(options.redirect, "error");
  assert.equal(options.body.get("order"), "[[0,1],[1,0]]");
  assert.deepEqual(Array.from(options.body.get("pages"), f => f.name), ["sideways.png", "first.jpg"]);
  release({ok: true, headers: {get: () => "application/pdf"}, blob: async () => ({name: "generated"})});
  await Promise.all([pending, second]);
  assert.deepEqual(ui.downloads, ["Book-scan.pdf"]);
  assert.deepEqual(ui.revoked, ["blob:exclude.png", "blob:generated"]);
  assert.equal(ui.controls.disabled, false);
});

test("size or server validation errors keep the prior selection available without downloading", async () => {
  const ui = scanner(async () => ({
    ok: false, headers: {get: () => "application/json"}, json: async () => ({error: "Select smaller pages."}),
  }));
  ui.add("good.png");
  ui.add({name: "big.png", size: 12});
  assert.equal(ui.list.children.length, 1);
  assert.match(ui.status.textContent, /too large/);
  await ui.form.event("submit");
  assert.equal(ui.status.textContent, "Select smaller pages.");
  assert.equal(ui.submit.disabled, false);
  assert.deepEqual(ui.downloads, []);
});

test("only a successful export supplies the exact in-memory PDF to the separate sharing form", async () => {
  const pdf = {name: "generated"};
  let requests = 0;
  const ui = scanner(async () => {
    requests += 1;
    return {ok: true, headers: {get: () => "application/pdf"}, blob: async () => pdf};
  }, true);
  const submission = () => {
    const slots = [];
    ui.share.event("formdata", {formData: {set: (...args) => slots.push(args)}});
    return slots;
  };
  ui.add("first.jpg");
  assert.deepEqual(submission(), []);
  await ui.form.event("submit");
  assert.equal(requests, 1);
  assert.equal(ui.share.hidden, false);
  assert.equal(ui.shareControls.disabled, false);
  assert.deepEqual(submission(), [["pdf", pdf, "Book-scan.pdf"]]);
  const before = ui.share.resetCount;
  ui.list.children[0].children[2].children[2].click();
  assert.equal(ui.share.hidden, true);
  assert.equal(ui.shareControls.disabled, true);
  assert.equal(ui.share.resetCount, before + 1);
  assert.deepEqual(submission(), []);
  await ui.form.event("submit");
  ui.window.event("pagehide");
  assert.deepEqual(submission(), []);
});
