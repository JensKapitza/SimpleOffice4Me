document.addEventListener('DOMContentLoaded', () => {
  const imageInput = document.getElementById('qr-image');
  const cameraButton = document.getElementById('qr-camera-button');
  const captureInput = document.createElement('input');
  captureInput.type = 'file';
  captureInput.accept = 'image/*';
  captureInput.setAttribute('capture', 'environment');
  captureInput.hidden = true;
  document.body.appendChild(captureInput);
  const closeButton = document.getElementById('qr-camera-close');
  const preview = document.getElementById('qr-camera-preview');
  const cameraPanel = document.getElementById('qr-camera-panel');
  const output = document.getElementById('qr-payload');
  const status = document.getElementById('qr-status');
  if (!imageInput || !cameraButton || !closeButton || !preview || !cameraPanel || !output || !status) return;

  let stream = null;
  let scanning = false;
  let scanTimer = null;
  let cameraGeneration = 0;
  const scanCanvas = document.createElement('canvas');
  const scanContext = scanCanvas.getContext('2d', {willReadFrequently: true});

  function decodePixels(source) {
    if (typeof window.jsQR !== 'function' || !scanContext) return '';
    const width = source.videoWidth || source.width;
    const height = source.videoHeight || source.height;
    if (!width || !height) return '';
    const scale = Math.min(1, 1200 / Math.max(width, height));
    scanCanvas.width = Math.max(1, Math.round(width * scale));
    scanCanvas.height = Math.max(1, Math.round(height * scale));
    scanContext.drawImage(source, 0, 0, scanCanvas.width, scanCanvas.height);
    const frame = scanContext.getImageData(0, 0, scanCanvas.width, scanCanvas.height);
    return window.jsQR(frame.data, frame.width, frame.height, {inversionAttempts: 'attemptBoth'})?.data || '';
  }

  function acceptPayload(value) {
    if (!value.startsWith('sofp://peer/')) {
      status.textContent = 'Der QR-Code ist kein SimpleOffice-Federation-Connect-Code.';
      return false;
    }
    output.value = value;
    status.textContent = 'SimpleOffice Connect QR-Code erkannt. Peer kann jetzt übernommen werden.';
    return true;
  }

  function stopCamera() {
    cameraGeneration += 1;
    scanning = false;
    if (scanTimer !== null) clearTimeout(scanTimer);
    scanTimer = null;
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    preview.pause();
    preview.srcObject = null;
    cameraPanel.hidden = true;
    cameraButton.disabled = false;
  }

  closeButton.addEventListener('click', stopCamera);
  window.addEventListener('pagehide', stopCamera);

  cameraButton.addEventListener('click', async () => {
    if (scanning || stream) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      status.textContent = 'Foto aufnehmen und QR-Code lokal auswerten.';
      captureInput.click();
      return;
    }

    cameraButton.disabled = true;
    const generation = ++cameraGeneration;
    let requestedStream = null;
    try {
      let detector = null;
      if (typeof window.BarcodeDetector === 'function') {
        try { detector = new BarcodeDetector({ formats: ['qr_code'] }); }
        catch (_error) { /* Use the bundled decoder instead. */ }
      }
      if (!detector && typeof window.jsQR !== 'function') {
        throw new Error('No QR decoder available');
      }
      requestedStream = await window.SimpleOfficeCamera.openCameraStream(
        navigator.mediaDevices,
        () => generation === cameraGeneration,
      );
      if (!requestedStream) return;
      if (generation !== cameraGeneration) {
        requestedStream.getTracks().forEach(track => track.stop());
        return;
      }
      stream = requestedStream;
      preview.srcObject = stream;
      cameraPanel.hidden = false;
      await preview.play();
      if (generation !== cameraGeneration) {
        requestedStream.getTracks().forEach(track => track.stop());
        if (stream === requestedStream) stream = null;
        if (preview.srcObject === requestedStream) preview.srcObject = null;
        return;
      }
      scanning = true;
      status.textContent = 'Kamera aktiv. Connect QR-Code vor die Kamera halten.';

      const scan = async () => {
        if (!scanning || generation !== cameraGeneration) return;
        try {
          let value = '';
          if (detector) {
            try {
              const codes = await detector.detect(preview);
              value = (codes[0]?.rawValue || '').trim();
            } catch (_error) {
              // Browser-native detection can fail; try the local decoder.
            }
          }
          if (generation !== cameraGeneration) return;
          if (!value) value = decodePixels(preview).trim();
          if (value && acceptPayload(value)) {
            stopCamera();
            return;
          }
        } catch (_error) {
          // A frame may be unreadable while the camera is refocusing.
        }
        if (scanning) scanTimer = setTimeout(scan, 250);
      };
      scanTimer = setTimeout(scan, 250);
    } catch (error) {
      if (generation !== cameraGeneration) return;
      stopCamera();
      status.textContent = error?.name === 'NotAllowedError'
        ? 'Kamerazugriff verweigert. Bitte die Kameraberechtigung im Browser freigeben.'
        : 'Kamera konnte nicht gestartet werden. Kamera, Browserberechtigungen und QR-Decoder prüfen.';
    }
  });

  async function processImage(event) {
    const sourceInput = event.currentTarget;
    const file = sourceInput.files?.[0];
    if (!file) return;
    status.textContent = 'QR-Foto wird lokal ausgewertet …';
    let bitmap;
    try {
      bitmap = await createImageBitmap(file);
      let value = '';
      if (typeof window.BarcodeDetector === 'function') {
        try {
          const detector = new BarcodeDetector({ formats: ['qr_code'] });
          const codes = await detector.detect(bitmap);
          value = (codes[0]?.rawValue || '').trim();
        } catch (_error) {
          // Unsupported formats and native detector errors use the local decoder.
        }
      }
      if (!value) value = decodePixels(bitmap).trim();
      if (!value) {
        status.textContent = 'Kein QR-Code erkannt. Bitte ein schärferes Foto auswählen.';
      } else {
        acceptPayload(value);
      }
    } catch (_error) {
      status.textContent = 'QR-Foto konnte nicht verarbeitet werden.';
    } finally {
      bitmap?.close?.();
      sourceInput.value = '';
    }
  }
  imageInput.addEventListener('change', processImage);
  captureInput.addEventListener('change', processImage);
});
