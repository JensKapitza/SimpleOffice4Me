/* Optional, local and deliberately silent input for syllable clapping. */
((scope) => {
  'use strict';
  const IDLE_MS = 30000;
  const WINDOW_MS = 32;

  function createSampler({openStream, makeContext, schedule, cancel, clock, active, onState, onHit, detector}) {
    let generation = 0, owned = null;
    const stopTracks = (stream) => stream?.getTracks().forEach(track => track.stop());
    function stop(reason = 'paused') {
      generation += 1;
      const session = owned;
      owned = null;
      if (!session) return false;
      if (session.frame !== null) cancel(session.frame);
      stopTracks(session.stream);
      try { session.source?.disconnect(); } catch { /* closed */ }
      try { session.analyser?.disconnect(); } catch { /* closed */ }
      try { Promise.resolve(session.context?.close()).catch(() => {}); } catch { /* closed */ }
      session.samples?.fill(0);
      onState(reason);
      return true;
    }
    async function start(mode) {
      if (owned || !active()) return;
      const version = ++generation;
      const session = {frame: null, state: 'permission'};
      const fresh = () => generation === version && owned === session && active();
      owned = session;
      onState('permission');
      try {
        // Resume under the explicit click while permission is still pending.
        const context = makeContext();
        session.context = context;
        const resumed = Promise.resolve(context.resume()).then(() => true, () => false);
        const stream = await openStream({video: false, audio: {
          echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: {ideal: 1},
        }});
        if (!fresh()) { stopTracks(stream); if (owned === session) stop(); return; }
        session.stream = stream;
        if (!await resumed || !fresh() || context.state !== 'running') {
          if (owned === session) stop('unavailable');
          return;
        }
        const tracks = stream.getAudioTracks();
        if (!tracks.length || stream.getVideoTracks().length || tracks.some(track => track.readyState !== 'live')) {
          stop('unavailable');
          return;
        }
        for (const track of tracks) track.addEventListener('ended', () => {
          if (fresh()) stop('unavailable');
        }, {once: true});
        const analyser = session.analyser = context.createAnalyser();
        analyser.fftSize = 2 ** Math.round(Math.log2(context.sampleRate * WINDOW_MS / 1000));
        const source = session.source = context.createMediaStreamSource(stream);
        const samples = session.samples = new Float32Array(analyser.fftSize);
        // Deliberately no connection to context.destination or an audio element.
        source.connect(analyser);
        const filter = detector(mode, context.sampleRate);
        let lastHit = clock(), lastRead = null;
        session.state = 'calibrating';
        onState(session.state);

        function inspect(now) {
          session.frame = null;
          if (!fresh()) { if (owned === session) stop(); return; }
          if (context.state !== 'running') { stop('unavailable'); return; }
          if (now - lastHit >= IDLE_MS) { stop('idle'); return; }
          if (lastRead === null || now - lastRead >= 14) {
            try {
              lastRead = now;
              analyser.getFloatTimeDomainData(samples);
              const outcome = filter.process(samples, now);
              if (outcome.state === 'noisy') { stop('noisy'); return; }
              if (outcome.state !== session.state) { session.state = outcome.state; onState(outcome.state); }
              if (outcome.hit && fresh()) { lastHit = now; onHit(); }
            } catch {
              stop('unavailable');
              return;
            }
          }
          if (fresh()) session.frame = schedule(inspect);
        }
        session.frame = schedule(inspect);
      } catch (error) {
        if (owned === session) stop(error?.name === 'NotAllowedError' || error?.name === 'SecurityError' ? 'denied' : 'unavailable');
      }
    }

    return {start, stop, running: () => owned !== null};
  }

  function mount(panel, {beforeStart, text}) {
    if (!panel || !scope.KidsSyllableSignal) return null;
    const find = (name) => panel.querySelector(`[data-syll-mic-${name}]`);
    const controls = [...panel.querySelectorAll('[data-syll-mic-mode]')];
    const status = find('status'), review = find('review'), countLabel = find('count'), targetLabel = find('target');
    const add = find('add'), subtract = find('subtract'), use = find('use'), pause = find('pause');
    const reasons = {
      permission: text('Bitte erlaube nur das Mikrofon.', 'Please allow the microphone only.'),
      calibrating: text('🤫 Einen Moment leise bleiben.', '🤫 Stay quiet for a moment.'),
      listening: text('🎙️ Mikrofon an. Jetzt bist du dran.', '🎙️ Microphone on. Your turn.'),
      paused: text('■ Mikrofon aus. Du kannst die Zahl prüfen oder wieder einschalten.', '■ Microphone off. Check the count or turn it on again.'),
      audio: text('🔊 Mikrofon aus. Nach dem Ton wieder selbst einschalten.', '🔊 Microphone off. Turn it on yourself after the sound.'),
      denied: text('Kein Mikrofonzugriff. Du kannst weiterhin auf die Hände tippen.', 'Microphone access denied. You can still tap the hands.'),
      unavailable: text('Mikrofon nicht verfügbar. Tippe stattdessen auf die Hände.', 'Microphone unavailable. Tap the hands instead.'),
      noisy: text('Zu laut zum Starten. Mikrofon aus. Versuche es in einem ruhigen Moment oder tippe auf die Hände.', 'Too noisy to start. Microphone off. Try when it is quiet or tap the hands.'),
      idle: text('■ Mikrofon nach einer Pause aus. Du kannst es wieder einschalten.', '■ Microphone turned off after a pause. You can turn it on again.'),
      wrong: text('Die Zahl passt noch nicht. Du kannst sie ändern oder nochmal hören.', 'That count does not match yet. Change it or listen again.'),
    };
    let task = null, mode = '', value = 0;
    const supported = () => scope.isSecureContext && !!scope.navigator.mediaDevices?.getUserMedia &&
      typeof (scope.AudioContext || scope.webkitAudioContext) === 'function';
    function render() {
      const live = sampler.running();
      panel.hidden = !task;
      review.hidden = !mode;
      pause.hidden = !live;
      countLabel.textContent = String(value);
      targetLabel.textContent = task ? String(task.target) : '';
      add.disabled = value >= scope.KidsSyllableSignal.LIMIT;
      subtract.disabled = value === 0;
      use.disabled = !task;
      for (const button of controls) button.setAttribute('aria-pressed', String(live && mode === button.dataset.syllMicMode));
    }
    function message(reason) {
      status.textContent = reasons[reason] || '';
      render();
    }
    function change(by) {
      value = Math.max(0, Math.min(scope.KidsSyllableSignal.LIMIT, value + by));
      render();
    }
    const sampler = createSampler({
      openStream: (constraints) => scope.navigator.mediaDevices.getUserMedia(constraints),
      makeContext: () => new (scope.AudioContext || scope.webkitAudioContext)(),
      schedule: (callback) => scope.requestAnimationFrame(callback),
      cancel: (id) => scope.cancelAnimationFrame(id),
      clock: () => scope.performance.now(),
      active: () => !!task && task.valid() && panel.open && !document.hidden,
      onState: message,
      onHit: () => change(1),
      detector: scope.KidsSyllableSignal.createDetector,
    });

    function setTask(replacement) {
      sampler.stop();
      task = replacement;
      mode = '';
      value = 0;
      message('');
    }
    for (const button of controls) button.addEventListener('click', () => {
      if (!task?.valid()) return;
      const selected = button.dataset.syllMicMode;
      if (sampler.running() && mode === selected) { sampler.stop(); return; }
      sampler.stop();
      if (selected !== mode) value = 0;
      mode = selected;
      if (!supported()) { message('unavailable'); return; }
      beforeStart();
      void sampler.start(mode);
    });
    pause.addEventListener('click', () => sampler.stop());
    add.addEventListener('click', () => change(1));
    subtract.addEventListener('click', () => change(-1));
    use.addEventListener('click', () => {
      if (!task?.valid()) return;
      sampler.stop();
      if (value === task.target) task.complete();
      else message('wrong');
    });
    panel.addEventListener('toggle', () => { if (!panel.open) sampler.stop(); });
    scope.addEventListener('pagehide', () => sampler.stop());
    document.addEventListener('visibilitychange', () => { if (document.hidden) sampler.stop(); });
    document.addEventListener('click', (event) => {
      const link = event.target.closest?.('a[href]');
      if (link && !link.getAttribute('href').startsWith('#') && !event.defaultPrevented &&
          !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey &&
          (!link.target || link.target === '_self')) sampler.stop();
    });
    render();
    return {setTask, pauseForAudio: () => sampler.stop('audio'), pause: () => sampler.stop()};
  }

  const api = {createSampler, mount};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else scope.KidsSyllableMic = api;
})(typeof window === 'undefined' ? globalThis : window);
