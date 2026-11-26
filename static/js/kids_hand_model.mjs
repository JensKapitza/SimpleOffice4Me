// Only staged Navigation Assistance opts in when both extra local assets
// passed server-side integrity checks; the Kids runtime selection stays fixed.
export async function handWasmStem(preferSimd, filesetResolver) {
  if (preferSimd && typeof filesetResolver?.isSimdSupported === 'function') {
    try {
      if (await filesetResolver.isSimdSupported()) return 'vision_wasm_internal';
    } catch { /* compiler unavailable under this browser or CSP */ }
  }
  return 'vision_wasm_nosimd_internal';
}

// No CDN or model downloader. All paths derive from the authenticated asset
// route delivered by the same-origin Kids page and are rechecked here.
export async function loadHandModel(runtimeHref, {preferSimd = false} = {}) {
  const runtime = new URL(runtimeHref, window.location.href);
  if (runtime.origin !== window.location.origin || !runtime.pathname.endsWith('/vision_bundle.mjs') ||
      runtime.search || runtime.hash || runtime.username || runtime.password) throw new Error('invalid local hand runtime');
  const asset = (name) => new URL(name, runtime).href;
  const {HandLandmarker, FilesetResolver} = await import(runtime.href);
  const stem = await handWasmStem(preferSimd, FilesetResolver);
  return HandLandmarker.createFromOptions({
    wasmLoaderPath: asset(stem + '.js'),
    wasmBinaryPath: asset(stem + '.wasm'),
  }, {
    baseOptions: {modelAssetPath: asset('hand_landmarker.task'), delegate: 'CPU'},
    runningMode: 'VIDEO',
    numHands: 2,
    minHandDetectionConfidence: 0.65,
    minHandPresenceConfidence: 0.65,
    minTrackingConfidence: 0.65,
  });
}
