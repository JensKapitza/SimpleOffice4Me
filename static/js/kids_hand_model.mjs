// No CDN or model downloader. All paths derive from the authenticated asset
// route delivered by the same-origin Kids page and are rechecked here.
export async function loadHandModel(runtimeHref) {
  const runtime = new URL(runtimeHref, window.location.href);
  if (runtime.origin !== window.location.origin || !runtime.pathname.endsWith('/vision_bundle.mjs') ||
      runtime.search || runtime.hash || runtime.username || runtime.password) throw new Error('invalid local hand runtime');
  const asset = (name) => new URL(name, runtime).href;
  const {HandLandmarker} = await import(runtime.href);
  return HandLandmarker.createFromOptions({
    wasmLoaderPath: asset('vision_wasm_nosimd_internal.js'),
    wasmBinaryPath: asset('vision_wasm_nosimd_internal.wasm'),
  }, {
    baseOptions: {modelAssetPath: asset('hand_landmarker.task'), delegate: 'CPU'},
    runningMode: 'VIDEO',
    numHands: 2,
    minHandDetectionConfidence: 0.65,
    minHandPresenceConfidence: 0.65,
    minTrackingConfidence: 0.65,
  });
}
