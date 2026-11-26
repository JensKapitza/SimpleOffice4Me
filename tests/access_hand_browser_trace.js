async ({runtime, logic}) => {
  // Disposable test page, synthetic fixture only. Never imported by the app.
  const [{HandLandmarker}, {readResult}] = await Promise.all([import(runtime), import(logic)]);
  const video = document.querySelector('[data-hand-nav-video]');
  const status = document.querySelector('[data-hand-nav-status]');
  const prototype = HandLandmarker.prototype;
  const original = prototype.detectForVideo;
  if (!video || !status || typeof original !== 'function' || window.handTestTrace) {
    throw new Error('Cannot attach synthetic fixture trace');
  }
  const phaseNames = new Set(['live', 'release', 'armed', 'expired', 'ended', 'inference_error']);
  const signals = new Set(['none', 'multiple', 'uncertain', 'recognized']);
  const signs = new Set(['spark', 'crystal', 'star', 'shield']);
  const since = performance.now(), maxMs = 16000;
  const phase = () => phaseNames.has(status.dataset.state) ? status.dataset.state : 'other';
  const round = (n) => Number.isFinite(n) ? Math.round(n) : null;
  const relative = (n) => round(n - since);
  const presented = [], detections = [], phases = [];
  const counts = {frames: 0, calls: 0, errors: 0, traceErrors: 0};
  const push = (rows, row, limit = 160) => {
    if (rows.length >= limit) rows.shift();
    rows.push(row);
  };
  let stopped = false, id;
  function framePresented(now, data) {
    id = undefined;
    if (stopped || now - since > maxMs) return;
    counts.frames++;
    push(presented, [relative(now), round(data.mediaTime * 1000), relative(data.presentationTime),
      Number.isInteger(data.presentedFrames) ? data.presentedFrames : null]);
    id = video.requestVideoFrameCallback(framePresented);
  }
  function changed() {
    if (!stopped && performance.now() - since <= maxMs && phases.at(-1)?.[1] !== phase()) {
      push(phases, [relative(performance.now()), phase()], 24);
    }
  }
  function observed(frame, timestamp, ...rest) {
    const entered = performance.now();
    if (stopped || frame !== video || entered - since > maxMs) {
      return Reflect.apply(original, this, [frame, timestamp, ...rest]);
    }
    counts.calls++;
    const state = phase();
    let result;
    try {
      result = Reflect.apply(original, this, [frame, timestamp, ...rest]);
    } catch (error) {
      counts.errors++;
      push(detections, [relative(entered), relative(timestamp), null, null, state, 'exception', null]);
      throw error;
    }
    const returned = performance.now();
    try {
      const observation = readResult(result);
      push(detections, [relative(entered), relative(timestamp), round(entered - timestamp),
        round(returned - timestamp), state, signals.has(observation.signal) ? observation.signal : 'other',
        signs.has(observation.gesture) ? observation.gesture : null]);
    } catch { counts.traceErrors++; }
    return result;
  }
  prototype.detectForVideo = observed;
  if (prototype.detectForVideo !== observed) throw new Error('Detector observer did not attach');
  const observer = new MutationObserver(changed);
  observer.observe(status, {attributes: true, attributeFilter: ['data-state']});
  changed();
  id = video.requestVideoFrameCallback(framePresented);
  function stop() {
    if (stopped) return;
    stopped = true;
    observer.disconnect();
    if (id !== undefined) video.cancelVideoFrameCallback(id);
    if (prototype.detectForVideo === observed) prototype.detectForVideo = original;
  }
  window.handTestTrace = () => {
    stop();
    return {counts, phases, presented, detections, phase: phase(), visibility: document.visibilityState,
      focused: document.hasFocus(), tracks: window.handTestTracks?.map((t) => t.kind + ':' + t.readyState) ?? []};
  };
  setTimeout(stop, maxMs);
}
