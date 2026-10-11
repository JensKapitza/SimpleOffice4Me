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
    try {
      let detector = null;
      if (typeof window.BarcodeDetector === 'function') {
        try { detector = new BarcodeDetector({ formats: ['qr_code'] }); }
        catch (_error) { /* Use the bundled decoder instead. */ }
      }
      if (!detector && typeof window.jsQR !== 'function') {
        throw new Error('No QR decoder available');
      }
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } }, audio: false
      });
      preview.srcObject = stream;
      cameraPanel.hidden = false;
      await preview.play();
      scanning = true;
      status.textContent = 'Kamera aktiv. Connect QR-Code vor die Kamera halten.';

      const scan = async () => {
        if (!scanning) return;
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
    if (!('BarcodeDetector' in window) || !window.isSecureContext) {
      status.textContent = 'QR-Foto wird lokal ausgewertet …';
      try {
        const bitmap = await createImageBitmap(file);
        try {
          const canvas = document.createElement('canvas');
          const ratio = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
          canvas.width = Math.max(1, Math.round(bitmap.width * ratio));
          canvas.height = Math.max(1, Math.round(bitmap.height * ratio));
          const context = canvas.getContext('2d', {willReadFrequently: true});
          context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
          if (typeof window.jsQR === 'function') {
            const pixels = context.getImageData(0, 0, canvas.width, canvas.height);
            const decoded = window.jsQR(pixels.data, pixels.width, pixels.height, {inversionAttempts: 'attemptBoth'});
            if (decoded?.data) acceptPayload(decoded.data.trim());
            else status.textContent = 'Kein QR-Code erkannt. Bitte ein schärferes Foto auswählen.';
          } else {
            status.textContent = 'Dieser Browser benötigt den lokalen QR-Decoder (jsQR). Bitte BarcodeDetector verwenden oder Payload manuell einfügen.';
          }
        } finally { bitmap.close?.(); }
      } catch (_error) { status.textContent = 'QR-Foto konnte nicht verarbeitet werden.'; }
      finally { sourceInput.value = ''; }
      return;
    }
    status.textContent = 'QR-Code wird gelesen …';
    let image;
    try {
      image = await createImageBitmap(file);
      const detector = new BarcodeDetector({ formats: ['qr_code'] });
      const codes = await detector.detect(image);
      if (!codes.length) {
        status.textContent = 'Kein QR-Code erkannt. Bitte ein schärferes Bild auswählen.';
        return;
      }
      acceptPayload((codes[0].rawValue || '').trim());
    } catch (_error) {
      status.textContent = 'QR-Code konnte nicht gelesen werden.';
    } finally {
      if (image && typeof image.close === 'function') image.close();
      sourceInput.value = '';
    }
  }
  imageInput.addEventListener('change', processImage);
  captureInput.addEventListener('change', processImage);
});
