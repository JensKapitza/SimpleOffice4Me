(() => {
  'use strict';

  if (!navigator.userAgent.includes('SimpleOffice4Me-Android/')) return;

  const statusBox = document.getElementById('stream-status');
  const audio = () => window.SimpleOfficeNativeAudio || null;

  const show = (message, kind = 'secondary') => {
    if (!statusBox) return;
    statusBox.className = `alert alert-${kind}`;
    statusBox.textContent = message;
  };

  const render = (state) => {
    const sender = state?.sender?.running ? 'Sender läuft' : 'Sender aus';
    const receiver = state?.receiver?.running ? `Receiver läuft auf Port ${state.receiver.port}` : 'Receiver aus';
    if (state?.last_error) show(state.last_error, 'danger');
    else show(`${sender} · ${receiver} · Android nativ`);
  };

  const configure = () => {
    const native = audio();
    if (!native) return;
    const state = native.status();
    const backend = document.getElementById('capture-backend');
    const source = document.getElementById('capture-source');
    const bitrate = document.getElementById('stream-bitrate');
    const port = document.getElementById('receiver-port');
    const speaker = document.getElementById('speaker-device');
    const virtualMic = document.getElementById('virtual-microphone');

    if (backend) {
      let option = Array.from(backend.options).find((item) => item.value === 'android');
      if (!option) {
        option = new Option('Android · automatisch', 'android', true, true);
        backend.prepend(option);
      }
      backend.value = 'android';
      backend.disabled = true;
    }
    if (source) {
      source.value = state.defaults?.source || 'Android Systemmikrofon';
      source.readOnly = true;
    }
    if (speaker) {
      speaker.value = state.defaults?.speaker_device || 'Android Systemausgabe';
      speaker.readOnly = true;
    }
    if (virtualMic) {
      virtualMic.checked = false;
      virtualMic.disabled = true;
    }
    if (bitrate) bitrate.value = String(state.defaults?.bitrate_kbps || 64);
    if (port) port.value = String(state.defaults?.port || 5004);
    render(state);
  };

  const targets = () => document.getElementById('stream-targets').value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const match = line.match(/^(.+):(\d+)$/);
      if (!match) throw new Error(`Ungültiges Ziel: ${line}`);
      return {host: match[1], port: Number(match[2])};
    });

  const explain = (result) => {
    if (result === 'permission') return ['Mikrofonzugriff bitte einmal erlauben. Der Sender startet danach automatisch.', 'primary'];
    if (result === 'unsupported') return ['Dieses Android-Gerät stellt keinen Opus-Encoder bereit.', 'warning'];
    if (result === 'invalid-target') return ['Bitte ein Ziel wie 192.168.1.50:5004 angeben.', 'warning'];
    if (result === 'invalid-port') return ['RTP-Port muss zwischen 1024 und 65535 liegen.', 'warning'];
    if (result === 'blocked') return ['Android-Audio wurde aus Sicherheitsgründen blockiert.', 'danger'];
    return [`Audio-Aktion fehlgeschlagen: ${result}`, 'danger'];
  };

  document.addEventListener('click', (event) => {
    const button = event.target?.closest?.('#sender-start,#sender-stop,#receiver-start,#receiver-stop');
    const native = audio();
    if (!button || !native) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    try {
      let result = 'ok';
      if (button.id === 'sender-start') {
        result = native.startSender(targets(), Number(document.getElementById('stream-bitrate').value || 64));
      } else if (button.id === 'sender-stop') {
        result = native.stopSender();
      } else if (button.id === 'receiver-start') {
        result = native.startReceiver(Number(document.getElementById('receiver-port').value || 5004));
      } else if (button.id === 'receiver-stop') {
        result = native.stopReceiver();
      }
      if (result === 'ok') render(native.status());
      else {
        const [message, kind] = explain(result);
        show(message, kind);
      }
    } catch (error) {
      show(error.message || 'Android-Audio konnte nicht gestartet werden.', 'danger');
    }
  }, true);

  window.addEventListener('simpleoffice:native-audio-ready', configure);
  window.addEventListener('simpleoffice:native-audio-status', (event) => {
    if (event.detail?.message) show(String(event.detail.message), 'danger');
    else if (event.detail?.status) render(event.detail.status);
    else configure();
  });

  if (audio()) configure();
  else show('Android-Audiomodul wird verbunden …', 'primary');
})();
