const assert = require("node:assert/strict");
const {test} = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
  filterItems, safeRepositoryUrl, itemMarkdown, gapMarkdown, readChecks, checklistEntries, platformChecklistMarkdown,
} = require("../static/js/repository_workspace.js");

const entry = (id, extra = {}) => ({
  id, title: `Eintrag ${id}`, type: "issue", area: "Dokumente", status: "open",
  priority: "P2", cause: "Ursache ungeprüft", blockers: [], updated_at: "2026-11-09T10:00:00Z", ...extra,
});

test("filters compose priority, area, type and status without changing source order", () => {
  const entries = [entry("a"), entry("b", {type: "pr", priority: "P1", status: "blocked"}), entry("c", {area: "Desktop"})];
  const before = entries.slice();
  assert.deepEqual(filterItems(entries, {priority: "P1", area: "Dokumente", type: "pr", status: "blocked"}).map((item) => item.id), ["b"]);
  assert.deepEqual(filterItems(entries, {area: "Nicht vorhanden"}), []);
  assert.deepEqual(entries, before);
});

test("search covers issue numbers, root causes and blockers case-insensitively", () => {
  const entries = [entry("pr-12", {number: 12, blockers: ["Windows PowerShell Timeout"]}), entry("issue-13", {cause: "OCR-Freigabe fehlt"})];
  for (const query of ["#12".slice(1), " powershell ", "pr-12"]) assert.equal(filterItems(entries, {q: query})[0].id, "pr-12");
  assert.equal(filterItems(entries, {q: "ocr-freigabe"})[0].id, "issue-13");
});

test("priority wins over recency and equal priorities show newest changes first", () => {
  const entries = [entry("low", {priority: "P3"}), entry("old", {priority: "P1", updated_at: "2026-10-01"}), entry("new", {priority: "P1", updated_at: "2026-11-01"})];
  assert.deepEqual(filterItems(entries, {}).map((item) => item.id), ["new", "old", "low"]);
});

test("source links allow only HTTPS GitHub links for the fixed repository", () => {
  assert.equal(safeRepositoryUrl("https://github.com/JensKapitza/SimpleOffice4Me/issues/874"), "https://github.com/JensKapitza/SimpleOffice4Me/issues/874");
  for (const url of ["javascript:alert(1)", "data:text/html,test", "https://github.com.evil.example/JensKapitza/SimpleOffice4Me", "https://github.com/other/repo", "https://github.com/JensKapitza/SimpleOffice4Me-evil", "https://user:secret@github.com/JensKapitza/SimpleOffice4Me", "http://github.com/JensKapitza/SimpleOffice4Me", "https://github.com/JensKapitza/SimpleOffice4Me/../../other/repo"]) {
    assert.equal(safeRepositoryUrl(url), null, url);
  }
});

test("Markdown exports preserve uncertainty and escape untrusted titles and links", () => {
  const content = itemMarkdown(entry("security-1", {
    title: "<script>untrusted</script> [title](https://evil.example)",
    type: "security", priority: "P1", status: "blocked", url: "javascript:alert(1)",
    test_status: {state: "unknown", summary: "Noch ungeprüft"},
    evidence: [{label: "Quelle [1]", url: "https://github.com/JensKapitza/SimpleOffice4Me/issues/874"}],
  }), "2026-11-09T10:00:00Z");
  assert.ok(content.includes("\\<script\\>"));
  assert.ok(!content.includes("[title](https://evil.example)"));
  assert.ok(!content.includes("javascript:"));
  assert.ok(content.includes("Noch ungeprüft"));
  assert.ok(content.includes("kein Freigabenachweis"));
  assert.deepEqual(content.split("\n").filter((line) => line.startsWith("- [")), [
    "- [Quelle \\[1\\]](https://github.com/JensKapitza/SimpleOffice4Me/issues/874)",
  ]);
});

test("draft exports are explicitly unpublished and never imply tests passed", () => {
  const content = gapMarkdown({title: "Neue Frage", cause: "Ursache offen"});
  assert.ok(content.includes("noch nicht geprüft und nicht als Issue veröffentlicht"));
  assert.ok(content.includes("Noch ungeprüft"));
});

test("persisted checklist reader retains only bounded boolean identifiers", () => {
  const storage = {getItem: () => JSON.stringify({scope: true, tests: false, token: "private", notes: {body: "private"}, "bad key": true})};
  assert.deepEqual(readChecks(storage), {scope: true});
  assert.throws(() => readChecks({getItem: () => "[]"}));
  assert.throws(() => readChecks({getItem: () => "invalid"}));
});

