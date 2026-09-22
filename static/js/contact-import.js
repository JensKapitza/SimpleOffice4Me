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