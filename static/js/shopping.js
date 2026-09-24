(() => {
  'use strict';

  const root = document.getElementById('shopping-app');
  if (!root) return;

  const barcode = document.getElementById('shopping-barcode');
  const name = document.getElementById('shopping-name');
  const quantity = document.getElementById('shopping-quantity');
  const unit = document.getElementById('shopping-unit');
  const category = document.getElementById('shopping-category');
  const store = document.getElementById('shopping-store');
  const brand = document.getElementById('shopping-brand');
  const packSize = document.getElementById('shopping-pack-size');
  const price = document.getElementById('shopping-price');
  const form = document.getElementById('shopping-item-form');
  const requestId = document.getElementById('shopping-request-id');
  const scanButton = document.getElementById('shopping-barcode-scan');
  const checkButton = document.getElementById('shopping-barcode-check');
  const stopButton = document.getElementById('shopping-camera-stop');
  const video = document.getElementById('shopping-camera');
  const cameraBox = document.getElementById('shopping-camera-box');
  const status = document.getElementById('shopping-scan-status');
  let stream = null;
  let scanning = false;
  let detector = null;
  let lastDetection = 0;

  const setStatus = (message, kind = 'secondary') => {
    if (!status) return;
    status.className = `alert alert-${kind} py-2 mb-2`;
    status.textContent = message;
  };

  const stopCamera = () => {
    scanning = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    if (cameraBox) cameraBox.hidden = true;
    if (stopButton) stopButton.hidden = true;
  };

  const applyKnown = (payload) => {
    if (!payload?.known || !payload.item) {
      setStatus('Barcode ist gültig, aber lokal noch unbekannt. Bitte Bezeichnung eintragen.', 'info');
      name?.focus();
      return;
    }
    const item = payload.item;
    if (name && !name.value.trim()) name.value = item.name || '';
    if (quantity && !quantity.value.trim()) quantity.value = item.quantity || '';
    if (unit && !unit.value.trim()) unit.value = item.unit || '';
    if (category && !category.value.trim()) category.value = item.category || '';
    if (store && !store.value.trim()) store.value = item.store || '';
    if (brand && !brand.value.trim()) brand.value = item.brand || '';
    if (packSize && !packSize.value.trim()) packSize.value = item.pack_size || '';
    if (price && !price.value.trim()) price.value = item.price || '';
    setStatus(`Lokal erkannt: ${item.name || payload.barcode}. Bitte kurz prüfen.`, 'success');
  };

  const lookup = async () => {
    const code = String(barcode?.value || '').trim();
    if (!code) {
      setStatus('Barcode eingeben oder scannen.', 'warning');
      barcode?.focus();
      return false;
    }
    try {
      const response = await fetch(`${root.dataset.barcodeUrl}?code=${encodeURIComponent(code)}`, {
        headers: {Accept: 'application/json'},
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        setStatus(payload.error || 'Barcode ist ungültig.', 'warning');
        return false;
      }
      if (barcode) barcode.value = payload.barcode || code;
      applyKnown(payload);
      return true;
    } catch (_error) {
      setStatus(navigator.onLine === false ? 'Offline: Barcode bleibt im Formular und kann manuell verwendet werden.' : 'Barcode-Prüfung fehlgeschlagen.', 'warning');
      return false;
    }
  };

  const detectLoop = async (timestamp) => {
    if (!scanning || !detector || !video) return;
    if (timestamp - lastDetection >= 250 && video.readyState >= 2) {
      lastDetection = timestamp;
      try {
        const codes = await detector.detect(video);
        const found = codes.find((entry) => String(entry.rawValue || '').trim());
        if (found) {
          barcode.value = String(found.rawValue).trim();
          stopCamera();
          await lookup();
          return;
        }
      } catch (_error) {
        setStatus('Kamera konnte den Barcode nicht lesen. Du kannst ihn weiterhin manuell eingeben.', 'warning');
      }
    }
    if (scanning) window.requestAnimationFrame(detectLoop);
  };

  const startCamera = async () => {
    if (!barcode || !video || !navigator.mediaDevices?.getUserMedia) {
      setStatus('Kamera-Scan wird von diesem Browser nicht unterstützt. Barcode bitte manuell eingeben.', 'warning');
      return;
    }
    if (!('BarcodeDetector' in window)) {
      setStatus('Dieser Browser hat keinen lokalen Barcode-Decoder. Barcode bitte manuell eingeben.', 'warning');
      return;
    }
    stopCamera();
    try {
      const desired = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];
      let formats = desired;
      if (typeof window.BarcodeDetector.getSupportedFormats === 'function') {
        const supported = await window.BarcodeDetector.getSupportedFormats();
        formats = desired.filter((value) => supported.includes(value));
      }
      if (!formats.length) throw new Error('no-supported-barcode-format');
      detector = new window.BarcodeDetector({formats});
      stream = await navigator.mediaDevices.getUserMedia({
        video: {facingMode: {ideal: 'environment'}},
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      cameraBox.hidden = false;
      stopButton.hidden = false;
      scanning = true;
      lastDetection = 0;
      setStatus('Kamera aktiv. EAN/UPC-Code in den Bildausschnitt halten.', 'primary');
      window.requestAnimationFrame(detectLoop);
    } catch (_error) {
      stopCamera();
      setStatus('Kamera oder Barcode-Erkennung ist nicht verfügbar. Manuelle Eingabe bleibt möglich.', 'warning');
    }
  };

  const queueKey = 'simpleoffice-shopping-offline-v1';

  const freshRequestId = () => {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    return `offline-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
  };

  const ensureRequestId = () => {
    if (requestId && !requestId.value) requestId.value = freshRequestId();
    return requestId?.value || freshRequestId();
  };

  const readQueue = () => {
    try {
      const value = JSON.parse(window.localStorage.getItem(queueKey) || '[]');
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  };

  const writeQueue = (rows) => {
    try {
      window.localStorage.setItem(queueKey, JSON.stringify(rows));
      return true;
    } catch (_error) {
      return false;
    }
  };

  const currentEntry = () => {
    if (!form) return null;
    ensureRequestId();
    return {
      action: form.action,
      fields: Object.fromEntries(new FormData(form).entries()),
    };
  };

  const enqueue = (entry) => {
    if (!entry?.fields?.request_id) return false;
    const rows = readQueue();
    if (!rows.some((row) => row?.fields?.request_id === entry.fields.request_id)) rows.push(entry);
    return writeQueue(rows);
  };

  const postEntry = async (entry) => {
    const fields = {...entry.fields};
    const csrf = form?.querySelector('input[name="_csrf_token"]');
    if (csrf?.value) fields._csrf_token = csrf.value;
    const response = await fetch(entry.action, {
      method: 'POST',
      body: new URLSearchParams(fields),
      headers: {Accept: 'text/html'},
      cache: 'no-store',
    });
    return {ok: response.ok, retry: response.status >= 500, url: response.url};
  };

  const resetQueuedForm = () => {
    if (!form) return;
    form.reset();
    if (requestId) requestId.value = freshRequestId();
    name?.focus();
  };

  const flushQueue = async () => {
    if (!form || navigator.onLine === false) return;
    const rows = readQueue();
    if (!rows.length) return;
    const remaining = [];
    let rejected = 0;
    for (const entry of rows) {
      try {
        const result = await postEntry(entry);
        if (!result.ok && result.retry) remaining.push(entry);
        if (!result.ok && !result.retry) rejected += 1;
      } catch (_error) {
        remaining.push(entry);
      }
    }
    if (!writeQueue(remaining)) return;
    if (!remaining.length && !rejected) {
      setStatus('Offline gespeicherte Einkäufe wurden synchronisiert.', 'success');
    } else if (rejected) {
      setStatus('Mindestens ein offline gespeicherter Einkauf wurde vom Server abgelehnt.', 'warning');
    }
  };

  if (form) {
    ensureRequestId();
    form.addEventListener('submit', async (event) => {
      const entry = currentEntry();
      if (!entry) return;
      event.preventDefault();
      if (navigator.onLine === false) {
        if (enqueue(entry)) {
          setStatus('Offline gespeichert. Der Eintrag wird bei wiederhergestellter Verbindung gesendet.', 'info');
          resetQueuedForm();
        } else {
          setStatus('Offline-Speicherung ist in diesem Browser nicht verfügbar.', 'warning');
        }
        return;
      }
      try {
        const result = await postEntry(entry);
        if (result.ok) {
          window.location.assign(result.url || window.location.href);
          return;
        }
        setStatus('Der Server hat den Einkauf abgelehnt. Bitte Eingaben prüfen.', 'warning');
      } catch (_error) {
        if (enqueue(entry)) {
          setStatus('Verbindung abgebrochen. Der Einkauf wurde lokal vorgemerkt.', 'info');
          resetQueuedForm();
        } else {
          setStatus('Verbindung abgebrochen und lokale Speicherung ist nicht verfügbar.', 'warning');
        }
      }
    });
    window.addEventListener('online', flushQueue);
    if (navigator.onLine !== false) window.setTimeout(flushQueue, 0);
  }

  scanButton?.addEventListener('click', startCamera);
  checkButton?.addEventListener('click', lookup);
  stopButton?.addEventListener('click', stopCamera);
  barcode?.addEventListener('change', () => { if (barcode.value.trim()) lookup(); });
  window.addEventListener('pagehide', stopCamera);
})();
