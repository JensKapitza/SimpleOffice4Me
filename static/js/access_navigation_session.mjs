import {AccessSelection, CALIBRATION, TARGETS, irisFeature} from './access_navigation_logic.mjs';
import {cameraController} from './kids_hand_camera.mjs';
import {readResult} from './kids_hand_logic.mjs';

// Deliberately not bound by the server-rendered prototype. A future vetted
// local adapter must produce BOTH outputs from the same newly presented frame.
export function decodeFrame(face, hands, aspect, capturedAt) {
  const hand = readResult(hands);
  return {gaze: irisFeature(face?.faceLandmarks, aspect),
    hand: hand.signal === 'recognized' ? hand.gesture : null, capturedAt};
}

export function bindAccessNavigation(root, {window: win, document: doc, openCamera, openModel, now}) {
  if (!openCamera || !openModel || !now || win.top !== win || !win.isSecureContext || doc.hidden) return null;
  const get = (name) => root.querySelector(`[data-access-${name}]`);
  const [start, capture, pause, end, status, cue, marker, video] =
    ['start', 'capture', 'pause', 'stop', 'status', 'cue', 'marker', 'video'].map(get);
  if (![start, capture, pause, end, status, cue, marker, video].every(Boolean) ||
      typeof video.requestVideoFrameCallback !== 'function' ||
      typeof video.cancelVideoFrameCallback !== 'function') return null;
  const targets = new Map();
  for (const id of TARGETS) {
    const anchor = root.querySelector(`a[data-access-target="${id}"]`);
    if (!anchor) return null;
    const url = new URL(anchor.href, win.location.href);
    if (url.origin !== win.location.origin || url.username || url.password || url.search || url.hash) return null;
    targets.set(id, {anchor, href: url.href});
  }
  const selection = new AccessSelection();
  const language = root.dataset.language;
  const text = language === 'en' ? {
    inactive: 'Gaze control off.', paused: 'Paused. Camera off.', calibrating: 'Calibration: look at the dot and measure.',
    steady: 'Hold still and keep both eyes visible.', recalibrate: 'Check point did not match. Repeat calibration.',
    ready: 'Check passed. Open your hand and then show two fingers (Focus Seal).',
    armed: 'Gaze control active. Hold your gaze and open your hand, then make a fist (Click Seal).',
    target: 'Current target:', activated: 'Gaze ninja: Hocus-pocus, focus!',
    loading: 'Loading local models…', permission: 'Allow camera only.',
    denied: 'Camera permission denied. Gaze control off.', error: 'Camera or local model stopped. Gaze control off.',
  } : {
    inactive: 'Blicksteuerung aus.', paused: 'Pausiert. Kamera aus.', calibrating: 'Kalibrierung: Schau zum Punkt und starte die Messung.',
    steady: 'Halte still und lass beide Augen sichtbar.', recalibrate: 'Der Kontrollpunkt passt nicht. Kalibriere erneut.',
    ready: 'Kontrolle bestanden. Öffne die Hand und zeige dann zwei Finger (Fokus-Siegel).',
    armed: 'Blicksteuerung aktiv. Sieh das Ziel an und öffne die Hand. Mach dann eine Faust (Klick-Siegel).',
    target: 'Aktuelles Ziel:', activated: 'Blick-Ninja: Hokuspokus Fokus!',
    loading: 'Lokale Modelle werden geladen …', permission: 'Erlaube nur die Kamera.',
    denied: 'Kamerazugriff abgelehnt. Blicksteuerung aus.', error: 'Kamera oder lokales Modell gestoppt. Blicksteuerung aus.',
  };
  let cueTimer, note, frameHandle, pendingFrame, lastFrame, streamEpoch = 0;

  function watchFrames(epoch) {
    frameHandle = video.requestVideoFrameCallback((presentedAt, metadata) => {
      if (epoch !== streamEpoch) return;
      if (Number.isFinite(presentedAt) && Number.isSafeInteger(metadata?.presentedFrames) &&
          (lastFrame === undefined || metadata.presentedFrames > lastFrame)) {
        lastFrame = metadata.presentedFrames;
        pendingFrame = presentedAt;
      }
      watchFrames(epoch);
    });
  }

  function detach() {
    streamEpoch += 1;
    if (frameHandle !== undefined) video.cancelVideoFrameCallback(frameHandle);
    frameHandle = pendingFrame = lastFrame = undefined;
    video.pause();
    video.srcObject = null;
  }
  function hideCue() { win.clearTimeout(cueTimer); cue.hidden = true; cue.textContent = ''; }
  function paint() {
    const {state, target} = selection;
    const calibrationPoint = state === 'calibrating' && CALIBRATION[selection.step];
    capture.hidden = capture.disabled = !calibrationPoint;
    marker.hidden = !calibrationPoint;
    if (calibrationPoint) {
      marker.style.position = 'fixed';
      marker.style.left = `${calibrationPoint[0] * 100}%`;
      marker.style.top = `${calibrationPoint[1] * 100}%`;
      marker.style.transform = 'translate(-50%, -50%)';
      marker.style.pointerEvents = 'none';
    }
    const selected = target && targets.get(target).anchor;
    const message = (note || text[state]) + (selected ? ` ${text.target} ${selected.textContent.trim()}.` : '');
    if (status.textContent !== message) status.textContent = message;
    for (const [id, {anchor}] of targets) {
      anchor.classList.toggle('border-3', target === id);
      anchor.classList.toggle('text-decoration-underline', target === id);
    }
  }
  function targetAt([x, y]) {
    const px = x * win.innerWidth, py = y * win.innerHeight;
    const matches = [];
    for (const [id, {anchor, href}] of targets) {
      if (!anchor.isConnected || anchor.href !== href || anchor.getAttribute('aria-disabled') === 'true') continue;
      const box = anchor.getBoundingClientRect();
      // Avoid border ambiguity and do not select a covered or clipped target.
      if (px > box.left + 6 && px < box.right - 6 && py > box.top + 6 && py < box.bottom - 6 &&
          anchor.contains(doc.elementFromPoint(px, py))) matches.push(id);
    }
    return matches.length === 1 ? matches[0] : null;
  }
  function stop(reason = 'stopped') { controller.stop(reason); }
  const controller = cameraController({
    openCamera, openModel,
    active: () => !doc.hidden && doc.hasFocus() && win.top === win && win.isSecureContext,
    attach: async (stream) => {
      const epoch = ++streamEpoch;
      video.srcObject = stream;
      await video.play();
      if (epoch === streamEpoch) watchFrames(epoch);
    },
    detach,
    schedule: (fn) => win.requestAnimationFrame(fn),
    cancel: (id) => win.cancelAnimationFrame(id),
    onState: (state) => {
      if (state === 'live') selection.startCalibration();
      else selection.stop(state === 'paused' ? 'paused' : 'inactive');
      note = ['loading', 'permission', 'denied'].includes(state) ? text[state] : state.endsWith('_error') ? text.error : '';
      if (state !== 'live') hideCue();
      const pending = ['loading', 'permission', 'live'].includes(state);
      start.disabled = pending;
      pause.hidden = pause.disabled = !pending;
      end.hidden = end.disabled = !pending;
      paint();
    },
    update: (model) => {
      if (doc.hidden || !doc.hasFocus()) { stop(); return; }
      const t = now();
      selection.expire(t);
      const capturedAt = pendingFrame;
      pendingFrame = undefined;
      if (video.readyState < 2 || capturedAt === undefined || t - capturedAt > 130 || t < capturedAt) {
        paint();
        return;
      }
      // Future real adapter must run both models on one frozen in-memory frame.
      const frame = {...model.sample(video, capturedAt), capturedAt};
      if (doc.hidden || !doc.hasFocus()) { stop(); return; }
      const result = selection.observe(frame, now(), targetAt);
      if (result === 'steady' || result === 'recalibrate') note = text[result];
      if (['next', 'ready', 'armed'].includes(result)) note = '';
      if (result === 'armed') {
        cue.textContent = text.activated;
        cue.hidden = false;
        cueTimer = win.setTimeout(hideCue, 2800);
      }
      if (TARGETS.includes(result)) {
        const {href} = targets.get(result);
        // Stop before a synchronous, explicit app navigation. Never queue input or click a DOM element.
        stop();
        win.location.assign(href);
        return;
      }
      paint();
    },
  });
  const cleanup = () => stop();
  const onVisibility = () => { if (doc.hidden) stop(); };
  const onKey = (event) => { if (event.key === 'Escape') stop(); };
  win.addEventListener('pagehide', cleanup);
  win.addEventListener('blur', cleanup);
  win.addEventListener('resize', cleanup);
  win.addEventListener('scroll', cleanup, {passive: true});
  doc.addEventListener('visibilitychange', onVisibility);
  doc.addEventListener('keydown', onKey);
  start.addEventListener('click', (event) => { if (event.isTrusted) controller.start(); });
  capture.addEventListener('click', (event) => {
    if (!event.isTrusted) return;
    selection.capture();
    note = '';
    paint();
  });
  pause.addEventListener('click', () => stop('paused'));
  end.addEventListener('click', cleanup);
  start.disabled = false;
  return {stop};
}
