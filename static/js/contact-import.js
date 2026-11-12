(() => {
  "use strict";

  const EN = (document.documentElement.lang || "").toLowerCase().startsWith("en");
  const STR = {
    serverUnreadable: EN ? "Could not read the server response." : "Die Serverantwort konnte nicht gelesen werden.",
    importFailed: EN ? "Contact import failed." : "Kontaktimport fehlgeschlagen.",
    evaluatingPhoto: EN ? "Evaluating photo locally …" : "Foto wird lokal ausgewertet …",
    fieldsAdopted: (count, engine) => EN
      ? `${count} field(s) adopted as suggestion${engine}. Please review before saving.`
      : `${count} Feld(er) als Vorschlag übernommen${engine}. Bitte vor dem Speichern prüfen.`,
    qrAdopted: (count) => EN
      ? `${count} field(s) adopted from the QR code. Please review before saving.`
      : `${count} Feld(er) aus dem QR-Code übernommen. Bitte vor dem Speichern prüfen.`,
    modelLabel: EN ? "Model: " : "Modell: ",
    unknown: EN ? "unknown" : "unbekannt",
    local: EN ? "local" : "lokal",
    nothingStored: EN ? "nothing stored" : "nichts gespeichert",
    spellingAdjusted: EN ? "Spelling adjusted – please review" : "Schreibweise angepasst – bitte prüfen",
    evidence: (source, text) => EN ? `Evidence (${source}): “${text}”` : `Beleg (${source}): „${text}“`,
    adopt: EN ? "Adopt" : "Übernehmen",
    suggestionsCount: (count, warnings) => EN
      ? `${count} field suggestion(s) – please review and adopt individually. ${warnings}`.trim()
      : `${count} Feldvorschlag(e) – bitte einzeln prüfen und übernehmen. ${warnings}`.trim(),
    targetMissing: (field) => EN ? `Target field for “${field}” is missing in the form.` : `Zielfeld für „${field}“ fehlt im Formular.`,
    reallyReplace: EN ? "Really replace?" : "Wirklich ersetzen?",
    alreadyFilled: (name) => EN ? `“${name}” is already filled – second click replaces the value.` : `„${name}“ ist bereits ausgefüllt – zweiter Klick ersetzt den Wert.`,
    adopted: EN ? "Adopted" : "Übernommen",
    adoptedOnce: (name) => EN ? `“${name}” adopted. It is only saved together with the form.` : `„${name}“ übernommen. Gespeichert wird erst mit „Kontakt speichern“.`,
    adoptEmpty: (filled, skipped) => EN
      ? `${filled} empty field(s) adopted, ${skipped} skipped (already filled or without target field). It is only saved together with the form.`
      : `${filled} leere(s) Feld(er) übernommen, ${skipped} übersprungen (bereits ausgefüllt oder ohne Zielfeld). Gespeichert wird erst mit „Kontakt speichern“.`,
    discarded: EN ? "Suggestions discarded. Nothing was adopted or saved." : "Vorschläge verworfen. Nichts wurde übernommen oder gespeichert.",
    needTextOrPhoto: EN ? "Please paste contact text or select at least one photo." : "Bitte Kontakttext einfügen oder mindestens ein Foto auswählen.",
    maxPhotos: EN ? "Please select at most 3 photos." : "Bitte höchstens 3 Fotos auswählen.",
    photoType: (name) => EN ? `“${name}”: only JPEG, PNG or WebP up to 5 MiB.` : `„${name}“: nur JPEG, PNG oder WebP bis 5 MiB.`,
    notAvailable: EN ? "Suggestions are not available here." : "Vorschläge sind hier nicht verfügbar.",
    cloudAssign: EN ? "External Meta AI (Muse Spark 1.3 Contributor) assigns the sources … (text only, no photos)" : "Externe Meta-KI (Muse Spark 1.3 Contributor) ordnet die Quellen zu … (nur Text, keine Fotos)",
    localAssign: EN ? "Local Meta AI assigns the sources …" : "Lokale Meta-KI ordnet die Quellen zu …",
    suggestFailed: EN ? "Could not create suggestions." : "Vorschläge konnten nicht erstellt werden.",
    needQrImage: EN ? "Please select a QR code image first or capture it with the camera." : "Bitte zuerst ein QR-Code-Bild auswählen oder mit der Kamera aufnehmen.",
    qrUnsupported: EN ? "This browser cannot read QR codes directly from images. You can paste the QR content manually below." : "Dieser Browser kann QR-Codes nicht direkt aus Bildern lesen. Du kannst den QR-Inhalt unten manuell einfügen.",
    qrReading: EN ? "Reading QR code locally in the browser …" : "QR-Code wird lokal im Browser gelesen …",
    qrNoNative: EN ? "The browser supports barcode detection, but no QR codes." : "Der Browser unterstützt Barcode-Erkennung, aber keine QR-Codes.",
    qrNotFound: EN ? "No QR code detected in the image. You can paste the content manually below." : "Auf dem Bild wurde kein QR-Code erkannt. Du kannst den Inhalt unten manuell einfügen.",
  };

  const createForm = document.getElementById("contact-create-form");
  if (!createForm) return;

  const statusBox = document.getElementById("contact-capture-status");
  const previewBox = document.getElementById("contact-capture-preview");
  const previewText = document.getElementById("contact-capture-text");

  const setStatus = (message, kind = "info") => {
    if (!statusBox) return;
    statusBox.className = `alert alert-${kind} mt-3 mb-0`;
    statusBox.textContent = message;
    statusBox.hidden = false;
  };

  const setPreview = (text) => {
    if (!previewBox || !previewText) return;
    const value = String(text || "").trim();
    previewText.textContent = value;
    previewBox.hidden = !value;
  };

  const applyFields = (fields) => {
    let count = 0;
    Object.entries(fields || {}).forEach(([name, value]) => {
      const input = createForm.elements.namedItem(name);
      if (!(input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement || input instanceof HTMLSelectElement)) return;
      const normalized = String(value || "").trim();
      if (!normalized) return;
      input.value = normalized;
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
      count += 1;
    });
    if (count) createForm.scrollIntoView({behavior: "smooth", block: "start"});
    return count;
  };

  const postForm = async (form) => {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      throw new Error(STR.serverUnreadable);
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || STR.importFailed);
    }
    return payload;
  };

  const photoForm = document.getElementById("contact-photo-form");
  photoForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    setStatus(STR.evaluatingPhoto, "secondary");
    setPreview("");
    try {
      const payload = await postForm(photoForm);
      const count = applyFields(payload.fields);
      const engine = payload.ocr?.engine ? ` · OCR: ${payload.ocr.engine}` : "";
      const warning = payload.warning ? ` ${payload.warning}` : "";
      setPreview(payload.text || "");
      setStatus(
        `${STR.fieldsAdopted(count, engine)}${warning}`,
        count ? "success" : "warning",
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      if (button instanceof HTMLButtonElement) button.disabled = false;
    }
  });

  const qrForm = document.getElementById("contact-qr-form");
  const qrText = document.getElementById("contact-qr-payload");
  const qrImage = document.getElementById("contact-qr-image");
  const qrScanButton = document.getElementById("contact-qr-scan");

  const submitQrPayload = async () => {
    if (!(qrForm instanceof HTMLFormElement)) return;
    const payload = await postForm(qrForm);
    const count = applyFields(payload.fields);
    setPreview(qrText instanceof HTMLTextAreaElement ? qrText.value : "");
    setStatus(
      STR.qrAdopted(count),
      count ? "success" : "warning",
    );
  };

  qrForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    try {
      await submitQrPayload();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      if (button instanceof HTMLButtonElement) button.disabled = false;
    }
  });

  const engelScope = document.querySelector("[data-engel-contact]");
  const engelForm = document.getElementById("engel-contact-form");
  const engelStatus = document.getElementById("engel-contact-status");
  const engelCard = document.getElementById("engel-contact-card");
  const engelList = document.getElementById("engel-contact-list");
  const engelProvider = document.getElementById("engel-contact-provider");
  const engelHeading = document.getElementById("engel-contact-card-heading");
  const engelAdoptEmpty = document.getElementById("engel-contact-adopt-empty");
  const engelDismiss = document.getElementById("engel-contact-dismiss");
  const engelPhotos = document.getElementById("engel-contact-photos");
  const engelText = document.getElementById("engel-contact-text");
  const engelConsent = document.getElementById("engel-contact-consent");
  const ADDRESS_INPUTS = {
    street: "contact_address_street", postal: "contact_address_postal",
    city: "contact_address_city", state: "contact_address_state",
    country: "contact_address_country",
  };
  let engelSuggestions = [];

  const engelTarget = (field) => {
    const input = createForm.elements.namedItem(ADDRESS_INPUTS[field] || field);
    return input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement || input instanceof HTMLSelectElement ? input : null;
  };

  const setEngelStatus = (message, kind = "info") => {
    if (!engelStatus) return;
    engelStatus.className = `alert alert-${kind} mt-3 mb-0`;
    engelStatus.textContent = message;
    engelStatus.hidden = false;
  };

  const hideEngelCard = () => {
    engelSuggestions = [];
    if (engelList) engelList.replaceChildren();
    if (engelCard) engelCard.hidden = true;
  };

  const fillTarget = (input, value) => {
    input.value = value;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
  };

  const renderEngelCard = (payload) => {
    if (!engelCard || !engelList) return;
    hideEngelCard();
    engelSuggestions = Array.isArray(payload.suggestions) ? payload.suggestions : [];
    if (engelProvider) {
      const parts = [`${STR.modelLabel}${payload.model || STR.unknown}`, payload.provider_label || STR.local, STR.nothingStored];
      engelProvider.textContent = parts.join(" · ");
    }
    engelSuggestions.forEach((item) => {
      const row = document.createElement("li");
      row.className = "list-group-item";
      const head = document.createElement("div");
      head.className = "d-flex justify-content-between align-items-start gap-2";
      const title = document.createElement("strong");
      title.textContent = item.label || item.field;
      head.appendChild(title);
      if (item.corrected) {
        const badge = document.createElement("span");
        badge.className = "badge text-bg-warning";
        badge.textContent = STR.spellingAdjusted;
        head.appendChild(badge);
      }
      row.appendChild(head);
      const value = document.createElement("div");
      value.textContent = item.value;
      row.appendChild(value);
      const proof = document.createElement("div");
      proof.className = "small text-secondary";
      proof.textContent = STR.evidence(item.source_label || item.source, item.evidence);
      row.appendChild(proof);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-primary mt-2";
      button.textContent = STR.adopt;
      button.dataset.field = item.field;
      button.dataset.value = item.value;
      row.appendChild(button);
      engelList.appendChild(row);
    });
    engelCard.hidden = false;
    const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(Boolean) : [];
    setEngelStatus(
      STR.suggestionsCount(engelSuggestions.length, warnings.join(" ")),
      engelSuggestions.length ? "success" : "warning",
    );
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    engelHeading?.focus({preventScroll: true});
    engelCard.scrollIntoView({behavior: reduceMotion ? "auto" : "smooth", block: "start"});
  };

  const resetAdoptButtons = () => {
    engelList?.querySelectorAll("button[data-field]").forEach((item) => {
      if (item.disabled) return;
      item.textContent = STR.adopt;
      item.classList.remove("btn-warning");
      item.classList.add("btn-outline-primary");
    });
  };

  engelList?.addEventListener("click", (event) => {
    const button = event.target instanceof HTMLButtonElement ? event.target : null;
    if (!button || button.disabled) return;
    const input = engelTarget(button.dataset.field || "");
    if (!input) {
      setEngelStatus(STR.targetMissing(button.dataset.field), "warning");
      return;
    }
    const current = input.value.trim();
    const next = String(button.dataset.value || "").trim();
    if (current && current !== next && button.textContent !== STR.reallyReplace) {
      resetAdoptButtons();
      button.textContent = STR.reallyReplace;
      button.classList.remove("btn-outline-primary");
      button.classList.add("btn-warning");
      setEngelStatus(STR.alreadyFilled(input.name), "warning");
      return;
    }
    fillTarget(input, next);
    button.disabled = true;
    button.textContent = STR.adopted;
    setEngelStatus(STR.adoptedOnce(input.name), "success");
  });

  engelAdoptEmpty?.addEventListener("click", () => {
    let filled = 0;
    let skipped = 0;
    engelSuggestions.forEach((item) => {
      const input = engelTarget(item.field);
      if (!input || input.value.trim()) { skipped += 1; return; }
      fillTarget(input, String(item.value || "").trim());
      filled += 1;
    });
    setEngelStatus(
      STR.adoptEmpty(filled, skipped),
      filled ? "success" : "warning",
    );
  });

  engelDismiss?.addEventListener("click", () => {
    hideEngelCard();
    setEngelStatus(STR.discarded, "secondary");
  });

  engelForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!(engelForm instanceof HTMLFormElement)) return;
    const files = Array.from(engelPhotos?.files || []);
    const hasText = Boolean(engelText?.value.trim());
    if (!hasText && !files.length) {
      setEngelStatus(STR.needTextOrPhoto, "warning");
      return;
    }
    if (files.length > 3) {
      setEngelStatus(STR.maxPhotos, "warning");
      return;
    }
    for (const file of files) {
      if (file.size > 5 * 1024 * 1024 || !/^image\/(jpeg|png|webp)$/i.test(file.type || "")) {
        setEngelStatus(STR.photoType(file.name), "warning");
        return;
      }
    }
    const url = engelScope?.dataset.suggestUrl || "";
    if (!url) {
      setEngelStatus(STR.notAvailable, "warning");
      return;
    }
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    setEngelStatus(
      engelConsent?.checked ? STR.cloudAssign : STR.localAssign,
      "secondary",
    );
    try {
      const response = await fetch(url, {method: "POST", body: new FormData(engelForm), cache: "no-store"});
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        throw new Error(STR.serverUnreadable);
      }
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || STR.suggestFailed);
      }
      renderEngelCard(payload);
    } catch (error) {
      hideEngelCard();
      setEngelStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      if (button instanceof HTMLButtonElement) button.disabled = false;
    }
  });

  const decodeQrBitmap = (bitmap) => {
    if (typeof window.jsQR !== "function") return "";
    const width = bitmap.width;
    const height = bitmap.height;
    if (!width || !height) return "";
    const scale = Math.min(1, 1200 / Math.max(width, height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext("2d", {willReadFrequently: true});
    if (!context) return "";
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const frame = context.getImageData(0, 0, canvas.width, canvas.height);
    const decoded = window.jsQR(frame.data, frame.width, frame.height, {inversionAttempts: "attemptBoth"});
    return decoded?.data ? String(decoded.data).trim() : "";
  };

  qrScanButton?.addEventListener("click", async () => {
    if (!(qrImage instanceof HTMLInputElement) || !qrImage.files?.length) {
      setStatus(STR.needQrImage, "warning");
      return;
    }
    const canUseNative = "BarcodeDetector" in window;
    const canUseLocalQr = typeof window.jsQR === "function";
    if (!canUseNative && !canUseLocalQr) {
      setStatus(STR.qrUnsupported, "warning");
      return;
    }

    qrScanButton.disabled = true;
    setStatus(STR.qrReading, "secondary");
    let bitmap = null;
    try {
      bitmap = await createImageBitmap(qrImage.files[0]);
      let rawValue = "";
      if (canUseNative) {
        try {
          if (typeof BarcodeDetector.getSupportedFormats === "function") {
            const supported = await BarcodeDetector.getSupportedFormats();
            if (!supported.includes("qr_code")) {
              throw new Error(STR.qrNoNative);
            }
          }
          const detector = new BarcodeDetector({formats: ["qr_code"]});
          const detections = await detector.detect(bitmap);
          rawValue = detections.find((item) => String(item.rawValue || "").trim())?.rawValue || "";
        } catch (error) {
          if (!canUseLocalQr) throw error;
        }
      }
      if (!rawValue && canUseLocalQr) rawValue = decodeQrBitmap(bitmap);
      if (!rawValue) throw new Error(STR.qrNotFound);
      if (qrText instanceof HTMLTextAreaElement) qrText.value = rawValue;
      await submitQrPayload();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      bitmap?.close?.();
      qrScanButton.disabled = false;
    }
  });
})();

