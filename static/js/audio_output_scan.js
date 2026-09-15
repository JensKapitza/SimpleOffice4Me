(() => {
  'use strict';
  const nativeAudio = () => Boolean(window.SimpleOfficeNativeAudio);
  const get = (id) => document.getElementById(id);
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const request = async (url, method) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const token = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch(url, {method: method || 'GET', cache: 'no-store', credentials: 'same-origin',
        signal: controller.signal, headers: {'X-CSRF-Token': token ? token.content : ''}});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Antwort nicht lesbar. Anmeldung prüfen.'); }
      if (!response.ok) throw new Error(data.error || 'Gerätesuche fehlgeschlagen.');
      return data;
    } finally { clearTimeout(timer); }
  };
  const root = get('audio-streamer-app');
  const attach = (prefix, key, manualId, noneLabel) => {
    const select = get(prefix + '-select'), search = get(prefix + '-search'), status = get(prefix + '-scan-status');
    const manual = get(manualId), button = get(prefix + '-scan');
    if (!select || !button) return;
    let devices = [], scannedAt = null;
    const render = () => {
      const current = manual.value;
      const query = search.value.trim().toLocaleLowerCase();
      const visible = devices.filter(item => !query || String(item.id + ' ' + item.driver + ' ' + (item.label || '')).toLocaleLowerCase().includes(query));
      clear(select);
      select.add(new Option(noneLabel, prefix === 'input' ? 'default' : ''));
      visible.forEach(item => select.add(new Option((item.label ? item.label + ' · ' : '') + item.id + (item.default ? ' (Standard)' : ''), item.id)));
      if (visible.some(item => item.id === current)) select.value = current;
      status.textContent = visible.length + ' von ' + devices.length + ' Geräten · ' + (scannedAt ? new Date(scannedAt * 1000).toLocaleTimeString() : '');
    };
    const scan = async () => {
      if (nativeAudio()) { status.textContent = 'Android verwendet die Systemauswahl für Audiogeräte.'; return; }
      button.disabled = true;
      status.textContent = '↻ Audiogeräte werden gesucht …';
      try {
        const backendQuery = prefix === 'input' && get('capture-backend').value === 'alsa' ? '?backend=alsa' : '';
        const data = await request(root.dataset.baseUrl + '/' + key + backendQuery);
        if (nativeAudio()) return;
        devices = Array.isArray(data[key]) ? data[key] : [];
        scannedAt = data.updated_at;
        const current = manual.value;
        const preferred = devices.find(item => item.id === current) || devices.find(item => item.default) || devices[0];
        // Preserve explicitly chosen manual/ALSA sources. An empty output gets
        // the discovered default; a removed previously-discovered device falls back.
        if (preferred && (!current || current === 'default' || manual.dataset.discovered === 'true')) {
          manual.value = preferred.id; manual.dataset.discovered = 'true';
          if (prefix === 'input' && preferred.backend) get('capture-backend').value = preferred.backend;
        }
        render();
        if (!devices.length) status.textContent = '○ Keine Geräte gefunden. Verbindung und Audio-Sitzung prüfen.';
      } catch (error) {
        if (!nativeAudio()) status.textContent = error.name === 'AbortError' ? 'Suche dauert zu lange. Erneut versuchen.' : error.message;
      } finally { button.disabled = nativeAudio(); }
    };
    button.addEventListener('click', scan);
    search.addEventListener('input', render);
    select.addEventListener('change', () => {
      manual.value = select.value; manual.dataset.discovered = 'true';
      const selected = devices.find(item => item.id === select.value);
      if (prefix === 'input' && selected && selected.backend) get('capture-backend').value = selected.backend;
    });
    manual.addEventListener('input', () => { manual.dataset.discovered = 'false'; });
    window.addEventListener('simpleoffice:native-audio-ready', () => {
      button.disabled = true; select.disabled = true; search.disabled = true;
      status.textContent = 'Android verwendet die Systemauswahl für Audiogeräte.';
    });
    setTimeout(scan, 300);
  };
  if (root) {
    attach('speaker', 'outputs', 'speaker-device', 'Keine lokale Wiedergabe');
    attach('input', 'inputs', 'capture-source', 'Systemstandard');
  }
  const discovery = get('audio-device-discovery');
  if (discovery) {
    const button = get('audio-local-scan'), status = get('audio-local-scan-status'), results = get('audio-local-scan-results');
    const scan = async () => {
      button.disabled = true; status.textContent = '↻ Audio-Ausgänge werden gesucht …';
      try {
        const data = await request(discovery.dataset.scanUrl, 'POST');
        clear(results);
        data.outputs.forEach(item => {
          const li = document.createElement('li');
          li.textContent = (item.online ? '● Verfügbar: ' : '○ Offline: ') + item.name;
          results.appendChild(li);
        });
        status.textContent = data.count + ' Ausgänge gefunden und gespeichert · ' + new Date(data.updated_at * 1000).toLocaleTimeString();
      } catch (error) { status.textContent = error.name === 'AbortError' ? 'Suche dauert zu lange. Erneut versuchen.' : error.message; }
      finally { button.disabled = false; }
    };
    button.addEventListener('click', scan);
    scan();
  }
})();
