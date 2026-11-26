// The staged shortcut refuses a slower fallback; Fingerzauber still
// always selects the portable runtime without a browser capability probe.
export async function handWasmStem(requireSimd, filesetResolver) {
  if (!requireSimd) return 'vision_wasm_nosimd_internal';
  if (await filesetResolver?.isSimdSupported?.() === true) return 'vision_wasm_internal';
  throw new Error('Hand navigation requires WebAssembly SIMD');
}

// No CDN or model downloader. All paths derive from the authenticated asset
// route delivered by the same-origin Kids page and are rechecked here.
export async function loadHandModel(runtimeHref, {requireSimd = false} = {}) {
  const runtime = new URL(runtimeHref, window.location.href);
  if (runtime.origin !== window.location.origin || !runtime.pathname.endsWith('/vision_bundle.mjs') ||
      runtime.search || runtime.hash || runtime.username || runtime.password) throw new Error('invalid local hand runtime');
  const asset = (name) => new URL(name, runtime).href;
  const {HandLandmarker, FilesetResolver} = await import(runtime.href);
  const stem = await handWasmStem(requireSimd, FilesetResolver);
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
