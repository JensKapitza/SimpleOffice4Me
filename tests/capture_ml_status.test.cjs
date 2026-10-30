const assert = require("node:assert/strict");
const {test} = require("node:test");

const {formatMlStatus} = require("../static/js/capture_ml_status.js");

const META = {
  model: "muse-spark-1.3-contributor",
  label: "Meta Muse Spark 1.3 Contributor (Meta Model API)",
};

test("meta without key never suggests active use", () => {
  const line = formatMlStatus({
    provider: "meta",
    meta: {...META, key_present: false, model_listed: null},
    ocr: {rapidocr: true, tesseract: false},
  });

  assert.equal(
    line,
    "ML: Meta Muse Spark 1.3 Contributor (Meta Model API) · muse-spark-1.3-contributor"
      + " · kein API-Schlüssel · Modell nicht geprüft · OCR: RapidOCR",
  );
  assert.ok(!line.includes("aktiv"));
});

test("meta with key distinguishes listed, denied and unchecked", () => {
  const base = {provider: "meta", ocr: {}};
  const listed = formatMlStatus({
    ...base, meta: {...META, key_present: true, model_listed: true},
  });
  const denied = formatMlStatus({
    ...base, meta: {...META, key_present: true, model_listed: false},
  });
  const unchecked = formatMlStatus({
    ...base, meta: {...META, key_present: true, model_listed: null},
  });

  assert.ok(listed.includes("Schlüssel hinterlegt · Modell gelistet"));
  assert.ok(denied.includes("Schlüssel hinterlegt · Modell nicht bestätigt"));
  assert.ok(unchecked.includes("Schlüssel hinterlegt · Modell nicht geprüft"));
  for (const line of [listed, denied, unchecked]) {
    assert.ok(line.includes("muse-spark-1.3-contributor"));
  }
});

test("local distinguishes reachable, unseen and missing model", () => {
  const active = formatMlStatus({
    provider: "local",
    ollama: {model: "llama3.2:1b", model_allowed: true, reachable: true, model_seen: true},
    ocr: {rapidocr: true, tesseract: true},
  });
  const unseen = formatMlStatus({
    provider: "local",
    ollama: {model: "llama3.2:1b", model_allowed: true, reachable: true, model_seen: false},
    ocr: {},
  });
  const down = formatMlStatus({
    provider: "local",
    ollama: {model: "llama3.2:1b", model_allowed: true, reachable: false, model_seen: false},
    ocr: {},
  });
  const unconfigured = formatMlStatus({
    provider: "local", ollama: {model: "", model_allowed: false, reachable: false}, ocr: {},
  });

  assert.equal(
    active,
    "ML aktiv · Meta-Modell lokal via Ollama · llama3.2:1b · OCR: RapidOCR + Tesseract",
  );
  assert.ok(unseen.includes("Ollama erreichbar, Modell llama3.2:1b nicht gesehen"));
  assert.ok(!unseen.includes("aktiv"));
  assert.ok(down.includes("Ollama nicht erreichbar · llama3.2:1b"));
  assert.ok(unconfigured.includes("kein Modell konfiguriert"));
});

test("unapproved models are never Meta-labeled, even when reachable", () => {
  for (const model of ["qwen3-vl:2b", "my-llama-alias"]) {
    const line = formatMlStatus({
      provider: "local",
      ollama: {model, model_allowed: false, reachable: true, model_seen: true},
      ocr: {},
    });
    assert.equal(line, `ML: Modell nicht freigegeben · ${model} · OCR: nicht installiert`);
    assert.ok(!line.includes("Meta-Modell"));
    assert.ok(!line.includes("aktiv"));
  }
  const missingFlag = formatMlStatus({
    provider: "local",
    ollama: {model: "qwen3-vl:2b", reachable: true, model_seen: true},
    ocr: {},
  });
  assert.ok(missingFlag.includes("Modell nicht freigegeben"));
});

test("mock, unknown and empty payloads stay understandable", () => {
  assert.ok(formatMlStatus({provider: "mock", ocr: {}}).includes("Demo-Platzhalter"));
  assert.ok(formatMlStatus({provider: "hyperdrive", ocr: {}}).includes("ML: hyperdrive"));
  assert.ok(formatMlStatus(null).includes("ML: unbekannt"));
  assert.ok(formatMlStatus({provider: "meta", ocr: {}}).includes("Meta-Modell"));
});

test("model values pass through verbatim for textContent assignment", () => {
  const line = formatMlStatus({
    provider: "local",
    ollama: {model: "<img src=x>", reachable: true, model_seen: true},
    ocr: {},
  });

  assert.ok(line.includes("<img src=x>"));
  assert.equal(typeof line, "string");
});
