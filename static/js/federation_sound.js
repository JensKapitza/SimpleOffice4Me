/* Federation peer discovery over sound. Announces the device endpoint and
 * fills received announcements into the direct-discovery form. Audio stays
 * in this tab; only bare endpoint URLs travel by sound, never identities,
 * tokens or keys. Network contact always needs an explicit form submit. */
(() => {
  'use strict';

  const MAX_LISTEN_MS = 90_000;
  const FALLBACK_SAMPLE_RATE = 48_000;
  const FEC = 'hamming';

  // Mirror of the server-side explicit-local-endpoint rule: IPv4 literal in
  // RFC1918 / RFC6598 / loopback plus an explicit port, no user info.
  const RANGES = [
    [0x0a000000, 8], [0xac100000, 12], [0xc0a80000, 16], [0x64400000, 10], [0x7f000000, 8],
  ];

  function ipv4ToInt(text) {
    const parts = String(text || '').split('.');
    if (parts.length !== 4) return null;
    let value = 0;
    for (const part of parts) {
      if (!/^(0|[1-9][0-9]{0,2})$/.test(part)) return null;
      const octet = Number(part);
      if (octet > 255) return null;
      value = value * 256 + octet;
    }
    return value >>> 0;
  }

  function inLocalRange(int) {
    return RANGES.some(([base, bits]) => (int >>> (32 - bits)) === (base >>> (32 - bits)));
  }

  function isAnnounceEndpoint(text) {
    const value = String(text || '').trim();
    const match = /^(https?):\/\/([^/\s?#]+)$/.exec(value);
    if (!match) return false;
    const hostPort = match[2];
    const separator = hostPort.lastIndexOf(':');
    if (separator < 0) return false;
    const host = hostPort.slice(0, separator);
    const portText = hostPort.slice(separator + 1);
    if (!/^[1-9][0-9]{0,4}$/.test(portText)) return false;
    const port = Number(portText);
    if (port > 65535) return false;
    if (host.includes('@') || host.startsWith('[')) return false;
    const int = ipv4ToInt(host);
    return int !== null && inLocalRange(int);
  }

  function init(scope) {
    const announceButtons = Array.from(scope.querySelectorAll('[data-fed-sound-announce]'));
    const listenButton = scope.querySelector('[data-fed-sound-listen]');
    const stopButton = scope.querySelector('[data-fed-sound-stop]');
    const status = scope.querySelector('[data-fed-sound-status]');
    const bandSelect = scope.querySelector('[data-fed-sound-band]');
    const target = document.getElementById('direct-endpoint');
    if ((!announceButtons.length && !listenButton) || !status) return;

    const isGerman = (document.documentElement.lang || '').toLowerCase().startsWith('de');
    const words = isGerman
      ? { ready: 'Bereit. Ton-Ansagen hören nur Geräte in Hörweite.', sending: 'Ansage wird gesendet …', sent: 'Ansage gesendet.', listening: 'Mikrofon aktiv. Warte auf eine Ton-Ansage …', received: 'Ton-Ansage erkannt und ins Formular übernommen.', invalid: 'Ton erkannt, aber kein gültiger Geräte-Endpunkt.', stopped: 'Gestoppt.', timeout: 'Keine Ton-Ansage erkannt. Bitte erneut versuchen.', consent: 'Bitte bestätige den Mikrofonzugriff.', unavailable: 'Audio oder Mikrofon ist in diesem Browser nicht verfügbar.', failed: 'Tonübertragung fehlgeschlagen. Bitte erneut versuchen.', band: 'Bitte auf beiden Geräten das hörbare Band wählen.', tooLong: 'Der Endpunkt ist für eine Ton-Ansage zu lang.' }
      : { ready: 'Ready. Sound announcements only reach nearby devices.', sending: 'Announcing …', sent: 'Announcement sent.', listening: 'Microphone active. Waiting for a sound announcement …', received: 'Sound announcement detected and filled into the form.', invalid: 'Sound detected, but not a valid device endpoint.', stopped: 'Stopped.', timeout: 'No sound announcement detected. Please try again.', consent: 'Please confirm microphone access.', unavailable: 'Audio or microphone is unavailable in this browser.', failed: 'Sound transfer failed. Please try again.', band: 'Choose the audible band on both devices.', tooLong: 'The endpoint is too long for a sound announcement.' };

    let active = null;
    const contextConstructor = () => window.AudioContext || window.webkitAudioContext;
    const selectedBand = () => (bandSelect && bandSelect.value === 'audible' ? 'audible' : 'ultrasound');
    const setStatus = (message) => { status.textContent = message; };
    function setBusy(busy) {
      announceButtons.forEach((button) => { button.disabled = busy; });
      if (listenButton) listenButton.disabled = busy;
      if (stopButton) stopButton.disabled = !busy;
      if (bandSelect) bandSelect.disabled = busy;
    }
    function closeAudio(operation) {
      if (!operation || operation.closed) return;
      operation.closed = true;
      if (operation.timer !== null) window.clearTimeout(operation.timer);
      try { operation.source?.stop(); } catch { /* already stopped */ }
      try { operation.processor?.disconnect(); } catch { /* already stopped */ }
      try { operation.stream?.getTracks().forEach((track) => track.stop()); } catch { /* already stopped */ }
      if (operation.context && operation.context.state !== 'closed') operation.context.close().catch(() => {});
    }
    function finish(operation, message) {
      if (active !== operation) return;
      closeAudio(operation);
      active = null;
      setBusy(false);
      setStatus(message);
    }
    function fail(operation, error) {
      if (window.console && window.console.warn) window.console.warn('federation sound failed', error);
      finish(operation, words.failed);
    }

    async function announce(endpoint) {
      if (active) return;
      const AudioContextClass = contextConstructor();
      if (!AudioContextClass || !window.AcousticCodec) { setStatus(words.unavailable); return; }
      const size = window.AcousticCodec.measure(endpoint, FEC);
      if (!size.fits) { setStatus(words.tooLong); return; }
      const operation = { kind: 'send', closed: false, context: null, source: null, timer: null };
      active = operation;
      setBusy(true);
      setStatus(words.sending);
      try {
        operation.context = new AudioContextClass();
        await operation.context.resume();
        if (operation.closed) return;
        const rate = operation.context.sampleRate || FALLBACK_SAMPLE_RATE;
        const band = selectedBand();
        if (band === 'ultrasound' && rate < 40000) { finish(operation, words.band); return; }
        const samples = window.AcousticCodec.encode(endpoint, { sampleRate: rate, band, fec: FEC });
        const buffer = operation.context.createBuffer(1, samples.length, rate);
        if (buffer.copyToChannel) buffer.copyToChannel(samples, 0);
        else buffer.getChannelData(0).set(samples);
        operation.source = operation.context.createBufferSource();
        operation.source.buffer = buffer;
        operation.source.connect(operation.context.destination);
        operation.source.onended = () => finish(operation, words.sent);
        operation.source.start();
        operation.timer = window.setTimeout(
          () => finish(operation, words.failed), Math.ceil((samples.length / rate) * 1000) + 2500);
      } catch (error) { fail(operation, error); }
    }

    async function listen() {
      if (active) return;
      const AudioContextClass = contextConstructor();
      if (!AudioContextClass || !window.AcousticCodec || !navigator.mediaDevices?.getUserMedia) {
        setStatus(words.unavailable);
        return;
      }
      const operation = { kind: 'receive', closed: false, context: null, stream: null, timer: null };
      active = operation;
      setBusy(true);
      setStatus(words.consent);
      operation.timer = window.setTimeout(() => finish(operation, words.timeout), MAX_LISTEN_MS);
      try {
        // This call happens only in response to the user's listen-button click.
        operation.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false,
        });
        if (operation.closed) { operation.stream.getTracks().forEach((track) => track.stop()); return; }
        operation.context = new AudioContextClass();
        await operation.context.resume();
        if (operation.closed) return;
        const rate = operation.context.sampleRate || FALLBACK_SAMPLE_RATE;
        const band = selectedBand();
        if (band === 'ultrasound' && rate < 40000) { finish(operation, words.band); return; }
        const decoder = new window.AcousticCodec.Decoder({ sampleRate: rate, band, fec: FEC });
        const inputNode = operation.context.createMediaStreamSource(operation.stream);
        const processor = operation.context.createScriptProcessor(4096, 1, 1);
        operation.processor = processor;
        processor.onaudioprocess = (event) => {
          if (operation.closed) return;
          try {
            const decoded = decoder.push(new Float32Array(event.inputBuffer.getChannelData(0)));
            if (decoded === null) return;
            if (isAnnounceEndpoint(decoded) && target) {
              target.value = String(decoded).trim();
              target.dispatchEvent(new Event('input', { bubbles: true }));
              finish(operation, words.received);
            } else {
              finish(operation, words.invalid);
            }
          } catch (error) { fail(operation, error); }
        };
        inputNode.connect(processor);
        // Keep processor alive without monitoring microphone audio through speakers.
        const silent = operation.context.createGain();
        silent.gain.value = 0;
        processor.connect(silent);
        silent.connect(operation.context.destination);
        setStatus(words.listening);
      } catch (error) { fail(operation, error); }
    }

    announceButtons.forEach((button) => {
      button.addEventListener('click', () => announce(button.getAttribute('data-endpoint') || ''));
    });
    if (listenButton) listenButton.addEventListener('click', listen);
    if (stopButton) stopButton.addEventListener('click', () => { if (active) finish(active, words.stopped); });
    setStatus(words.ready);
    setBusy(false);
    window.addEventListener('pagehide', () => closeAudio(active));
  }

  document.querySelectorAll('[data-fed-sound]').forEach(init);
  window.FederationSound = Object.freeze({ isAnnounceEndpoint });
})();