(() => {
  "use strict";

  const EN = (document.documentElement.lang || "").toLowerCase().startsWith("en");
  const STR = {
    demoPlaceholder: EN ? "ML: demo placeholder (no real analysis)" : "ML: Demo-Platzhalter (keine echte Analyse)",
    metaModel: EN ? "Meta model" : "Meta-Modell",
    keyStored: EN ? "key stored" : "Schlüssel hinterlegt",
    noApiKey: EN ? "no API key" : "kein API-Schlüssel",
    modelListed: EN ? " · model listed" : " · Modell gelistet",
    modelUnconfirmed: EN ? " · model unconfirmed" : " · Modell nicht bestätigt",
    modelUnchecked: EN ? " · model unchecked" : " · Modell nicht geprüft",
    modelLabel: EN ? "Model: " : "Modell: ",
    unknown: EN ? "unknown" : "unbekannt",
    noModel: EN ? "ML: no model configured" : "ML: kein Modell konfiguriert",
    modelNotApproved: (model) => EN ? `ML: invalid model ID · ${model}` : `ML: Modell-ID ungültig · ${model}`,
    activeLocal: (model) => EN ? `ML configured · local model via Ollama · ${model}` : `ML konfiguriert · Modell lokal via Ollama · ${model}`,
    reachableMissing: (model) => EN ? `ML reachable, model missing · ${model}` : `ML erreichbar, Modell fehlt · ${model}`,
    unreachable: (model, hint) => EN ? `ML unreachable · ${model} (${hint})` : `ML nicht erreichbar · ${model} (${hint})`,
    checkOllama: EN ? "check Ollama" : "Ollama prüfen",
    ocrMissing: EN ? "OCR: not installed" : "OCR: nicht installiert",
    statusUnknown: EN ? "ML status unknown" : "ML-Status unbekannt",
  };

  const mlBadge = document.getElementById("ml-status-badge");
  const mlUrl = mlBadge?.dataset.mlStatusUrl || "";

  const renderMlStatus = (payload) => {
    if (!mlBadge) return;
    const parts = [];
    const provider = String(payload.provider || "local");
    const ollama = payload.ollama || {};
    const meta = payload.meta || {};
    const ocr = payload.ocr || {};
    if (provider === "mock") {
      parts.push(STR.demoPlaceholder);
    } else if (provider === "meta") {
      const key = meta.key_present ? STR.keyStored : STR.noApiKey;
      const listed = meta.model_listed === true ? STR.modelListed : (meta.model_listed === false ? STR.modelUnconfirmed : STR.modelUnchecked);
      parts.push(`ML: ${meta.label || STR.metaModel} · ${key}${listed}`);
      mlBadge.title = `${STR.modelLabel}${meta.model || STR.unknown}`;
    } else if (provider === "openai" || provider === "compatible") {
      const api = payload.openai || {};
      const key = api.local ? (EN ? "local endpoint · no cloud key required" : "lokaler Endpunkt · kein Cloud-Key nötig") : (api.key_present ? STR.keyStored : STR.noApiKey);
      const checked = api.model_listed === true ? STR.modelListed : (api.model_listed === false ? STR.modelUnconfirmed : STR.modelUnchecked);
      parts.push(`ML: ${api.label || provider} · ${api.model || STR.unknown} · ${key} · ${checked}`);
    } else if (!ollama.model) {
      parts.push(STR.noModel);
    } else if (ollama.model_allowed !== true) {
      parts.push(STR.modelNotApproved(ollama.model));
    } else if (ollama.reachable && ollama.model_seen) {
      parts.push(STR.activeLocal(ollama.model));
    } else if (ollama.reachable) {
      parts.push(STR.reachableMissing(ollama.model));
    } else {
      parts.push(STR.unreachable(ollama.model, ollama.error || STR.checkOllama));
    }
    const engines = [];
    if (ocr.rapidocr) engines.push("RapidOCR");
    if (ocr.tesseract) engines.push("Tesseract");
    parts.push(engines.length ? `OCR: ${engines.join(" + ")}` : STR.ocrMissing);
    mlBadge.textContent = parts.join(" · ");
  };

  if (!mlBadge || !mlUrl) return;
  fetch(mlUrl, {method: "GET", cache: "no-store"})
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
    .then((payload) => {
      if (payload && payload.ok !== false) renderMlStatus(payload);
      else mlBadge.textContent = STR.statusUnknown;
    })
    .catch(() => {
      mlBadge.textContent = STR.statusUnknown;
    });
})();
