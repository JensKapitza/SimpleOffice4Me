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
    const newRow = ids.some(id => !visibleIds.includes(id));
    if (ids.length === visibleIds.length && ids.every((id, index) => id === visibleIds[index])) return;
    const entries = rows.map(item => {
      const li = document.createElement('li');
      const instant = document.createElement('time');
      li.dataset.noticeId = item.id;
      instant.dateTime = item.at;
      instant.textContent = item.at;
      li.append(pane.dataset[item.kind === 'job' ? 'jobLabel' : 'browserLabel'], ' · ', instant);
      return li;
    });
    if (!entries.length) {
      const empty = document.createElement('li');
      empty.textContent = pane.dataset.emptyLabel;
      entries.push(empty);
    }
    list.replaceChildren(...entries);
    visibleIds = ids;
    announcement.textContent = announce && newRow ? pane.dataset.newLabel : '';
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
        render([], false);
        return;
      }
      if (!response.ok) throw new Error('copy notice feed unavailable');
      const payload = await response.json();
      if (controller !== own) return;
      if (payload?.enabled === false) {
        stopped = true;
        render([], false);
        return;
      }
      const rows = payload?.notices;
      if (!Array.isArray(rows) || rows.length > 10 || !rows.every(item => (
        item && typeof item.id === 'string' && /^[a-f0-9]{64}$/.test(item.id) &&
        ['browser', 'job'].includes(item.kind) && typeof item.at === 'string' && !Number.isNaN(Date.parse(item.at))
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
    if (document.hidden) cancel(); else schedule(0);
  });
  window.addEventListener('offline', cancel);
  window.addEventListener('online', () => schedule(0));
  window.addEventListener('pagehide', () => { active = false; cancel(); });
  window.addEventListener('pageshow', event => {
    if (event.persisted) { active = true; schedule(0); }
  });
  schedule(REFRESH);
})();
