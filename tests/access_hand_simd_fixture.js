async ({runtime, logic, variant}) => {
  // The only input is the explicit generated CI fixture. Do not alter the
  // camera, model/task settings, classification or application timing gates.
  const [{HandLandmarker, FilesetResolver}, {readResult}] = await Promise.all([import(runtime), import(logic)]);
  const base = new URL('.', runtime);
  if (base.origin !== location.origin || !['baseline', 'simd'].includes(variant)) throw new Error('bad comparison setup');
  const supported = await FilesetResolver.isSimdSupported();
  if (variant === 'simd' && !supported) throw new Error('Pinned MediaPipe SIMD probe failed under page CSP');
  const video = document.querySelector('[data-hand-nav-video]');
  const status = document.querySelector('[data-hand-nav-status]');
  const create = HandLandmarker.createFromOptions;
  const detect = HandLandmarker.prototype.detectForVideo;
  const selected = variant === 'simd' ? 'vision_wasm_internal' : 'vision_wasm_nosimd_internal';
  const stem = (extension) => new URL(selected + extension, base).href;
  const seen = [], notices = [];
  let starts = 0, callbackTime, presentedTime;
  const register = video.requestVideoFrameCallback.bind(video);
  video.requestVideoFrameCallback = (callback) => register((at, info) => {
    callbackTime = at;
    presentedTime = info.presentationTime;
    callback(at, info);
  });
  const began = performance.now();
  const n = (value) => Math.round(value * 10) / 10;
  const phases = new Set(['live','release','armed','expired','ended','inference_error']);
  const known = new Set(['none','uncertain','multiple','recognized']);
  HandLandmarker.createFromOptions = function(files, options) {
    starts++;
    if (files.wasmLoaderPath !== new URL('vision_wasm_nosimd_internal.js', base).href ||
        files.wasmBinaryPath !== new URL('vision_wasm_nosimd_internal.wasm', base).href ||
        options.baseOptions?.modelAssetPath !== new URL('hand_landmarker.task', base).href ||
        options.baseOptions.delegate !== 'CPU' || options.numHands !== 2 || options.runningMode !== 'VIDEO') {
      throw new Error('Unexpected production hand settings');
    }
    return create.call(this, {...files, wasmLoaderPath:stem('.js'), wasmBinaryPath:stem('.wasm')}, options);
  };
  HandLandmarker.prototype.detectForVideo = function(frame, at, ...rest) {
    if (frame !== video) throw new Error('Unexpected non-video inference');
    const entered = performance.now();
    const result = detect.call(this, frame, at, ...rest);
    const returned = performance.now();
    const parsed = readResult(result);
    if (seen.length < 160) seen.push([n(at - began), n(returned - at), n(returned - entered),
      phases.has(status.dataset.state) ? status.dataset.state : 'other',
      known.has(parsed.signal) ? parsed.signal : 'other', parsed.gesture === 'spark',
      at === callbackTime && Number.isFinite(presentedTime) ? n(returned - presentedTime) : null]);
    return result;
  };
  const observer = new MutationObserver(() => {
    if (notices.length < 24 && phases.has(status.dataset.state) && notices.at(-1)?.[1] !== status.dataset.state) {
      notices.push([n(performance.now() - began), status.dataset.state]);
    }
  });
  observer.observe(status, {attributes:true,attributeFilter:['data-state']});
  window.handSimdFixtureTrace = () => ({variant, supported, selected, starts,
    dimensions:[video.videoWidth, video.videoHeight], seen, notices});
  return supported;
}
