/* Browser audio controls for AcousticCodec. Audio stays in this tab. */
(() => {
  'use strict';

  // The bounded mobile frame is under 90 seconds, including repetition FEC.
  const MAX_LISTEN_MS = 90_000;
  const FALLBACK_SAMPLE_RATE = 48_000;

  function init(root) {
    const sendButton = root.querySelector('[data-acoustic-send]');
    const receiveButton = root.querySelector('[data-acoustic-receive]');
    const stopButton = root.querySelector('[data-acoustic-stop]');
    const status = root.querySelector('[data-acoustic-status]');
    const input = root.querySelector('[data-acoustic-text]');
    const output = root.querySelector('[data-acoustic-result]');
    const bandSelect = root.querySelector('[data-acoustic-band]');
    const fecSelect = root.querySelector('[data-acoustic-fec]');
    if (!sendButton || !receiveButton || !stopButton || !status || !input || !output) return;

    const isGerman = (document.documentElement.lang || '').toLowerCase().startsWith('de');
    const words = isGerman
      ? { ready: 'Bereit. Übertragung erfolgt akustisch; Personen in Hörweite können sie empfangen.', sending: 'Audio wird gesendet …', sent: 'Senden abgeschlossen.', listening: 'Mikrofon aktiv. Warte auf eine Übertragung …', received: 'Übertragung empfangen.', stopped: 'Übertragung gestoppt.', timeout: 'Keine Übertragung erkannt. Bitte erneut versuchen.', empty: 'Bitte gib zuerst Text ein.', consent: 'Bitte bestätige den Mikrofonzugriff.', unavailable: 'Audio oder Mikrofon ist in diesem Browser nicht verfügbar.', failed: 'Audioübertragung fehlgeschlagen. Bitte erneut versuchen.', badText: 'Der Text ist für eine Audioübertragung zu lang.' }
      : { ready: 'Ready. Transfers use sound; people nearby may be able to receive them.', sending: 'Sending audio …', sent: 'Sending complete.', listening: 'Microphone active. Waiting for a transfer …', received: 'Transfer received.', stopped: 'Transfer stopped.', timeout: 'No transfer detected. Please try again.', empty: 'Enter some text first.', consent: 'Please confirm microphone access.', unavailable: 'Audio or microphone is unavailable in this browser.', failed: 'Audio transfer failed. Please try again.', badText: 'This text is too long for an audio transfer.' };

    let active = null;
    function setStatus(message) { status.textContent = message; }
    function setBusy(busy) {
      sendButton.disabled = busy;
      receiveButton.disabled = busy;
      stopButton.disabled = !busy;
      bandSelect.disabled = busy;
      fecSelect.disabled = busy;
    }
    function selectedBand() {
      return bandSelect?.value === 'audible' ? 'audible' : 'ultrasound';
    }
    function selectedFec() {
      const value = fecSelect?.value;
      return ['hamming', 'repeat3', 'none'].includes(value) ? value : 'hamming';
    }
    function contextConstructor() { return window.AudioContext || window.webkitAudioContext; }

    function closeAudio(operation) {
      if (!operation || operation.closed) return;
      operation.closed = true;
      if (operation.timer) window.clearTimeout(operation.timer);
      if (operation.source) {
        operation.source.onended = null;
        try { operation.source.stop(); } catch (error) { if (error.name !== 'InvalidStateError') console.warn('Acoustic audio source stop failed.', error); }
        try { operation.source.disconnect(); } catch (error) { console.warn('Acoustic audio source disconnect failed.', error); }
      }
      if (operation.processor) {
        operation.processor.onaudioprocess = null;
        try { operation.processor.disconnect(); } catch (error) { console.warn('Acoustic audio processor disconnect failed.', error); }
      }
      if (operation.inputNode) {
        try { operation.inputNode.disconnect(); } catch (error) { console.warn('Acoustic microphone node disconnect failed.', error); }
      }
      if (operation.silent) {
        try { operation.silent.disconnect(); } catch (error) { console.warn('Acoustic silent output disconnect failed.', error); }
      }
      if (operation.stream) operation.stream.getTracks().forEach((track) => track.stop());
      if (operation.context && operation.context.state !== 'closed') {
        operation.context.close().catch((error) => console.warn('Acoustic audio context close failed.', error));
      }
      if (active === operation) active = null;
      setBusy(false);
    }

    function finish(operation, message) {
      if (active !== operation || operation.closed) return;
      closeAudio(operation);
      setStatus(message);
    }

    function fail(operation, error) {
      if (active !== operation || operation.closed) return;
      console.warn('Acoustic audio transfer failed.', error);
      const message = error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError'
        ? (isGerman ? 'Mikrofonzugriff wurde abgelehnt.' : 'Microphone access was denied.')
        : error?.name === 'NotFoundError'
          ? (isGerman ? 'Kein Mikrofon gefunden.' : 'No microphone was found.')
          : words.failed;
      finish(operation, message);
    }

    async function send() {
      if (active) return;
      const text = input.value;
      if (!text.trim()) { setStatus(words.empty); input.focus(); return; }
      const AudioContextClass = contextConstructor();
      if (!AudioContextClass || !window.AcousticCodec) { setStatus(words.unavailable); return; }
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
        if (band === 'ultrasound' && rate < 40000) {
          finish(operation, isGerman ? 'Bitte auf beiden Geräten das hörbare Band wählen.' : 'Choose the audible band on both devices.');
          return;
        }
        let samples;
        try { samples = window.AcousticCodec.encode(text, { sampleRate: rate, band, fec: selectedFec() }); }
        catch (error) {
          if (error instanceof RangeError && /payload|exceed|bytes/i.test(error.message)) { finish(operation, words.badText); return; }
          throw error;
        }
        const durationSeconds = Math.max(1, Math.ceil(samples.length / rate));
        const durationLabel = durationSeconds < 60
          ? (isGerman ? `${durationSeconds} Sek.` : `${durationSeconds} sec`)
          : (isGerman ? `${Math.ceil(durationSeconds / 60)} Min.` : `${Math.ceil(durationSeconds / 60)} min`);
        setStatus(isGerman ? `Audio wird gesendet (ca. ${durationLabel}) …` : `Sending audio (about ${durationLabel}) …`);
        const buffer = operation.context.createBuffer(1, samples.length, rate);
        buffer.copyToChannel ? buffer.copyToChannel(samples, 0) : buffer.getChannelData(0).set(samples);
        operation.source = operation.context.createBufferSource();
        operation.source.buffer = buffer;
        operation.source.connect(operation.context.destination);
        operation.source.onended = () => finish(operation, words.sent);
        operation.source.start();
        // Some browser implementations fail to dispatch `ended` after page suspension.
        operation.timer = window.setTimeout(() => finish(operation, words.failed), Math.ceil(samples.length / rate * 1000) + 2500);
      } catch (error) { fail(operation, error); }
    }

    async function receive() {
      if (active) return;
      const AudioContextClass = contextConstructor();
      if (!AudioContextClass || !window.AcousticCodec || !navigator.mediaDevices?.getUserMedia) { setStatus(words.unavailable); return; }
      const operation = { kind: 'receive', closed: false, context: null, stream: null, inputNode: null, processor: null, timer: null };
      active = operation;
      setBusy(true);
      setStatus(isGerman ? 'Mikrofonzugriff wird angefordert …' : 'Requesting microphone access …');
      operation.timer = window.setTimeout(() => finish(operation, words.timeout), MAX_LISTEN_MS);
      try {
        // This call happens only in response to the user's receive-button click.
        operation.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }, video: false });
        if (operation.closed) { operation.stream.getTracks().forEach((track) => track.stop()); return; }
        operation.context = new AudioContextClass();
        await operation.context.resume();
        if (operation.closed) return;
        const rate = operation.context.sampleRate || FALLBACK_SAMPLE_RATE;
        const band = selectedBand();
        if (band === 'ultrasound' && rate < 40000) {
          finish(operation, isGerman ? 'Bitte auf beiden Geräten das hörbare Band wählen.' : 'Choose the audible band on both devices.');
          return;
        }
        const decoder = new window.AcousticCodec.Decoder({ sampleRate: rate, band, fec: selectedFec() });
        operation.inputNode = operation.context.createMediaStreamSource(operation.stream);
        operation.processor = operation.context.createScriptProcessor(4096, 1, 1);
        operation.processor.onaudioprocess = (event) => {
          if (operation.closed) return;
          try {
            const decoded = decoder.push(new Float32Array(event.inputBuffer.getChannelData(0)));
            if (decoded !== null) {
              output.value = decoded;
              finish(operation, words.received);
            }
          } catch (error) { fail(operation, error); }
        };
        operation.inputNode.connect(operation.processor);
        // Keep processor alive without monitoring microphone audio through speakers.
        const silent = operation.context.createGain();
        silent.gain.value = 0;
        operation.silent = silent;
        operation.processor.connect(silent);
        silent.connect(operation.context.destination);
        setStatus(words.listening);
      } catch (error) { fail(operation, error); }
    }

    sendButton.addEventListener('click', send);
    receiveButton.addEventListener('click', receive);
    stopButton.addEventListener('click', () => {
      if (active) finish(active, words.stopped);
    });
    // Copy an existing quick-share draft once; transfer results never overwrite it.
    const draft = document.getElementById('quick-share-text');
    if (draft && !input.value) input.value = draft.value;
    setStatus(words.ready);
    setBusy(false);
    window.addEventListener('pagehide', () => closeAudio(active));
  }

  document.querySelectorAll('[data-acoustic-share]').forEach(init);
})();
