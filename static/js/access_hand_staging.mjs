import {cameraController} from './kids_hand_camera.mjs';
import {readResult} from './kids_hand_logic.mjs';
import {loadHandModel} from './kids_hand_model.mjs';
import {FRAME_DELAY, HAND_DESTINATIONS, HandShortcut, handBrowserSupported, playHandPreview} from './access_hand_staging_logic.mjs';

const root = document.querySelector('[data-hand-nav][data-ready="yes"]');
if (root) void setup(root);
async function setup(panel) {
  const parent = panel.closest('[data-access-proof]');
  if (!parent) return;
  const get = (name) => panel.querySelector(`[data-hand-nav-${name}]`);
  const [start, arm, stopButton, select, status, video, preview] =
    ['start', 'arm', 'stop', 'destination', 'status', 'video', 'preview'].map(get);
  if (![start, arm, stopButton, select, status, video, preview].every(Boolean)) return;
  const en = panel.dataset.language === 'en';
  if (!window.isSecureContext || window.top !== window || !navigator.mediaDevices?.getUserMedia ||
      typeof video?.requestVideoFrameCallback !== 'function' ||
      typeof video.cancelVideoFrameCallback !== 'function') {
    status.textContent = en ? 'Use the links below. Hand navigation needs a current HTTPS or localhost browser with a webcam.' :
      'Nutze die Links unten. Die Handsteuerung braucht einen aktuellen Browser auf HTTPS oder localhost und eine Webcam.';
    return;
  }
  const destinations = new Map();
  for (const id of HAND_DESTINATIONS) {
    const anchor = parent.querySelector(`a[data-access-target="${id}"]`);
    if (!anchor) return;
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin || url.username || url.password || url.hash || url.search) return;
    destinations.set(id, {url: url.href, anchor});
  }
  if (!await handBrowserSupported()) {
    status.textContent = en ? 'This browser does not support the acceleration needed for hand navigation. Use the links below.' :
      'Dieser Browser unterstützt die nötige Beschleunigung nicht. Nutze die Links unten.';
    status.dataset.state = 'unsupported';
    return;
  }
  const text = en ? {
    loading:'Loading the local hand model…', permission:'Allow only the camera.',
    live:'Camera on. Choose a destination and arm once.',
    release:'Move your hand out of the frame to arm.',
    armed:'Armed: hold up only your index finger.',
    ended:'Camera off.', denied:'Camera permission denied.',
    model_error:'The local hand model could not start. Camera off. Check the verified setup.',
    camera_error:'The camera or its preview could not start. Check browser access and whether the webcam is in use.',
    inference_error:'Hand recognition stopped. Camera off.',
    expired:'Time expired. Camera off.',
  } : {
    loading:'Das lokale Handmodell wird geladen …', permission:'Erlaube nur die Kamera.',
    live:'Kamera an. Wähle ein Ziel und schalte einmal scharf.',
    release:'Nimm die Hand aus dem Bild, um scharfzuschalten.',
    armed:'Scharf: Halte nur den Zeigefinger hoch.',
    ended:'Kamera aus.', denied:'Kamerazugriff abgelehnt.',
    model_error:'Das lokale Handmodell konnte nicht starten. Kamera aus. Prüfe die Einrichtung.',
    camera_error:'Kamera oder Vorschau konnte nicht starten. Prüfe die Browserfreigabe und ob die Kamera schon genutzt wird.',
    inference_error:'Handerkennung gestoppt. Kamera aus.',
    expired:'Zeit abgelaufen. Kamera aus.',
  };
  const logic = new HandShortcut();
  let sequence, timer, watchdog;
  const present = () => !document.hidden && document.hasFocus();
  function onPresentedFrame(next) {
    return video.requestVideoFrameCallback((at, info) => {
      const isNew = Number.isSafeInteger(info.presentedFrames) && info.presentedFrames > sequence;
      if (isNew) sequence = info.presentedFrames;
      // The result must still meet FRAME_DELAY after inference. Remove the
      // separate paint-frame queue rather than widening that safety budget.
      next(isNew ? at : NaN);
    });
  }
  function checkSession(now, presented = null) {
    if (!present()) { controller.stop(); return false; }
    if (logic.expire(now, presented)) { controller.stop('expired'); return false; }
    return true;
  }
  function repaint(state) {
    const message = text[state];
    if (status.textContent !== message) status.textContent = message;
    status.dataset.state = state;
  }
  const controller = cameraController({
    openCamera: async (constraints) => {
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      const tracks = stream.getTracks();
      if (tracks.length === 1 && tracks[0].kind === 'video') return stream;
      tracks.forEach((track) => track.stop());
      throw new Error('Expected one video track');
    },
    openModel: () => loadHandModel(panel.dataset.runtime, {requireSimd: true}),
    active: present,
    attach: (stream) => {
      sequence = -1;
      return playHandPreview(video, stream);
    },
    detach: () => {
      video.pause();
      video.srcObject = null;
    },
    schedule: onPresentedFrame,
    cancel: (id) => video.cancelVideoFrameCallback(id),
    onState: (state) => {
      const live = state === 'live';
      const active = live || ['loading', 'permission'].includes(state);
      start.disabled = active;
      arm.disabled = !live;
      stopButton.disabled = !active;
      select.disabled = !live && active;
      preview.hidden = !live;
      if (!live) logic.stop();
      clearTimeout(timer);
      clearInterval(watchdog);
      // The heartbeat still disarms/expires when a live stream stops
      // presenting frames; it never performs inference or navigation.
      if (live) {
        timer = setTimeout(() => controller.stop('expired'), 30000);
        watchdog = setInterval(() => {
          if (checkSession(performance.now()) && logic.state !== 'inactive') repaint(logic.state);
        }, 50);
      }
      repaint(text[state] ? state : 'ended');
    },
    update: (model, at) => {
      const now = performance.now();
      if (!checkSession(now, at) || logic.state === 'inactive') return;
      if (Number.isFinite(at) && at <= now && now - at <= FRAME_DELAY && video.readyState >= 2) {
        const result = readResult(model.detectForVideo(video, at));
        if (!present()) { controller.stop(); return; }
        const destination = logic.observe(result, at, performance.now());
        if (destination) {
          const expected = destinations.get(destination);
          controller.stop();
          if (expected.anchor.isConnected && expected.anchor.href === expected.url) window.location.assign(expected.url);
          return;
        }
      }
      if (logic.state !== 'inactive') repaint(logic.state);
    },
  });
  repaint('ended');
  start.disabled = false;
  start.addEventListener('click', (event) => { if (event.isTrusted) controller.start(); });
  arm.addEventListener('click', (event) => {
    if (!event.isTrusted || !controller.live() || !present() || !logic.arm(select.value, performance.now())) return;
    arm.disabled = select.disabled = true;
    repaint('release');
  });
  stopButton.addEventListener('click', () => controller.stop());
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') controller.stop(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) controller.stop(); });
  window.addEventListener('pagehide', () => controller.stop());
  window.addEventListener('blur', () => controller.stop());
}
