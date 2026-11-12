(() => {
  "use strict";

  const EN = typeof document !== "undefined"
    ? (document.documentElement.lang || "").toLowerCase().startsWith("en")
    : false;
  const STR = {
    ocrMissing: EN ? "OCR: not installed" : "OCR: nicht installiert",
    metaModel: EN ? "Meta model" : "Meta-Modell",
    keyStored: EN ? "key stored" : "Schlüssel hinterlegt",
    noApiKey: EN ? "no API key" : "kein API-Schlüssel",
    modelListed: EN ? "model listed" : "Modell gelistet",
    modelUnconfirmed: EN ? "model unconfirmed" : "Modell nicht bestätigt",
    modelUnchecked: EN ? "model unchecked" : "Modell nicht geprüft",
    unknown: EN ? "unknown" : "unbekannt",
    noModel: EN ? "ML: no model configured" : "ML: kein Modell konfiguriert",
    modelNotApproved: (model) => EN ? `ML: invalid model ID · ${model}` : `ML: Modell-ID ungültig · ${model}`,
    activeLocal: (model) => EN ? `ML configured · local model via Ollama · ${model}` : `ML konfiguriert · Modell lokal via Ollama · ${model}`,
    reachableUnseen: (model) => EN ? `ML: Ollama reachable, model ${model} not seen` : `ML: Ollama erreichbar, Modell ${model} nicht gesehen`,
    unreachable: (model) => EN ? `ML: Ollama unreachable · ${model}` : `ML: Ollama nicht erreichbar · ${model}`,
    demoPlaceholder: EN ? "ML: demo placeholder (no real analysis)" : "ML: Demo-Platzhalter (keine echte Analyse)",
  };

  const cleanText = (value) => String(value || "").trim();

  const ocrSuffix = (ocr) => {
    const engines = [
      ocr && ocr.rapidocr ? "RapidOCR" : "",
      ocr && ocr.tesseract ? "Tesseract" : "",
    ].filter(Boolean);
    return engines.length ? `OCR: ${engines.join(" + ")}` : STR.ocrMissing;
  };

  const metaLine = (meta) => {
    const label = cleanText(meta.label) || STR.metaModel;
    const modelId = cleanText(meta.model);
    const key = meta.local ? (EN ? "local endpoint · no cloud key required" : "lokaler Endpunkt · kein Cloud-Key nötig") : (meta.key_present ? STR.keyStored : STR.noApiKey);
    const listed = meta.model_listed === true
      ? STR.modelListed
      : (meta.model_listed === false ? STR.modelUnconfirmed : STR.modelUnchecked);
    const head = modelId ? `ML: ${label} · ${modelId}` : `ML: ${label}`;
    return `${head} · ${key} · ${listed}`;
  };

  const localLine = (ollama) => {
    const modelId = cleanText(ollama.model);
    if (!modelId) return STR.noModel;
    if (!ollama.model_allowed) return STR.modelNotApproved(modelId);
    if (ollama.reachable && ollama.model_seen) {
      return STR.activeLocal(modelId);
    }
    if (ollama.reachable) {
      return STR.reachableUnseen(modelId);
    }
    return STR.unreachable(modelId);
  };

  function formatMlStatus(payload) {
    const data = payload && typeof payload === "object" ? payload : {};
    const provider = cleanText(data.provider) || STR.unknown;
    let ml;
    if (provider === "meta") {
      ml = metaLine(data.meta || {});
    } else if (provider === "openai" || provider === "compatible") {
      ml = metaLine(data.openai || {});
    } else if (provider === "local") {
      ml = localLine(data.ollama || {});
    } else if (provider === "mock") {
      ml = STR.demoPlaceholder;
    } else {
      ml = `ML: ${provider}`;
    }
    return `${ml} · ${ocrSuffix(data.ocr || {})}`;
  }

  const api = {formatMlStatus};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.SimpleOfficeCaptureMl = api;
})();
