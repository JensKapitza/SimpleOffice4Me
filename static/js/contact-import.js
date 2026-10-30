(() => {
  "use strict";

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
      throw new Error("Die Serverantwort konnte nicht gelesen werden.");
    }
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "Kontaktimport fehlgeschlagen.");
    }
    return payload;
  };

  const photoForm = document.getElementById("contact-photo-form");
  photoForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    setStatus("Foto wird lokal ausgewertet …", "secondary");
    setPreview("");
    try {
      const payload = await postForm(photoForm);
      const count = applyFields(payload.fields);
      const engine = payload.ocr?.engine ? ` · OCR: ${payload.ocr.engine}` : "";
      const warning = payload.warning ? ` ${payload.warning}` : "";
      setPreview(payload.text || "");
      setStatus(
        `${count} Feld(er) als Vorschlag übernommen${engine}. Bitte vor dem Speichern prüfen.${warning}`,
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
      `${count} Feld(er) aus dem QR-Code übernommen. Bitte vor dem Speichern prüfen.`,
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
      const parts = [`Modell: ${payload.model || "unbekannt"}`, payload.provider_label || "lokal", "nichts gespeichert"];
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
        badge.textContent = "Schreibweise angepasst – bitte prüfen";
        head.appendChild(badge);
      }
      row.appendChild(head);
      const value = document.createElement("div");
      value.textContent = item.value;
      row.appendChild(value);
      const proof = document.createElement("div");
      proof.className = "small text-secondary";
      proof.textContent = `Beleg (${item.source_label || item.source}): „${item.evidence}“`;
      row.appendChild(proof);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-sm btn-outline-primary mt-2";
      button.textContent = "Übernehmen";
      button.dataset.field = item.field;
      button.dataset.value = item.value;
      row.appendChild(button);
      engelList.appendChild(row);
    });
    engelCard.hidden = false;
    const warnings = Array.isArray(payload.warnings) ? payload.warnings.filter(Boolean) : [];
    setEngelStatus(
      `${engelSuggestions.length} Feldvorschlag(e) – bitte einzeln prüfen und übernehmen. ${warnings.join(" ")}`.trim(),
      engelSuggestions.length ? "success" : "warning",
    );
    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    engelHeading?.focus({preventScroll: true});
    engelCard.scrollIntoView({behavior: reduceMotion ? "auto" : "smooth", block: "start"});
  };

  const resetAdoptButtons = () => {
    engelList?.querySelectorAll("button[data-field]").forEach((item) => {
      if (item.disabled) return;
      item.textContent = "Übernehmen";
      item.classList.remove("btn-warning");
      item.classList.add("btn-outline-primary");
    });
  };

  engelList?.addEventListener("click", (event) => {
    const button = event.target instanceof HTMLButtonElement ? event.target : null;
    if (!button || button.disabled) return;
    const input = engelTarget(button.dataset.field || "");
    if (!input) {
      setEngelStatus(`Zielfeld für „${button.dataset.field}“ fehlt im Formular.`, "warning");
      return;
    }
    const current = input.value.trim();
    const next = String(button.dataset.value || "").trim();
    if (current && current !== next && button.textContent !== "Wirklich ersetzen?") {
      resetAdoptButtons();
      button.textContent = "Wirklich ersetzen?";
      button.classList.remove("btn-outline-primary");
      button.classList.add("btn-warning");
      setEngelStatus(`„${input.name}“ ist bereits ausgefüllt – zweiter Klick ersetzt den Wert.`, "warning");
      return;
    }
    fillTarget(input, next);
    button.disabled = true;
    button.textContent = "Übernommen";
    setEngelStatus(`„${input.name}“ übernommen. Gespeichert wird erst mit „Kontakt speichern“.`, "success");
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
      `${filled} leere(s) Feld(er) übernommen, ${skipped} übersprungen (bereits ausgefüllt oder ohne Zielfeld). Gespeichert wird erst mit „Kontakt speichern“.`,
      filled ? "success" : "warning",
    );
  });

  engelDismiss?.addEventListener("click", () => {
    hideEngelCard();
    setEngelStatus("Vorschläge verworfen. Nichts wurde übernommen oder gespeichert.", "secondary");
  });

  engelForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!(engelForm instanceof HTMLFormElement)) return;
    const files = Array.from(engelPhotos?.files || []);
    const hasText = Boolean(engelText?.value.trim());
    if (!hasText && !files.length) {
      setEngelStatus("Bitte Kontakttext einfügen oder mindestens ein Foto auswählen.", "warning");
      return;
    }
    if (files.length > 3) {
      setEngelStatus("Bitte höchstens 3 Fotos auswählen.", "warning");
      return;
    }
    for (const file of files) {
      if (file.size > 5 * 1024 * 1024 || !/^image\/(jpeg|png|webp)$/i.test(file.type || "")) {
        setEngelStatus(`„${file.name}“: nur JPEG, PNG oder WebP bis 5 MiB.`, "warning");
        return;
      }
    }
    const url = engelScope?.dataset.suggestUrl || "";
    if (!url) {
      setEngelStatus("Vorschläge sind hier nicht verfügbar.", "warning");
      return;
    }
    const button = event.submitter;
    if (button instanceof HTMLButtonElement) button.disabled = true;
    setEngelStatus(
      engelConsent?.checked
        ? "Externe Meta-KI (Muse Spark 1.3 Contributor) ordnet die Quellen zu … (nur Text, keine Fotos)"
        : "Lokale Meta-KI ordnet die Quellen zu …",
      "secondary",
    );
    try {
      const response = await fetch(url, {method: "POST", body: new FormData(engelForm), cache: "no-store"});
      let payload = {};
      try {
        payload = await response.json();
      } catch (_error) {
        throw new Error("Die Serverantwort konnte nicht gelesen werden.");
      }
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.error || "Vorschläge konnten nicht erstellt werden.");
      }
      renderEngelCard(payload);
    } catch (error) {
      hideEngelCard();
      setEngelStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      if (button instanceof HTMLButtonElement) button.disabled = false;
    }
  });

  qrScanButton?.addEventListener("click", async () => {
    if (!(qrImage instanceof HTMLInputElement) || !qrImage.files?.length) {
      setStatus("Bitte zuerst ein QR-Code-Bild auswählen oder mit der Kamera aufnehmen.", "warning");
      return;
    }
    if (!("BarcodeDetector" in window)) {
      setStatus("Dieser Browser kann QR-Codes nicht direkt aus Bildern lesen. Du kannst den QR-Inhalt unten manuell einfügen.", "warning");
      return;
    }

    qrScanButton.disabled = true;
    setStatus("QR-Code wird lokal im Browser gelesen …", "secondary");
    try {
      if (typeof BarcodeDetector.getSupportedFormats === "function") {
        const supported = await BarcodeDetector.getSupportedFormats();
        if (!supported.includes("qr_code")) {
          throw new Error("Der Browser unterstützt Barcode-Erkennung, aber keine QR-Codes.");
        }
      }
      const bitmap = await createImageBitmap(qrImage.files[0]);
      let detections;
      try {
        const detector = new BarcodeDetector({formats: ["qr_code"]});
        detections = await detector.detect(bitmap);
      } finally {
        bitmap.close?.();
      }
      const rawValue = detections.find((item) => String(item.rawValue || "").trim())?.rawValue || "";
      if (!rawValue) throw new Error("Auf dem Bild wurde kein QR-Code erkannt.");
      if (qrText instanceof HTMLTextAreaElement) qrText.value = rawValue;
      await submitQrPayload();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error), "danger");
    } finally {
      qrScanButton.disabled = false;
    }
  });
})();

