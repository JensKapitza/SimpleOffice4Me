(() => {
  'use strict';
  const root = document.getElementById('audio-streamer-app');
  if (!root) return;
  const base = root.dataset.baseUrl || '';
  const get = id => document.getElementById(id);
  const statusBox = get('stream-status');
  let busy = false, saved = {};
  const nativeAudio = () => Boolean(window.SimpleOfficeNativeAudio);
  const show = (message, error) => {
    statusBox.className = 'alert alert-' + (error ? 'danger' : 'secondary');
    statusBox.textContent = message;
  };
  const apply = (settings) => {
    saved = settings;
    const sender = settings.sender, receiver = settings.receiver;
    get('capture-backend').value = sender.backend;
    get('capture-source').value = sender.source;
    get('stream-targets').value = sender.destinations.map(item => item.host + ':' + item.port).join('\n');
    get('stream-bitrate').value = sender.bitrate_kbps;
    get('receiver-port').value = receiver.port;
    get('receiver-bind').value = receiver.bind;
    get('speaker-device').value = receiver.speaker_devices[0] || '';
    get('virtual-microphone').checked = receiver.virtual_microphone;
    ['sender', 'receiver'].forEach(service => {
      get(service + '-enabled').checked = settings[service].enabled;
      get(service + '-autostart').checked = settings[service].autostart;
      get(service + '-retries').value = settings[service].retry_limit;
    });
  };
  if (!nativeAudio()) {
    try { apply(JSON.parse(root.dataset.settings || '{}')); }
    catch (_) { show('Gespeicherte Einstellungen konnten nicht geladen werden.', true); }
  }
  const request = async (path, body) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const token = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch(base + path, {method: body === undefined ? 'GET' : 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token ? token.content : ''},
        body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Antwort nicht lesbar. Anmeldung prüfen.'); }
      if (!response.ok) throw new Error(data.error || 'Audio-Aktion fehlgeschlagen.');
      return data;
    } finally { clearTimeout(timeout); }
  };
  const refresh = async () => {
    if (nativeAudio()) return;
    const data = await request('/status');
    const states = {running: '● Läuft', waiting: '◷ Wartet auf Audio', failed: '⚠ Fehler', starting: '↻ Startet', stopping: '↻ Stoppt', stopped: '■ Gestoppt', disabled: '○ Deaktiviert'};
    const parts = ['sender', 'receiver'].map(service => {
      const row = data[service] || {};
      return (service === 'sender' ? 'Sender: ' : 'Receiver: ') + (states[row.state] || (row.running ? '● Läuft' : '■ Gestoppt'));
    });
    const errors = ['sender', 'receiver'].map(service => data[service]).filter(row => row && row.state === 'failed' && row.last_error);
    show(parts.join(' · ') + (errors.length ? ' · ' + errors.map(row => row.last_error.message).join(' ') : ''), errors.length > 0);
    get('audio-diagnosis').textContent = JSON.stringify(data, null, 2);
  };
  const collect = (service) => {
    const result = Object.assign({}, saved[service], {
      enabled: get(service + '-enabled').checked,
      autostart: get(service + '-autostart').checked,
      retry_limit: Number(get(service + '-retries').value)
    });
    if (service === 'sender') {
      result.backend = get('capture-backend').value;
      result.source = get('capture-source').value;
      result.bitrate_kbps = Number(get('stream-bitrate').value);
      result.destinations = get('stream-targets').value.split(/\r?\n/).map(line => line.trim()).filter(Boolean).map(line => {
        const match = line.match(/^(.+):(\d+)$/);
        if (!match) throw new Error('Ziel benötigt Host und Port: ' + line);
        const host = match[1].replace(/^\[|\]$/g, '');
        return {host: host, port: Number(match[2])};
      });
    } else {
      result.port = Number(get('receiver-port').value);
      result.bind = get('receiver-bind').value.trim();
      const selected = get('speaker-device').value.trim();
      // Keep additional saved outputs when only the first choice is displayed.
      result.speaker_devices = selected === (saved.receiver.speaker_devices[0] || '') ? saved.receiver.speaker_devices : (selected ? [selected] : []);
      result.virtual_microphone = get('virtual-microphone').checked;
    }
    return result;
  };
  const action = async (service, operation) => {
    if (nativeAudio() || busy) return;
    busy = true; root.setAttribute('aria-busy', 'true');
    root.querySelectorAll('button').forEach(button => { button.disabled = true; });
    show('↻ Audio-Aktion wird ausgeführt …');
    try {
      const body = operation === 'start' || operation === 'settings' ? collect(service) : {};
      await request('/' + service + '/' + operation, body);
      if (operation === 'settings' || operation === 'reset') {
        apply(await request('/settings'));
        show(operation === 'reset' ? 'Standardwerte gespeichert; Dienst gestoppt.' : 'Einstellungen gespeichert. Aktive Streams übernehmen sie beim nächsten Start.');
      } else { await refresh(); }
    } catch (error) {
      show(error.name === 'AbortError' ? 'Zeitüberschreitung. Status aktualisieren, bevor du die Aktion wiederholst.' : error.message, true);
    } finally {
      busy = false; root.removeAttribute('aria-busy');
      root.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  };
  ['sender', 'receiver'].forEach(service => {
    ['start', 'stop'].forEach(operation => get(service + '-' + operation).addEventListener('click', () => action(service, operation)));
  });
  ['save', 'reset', 'restart'].forEach(operation => root.querySelectorAll('[data-audio-' + operation + ']').forEach(button => {
    button.addEventListener('click', () => action(button.dataset['audio' + operation.charAt(0).toUpperCase() + operation.slice(1)], operation === 'save' ? 'settings' : operation));
  }));
  get('receiver-port').addEventListener('input', () => {
    get('sdp-download').href = base + '/receiver.sdp?port=' + encodeURIComponent(get('receiver-port').value || '5004');
  });
  const useNative = () => { get('audio-settings-panel').hidden = true; };
  window.addEventListener('simpleoffice:native-audio-ready', useNative);
  if (nativeAudio()) useNative();
  const poll = async () => {
    if (!busy && !document.hidden && !nativeAudio()) {
      try { await refresh(); } catch (error) { show(error.message, true); }
    }
    setTimeout(poll, 3000);
  };
  poll();
})();
