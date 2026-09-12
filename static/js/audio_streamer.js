(() => {
  'use strict';

  const root = document.getElementById('audio-streamer-app');
  if (!root) return;

  const base = root.dataset.baseUrl || '';
  const statusBox = document.getElementById('stream-status');
  const setError = (message) => {
    if (!statusBox) return;
    statusBox.className = 'alert alert-danger';
    statusBox.textContent = String(message || 'Unbekannter Fehler');
  };

  const post = async (path, body = {}) => {
    const response = await fetch(base + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
      credentials: 'same-origin',
      cache: 'no-store'
    });
    let data = {};
    try { data = await response.json(); } catch (_error) {}
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    return data;
  };

  const refresh = async () => {
    try {
      const response = await fetch(base + '/status', {cache: 'no-store', credentials: 'same-origin'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      const sender = data.sender && data.sender.running ? 'Sender läuft' : 'Sender aus';
      const receiver = data.receiver && data.receiver.running
        ? `Receiver läuft auf Port ${data.receiver.port}`
        : 'Receiver aus';
      const mic = data.receiver && data.receiver.virtual_microphone
        ? ` · Mikrofon: ${data.receiver.virtual_microphone}`
        : '';
      statusBox.className = 'alert alert-secondary';
      statusBox.textContent = `${sender} · ${receiver}${mic}`;
    } catch (error) {
      setError(error.message);
    }
  };

  const destinations = () => document.getElementById('stream-targets').value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(/^(.+):(\d+)$/);
      if (!match) throw new Error(`Ungültiges Ziel: ${line}`);
      return {host: match[1], port: Number(match[2])};
    });

  document.getElementById('sender-start')?.addEventListener('click', async () => {
    try {
      await post('/sender/start', {
        backend: document.getElementById('capture-backend').value,
        source: document.getElementById('capture-source').value,
        destinations: destinations(),
        bitrate_kbps: Number(document.getElementById('stream-bitrate').value || 64)
      });
      await refresh();
    } catch (error) {
      setError(error.message);
    }
  });

  document.getElementById('sender-stop')?.addEventListener('click', async () => {
    try {
      await post('/sender/stop');
      await refresh();
    } catch (error) {
      setError(error.message);
    }
  });

  document.getElementById('receiver-start')?.addEventListener('click', async () => {
    const speaker = document.getElementById('speaker-device').value.trim();
    try {
      await post('/receiver/start', {
        port: Number(document.getElementById('receiver-port').value || 5004),
        speaker_devices: speaker ? [speaker] : [],
        virtual_microphone: document.getElementById('virtual-microphone').checked,
        virtual_sink: 'simpleoffice_stream'
      });
      await refresh();
    } catch (error) {
      setError(error.message);
    }
  });

  document.getElementById('receiver-stop')?.addEventListener('click', async () => {
    try {
      await post('/receiver/stop');
      await refresh();
    } catch (error) {
      setError(error.message);
    }
  });

  document.getElementById('receiver-port')?.addEventListener('input', (event) => {
    const target = document.getElementById('sdp-download');
    if (target) target.href = `${base}/receiver.sdp?port=${encodeURIComponent(event.target.value || '5004')}`;
  });

  refresh();
})();
