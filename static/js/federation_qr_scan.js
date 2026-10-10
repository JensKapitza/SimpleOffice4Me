document.addEventListener('DOMContentLoaded', () => {
  const imageInput = document.getElementById('qr-image');
  const cameraButton = document.getElementById('qr-camera-button');
  const closeButton = document.getElementById('qr-camera-close');
  const preview = document.getElementById('qr-camera-preview');
  const cameraPanel = document.getElementById('qr-camera-panel');
  const output = document.getElementById('qr-payload');
  const status = document.getElementById('qr-status');
  if (!imageInput || !cameraButton || !closeButton || !preview || !cameraPanel || !output || !status) return;

  let stream = null;
  let scanning = false;
  let scanTimer = null;

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
      status.textContent = 'Live-Kamera nicht verfügbar. HTTPS oder localhost und eine Kamerafreigabe sind erforderlich. Alternativ ein QR-Bild auswählen.';
      return;
    }
    if (!('BarcodeDetector' in window)) {
      status.textContent = 'Dieser Browser unterstützt keine QR-Erkennung im Kamerabild. Bitte einen kompatiblen Browser oder den Bild-Upload verwenden.';
      return;
    }

    cameraButton.disabled = true;
    try {
      const detector = new BarcodeDetector({ formats: ['qr_code'] });
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
          const codes = await detector.detect(preview);
          const value = (codes[0]?.rawValue || '').trim();
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
        : 'Kamera konnte nicht gestartet werden. Kamera und Browserberechtigungen prüfen.';
    }
  });

  imageInput.addEventListener('change', async () => {
    const file = imageInput.files?.[0];
    if (!file) return;
    if (!('BarcodeDetector' in window)) {
      status.textContent = 'QR-Erkennung wird von diesem Browser nicht unterstützt. Payload bitte manuell einfügen.';
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
      imageInput.value = '';
    }
  });
});