// Run the real browser controller against a minimal DOM and a controlled HTTP
// boundary. No global state export or production-only test hooks are required.
async function workspaceHarness() {
  const nodes = new Map(), stored = new Map();
  class Node {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase(); this.childNodes = []; this.events = {}; this.value = "";
      this.text = ""; this.dataset = {}; this.hidden = false; this.open = false;
      this.classList = {toggle() {}};
    }
    set id(value) { this.ident = value; nodes.set(value, this); }
    get id() { return this.ident; }
    set textContent(value) { this.text = String(value || ""); this.childNodes = []; }
    get textContent() { return this.text + this.childNodes.map((node) => node.textContent).join(""); }
    append(...children) { this.childNodes.push(...children); }
    replaceChildren(...children) { this.childNodes = children; this.text = ""; }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, handler) { this.events[name] = handler; }
    querySelectorAll() { return []; }
    get options() { return this.childNodes; }
    add(option) { this.append(option); }
    focus() {}
    showModal() { this.open = true; }
    close() { this.open = false; }
    reportValidity() { return true; }
    reset() {
      const identifiers = this.id === "rw-filters" ? ["search", "priority", "area", "type", "status", "platform"]
        : ["draft-title", "draft-area", "draft-cause", "draft-next", "draft-tests", "draft-docs"];
      identifiers.forEach((id) => { get(id).value = ""; });
    }
  }
  function get(id) {
    const full = `rw-${id}`;
    if (!nodes.has(full)) { const node = new Node(); node.id = full; }
    return nodes.get(full);
  }
  const root = new Node(); root.dataset.apiBase = "/repository-workspace/api";
  const document = {
    hidden: false, body: new Node(),
    querySelector: (selector) => selector === "[data-repository-workspace]" ? root : {content: "synthetic-csrf"},
    getElementById: (id) => get(id.replace(/^rw-/, "")),
    createElement: (tag) => new Node(tag), addEventListener() {},
  };
  let auth = {context_id: "account", enabled: true, authenticated: true, login: "Testkonto", can_authenticate: true};
  const publicAuth = {context_id: "public", enabled: true, authenticated: false, can_authenticate: true};
  const privateItem = entry("private", {type: "security", title: "Private finding", status: "blocked", platforms: ["Windows"], merge_preparation: {state: "not_applicable", summary: "Kein Pull Request"}, next_steps: ["Private next step"], documentation: [], evidence: [], test_status: {state: "unknown", summary: "Ungeprüft"}});
  function snapshot(currentAuth) {
    return {auth: currentAuth, mode: currentAuth.authenticated ? "account" : "public", items: currentAuth.authenticated ? [privateItem] : [], sources: [], checklist: [{id: "scope", title: "Umfang", detail: "Prüfen"}], documentation_gaps: [], platform_checklists: Object.fromEntries(["Windows", "Linux", "macOS", "Android"].map(p => [p, [{id: `platform-${p.toLowerCase()}-setup`, title: `${p} Start`, detail: "Ungeprüft"}]])), fetched_at: "2026-11-09T00:00:00Z", next_refresh_at: "2020-01-01"};
  }
  const response = (data, status = 200) => ({ok: status < 400, status, json: async () => data});
  let onRefresh = () => { auth = publicAuth; return response({auth}, 401); };
  const context = {
    document, window: {addEventListener() {}}, module: {exports: {}}, URL, Date, Blob, AbortController,
    setTimeout: () => 1, clearTimeout() {},
    Option: class extends Node { constructor(label, value) { super("option"); this.textContent = label; this.value = value; } },
    localStorage: {getItem: (key) => stored.get(key) || null, setItem: (key, value) => stored.set(key, value)},
    fetch: async (url) => {
      if (url.endsWith("/auth/logout")) { auth = publicAuth; return response({status: "signed_out"}); }
      if (url.endsWith("/status")) return response(auth);
      if (url.endsWith("/refresh")) return onRefresh();
      if (url.endsWith("/snapshot")) return response(snapshot(auth));
      throw new Error(`Unexpected request: ${url}`);
    },
  };
  const source = fs.readFileSync(path.join(__dirname, "../static/js/repository_workspace.js"), "utf8");
  vm.runInNewContext(source, context);
  await new Promise(setImmediate);
  const fire = (id, event = "click") => get(id).events[event]({preventDefault() {}});
  const findCard = (node) => node.tagName === "BUTTON" && node.className === "rw-entry" ? node : node.childNodes.map(findCard).find(Boolean);
  findCard(get("board")).events.click();
  fire("bookmark");
  get("note-0").value = "Private note"; fire("note-0", "input");
  get("draft-title").value = "Private draft"; get("draft-cause").value = "Private question"; fire("draft-form", "submit");
  return {get, fire, stored, privateSnapshot: () => response(snapshot({...publicAuth, authenticated: true, context_id: "account"})), setRefresh: (callback) => { onRefresh = callback; }};
}

