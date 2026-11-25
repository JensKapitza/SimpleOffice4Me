// Camera lifecycle independent from gesture/duel logic. Every await has a
// generation check so a stale grant cannot revive a stopped or departed page.
export function cameraController({openCamera, openModel, attach, detach, active, update, onState, schedule, cancel}) {
  let version = 0, stream = null, model = null, scheduled = null, state = 'idle';
  const stopTracks = (input) => input?.getTracks().forEach((track) => track.stop());
  const close = (input) => { try { input?.close(); } catch { /* tracks already stopped */ } };

  function stop(reason = 'stopped') {
    version += 1;
    if (scheduled !== null) cancel(scheduled);
    scheduled = null;
    stopTracks(stream);
    stream = null;
    detach();
    close(model);
    model = null;
    state = 'idle';
    onState(reason);
  }

  function tick(time) {
    scheduled = null;
    if (state !== 'live') return;
    try {
      update(model, time);
    } catch {
      stop('inference_error');
      return;
    }
    if (state === 'live') scheduled = schedule(tick);
  }

  async function start() {
    if (state !== 'idle' || !active()) return;
    state = 'loading';
    const current = ++version;
    const fresh = () => current === version && active();
    onState('loading');
    try {
      const detector = await openModel();
      if (!fresh()) { close(detector); return; }
      model = detector;
    } catch {
      if (fresh()) stop('model_error');
      return;
    }
    onState('permission');
    try {
      const input = await openCamera({video: {width: {ideal: 640}, height: {ideal: 480}}, audio: false});
      if (!fresh()) { stopTracks(input); return; }
      stream = input;
      const tracks = input.getVideoTracks();
      if (tracks.length !== 1 || tracks[0].readyState !== 'live') {
        stop('camera_error');
        return;
      }
      tracks[0].addEventListener('ended', () => { if (fresh()) stop('camera_error'); }, {once: true});
      await attach(input);
      if (!fresh()) return;
      state = 'live';
      onState('live');
      scheduled = schedule(tick);
    } catch (error) {
      if (fresh()) stop(error?.name === 'NotAllowedError' ? 'denied' : 'camera_error');
    }
  }

  return {start, stop, live: () => state === 'live'};
}
