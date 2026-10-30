const assert = require("node:assert/strict");
const {test} = require("node:test");

const {
  mergeTags,
  suggestionDescription,
  formatSuggestionTags,
} = require("../static/js/suggestion_fields.js");

test("mergeTags appends new tags deduplicated", () => {
  assert.equal(mergeTags("", ["rot", "Metall"]), "rot, Metall");
  assert.equal(
    mergeTags("werkstatt", ["Rot", "metall", "werkstatt", "  Holz  "]),
    "werkstatt, Rot, metall, Holz",
  );
});

test("mergeTags keeps existing entries and drops junk", () => {
  assert.equal(mergeTags("a, b", null), "a, b");
  assert.equal(mergeTags("a", ["", "  ", 7, null, "b,b", "c"]), "a, c");
  assert.equal(mergeTags("a", "not-a-list"), "a");
});

test("mergeTags returns pre-existing over-limit value unchanged", () => {
  const existing = "x".repeat(10);
  assert.equal(mergeTags(existing, ["y"], 8), existing);
  const pair = "aa, bb";
  assert.equal(mergeTags(pair, ["cc"], 5), pair);
});

test("mergeTags appends only tags that fully fit", () => {
  assert.equal(mergeTags("bestand", ["neu1", "neu2", "neu3"], 20), "bestand, neu1, neu2");
  assert.equal(mergeTags("aa", ["bb", "ccccc", "dd"], 10), "aa, bb, dd");
  assert.equal(mergeTags("", ["toolongvalue"], 5), "");
});

test("suggestionDescription fills only empty fields", () => {
  assert.equal(suggestionDescription("", "Notiz"), "Notiz");
  assert.equal(suggestionDescription("   ", "Notiz"), "Notiz");
  assert.equal(suggestionDescription("vom Nutzer", "Notiz"), "vom Nutzer");
  assert.equal(suggestionDescription("", "  "), "");
  assert.equal(suggestionDescription("", null), "");
});

test("formatSuggestionTags renders review line", () => {
  assert.equal(formatSuggestionTags(["rot", "Metall"]), "Tags: rot, Metall");
  assert.equal(formatSuggestionTags([]), "");
  assert.equal(formatSuggestionTags(null), "");
  assert.equal(formatSuggestionTags(["", "  "]), "");
});
