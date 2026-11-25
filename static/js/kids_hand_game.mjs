import {cameraController} from './kids_hand_camera.mjs';
import {Duel, HoldFilter, POSES, ROUND_MS, readResult} from './kids_hand_logic.mjs';
import {loadHandModel} from './kids_hand_model.mjs';

const SKELETON = [[0,1,2,3,4], [0,5,6,7,8], [5,9,10,11,12], [9,13,14,15,16], [13,17,18,19,20], [17,0]];
const TEXT = {
  en: {
    loading: 'Loading the model from this SimpleOffice server…',
    permission: 'Allow only the camera in the browser prompt.',
    live: 'Camera on. The preview and detection stay in this browser.',
    denied: 'Camera access was denied. You can allow it in the browser and try again.',
    camera_error: 'No camera image. Check that your webcam is connected and not in use, then try again.',
    inference_error: 'Hand detection stopped and the camera is off. You can try again.',
    model_error: 'The model could not start. Check local setup and use a current desktop browser.',
    insecure: 'A webcam requires HTTPS or localhost in a current desktop browser.',
    stopped: 'Camera off.',
    none: 'Show one hand.',
    multiple: 'Only one hand at a time.',
    uncertain: 'Try holding your hand still in good light.',
    release: 'Great! Lower your hand briefly before repeating.',
    next: 'Great! Now try the next shape.',
    wrong: 'Try the highlighted shape.',
    repeat: 'Lift your hand out of the frame briefly, then show the same shape again.',
    practice: 'No-pressure practice',
    player: 'Player',
    seconds: 'seconds remaining',
    waiting: 'Ready for',
    roundFinished: 'Turn complete!',
    timeout: 'Time is up!',
    winner: 'wins!',
    tie: 'Tie! Both played just as well.',
    done: 'Duel finished',
    go: 'Start turn:',
  },
  de: {
    loading: 'Das Modell wird von diesem SimpleOffice-Server geladen …',
    permission: 'Erlaube im Browser nur die Kamera.',
    live: 'Kamera an. Vorschau und Erkennung bleiben in diesem Browser.',
    denied: 'Die Kamera wurde nicht erlaubt. Erlaube sie im Browser und versuche es erneut.',
    camera_error: 'Kein Kamerabild. Prüfe Anschluss und ob die Webcam schon benutzt wird. Versuche es dann erneut.',
    inference_error: 'Die Handerkennung wurde beendet. Die Kamera ist aus. Du kannst es erneut versuchen.',
    model_error: 'Das Modell konnte nicht starten. Prüfe die lokale Einrichtung und nutze einen aktuellen PC-Browser.',
    insecure: 'Für die Webcam brauchst du HTTPS oder localhost und einen aktuellen PC-Browser.',
    stopped: 'Kamera aus.',
    none: 'Zeige eine Hand.',
    multiple: 'Immer nur eine Hand zeigen.',
    uncertain: 'Halte deine Hand ruhig und achte auf gutes Licht.',
    release: 'Gut gemacht! Nimm die Hand vor der Wiederholung kurz aus dem Bild.',
    next: 'Gut gemacht! Jetzt kommt das nächste Zeichen.',
    wrong: 'Versuche das markierte Zeichen.',
    repeat: 'Nimm die Hand kurz aus dem Bild und zeige das gleiche Zeichen nochmal.',
    practice: 'Üben ohne Zeitdruck',
    player: 'Spieler',
    seconds: 'Sekunden übrig',
    waiting: 'Bereit für',
    roundFinished: 'Zug geschafft!',
    timeout: 'Zeit um!',
    winner: 'gewinnt!',
    tie: 'Unentschieden! Ihr wart gleich gut.',
    done: 'Duell beendet',
    go: 'Zug starten:',
  },
};

