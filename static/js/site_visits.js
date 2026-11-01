(() => {
  const root = document.querySelector('[data-site-visit]');
  if (!root) return;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const visitId = location.pathname.split('/')[2];
  const capability = (name, yes, no) => {
    const badge = root.querySelector(`[data-cap="${name}"]`);
    if (badge) { badge.textContent = yes ? `${name}: verfügbar` : `${name}: nicht verfügbar`; badge.className = `badge ${yes ? 'text-bg-success' : 'text-bg-light'}`; }
  };
  const camera = !!(navigator.mediaDevices?.getUserMedia);
  const scanner = 'BarcodeDetector' in window;
  capability('camera', camera, false); capability('barcode', scanner, false);
  capability('location', !!navigator.geolocation, false);
  capability('orientation', 'DeviceOrientationEvent' in window, false);
  const postForm = (url, values) => {
    const form = document.createElement('form'); form.method = 'post'; form.action = url;
    Object.entries({...values, _csrf_token: csrf}).forEach(([name, value]) => { const input = document.createElement('input'); input.type = 'hidden'; input.name = name; input.value = value; form.append(input); });
    document.body.append(form); form.submit();
  };
  root.querySelector('[data-location-button]')?.addEventListener('click', () => {
    if (!navigator.geolocation) return alert('Standortermittlung wird von diesem Gerät nicht angeboten.');
    navigator.geolocation.getCurrentPosition(({coords}) => {
      const label = root.querySelector('[data-location-result]'); if (label) label.textContent = `${coords.latitude.toFixed(6)}, ${coords.longitude.toFixed(6)} · Genauigkeit ${Math.round(coords.accuracy)} m`;
      const form = document.querySelector('form[action$="/update"]');
      if (!form) return;
      ['latitude', 'longitude'].forEach((key, i) => { const input = document.createElement('input'); input.type = 'hidden'; input.name = key; input.value = i ? coords.longitude : coords.latitude; form.append(input); }); form.requestSubmit();
    }, error => alert(`Standort konnte nicht gelesen werden: ${error.message}`), {enableHighAccuracy: true, timeout: 10000, maximumAge: 30000});
  });
  root.querySelector('[data-compass-button]')?.addEventListener('click', async () => {
    const output = root.querySelector('[data-compass-result]');
    if (!('DeviceOrientationEvent' in window)) { if (output) output.textContent = 'Kompass wird nicht angeboten.'; return; }
    try {
      if (typeof DeviceOrientationEvent.requestPermission === 'function' && await DeviceOrientationEvent.requestPermission() !== 'granted') { if (output) output.textContent = 'Sensorfreigabe wurde nicht erteilt.'; return; }
      const listener = event => {
        const heading = event.webkitCompassHeading ?? (event.alpha == null ? null : (360 - event.alpha) % 360);
        if (output) output.textContent = heading == null ? 'Keine Kompassrichtung verfügbar.' : `Richtung ${Math.round(heading)}°`;
        window.removeEventListener('deviceorientation', listener);
      };
      window.addEventListener('deviceorientation', listener, {once: true});
      window.setTimeout(() => window.removeEventListener('deviceorientation', listener), 5000);
      if (output) output.textContent = 'Warte auf Sensor…';
    } catch (error) { if (output) output.textContent = `Sensorfreigabe fehlgeschlagen: ${error.message}`; }
  });
  root.querySelectorAll('[data-visit-upload]').forEach(input => input.addEventListener('change', () => {
    const file = input.files?.[0]; if (!file) return;
    const form = document.createElement('form'); form.method = 'post'; form.enctype = 'multipart/form-data'; form.action = `/site-visits/${visitId}/attachments`;
    const hidden = (name, value) => { const el = document.createElement('input'); el.type = 'hidden'; el.name = name; el.value = value; form.append(el); };
    hidden('_csrf_token', csrf); hidden('target_type', input.dataset.visitUpload === 'asset' ? 'asset' : 'visit'); hidden('target_id', input.dataset.targetId || visitId); if (input.dataset.visitUpload === 'floorplan') hidden('purpose', 'floorplan');
    input.name = 'file'; input.hidden = false; form.append(input); document.body.append(form); form.requestSubmit();
  }));
  const roomSelect = root.querySelector('[data-room-select]'); let selected = root.querySelector('[data-asset-marker].is-selected');
  root.querySelectorAll('[data-asset-marker]').forEach(marker => marker.addEventListener('click', () => { selected?.classList.remove('is-selected'); selected = marker; marker.classList.add('is-selected'); if (roomSelect) roomSelect.value = marker.dataset.room; }));
  root.querySelector('[data-map]')?.addEventListener('click', event => {
    if (!selected || event.target.closest('[data-asset-marker]')) return;
    const map = event.currentTarget, rect = map.getBoundingClientRect(), room = event.target.closest('.site-room');
    if (!room || !roomSelect?.value) return;
    const roomRect = room.getBoundingClientRect(); const x = Math.max(0, Math.min(100, (event.clientX - roomRect.left) / roomRect.width * 100)); const y = Math.max(0, Math.min(100, (event.clientY - roomRect.top) / roomRect.height * 100));
    selected.style.left = `${x}%`; selected.style.top = `${y}%`;
    postForm(`/site-visits/${visitId}/assets/${selected.dataset.assetMarker}`, {room_id: roomSelect.value, x, y});
  });
  let scanGeneration = 0;
  let scanStream = null;
  let scanVideo = null;
  let scanNodes = [];
  const stopScanStream = () => {
    scanGeneration += 1;
    if (scanStream) { scanStream.getTracks().forEach(track => track.stop()); scanStream = null; }
    if (scanVideo) { try { scanVideo.pause(); } catch (_) {} scanVideo.srcObject = null; scanVideo = null; }
  };
  const closeScanner = () => {
    stopScanStream();
    scanNodes.forEach(node => node.remove());
    scanNodes = [];
  };
  window.addEventListener('pagehide', closeScanner);
  const decodeQrPixels = (source, width, height) => {
    if (typeof window.jsQR !== 'function' || !width || !height) return '';
    const canvas = document.createElement('canvas');
    const scale = Math.min(1, 1200 / Math.max(width, height));
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const context = canvas.getContext('2d', {willReadFrequently: true});
    if (!context) return '';
    context.drawImage(source, 0, 0, canvas.width, canvas.height);
    const frame = context.getImageData(0, 0, canvas.width, canvas.height);
    return window.jsQR(frame.data, frame.width, frame.height, {inversionAttempts: 'attemptBoth'})?.data || '';
  };
  root.querySelector('[data-barcode-scan]')?.addEventListener('click', async event => {
    event.preventDefault();
    if (scanNodes.length) return;
    closeScanner();
    const generation = scanGeneration;
    const target = root.querySelector('[data-barcode-target]');
    const overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:2000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.75rem;padding:1rem';
    const video = document.createElement('video');
    video.setAttribute('playsinline', ''); video.muted = true; video.autoplay = true;
    video.style.cssText = 'width:min(92vw,640px);max-height:60vh;object-fit:contain;background:#000;border-radius:.5rem';
    const status = document.createElement('p');
    status.style.cssText = 'color:#fff;margin:0;max-width:min(92vw,640px);text-align:center';
    status.setAttribute('role', 'status');
    const controls = document.createElement('div');
    controls.style.cssText = 'display:flex;gap:.5rem;flex-wrap:wrap;justify-content:center';
    const fileLabel = document.createElement('label');
    fileLabel.className = 'btn btn-light btn-sm mb-0'; fileLabel.textContent = 'Foto wählen';
    const fileInput = document.createElement('input');
    fileInput.hidden = true; fileInput.type = 'file'; fileInput.accept = 'image/*';
    fileLabel.append(fileInput);
    const close = document.createElement('button');
    close.type = 'button'; close.textContent = 'Kamera schließen'; close.className = 'btn btn-light btn-sm';
    controls.append(fileLabel, close);
    overlay.append(video, status, controls);
    document.body.append(overlay);
    scanNodes = [overlay];
    scanVideo = video;
    close.addEventListener('click', closeScanner);
    const showStatus = text => { status.textContent = text; };
    const applyValue = value => {
      const clean = String(value || '').trim();
      if (!clean) return false;
      try {
        const label = new URL(clean);
        if (label.origin === location.origin && /^\/site-visits\/[0-9a-f-]{36}$/.test(label.pathname) && label.searchParams.has('asset')) { closeScanner(); location.assign(label.href); return true; }
      } catch (_) {}
      if (target) target.value = clean;
      closeScanner();
      return true;
    };
    let detector = null;
    if (scanner) {
      try { detector = new BarcodeDetector(); } catch (_) { detector = null; }
    }
    const canDecodeQr = typeof window.jsQR === 'function';
    const decodeSource = async source => {
      if (detector) {
        try {
          const codes = await detector.detect(source);
          const hit = (codes || []).find(entry => String(entry.rawValue || '').trim());
          if (hit) return String(hit.rawValue).trim();
        } catch (_) {}
      }
      if (!canDecodeQr) return '';
      if (source instanceof HTMLVideoElement) return decodeQrPixels(source, source.videoWidth, source.videoHeight).trim();
      return decodeQrPixels(source, source.width, source.height).trim();
    };
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      showStatus('Foto wird lokal ausgewertet …');
      let bitmap = null;
      try {
        bitmap = await createImageBitmap(file);
        const value = await decodeSource(bitmap);
        if (generation !== scanGeneration) return;
        if (value) { applyValue(value); return; }
        showStatus('Kein Code erkannt. Bitte ein schärferes Foto wählen oder die Seriennummer manuell eingeben.');
      } catch (_) {
        if (generation === scanGeneration) showStatus('Foto konnte nicht gelesen werden. Seriennummer bitte manuell eingeben.');
      } finally {
        bitmap?.close?.();
        fileInput.value = '';
      }
    });
    if (!camera) {
      showStatus(detector || canDecodeQr
        ? 'Keine Live-Kamera in diesem Browser (HTTPS oder Berechtigung prüfen). QR-Code stattdessen als Foto wählen oder Seriennummer manuell eingeben.'
        : 'Barcode-Scan ist in diesem Browser nicht verfügbar. Seriennummer bitte manuell eingeben.');
      return;
    }
    if (!detector && !canDecodeQr) {
      showStatus('Kein lokaler Decoder verfügbar. Seriennummer bitte manuell eingeben.');
      return;
    }
    try {
      const requestedStream = await window.SimpleOfficeCamera.openCameraStream(
        navigator.mediaDevices,
        () => generation === scanGeneration,
      );
      if (!requestedStream || generation !== scanGeneration) {
        requestedStream?.getTracks().forEach(track => track.stop());
        return;
      }
      scanStream = requestedStream;
      video.srcObject = scanStream;
      await video.play();
      if (generation !== scanGeneration) return;
      showStatus(detector ? 'Code vor die Kamera halten.' : 'QR-Code vor die Kamera halten (andere Codes manuell eingeben oder als Foto wählen).');
      const poll = async () => {
        if (generation !== scanGeneration || !video.isConnected) return;
        try {
          if (video.readyState >= 2) {
            const value = await decodeSource(video);
            if (generation !== scanGeneration) return;
            if (value) { applyValue(value); return; }
          }
        } catch (_) {}
        requestAnimationFrame(poll);
      };
      poll();
    } catch (error) {
      if (generation !== scanGeneration) return;
      if (scanStream) { scanStream.getTracks().forEach(track => track.stop()); scanStream = null; }
      video.srcObject = null;
      showStatus(error?.name === 'NotAllowedError'
        ? 'Kamerazugriff verweigert. Bitte die Kameraberechtigung im Browser freigeben – oder Seriennummer manuell eingeben.'
        : error?.name === 'NotFoundError'
        ? 'Keine Kamera gefunden. Foto wählen oder Seriennummer manuell eingeben.'
        : error?.name === 'NotReadableError'
        ? 'Kamera ist belegt. Anderen Tab schließen – oder Foto wählen bzw. manuell eingeben.'
        : 'Kamera konnte nicht geöffnet werden (HTTPS oder Browserberechtigung prüfen). Foto wählen oder manuell eingeben.');
    }
  });
})();