(() => {
  "use strict";

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
      parts.push("ML: Demo-Platzhalter (keine echte Analyse)");
    } else if (provider === "meta") {
      const key = meta.key_present ? "Schlüssel hinterlegt" : "kein API-Schlüssel";
      const listed = meta.model_listed === true ? " · Modell gelistet" : (meta.model_listed === false ? " · Modell nicht bestätigt" : " · Modell nicht geprüft");
      parts.push(`ML: ${meta.label || "Meta-Modell"} · ${key}${listed}`);
      mlBadge.title = `Modell: ${meta.model || "unbekannt"}`;
    } else if (!ollama.model) {
      parts.push("ML: kein Modell konfiguriert");
    } else if (ollama.model_allowed !== true) {
      parts.push(`ML: Modell nicht freigegeben · ${ollama.model}`);
    } else if (ollama.reachable && ollama.model_seen) {
      parts.push(`ML aktiv · Meta-Modell lokal via Ollama · ${ollama.model}`);
    } else if (ollama.reachable) {
      parts.push(`ML erreichbar, Modell fehlt · ${ollama.model}`);
    } else {
      parts.push(`ML nicht erreichbar · ${ollama.model} (${ollama.error || "Ollama prüfen"})`);
    }
    const engines = [];
    if (ocr.rapidocr) engines.push("RapidOCR");
    if (ocr.tesseract) engines.push("Tesseract");
    parts.push(engines.length ? `OCR: ${engines.join(" + ")}` : "OCR: nicht installiert");
    mlBadge.textContent = parts.join(" · ");
  };

  if (!mlBadge || !mlUrl) return;
  fetch(mlUrl, {method: "GET", cache: "no-store"})
    .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
    .then((payload) => {
      if (payload && payload.ok !== false) renderMlStatus(payload);
      else mlBadge.textContent = "ML-Status unbekannt";
    })
    .catch(() => {
      mlBadge.textContent = "ML-Status unbekannt";
    });
})();