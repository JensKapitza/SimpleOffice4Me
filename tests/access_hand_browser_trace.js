async ({runtime, logic, halfResolution}) => {
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
  // Candidate is isolated to this fake-camera test context; the very same
  // runtime/model and real camera frames reach the unchanged app/reducer.
  const scaled = halfResolution ? document.createElement('canvas') : null;
  const context = scaled?.getContext('2d', {alpha:false});
  if (scaled && !context) throw new Error('Fixture context unavailable');
  const capture = [video.videoWidth, video.videoHeight];
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
    let result, inferenceStart = entered;
    try {
      let image = frame;
      if (scaled) {
        const width = Math.floor(video.videoWidth / 2), height = Math.floor(video.videoHeight / 2);
        if (!width || !height) throw new Error('Synthetic stream has no decoded size');
        if (scaled.width !== width) scaled.width = width;
        if (scaled.height !== height) scaled.height = height;
        context.drawImage(video, 0, 0, width, height);
        image = scaled;
      }
      inferenceStart = performance.now();
      result = Reflect.apply(original, this, [image, timestamp, ...rest]);
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
        signs.has(observation.gesture) ? observation.gesture : null,
        round(inferenceStart - entered), round(returned - inferenceStart)]);
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
    return {counts, phases, presented, detections, halfResolution,
      capture, phase: phase(), visibility: document.visibilityState,
      focused: document.hasFocus(), tracks: window.handTestTracks?.map((t) => t.kind + ':' + t.readyState) ?? []};
  };
  window.addEventListener('pagehide', () => {
    console.info('HAND_NAV_FIXTURE_TRACE', JSON.stringify(window.handTestTrace()));
  }, {once:true});
  setTimeout(stop, maxMs);
}
