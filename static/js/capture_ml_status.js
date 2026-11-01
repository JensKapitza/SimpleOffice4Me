(() => {
  "use strict";

  const cleanText = (value) => String(value || "").trim();

  const ocrSuffix = (ocr) => {
    const engines = [
      ocr && ocr.rapidocr ? "RapidOCR" : "",
      ocr && ocr.tesseract ? "Tesseract" : "",
    ].filter(Boolean);
    return engines.length ? `OCR: ${engines.join(" + ")}` : "OCR: nicht installiert";
  };

  const metaLine = (meta) => {
    const label = cleanText(meta.label) || "Meta-Modell";
    const modelId = cleanText(meta.model);
    const key = meta.key_present ? "Schlüssel hinterlegt" : "kein API-Schlüssel";
    const listed = meta.model_listed === true
      ? "Modell gelistet"
      : (meta.model_listed === false ? "Modell nicht bestätigt" : "Modell nicht geprüft");
    const head = modelId ? `ML: ${label} · ${modelId}` : `ML: ${label}`;
    return `${head} · ${key} · ${listed}`;
  };

  const localLine = (ollama) => {
    const modelId = cleanText(ollama.model);
    if (!modelId) return "ML: kein Modell konfiguriert";
    if (!ollama.model_allowed) return `ML: Modell nicht freigegeben · ${modelId}`;
    if (ollama.reachable && ollama.model_seen) {
      return `ML aktiv · Meta-Modell lokal via Ollama · ${modelId}`;
    }
    if (ollama.reachable) {
      return `ML: Ollama erreichbar, Modell ${modelId} nicht gesehen`;
    }
    return `ML: Ollama nicht erreichbar · ${modelId}`;
  };

  function formatMlStatus(payload) {
    const data = payload && typeof payload === "object" ? payload : {};
    const provider = cleanText(data.provider) || "unbekannt";
    let ml;
    if (provider === "meta") {
      ml = metaLine(data.meta || {});
    } else if (provider === "local") {
      ml = localLine(data.ollama || {});
    } else if (provider === "mock") {
      ml = "ML: Demo-Platzhalter (keine echte Analyse)";
    } else {
      ml = `ML: ${provider}`;
    }
    return `${ml} · ${ocrSuffix(data.ocr || {})}`;
  }

  const api = {formatMlStatus};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof window !== "undefined") window.SimpleOfficeCaptureMl = api;
})();
