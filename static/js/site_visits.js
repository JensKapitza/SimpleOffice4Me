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
  root.querySelector('[data-barcode-scan]')?.addEventListener('click', async event => {
    if (!scanner || !camera) return alert('Barcode-Scan ist in diesem Browser nicht verfügbar. Seriennummer bitte manuell eingeben.');
    const video = document.createElement('video'); video.setAttribute('playsinline', ''); video.style.cssText = 'position:fixed;inset:10%;width:80%;height:80%;object-fit:contain;background:#000;z-index:2000';
    const close = document.createElement('button'); close.type = 'button'; close.textContent = 'Kamera schließen'; close.className = 'btn btn-light'; close.style.cssText = 'position:fixed;top:12%;right:12%;z-index:2001'; document.body.append(video, close);
    let stream; try { stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: {ideal: 'environment'}}, audio: false}); video.srcObject = stream; await video.play(); const detector = new BarcodeDetector();
      const poll = async () => { if (!video.isConnected) return; try { const codes = await detector.detect(video); if (codes[0]) {
          const value = codes[0].rawValue;
          try {
            const label = new URL(value);
            if (label.origin === location.origin && /^\/site-visits\/[0-9a-f-]{36}$/.test(label.pathname) && label.searchParams.has('asset')) { close.click(); location.assign(label.href); return; }
          } catch (_) {}
          const target = root.querySelector('[data-barcode-target]'); if (target) target.value = value; close.click(); return;
        } } catch (_) {} requestAnimationFrame(poll); }; poll();
    } catch (error) { close.click(); alert(`Kamera konnte nicht geöffnet werden: ${error.message}`); }
    close.addEventListener('click', () => { stream?.getTracks().forEach(track => track.stop()); video.remove(); close.remove(); }, {once: true});
  });
})();
