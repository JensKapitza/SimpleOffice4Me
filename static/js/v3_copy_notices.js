/* Private, foreground-only refresh of local automation notifications. */
(() => {
  'use strict';
  const pane = document.querySelector('[data-copy-notice-feed]');
  if (!pane) return;
  const list = pane.querySelector('[data-copy-notice-list]');
  const announcement = pane.querySelector('[data-copy-notice-announcement]');
  if (!list || !announcement || !pane.dataset.endpoint) return;

  const REFRESH = 5000;
  const BACKOFF = 30000;
  const TIMEOUT = 10000;
  let visibleIds = Array.from(list.querySelectorAll('[data-notice-id]'), row => row.dataset.noticeId);
  let timer;
  let controller;
  let stopped = false;
  let active = true;

  const soundButton = pane.querySelector('[data-copy-notice-sound]');
  const soundStatus = pane.querySelector('[data-copy-notice-sound-status]');
  const Audio = window.AudioContext || window.webkitAudioContext;
  let audio;
  let armed = false;

  const showSoundState = message => {
    if (!soundButton || !soundStatus) return;
    soundButton.setAttribute('aria-pressed', String(armed));
    soundStatus.textContent = message || '';
  };
  const disarm = () => {
    const previous = audio;
    const hadSound = Boolean(previous);
    audio = undefined;
    armed = false;
    if (previous) {
      void previous.close().catch(() => {});
      showSoundState(pane.dataset.soundOffLabel);
    }
    return hadSound;
  };
  const maySound = () => active && !stopped && !document.hidden && navigator.onLine !== false;
  if (soundButton && soundStatus && Audio) {
    soundButton.hidden = false;
    soundButton.addEventListener('click', async () => {
      if (disarm() || !maySound()) return;
      let own;
      try {
        own = new Audio();
        audio = own;
        await own.resume();
        if (audio !== own) return;
        if (!maySound()) { disarm(); return; }
        if (own.state !== 'running') throw new Error('copy tone unavailable');
        armed = true;
        showSoundState(pane.dataset.soundOnLabel);
      } catch (_error) {
        if (!own || audio === own) {
          disarm();
          showSoundState(pane.dataset.soundUnavailableLabel);
        }
      }
    });
  }
  const playTone = () => {
    if (!armed || !audio || audio.state !== 'running' || !maySound()) return;
    try {
      const own = audio;
      const now = own.currentTime;
      const oscillator = own.createOscillator();
      const envelope = own.createGain();
      oscillator.frequency.value = 660;
      envelope.gain.setValueAtTime(0, now);
      envelope.gain.linearRampToValueAtTime(0.065, now + 0.015);
      envelope.gain.linearRampToValueAtTime(0, now + 0.14);
      oscillator.connect(envelope);
      envelope.connect(own.destination);
      oscillator.onended = () => { oscillator.disconnect(); envelope.disconnect(); };
      oscillator.start(now);
      oscillator.stop(now + 0.15);
    } catch (_error) {
      disarm();
      showSoundState(pane.dataset.soundUnavailableLabel);
    }
  };
  const fresh = item => {
    const age = Date.now() - Date.parse(item.at);
    return age >= -2000 && age <= 20000;
  };

  const cancel = () => {
    clearTimeout(timer);
    timer = undefined;
    if (controller) {
      const previous = controller;
      controller = undefined;
      previous.abort();
    }
  };
  const schedule = (delay) => {
    if (active && !stopped && !document.hidden && navigator.onLine !== false) {
      clearTimeout(timer);
      timer = setTimeout(refresh, delay);
    }
  };
  const render = (rows, announce) => {
    const ids = rows.map(item => item.id);
    const newRows = rows.filter(item => !visibleIds.includes(item.id));
    if (ids.length === visibleIds.length && ids.every((id, index) => id === visibleIds[index])) return;
    const entries = rows.map(item => {
      const li = document.createElement('li');
      const instant = document.createElement('time');
      li.dataset.noticeId = item.id;
      instant.dateTime = item.at;
      instant.textContent = item.at;
      li.append(pane.dataset[item.kind === 'peer' ? 'peerLabel' : (item.kind === 'job' ? 'jobLabel' : 'browserLabel')], ' · ', instant);
      return li;
    });
    if (!entries.length) {
      const empty = document.createElement('li');
      empty.textContent = pane.dataset.emptyLabel;
      entries.push(empty);
    }
    list.replaceChildren(...entries);
    visibleIds = ids;
    announcement.textContent = announce && newRows.length ? pane.dataset.newLabel : '';
    if (announce && newRows.some(item => item.kind === 'peer' && fresh(item))) playTone();
  };
  const refresh = async () => {
    timer = undefined;
    if (!active || stopped || document.hidden || navigator.onLine === false || controller) return;
    const own = new AbortController();
    controller = own;
    const requestTimeout = setTimeout(() => own.abort(), TIMEOUT);
    let delay = REFRESH;
    try {
      const response = await fetch(pane.dataset.endpoint, {
        credentials: 'same-origin', cache: 'no-store', redirect: 'manual', signal: own.signal,
        headers: {Accept: 'application/json'},
      });
      if (controller !== own) return;
      if (response.type === 'opaqueredirect' || [302, 401, 403, 404].includes(response.status)) {
        stopped = true;
        disarm();
        if (soundButton) soundButton.hidden = true;
        render([], false);
        return;
      }
      if (!response.ok) throw new Error('copy notice feed unavailable');
      const payload = await response.json();
      if (controller !== own) return;
      if (payload?.enabled === false) {
        stopped = true;
        disarm();
        if (soundButton) soundButton.hidden = true;
        render([], false);
        return;
      }
      const rows = payload?.notices;
      if (!Array.isArray(rows) || rows.length > 10 || !rows.every(item => (
        item && typeof item.id === 'string' && /^[a-f0-9]{64}$/.test(item.id) &&
        ['browser', 'job', 'peer'].includes(item.kind) && typeof item.at === 'string' && !Number.isNaN(Date.parse(item.at))
      ))) throw new Error('copy notice response invalid');
      render(rows, true);
    } catch (_error) {
      delay = BACKOFF; // Keep the current list during transient failures.
    } finally {
      clearTimeout(requestTimeout);
      if (controller === own) {
        controller = undefined;
        schedule(delay);
      }
    }
  };
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { disarm(); cancel(); } else schedule(0);
  });
  window.addEventListener('offline', () => { disarm(); cancel(); });
  window.addEventListener('online', () => schedule(0));
  window.addEventListener('pagehide', () => { active = false; disarm(); cancel(); });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { active = true; schedule(0); }
  });
  schedule(REFRESH);
})();
