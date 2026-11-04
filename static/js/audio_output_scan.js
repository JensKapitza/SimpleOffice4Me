(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    unreadable: EN ? 'Response unreadable. Check your login.' : 'Antwort nicht lesbar. Anmeldung prüfen.',
    searchFailed: EN ? 'Device search failed.' : 'Gerätesuche fehlgeschlagen.',
    scanningTargets: EN ? 'Searching for recipients …' : 'Empfänger werden gesucht …',
    pickRecipient: EN ? 'Select recipient' : 'Empfänger auswählen',
    activeTargets: (n, time) => EN ? `${n} active recipients · ${time}` : `${n} aktive Empfänger · ${time}`,
    slowRetryLater: EN ? 'Search is taking too long. Try again later.' : 'Suche dauert zu lange. Später erneut versuchen.',
    maxTargets: EN ? 'Maximum 16 targets. Remove a target first.' : 'Maximal 16 Ziele. Zuerst ein Ziel entfernen.',
    targetAdded: EN ? 'Target added. Start the sender to transmit.' : 'Ziel hinzugefügt. Zum Übertragen den Sender starten.',
    deviceCount: (v, d) => EN ? `${v} of ${d} devices` : `${v} von ${d} Geräten`,
    isDefault: EN ? ' (Default)' : ' (Standard)',
    androidPicker: EN ? 'Android uses the system picker for audio devices.' : 'Android verwendet die Systemauswahl für Audiogeräte.',
    scanningDevices: EN ? '↻ Searching for audio devices …' : '↻ Audiogeräte werden gesucht …',
    noDevices: EN ? '○ No devices found. Check the connection and audio session.' : '○ Keine Geräte gefunden. Verbindung und Audio-Sitzung prüfen.',
    slowRetry: EN ? 'Search is taking too long. Try again.' : 'Suche dauert zu lange. Erneut versuchen.',
    scanningOutputs: EN ? '↻ Searching for audio outputs …' : '↻ Audio-Ausgänge werden gesucht …',
    available: EN ? '● Available: ' : '● Verfügbar: ',
    offline: EN ? '○ Offline: ' : '○ Offline: ',
    outputsSaved: (n, time) => EN ? `${n} outputs found and saved · ${time}` : `${n} Ausgänge gefunden und gespeichert · ${time}`,
    noLocalPlayback: EN ? 'No local playback' : 'Keine lokale Wiedergabe',
    systemDefault: EN ? 'System default' : 'Systemstandard',
  };
  const nativeAudio = () => Boolean(window.SimpleOfficeNativeAudio);
  const get = (id) => document.getElementById(id);
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const request = async (url, method, timeout = 12000) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const token = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch(url, {method: method || 'GET', cache: 'no-store', credentials: 'same-origin',
        signal: controller.signal, headers: {'X-CSRF-Token': token ? token.content : ''}});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error(STR.unreadable); }
      if (!response.ok) throw new Error(data.error || STR.searchFailed);
      return data;
    } finally { clearTimeout(timer); }
  };
  const root = get('audio-streamer-app');
  const targetScan = get('target-scan');
  if (root && targetScan) {
    const select = get('target-select'), add = get('target-add');
    const status = get('target-scan-status'), manual = get('stream-targets');
    targetScan.addEventListener('click', async () => {
      targetScan.disabled = true;
      add.disabled = true;
      clear(select);
      status.textContent = STR.scanningTargets;
      try {
        const data = await request(root.dataset.baseUrl + '/targets/scan', 'POST', 60000);
        const placeholder = document.createElement('option');
        placeholder.value = ''; placeholder.textContent = STR.pickRecipient; select.appendChild(placeholder);
        for (const target of data.targets || []) {
          const option = document.createElement('option');
          option.value = target.id; option.textContent = target.label + ' · ' + target.id;
          select.appendChild(option);
        }
        status.textContent = STR.activeTargets(data.count, new Date(data.updated_at * 1000).toLocaleTimeString());
      } catch (error) {
        status.textContent = error.name === 'AbortError' ? STR.slowRetryLater : error.message;
      } finally { targetScan.disabled = false; }
    });
    select.addEventListener('change', () => { add.disabled = !select.value; });
    add.addEventListener('click', () => {
      const values = manual.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
      if (!select.value || values.includes(select.value)) return;
      if (values.length >= 16) { status.textContent = STR.maxTargets; return; }
      values.push(select.value); manual.value = values.join('\n');
      manual.dispatchEvent(new Event('input', {bubbles: true}));
      status.textContent = STR.targetAdded;
    });
  }
  const attach = (prefix, key, manualId, noneLabel) => {
    const select = get(prefix + '-select'), search = get(prefix + '-search'), status = get(prefix + '-scan-status');
    const manual = get(manualId), button = get(prefix + '-scan');
    if (!select || !button) return;
    let devices = [], scannedAt = null, scanMessage = '';
    const render = () => {
      const current = manual.value;
      const query = search.value.trim().toLocaleLowerCase();
      const visible = devices.filter(item => !query || String(item.id + ' ' + item.driver + ' ' + (item.label || '')).toLocaleLowerCase().includes(query));
      clear(select);
      select.add(new Option(noneLabel, prefix === 'input' ? 'default' : ''));
      visible.forEach(item => select.add(new Option((item.label ? item.label + ' · ' : '') + item.id + (item.default ? STR.isDefault : ''), item.id)));
      if (visible.some(item => item.id === current)) select.value = current;
      status.textContent = (scanMessage || STR.deviceCount(visible.length, devices.length)) + ' · ' + (scannedAt ? new Date(scannedAt * 1000).toLocaleTimeString() : '');
    };
    const scan = async () => {
      if (nativeAudio()) { status.textContent = STR.androidPicker; return; }
      button.disabled = true;
      status.textContent = STR.scanningDevices;
      try {
        const backendQuery = prefix === 'input' && ['alsa', 'dshow'].includes(get('capture-backend').value) ? '?backend=' + get('capture-backend').value : '';
        const data = await request(root.dataset.baseUrl + '/' + key + backendQuery);
        if (nativeAudio()) return;
        devices = Array.isArray(data[key]) ? data[key] : [];
        scannedAt = data.updated_at;
        scanMessage = data.message || '';
        const current = manual.value;
        const preferred = devices.find(item => item.id === current) || devices.find(item => item.default) || devices[0];
        // Preserve explicitly chosen manual/ALSA sources. An empty output gets
        // the discovered default; a removed previously-discovered device falls back.
        if (preferred && (!current || current === 'default' || manual.dataset.discovered === 'true')) {
          manual.value = preferred.id; manual.dataset.discovered = 'true';
          if (prefix === 'input' && preferred.backend) get('capture-backend').value = preferred.backend;
        }
        render();
        if (!devices.length) status.textContent = STR.noDevices;
      } catch (error) {
        if (!nativeAudio()) status.textContent = error.name === 'AbortError' ? STR.slowRetry : error.message;
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
      status.textContent = STR.androidPicker;
    });
    setTimeout(scan, 300);
  };
  if (root) {
    attach('speaker', 'outputs', 'speaker-device', STR.noLocalPlayback);
    attach('input', 'inputs', 'capture-source', STR.systemDefault);
  }
  const discovery = get('audio-device-discovery');
  if (discovery) {
    const button = get('audio-local-scan'), status = get('audio-local-scan-status'), results = get('audio-local-scan-results');
    const scan = async () => {
      button.disabled = true; status.textContent = STR.scanningOutputs;
      try {
        const data = await request(discovery.dataset.scanUrl, 'POST');
        clear(results);
        data.outputs.forEach(item => {
          const li = document.createElement('li');
          li.textContent = (item.online ? STR.available : STR.offline) + item.name;
          results.appendChild(li);
        });
        status.textContent = STR.outputsSaved(data.count, new Date(data.updated_at * 1000).toLocaleTimeString());
      } catch (error) { status.textContent = error.name === 'AbortError' ? STR.slowRetry : error.message; }
      finally { button.disabled = false; }
    };
    button.addEventListener('click', scan);
    // Local audio hardware scanning is user initiated.
  }
})();
