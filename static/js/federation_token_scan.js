/* Scan a rotated federation token from the counterpart's QR code into the
 * peer-token field. Only strict token shapes are accepted; anything else is
 * reported and ignored. The token still travels only visually. */
(() => {
  'use strict';

  // secrets.token_urlsafe(48) shape, tolerant to future lengths.
  const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;

  function isTokenPayload(value) {
    return TOKEN_PATTERN.test(String(value || '').trim());
  }

  document.addEventListener('DOMContentLoaded', () => {
    const cameraButton = document.getElementById('token-qr-camera-button');
    const closeButton = document.getElementById('token-qr-camera-close');
    const preview = document.getElementById('token-qr-camera-preview');
    const cameraPanel = document.getElementById('token-qr-camera-panel');
    const output = document.getElementById('peer-token');
    const status = document.getElementById('token-qr-status');
    if (!cameraButton || !closeButton || !preview || !cameraPanel || !output || !status) return;
    const isGerman = (document.documentElement.lang || '').toLowerCase().startsWith('de');

    let stream = null;
    let scanning = false;
    let scanTimer = null;
    let cameraGeneration = 0;
    const scanCanvas = document.createElement('canvas');
    const scanContext = scanCanvas.getContext('2d', { willReadFrequently: true });

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
      return window.jsQR(frame.data, frame.width, frame.height, { inversionAttempts: 'attemptBoth' })?.data || '';
    }

    function stopCamera() {
      cameraGeneration += 1;
      scanning = false;
      if (scanTimer !== null) clearTimeout(scanTimer);
      scanTimer = null;
      if (stream) stream.getTracks().forEach((track) => track.stop());
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
        status.textContent = isGerman
          ? 'Keine Kamera verfügbar. Token manuell einfügen.'
          : 'No camera available. Paste the token manually.';
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
        if (typeof window.SimpleOfficeCamera?.openCameraStream === 'function') {
          requestedStream = await window.SimpleOfficeCamera.openCameraStream(
            navigator.mediaDevices, () => generation === cameraGeneration);
        } else {
          requestedStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
        }
        if (!requestedStream) return;
        if (generation !== cameraGeneration) {
          requestedStream.getTracks().forEach((track) => track.stop());
          return;
        }
        stream = requestedStream;
        preview.srcObject = stream;
        cameraPanel.hidden = false;
        await preview.play();
        if (generation !== cameraGeneration) {
          requestedStream.getTracks().forEach((track) => track.stop());
          if (stream === requestedStream) stream = null;
          if (preview.srcObject === requestedStream) preview.srcObject = null;
          return;
        }
        scanning = true;
        status.textContent = isGerman
          ? 'Kamera aktiv. Token-QR-Code der Gegenstelle vor die Kamera halten.'
          : 'Camera active. Hold the counterpart token QR code in front of the camera.';

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
            if (value) {
              if (isTokenPayload(value)) {
                output.value = value.trim();
                output.dispatchEvent(new Event('input', { bubbles: true }));
                status.textContent = isGerman
                  ? 'Token-QR-Code erkannt und übernommen.'
                  : 'Token QR code recognized and adopted.';
                stopCamera();
                return;
              }
              status.textContent = isGerman
                ? 'Der QR-Code enthält kein gültiges Token.'
                : 'The QR code does not contain a valid token.';
            }
          } catch (_error) {
            // A frame may be unreadable while the camera is refocusing.
          }
          if (scanning) scanTimer = setTimeout(scan, 250);
        };
        scanTimer = setTimeout(scan, 250);
      } catch (error) {
        if (generation !== cameraGeneration) return;
        if (requestedStream) requestedStream.getTracks().forEach((track) => track.stop());
        if (stream === requestedStream) stream = null;
        status.textContent = isGerman
          ? 'Kamera konnte nicht geöffnet werden.'
          : 'Camera could not be opened.';
        cameraButton.disabled = false;
      }
    });
  });

  window.FederationTokenScan = Object.freeze({ isTokenPayload });
})();