function draw(canvas, video, points) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  const {videoWidth: width, videoHeight: height} = video;
  if (width && height && (canvas.width !== width || canvas.height !== height)) {
    canvas.width = width;
    canvas.height = height;
  }
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!points || !width || !height) return;
  ctx.strokeStyle = '#b6ffe9';
  ctx.fillStyle = '#fff9ac';
  ctx.lineWidth = 3;
  for (const chain of SKELETON) {
    ctx.beginPath();
    chain.forEach((id, index) => {
      const {x, y} = points[id];
      if (!index) ctx.moveTo(x * width, y * height);
      else ctx.lineTo(x * width, y * height);
    });
    ctx.stroke();
  }
  for (const point of points) {
    ctx.beginPath();
    ctx.arc(point.x * width, point.y * height, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}

function boot(root) {
  const get = (name) => root.querySelector(`[data-hand-${name}]`);
  const lang = root.dataset.language === 'en' ? 'en' : 'de';
  const text = TEXT[lang], ready = root.dataset.ready === 'yes';
  const on = get('camera'), off = get('stop'), video = get('video'), overlay = get('overlay');
  const preview = get('preview'), mirror = get('mirror'), signal = get('signal');
  const duelButton = get('duel'), roundStart = get('round-start'), back = get('back-practice');
  const nextButton = get('next'), feedback = get('feedback');
  const names = Object.keys(POSES);
  const hold = new HoldFilter();
  let index = 0, duel = null, videoTime = -1, lastInference = -Infinity;

  const showFeedback = (message = '', won = false) => {
    if (feedback.textContent !== message) feedback.textContent = message;
    get('target').dataset.win = won ? 'true' : 'false';
  };
  const player = (n) => `${text.player} ${n}`;
  const showPose = (name) => {
    const pose = POSES[name];
    get('icon').textContent = pose?.icon || '🏁';
    get('pose').textContent = pose?.hand || '';
    get('title').textContent = pose?.[lang][0] || text.done;
    get('description').textContent = pose?.[lang][1] || '';
  };
  const steps = () => {
    get('steps').replaceChildren(...duel.order.map((name, i) => {
      const step = document.createElement('li');
      step.textContent = POSES[name].icon;
      step.setAttribute('aria-label', POSES[name][lang][0]);
      if (duel.started !== null && i === duel.index) step.setAttribute('aria-current', 'step');
      if (i < duel.index) {
        step.dataset.complete = 'true';
        step.textContent += '✓';
      }
      return step;
    }));
  };
  const timeLabel = (now) => {
    if (!duel) return;
    const label = `${Math.ceil(duel.remaining(now) / 1000)} ${text.seconds}`;
    if (get('clock').textContent !== label) get('clock').textContent = label;
  };
  const showResults = () => {
    const area = get('results');
    area.hidden = !duel?.results.length;
    if (area.hidden) return;
    const list = get('score-list');
    list.replaceChildren(...duel.results.map((row) => {
      const item = document.createElement('li');
      const seconds = (row.ms / 1000).toLocaleString(lang, {maximumFractionDigits: 2});
      item.textContent = `${player(row.player)}: ${row.hits}/${duel.order.length} ${lang === 'en' ? 'signs' : 'Zeichen'} · ${row.complete ? '🏁 1/1' : '🏁 0/1'} · ${seconds} s`;
      return item;
    }));
    const winner = duel.winner();
    get('summary').textContent = !duel.finished ? text.roundFinished : winner ? `${player(winner)} ${text.winner}` : text.tie;
  };

  function render() {
    const playing = duel?.started != null;
    const live = camera.live();
    get('round').hidden = !duel;
    back.hidden = !duel;
    nextButton.hidden = Boolean(duel);
    duelButton.hidden = Boolean(duel && !duel.finished);
    duelButton.disabled = !live;
    roundStart.hidden = !duel || playing || duel.finished;
    roundStart.disabled = !live;
    if (duel) {
      get('phase').textContent = duel.finished ? text.done : playing ? player(duel.player) : `${text.waiting} ${player(duel.player)}`;
      if (!roundStart.hidden) roundStart.textContent = `${text.go} ${player(duel.player)}`;
      if (playing) timeLabel(performance.now());
      else get('clock').textContent = `${ROUND_MS / 1000} s`;
      steps();
      showPose(duel.target);
    } else {
      get('phase').textContent = text.practice;
      showPose(names[index]);
    }
    showResults();
  }

  function onFrame(model, now) {
    // The clock doesn't wait for a new frame. A frozen webcam cannot extend turns.
    const timed = duel?.update(null, now);
    if (timed === 'timeout') {
      hold.reset();
      render();
      showFeedback(text.timeout);
      (roundStart.hidden ? duelButton : roundStart).focus();
    }
    timeLabel(now);
    if (video.readyState < 2 || video.currentTime === videoTime || now - lastInference < 65) return;
    videoTime = video.currentTime;
    lastInference = now;
    const result = readResult(model.detectForVideo(video, now));
    draw(overlay, video, result.points);
    // Hand selector is not required: world-point geometry accepts either hand;
    // CSS mirrors video and overlay together without changing inferred geometry.
    signal.textContent = result.gesture ? `${POSES[result.gesture].icon} ${POSES[result.gesture][lang][0]}` : text[result.signal];
    const observed = hold.observe(result, now);
    if (!observed || observed === 'released') return;
    if (!duel) {
      if (observed === names[index]) showFeedback(text.release, true);
      else showFeedback(text.wrong);
      return;
    }
    const current = duel.target;
    const hit = duel.update(observed, now);
    if (hit === 'complete') {
      hold.reset();
      render();
      showFeedback(text.roundFinished, true);
      (roundStart.hidden ? duelButton : roundStart).focus();
    } else if (hit === 'correct') {
      render();
      showFeedback(duel.target === current ? text.repeat : text.next, true);
    } else if (hit === 'wrong') showFeedback(text.wrong);
  }

  const camera = cameraController({
    openCamera: (constraints) => navigator.mediaDevices.getUserMedia(constraints),
    openModel: () => loadHandModel(root.dataset.runtime),
    active: () => !document.hidden && root.isConnected,
    attach: async (stream) => { video.srcObject = stream; await video.play(); },
    detach: () => {
      video.pause();
      video.srcObject = null;
      draw(overlay, video, null);
      videoTime = -1;
      lastInference = -Infinity;
    },
    schedule: (callback) => requestAnimationFrame(callback),
    cancel: (id) => cancelAnimationFrame(id),
    update: onFrame,
    onState: (state) => {
      const busy = state === 'loading' || state === 'permission';
      const live = state === 'live';
      on.disabled = busy || live || !ready;
      off.hidden = !busy && !live;
      preview.hidden = !live;
      get('mirror-wrap').hidden = !live;
      get('camera-status').textContent = text[state] || '';
      if (!busy) {
        hold.reset();
        if (!live && !duel?.finished) duel = null;
        if (!live) showFeedback();
        signal.textContent = text.none;
        render();
      }
    },
  });

  preview.dataset.mirrored = String(mirror.checked);
  mirror.addEventListener('change', () => { preview.dataset.mirrored = String(mirror.checked); });
  on.addEventListener('click', () => {
    if (!ready) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.WebAssembly) {
      get('camera-status').textContent = text.insecure;
      return;
    }
    void camera.start();
  });
  off.addEventListener('click', () => { camera.stop(); on.focus(); });
  nextButton.addEventListener('click', () => {
    index = (index + 1) % names.length;
    showFeedback();
    render();
  });
  duelButton.addEventListener('click', () => {
    if (!camera.live()) return;
    duel = new Duel();
    hold.reset();
    showFeedback();
    render();
    roundStart.focus();
  });
  roundStart.addEventListener('click', () => {
    if (!camera.live() || !duel?.start(performance.now())) return;
    hold.reset();
    showFeedback();
    render();
  });
  back.addEventListener('click', () => {
    duel = null;
    hold.reset();
    showFeedback();
    render();
    nextButton.focus();
  });

  window.addEventListener('pagehide', () => camera.stop());
  document.addEventListener('visibilitychange', () => { if (document.hidden) camera.stop(); });
  document.addEventListener('click', (event) => {
    const link = event.target.closest?.('a[href]');
    if (link && !link.getAttribute('href').startsWith('#') && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey &&
        (link.target === '' || link.target === '_self')) camera.stop();
  });
  render();
  // The server initially disables the control so an early tap cannot be lost
  // while the module graph is still arriving. Enable only after handlers exist.
  on.disabled = !ready;
}

const root = document.querySelector('[data-hand-game]');
if (root) boot(root);
