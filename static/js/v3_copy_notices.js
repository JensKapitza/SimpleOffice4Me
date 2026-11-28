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
  const initialRows = Array.from(list.querySelectorAll('[data-notice-id]'));
  let visibleIds = initialRows.map(row => row.dataset.noticeId);
  let seenIds = new Set(initialRows.filter(row => row.dataset.noticeRead === '1').map(row => row.dataset.noticeId));
  const taskTemplate = pane.querySelector('[data-copy-task-template]');
  const readButton = pane.querySelector('[data-copy-read-button]');
  const readStatus = pane.querySelector('[data-copy-read-status]');
  const token = pane.querySelector('[data-copy-read-token]')?.value;
  let marking;
  let timer;
  let controller;
  let stopped = false;
  let active = true;

  const audioControls = pane.querySelector('[data-copy-notice-audio]');
  const sourceSelect = pane.querySelector('[data-copy-notice-sound-source]');
  const testSound = pane.querySelector('[data-copy-notice-test-sound]');
  const soundButton = pane.querySelector('[data-copy-notice-sound]');
  const soundStatus = pane.querySelector('[data-copy-notice-sound-status]');
  const Audio = window.AudioContext || window.webkitAudioContext;
  let audio;
  let armedSource;
  let armedAt;
  let tone;

  const showSoundState = message => {
    if (!soundButton || !soundStatus) return;
    soundButton.setAttribute('aria-pressed', String(Boolean(armedSource)));
    if (testSound) testSound.disabled = !armedSource;
    soundStatus.textContent = message || '';
  };
  const disarm = () => {
    const previous = audio;
    const hadSound = Boolean(previous);
    audio = undefined;
    armedSource = undefined;
    armedAt = undefined;
    tone = undefined;
    if (previous) {
      void previous.close().catch(() => {});
      showSoundState(pane.dataset.soundOffLabel);
    }
    return hadSound;
  };
  const maySound = () => active && !stopped && !document.hidden && navigator.onLine !== false;
  if (soundButton && soundStatus && Audio) {
    if (audioControls) audioControls.hidden = false;
    sourceSelect?.addEventListener('change', disarm);
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
        const source = sourceSelect?.value ?? 'peer';
        if (!['peer', 'local', 'all'].includes(source)) throw new Error('invalid audio selection');
        armedSource = source;
        armedAt = Date.now();
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
    if (tone || !armedSource || !audio || audio.state !== 'running' || !maySound()) return;
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
      tone = oscillator;
      oscillator.onended = () => {
        if (tone === oscillator) tone = undefined;
        oscillator.disconnect();
        envelope.disconnect();
      };
      oscillator.start(now);
      oscillator.stop(now + 0.15);
    } catch (_error) {
      disarm();
      showSoundState(pane.dataset.soundUnavailableLabel);
    }
  };
  testSound?.addEventListener('click', playTone);
  const audible = item => {
    const at = Date.parse(item.at);
    const age = Date.now() - at;
    const group = item.kind === 'peer' ? 'peer' : 'local';
    // Do not play backlog from before the explicit click.
    return Boolean(armedSource) && (armedSource === 'all' || armedSource === group) &&
      at >= armedAt && age >= -2000 && age <= 20000;
  };

  const updateReadButton = () => {
    if (readButton) {
      readButton.hidden = stopped || !token || !pane.dataset.readEndpoint;
      const hasUnread = visibleIds.some(id => !seenIds.has(id));
      readButton.disabled = Boolean(marking) || !hasUnread || !maySound();
      if (readStatus) {
        if (hasUnread && navigator.onLine === false) readStatus.textContent = pane.dataset.readOffline;
        else if (readStatus.textContent === pane.dataset.readOffline) readStatus.textContent = '';
      }
    }
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
  const render = (rows, announce, read = new Set()) => {
    const ids = rows.map(item => item.id);
    const newRows = rows.filter(item => !visibleIds.includes(item.id) && !read.has(item.id));
    if (ids.length === visibleIds.length && ids.every((id, index) =>
      id === visibleIds[index] && read.has(id) === seenIds.has(id))) {
      updateReadButton();
      return;
    }
    const entries = rows.map(item => {
      const li = document.createElement('li');
      const instant = document.createElement('time');
      li.dataset.noticeId = item.id;
      li.dataset.noticeRead = read.has(item.id) ? '1' : '0';
      const status = document.createElement('span');
      status.className = 'small text-secondary';
      status.dataset.noticeReadLabel = '1';
      status.textContent = read.has(item.id) ? pane.dataset.readLabel : pane.dataset.unreadLabel;
      instant.dateTime = item.at;
      instant.textContent = item.at;
      li.append(pane.dataset[item.kind === 'peer' ? 'peerLabel' : (item.kind === 'job' ? 'jobLabel' : 'browserLabel')], ' · ', instant, ' · ', status);
      if (taskTemplate?.content) {
        const taskForm = taskTemplate.content.cloneNode(true);
        taskForm.querySelector('[name="notice_id"]').value = item.id;
        li.append(taskForm);
      }
      return li;
    });
    if (!entries.length) {
      const empty = document.createElement('li');
      empty.textContent = pane.dataset.emptyLabel;
      entries.push(empty);
    }
    list.replaceChildren(...entries);
    visibleIds = ids;
    seenIds = read;
    updateReadButton();
    announcement.textContent = announce && newRows.length ? pane.dataset.newLabel : '';
    if (announce && newRows.some(audible)) playTone();
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
        if (audioControls) audioControls.hidden = true;
        render([], false);
        return;
      }
      if (!response.ok) throw new Error('copy notice feed unavailable');
      const payload = await response.json();
      if (controller !== own) return;
      if (payload?.enabled === false) {
        stopped = true;
        disarm();
        if (audioControls) audioControls.hidden = true;
        render([], false);
        return;
      }
      const rows = payload?.notices;
      if (!Array.isArray(rows) || rows.length > 10 || !rows.every(item => (
        item && typeof item.id === 'string' && /^[a-f0-9]{64}$/.test(item.id) &&
        ['browser', 'job', 'peer'].includes(item.kind) && typeof item.at === 'string' && !Number.isNaN(Date.parse(item.at))
      ))) throw new Error('copy notice response invalid');
      const read = payload.seen === undefined ? [] : payload.seen;
      if (!Array.isArray(read) || read.length > 10 || !read.every(id => rows.some(row => row.id === id)))
        throw new Error('copy notice receipt invalid');
      render(rows, true, new Set(read));
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
  readButton?.addEventListener?.('click', async () => {
    if (!token || !pane.dataset.readEndpoint || !maySound() || marking) return;
    const ids = visibleIds.filter(id => !seenIds.has(id));
    if (!ids.length) return;
    marking = new AbortController();
    const timeout = setTimeout(() => marking?.abort(), TIMEOUT);
    updateReadButton();
    try {
      const response = await fetch(pane.dataset.readEndpoint, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: marking.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token}, body: JSON.stringify({ids}),
      });
      if (response.status !== 204) throw new Error('copy notice receipt unavailable');
      seenIds = new Set([...seenIds, ...ids]);
      for (const row of list.querySelectorAll('[data-notice-id]')) {
        if (!ids.includes(row.dataset.noticeId)) continue;
        row.dataset.noticeRead = '1';
        const label = row.querySelector('[data-notice-read-label]');
        if (label) label.textContent = pane.dataset.readLabel;
      }
      updateReadButton();
      if (readStatus) readStatus.textContent = pane.dataset.readOk;
    } catch (_error) {
      if (readStatus) readStatus.textContent = pane.dataset.readFailed;
    } finally {
      clearTimeout(timeout);
      marking = undefined;
      updateReadButton();
      cancel();
      schedule(0);
    }
  });
  updateReadButton();
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { disarm(); cancel(); }
    else schedule(0);
    updateReadButton();
  });
  window.addEventListener('offline', () => { disarm(); cancel(); updateReadButton(); });
  window.addEventListener('online', () => { updateReadButton(); schedule(0); });
  window.addEventListener('pagehide', () => { active = false; disarm(); cancel(); });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { active = true; schedule(0); }
  });
  schedule(REFRESH);
})();
