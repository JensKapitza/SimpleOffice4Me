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

  scanButton?.addEventListener('click', startCamera);
  checkButton?.addEventListener('click', lookup);
  stopButton?.addEventListener('click', stopCamera);
  barcode?.addEventListener('change', () => { if (barcode.value.trim()) lookup(); });
  window.addEventListener('pagehide', stopCamera);
})();