function assertPrivateDataRemoved(harness) {
  const {get} = harness;
  assert.equal(get("auth-title").textContent, "Öffentliche Ansicht");
  assert.ok(get("results").textContent.includes("0 von 0"));
  assert.equal(get("detail-body").textContent, "");
  assert.equal(get("detail-title").textContent, "");
  assert.equal(get("detail").open, false);
  assert.equal(get("local-drafts").childNodes.length, 0);
  assert.equal(get("draft-title").value, "");
  assert.ok(!get("bookmarks").textContent.includes("Private"));
  assert.ok(!get("board").textContent.includes("Private"));
  assert.ok(!JSON.stringify([...harness.stored.values()]).includes("Private"));
}

test("401 after token revocation purges private snapshot, dialog, notes and drafts before public reload", async () => {
  const harness = await workspaceHarness();
  assert.ok(harness.get("board").textContent.includes("Private finding"));
  assert.equal(harness.get("local-drafts").childNodes.length, 1);
  await harness.fire("refresh");
  assertPrivateDataRemoved(harness);
});

test("late private response cannot repopulate the workspace after logout", async () => {
  const harness = await workspaceHarness();
  let resolveRefresh;
  harness.setRefresh(() => new Promise((resolve) => { resolveRefresh = resolve; }));
  const pending = harness.fire("refresh");
  await new Promise(setImmediate);
  await harness.fire("logout");
  resolveRefresh(harness.privateSnapshot());
  await pending;
  assertPrivateDataRemoved(harness);
});


test("platform filter combines status and priority while unassigned stays unknown", () => {
  const entries = [entry("win", {platforms: ["Windows"], status: "missing_evidence", priority: "P1"}),
    entry("linux", {platforms: ["Linux"]}), entry("unknown")];
  assert.deepEqual(filterItems(entries, {platform: "Windows", status: "missing_evidence", priority: "P1"}).map(x => x.id), ["win"]);
  assert.deepEqual(filterItems(entries, {platform: "unknown"}).map(x => x.id), ["unknown"]);
  assert.deepEqual(filterItems(entries, {platform: "Android"}), []);
});

test("detail exports preserve platform scope and conditional merge decision", () => {
  const content = itemMarkdown(entry("pr-1", {platforms: ["Windows"], platform_assessment: "Betroffenheit ungeprüft",
    status: "missing_evidence", merge_preparation: {state: "unknown", summary: "Nachweis fehlt; keine Freigabe"}}));
  assert.ok(content.includes("Plattform / Prüfumfang: Windows"));
  assert.ok(content.includes("Status: Nachweis fehlt"));
  assert.ok(content.includes("Merge grundsätzlich vorbereitbar?"));
  assert.ok(content.includes("Nachweis fehlt; keine Freigabe"));
});

test("platform checklist export includes every platform without claiming acceptance", () => {
  const snapshot = {checklist: [{id: "scope", title: "Scope"}], platform_checklists: Object.fromEntries(
    ["Windows", "Linux", "macOS", "Android"].map(p => [p, [{id: `platform-${p}-start`, title: `${p} Start`, detail: "Gerät prüfen"}]]))};
  assert.equal(checklistEntries(snapshot).length, 5);
  const markdown = platformChecklistMarkdown(snapshot, {"platform-Windows-start": true});
  for (const platform of Object.keys(snapshot.platform_checklists)) assert.ok(markdown.includes(`### ${platform}`));
  assert.ok(markdown.includes("[x] Windows Start"));
  assert.ok(markdown.includes("keine bestätigte Abnahme"));
  assert.ok(markdown.includes("Nachweis: offen"));
});


test("browser controller applies platform filtering and exposes merge interpretation in detail", async () => {
  const harness = await workspaceHarness();
  assert.ok(harness.get("detail-body").textContent.includes("Merge grundsätzlich vorbereitbar?"));
  assert.ok(harness.get("detail-body").textContent.includes("Kein Pull Request"));
  harness.get("platform").value = "Linux";
  harness.fire("platform", "change");
  assert.ok(harness.get("results").textContent.includes("0 von 1"));
  harness.get("platform").value = "Windows";
  harness.fire("platform", "change");
  assert.ok(harness.get("results").textContent.includes("1 von 1"));
  harness.fire("filter-reset");
  assert.equal(harness.get("platform").value, "");
});

test("platform checklist markers use only static IDs and reset without persisting private data", async () => {
  const harness = await workspaceHarness();
  assert.equal(harness.get("platform-checklists").childNodes.length, 4);
  const checkbox = harness.get("platform-windows-0");
  checkbox.checked = true; harness.fire("platform-windows-0", "change");
  assert.ok([...harness.stored.values()].some(value => value.includes('"platform-windows-setup":true')));
  assert.ok(!JSON.stringify([...harness.stored.values()]).includes("Private"));
  harness.fire("checklist-reset");
  assert.equal(harness.get("platform-windows-0").checked, false);
  assert.ok([...harness.stored.values()].every(value => value === "{}"));
});
