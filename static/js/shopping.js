(() => {
  'use strict';

  const root = document.getElementById('shopping-app');
  if (!root) return;
  const tx = (german, english) => window.SimpleOfficeI18n?.text(german, english) ?? (document.documentElement.lang === 'de' ? german : english);

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
  const photoInput = document.getElementById('shopping-product-photo');
  const photoId = document.getElementById('shopping-photo-id');
  const photoPreview = document.getElementById('shopping-photo-preview');
  const photoClear = document.getElementById('shopping-photo-clear');
  let stream = null;
  let scanning = false;
  let detector = null;
  let lastDetection = 0;
  let cameraGeneration = 0;
  let photoGeneration = 0;
  let lookupGeneration = 0;

  const setStatus = (message, kind = 'secondary') => {
    if (!status) return;
    status.className = `alert alert-${kind} py-2 mb-2`;
    status.textContent = message;
  };

  const showPhoto = (url = '', id = '') => {
    if (!photoPreview || !photoClear) return;
    if (!url) {
      photoPreview.removeAttribute('src');
      photoPreview.hidden = true;
      photoClear.hidden = true;
      if (photoId) photoId.value = '';
      return;
    }
    photoPreview.src = url;
    photoPreview.hidden = false;
    photoClear.hidden = false;
    if (photoId) photoId.value = id || '';
  };

  const stopCamera = () => {
    cameraGeneration += 1;
    scanning = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    if (video?.srcObject) {
      video.pause();
      video.srcObject = null;
    }
    if (cameraBox) cameraBox.hidden = true;
    if (stopButton) stopButton.hidden = true;
  };

  const applyKnown = (payload) => {
    if (!payload?.known || !payload.item) {
      if (!photoInput?.files?.length) showPhoto();
      setStatus(tx('Barcode ist gültig, aber lokal noch unbekannt. Bitte Bezeichnung eintragen.', 'This barcode is valid but not known locally. Enter a name.'), 'info');
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
    if (!photoInput?.files?.length) {
      if (item.photo_url) showPhoto(item.photo_url, item.photo_id || '');
      else showPhoto();
    }
    setStatus(`${tx('Lokal erkannt:', 'Recognized locally:')} ${item.name || payload.barcode}. ${tx('Bitte kurz prüfen.', 'Please check it.')}`, 'success');
  };

  const lookup = async () => {
    const code = String(barcode?.value || '').trim();
    const generation = ++lookupGeneration;
    const current = () => generation === lookupGeneration && barcode?.value.trim() === code;
    if (!code) {
      setStatus(tx('Barcode eingeben oder scannen.', 'Enter or scan a barcode.'), 'warning');
      barcode?.focus();
      return false;
    }
    try {
      const response = await fetch(`${root.dataset.barcodeUrl}?code=${encodeURIComponent(code)}`, {
        headers: {Accept: 'application/json'},
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      if (!current()) return false;
      if (!response.ok) {
        setStatus(payload.error || tx('Barcode ist ungültig.', 'Invalid barcode.'), 'warning');
        return false;
      }
      if (barcode) barcode.value = payload.barcode || code;
      applyKnown(payload);
      return true;
    } catch (_error) {
      if (!current()) return false;
      setStatus(navigator.onLine === false ? tx('Offline: Barcode bleibt im Formular und kann manuell verwendet werden.', 'Offline: the barcode remains in the form and can be used manually.') : tx('Barcode-Prüfung fehlgeschlagen.', 'Barcode lookup failed.'), 'warning');
      return false;
    }
  };

  const decodePhotoFile = async (file, current) => {
    if (!file || !barcode || barcode.value.trim()) return false;
    if (!("BarcodeDetector" in window)) return false;
    let bitmap = null;
    try {
      bitmap = await createImageBitmap(file);
      if (!current()) return false;
      let formats = ["ean_13", "ean_8", "upc_a", "upc_e"];
      if (typeof window.BarcodeDetector.getSupportedFormats === "function") {
        const supported = await window.BarcodeDetector.getSupportedFormats();
        formats = formats.filter((value) => supported.includes(value));
      }
      if (!formats.length) return false;
      if (!current()) return false;
      const detector = new window.BarcodeDetector({formats});
      const codes = await detector.detect(bitmap);
      if (!current() || barcode.value.trim()) return false;
      const found = (codes || []).find((entry) => String(entry.rawValue || "").trim());
      if (!found) return false;
      barcode.value = String(found.rawValue).trim();
      setStatus(`${tx('Barcode aus dem Foto gelesen:', 'Read barcode from photo:')} ${barcode.value}. ${tx('Bitte kurz prüfen.', 'Please check it.')}`, "success");
      await lookup();
      return true;
    } catch (_error) {
      return false;
    } finally {
      bitmap?.close?.();
    }
  };

  const detectLoop = async (timestamp, generation) => {
    if (!scanning || generation !== cameraGeneration || !detector || !video) return;
    if (timestamp - lastDetection >= 250 && video.readyState >= 2) {
      lastDetection = timestamp;
      try {
        const codes = await detector.detect(video);
        if (!scanning || generation !== cameraGeneration) return;
        const found = codes.find((entry) => String(entry.rawValue || '').trim());
        if (found) {
          barcode.value = String(found.rawValue).trim();
          stopCamera();
          await lookup();
          return;
        }
      } catch (_error) {
        if (!scanning || generation !== cameraGeneration) return;
        setStatus(tx('Kamera konnte den Barcode nicht lesen. Du kannst ihn weiterhin manuell eingeben.', 'The camera could not read the barcode. You can still enter it manually.'), 'warning');
      }
    }
    if (scanning && generation === cameraGeneration) {
      window.requestAnimationFrame((next) => detectLoop(next, generation));
    }
  };

  const startCamera = async () => {
    if (!barcode || !video || !navigator.mediaDevices?.getUserMedia) {
      setStatus(tx('Kamera-Scan wird von diesem Browser nicht unterstützt (HTTPS kann erforderlich sein). Barcode bitte manuell eingeben.', 'Camera scanning is not supported by this browser (HTTPS may be required). Enter the barcode manually.'), 'warning');
      return;
    }
    if (!('BarcodeDetector' in window)) {
      setStatus(tx('Dieser Browser hat keinen lokalen Barcode-Decoder. Barcode bitte manuell eingeben.', 'This browser has no local barcode decoder. Enter the barcode manually.'), 'warning');
      return;
    }
    stopCamera();
    photoGeneration += 1;
    lookupGeneration += 1;
    const generation = cameraGeneration;
    try {
      const desired = ['ean_13', 'ean_8', 'upc_a', 'upc_e'];
      let formats = desired;
      if (typeof window.BarcodeDetector.getSupportedFormats === 'function') {
        const supported = await window.BarcodeDetector.getSupportedFormats();
        if (generation !== cameraGeneration) return;
        formats = desired.filter((value) => supported.includes(value));
      }
      if (!formats.length) throw new Error('no-supported-barcode-format');
      detector = new window.BarcodeDetector({formats});
      const requestedStream = await window.SimpleOfficeCamera.openCameraStream(
        navigator.mediaDevices,
        () => generation === cameraGeneration,
      );
      if (!requestedStream || generation !== cameraGeneration) {
        requestedStream?.getTracks().forEach((track) => track.stop());
        return;
      }
      stream = requestedStream;
      video.srcObject = stream;
      await video.play();
      if (generation !== cameraGeneration) return;
      cameraBox.hidden = false;
      stopButton.hidden = false;
      scanning = true;
      lastDetection = 0;
      setStatus(tx('Kamera aktiv. EAN/UPC-Code in den Bildausschnitt halten.', 'Camera is active. Hold the EAN/UPC code in view.'), 'primary');
      window.requestAnimationFrame((next) => detectLoop(next, generation));
    } catch (error) {
      if (generation !== cameraGeneration) return;
      stopCamera();
      setStatus(
        error?.name === 'NotAllowedError'
          ? tx('Kamerazugriff verweigert. Bitte die Kameraberechtigung im Browser freigeben.', 'Camera access was denied. Allow camera access in your browser.')
          : error?.name === 'NotFoundError'
          ? tx('Keine Kamera gefunden. Barcode bitte manuell eingeben oder als Foto wählen.', 'No camera found. Enter the barcode manually or choose a photo.')
          : error?.name === 'NotReadableError'
          ? tx('Kamera ist belegt (anderer Tab?). Barcode bitte manuell eingeben oder als Foto wählen.', 'Camera is in use (another tab?). Enter the barcode manually or choose a photo.')
          : tx('Kamera oder Barcode-Erkennung ist nicht verfügbar (HTTPS oder Browserberechtigung prüfen). Manuelle Eingabe bleibt möglich.', 'Camera or barcode detection is unavailable (check HTTPS or browser permissions). Manual entry is still available.'),
        'warning',
      );
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
    const formData = new FormData(form);
    formData.delete('product_photo');
    const fields = Object.fromEntries(formData.entries());
    delete fields._csrf_token;
    return {action: form.action, fields};
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
      headers: {Accept: 'application/json', 'X-Shopping-Sync': '1'},
      cache: 'no-store',
    });
    const payload = await response.json().catch(() => ({}));
    return {
      ok: response.ok,
      retry: !response.ok,
      url: payload.redirect || window.location.href,
    };
  };

  const resetQueuedForm = () => {
    if (!form) return;
    form.reset();
    if (requestId) requestId.value = freshRequestId();
    showPhoto();
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
        if (!result.ok) {
          remaining.push(entry);
          rejected += 1;
        }
      } catch (_error) {
        remaining.push(entry);
      }
    }
    if (!writeQueue(remaining)) return;
    if (!remaining.length && !rejected) {
      setStatus(tx('Offline gespeicherte Einkäufe wurden synchronisiert.', 'Offline purchases were synced.'), 'success');
    } else if (rejected) {
      setStatus(tx('Mindestens ein offline gespeicherter Einkauf wurde vom Server abgelehnt und bleibt lokal vorgemerkt.', 'At least one offline purchase was rejected by the server and remains saved locally.'), 'warning');
    }
  };

  if (form) {
    ensureRequestId();
    form.addEventListener('submit', async (event) => {
      const hasNewPhoto = Boolean(photoInput?.files?.length);
      if (hasNewPhoto) {
        if (navigator.onLine === false) {
          event.preventDefault();
          setStatus(tx('Das Produktfoto kann offline nicht sicher vorgemerkt werden. Bitte mit Verbindung senden oder das Foto entfernen.', 'The product photo cannot be safely queued offline. Reconnect before sending or remove the photo.'), 'warning');
        }
        return;
      }
      const entry = currentEntry();
      if (!entry) return;
      event.preventDefault();
      if (navigator.onLine === false) {
        if (enqueue(entry)) {
          setStatus(tx('Offline gespeichert. Der Eintrag wird bei wiederhergestellter Verbindung gesendet.', 'Saved offline. The item will be sent when the connection is restored.'), 'info');
          resetQueuedForm();
        } else {
          setStatus(tx('Offline-Speicherung ist in diesem Browser nicht verfügbar.', 'Offline storage is not available in this browser.'), 'warning');
        }
        return;
      }
      try {
        const result = await postEntry(entry);
        if (result.ok) {
          window.location.assign(result.url || window.location.href);
          return;
        }
        setStatus(tx('Der Server hat den Einkauf abgelehnt. Bitte Eingaben prüfen.', 'The server rejected the purchase. Check the entered details.'), 'warning');
      } catch (_error) {
        if (enqueue(entry)) {
          setStatus(tx('Verbindung abgebrochen. Der Einkauf wurde lokal vorgemerkt.', 'Connection lost. The purchase was saved locally.'), 'info');
          resetQueuedForm();
        } else {
          setStatus(tx('Verbindung abgebrochen und lokale Speicherung ist nicht verfügbar.', 'Connection lost and local storage is unavailable.'), 'warning');
        }
      }
    });
    window.addEventListener('online', flushQueue);
    if (navigator.onLine !== false) window.setTimeout(flushQueue, 0);
  }

  photoInput?.addEventListener('change', () => {
    const file = photoInput.files?.[0];
    const generation = ++photoGeneration;
    const current = () => generation === photoGeneration;
    if (!file) return;
    stopCamera();
    lookupGeneration += 1;
    if (file.size > 8 * 1024 * 1024) {
      photoInput.value = '';
      showPhoto();
      setStatus(tx('Produktfoto ist größer als 8 MiB.', 'Product photo is larger than 8 MiB.'), 'warning');
      return;
    }
    if (photoId) photoId.value = '';
    if (photoPreview) {
      photoPreview.removeAttribute('src');
      photoPreview.hidden = true;
    }
    if (photoClear) photoClear.hidden = false;
    decodePhotoFile(file, current).then((found) => {
      if (!found && current()) setStatus(`${tx('Produktfoto ausgewählt:', 'Product photo selected:')} ${file.name}. ${tx('Es wird beim Hinzufügen lokal gespeichert.', 'It will be saved locally when you add the item.')}`, 'info');
    });
  });
  photoClear?.addEventListener('click', () => {
    photoGeneration += 1;
    if (photoInput) photoInput.value = '';
    showPhoto();
  });
  scanButton?.addEventListener('click', startCamera);
  checkButton?.addEventListener('click', lookup);
  stopButton?.addEventListener('click', stopCamera);
  barcode?.addEventListener('input', () => {
    photoGeneration += 1;
    lookupGeneration += 1;
    stopCamera();
  });
  barcode?.addEventListener('change', () => { if (barcode.value.trim()) lookup(); });
  window.addEventListener('pagehide', stopCamera);
  window.addEventListener('pagehide', () => {
    photoGeneration += 1;
    lookupGeneration += 1;
  });
})();
