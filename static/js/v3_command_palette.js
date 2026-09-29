(() => {
  'use strict';

  const modalEl = document.getElementById('v3-command-palette');
  if (!modalEl) return;

  const input = modalEl.querySelector('[data-v3-search-input]');
  const list = modalEl.querySelector('[data-v3-search-results]');
  const status = modalEl.querySelector('[data-v3-search-status]');
  const modal = window.bootstrap ? new bootstrap.Modal(modalEl) : null;
  let controller = null;
  let timer = null;
  let sequence = 0;

  const clearResults = () => list.replaceChildren();

  const safeHref = (value) => {
    try {
      const url = new URL(String(value ?? ''), window.location.origin);
      return url.origin === window.location.origin ? url.href : '#';
    } catch {
      return '#';
    }
  };

  const render = (rows) => {
    clearResults();
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'list-group-item text-secondary';
      empty.textContent = 'Keine Treffer.';
      list.appendChild(empty);
      return;
    }

    const fragment = document.createDocumentFragment();
    rows.forEach((row, index) => {
      const link = document.createElement('a');
      link.className = 'list-group-item list-group-item-action';
      link.href = safeHref(row.url);
      link.dataset.v3Result = String(index);

      const heading = document.createElement('div');
      heading.className = 'd-flex justify-content-between gap-2';

      const title = document.createElement('strong');
      title.textContent = String(row.title ?? '');

      const badge = document.createElement('span');
      badge.className = 'badge text-bg-light border';
      badge.textContent = String(row.kind ?? '');

      const subtitle = document.createElement('div');
      subtitle.className = 'small text-secondary text-truncate';
      subtitle.textContent = String(row.subtitle ?? '');

      heading.append(title, badge);
      link.append(heading, subtitle);
      fragment.appendChild(link);
    });
    list.appendChild(fragment);
  };

  const run = async () => {
    const q = input.value.trim();
    if (controller) controller.abort();
    if (!q) {
      clearResults();
      status.textContent = '';
      return;
    }
    controller = new AbortController();
    const current = ++sequence;
    status.textContent = 'Suche …';
    try {
      const response = await fetch(
        '/api/v3/search?q=' + encodeURIComponent(q) + '&limit=30',
        {
          headers: {Accept: 'application/json'},
          signal: controller.signal
        }
      );
      if (!response.ok) throw new Error('search failed');
      const data = await response.json();
      if (current !== sequence) return;
      const rows = Array.isArray(data.results) ? data.results : [];
      render(rows);
      status.textContent = (data.unavailable_providers || []).length
        ? 'Ein Suchbereich ist vorübergehend nicht verfügbar.'
        : rows.length + ' Treffer';
    } catch (error) {
      if (error.name === 'AbortError') return;
      if (current === sequence) {
        clearResults();
        status.textContent = 'Suche vorübergehend nicht verfügbar.';
      }
    }
  };

  const open = () => {
    if (!modal) return;
    modal.show();
    window.setTimeout(() => input.focus(), 120);
  };

  document.querySelectorAll('[data-v3-search-open]').forEach(
    (button) => button.addEventListener('click', open)
  );

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      open();
    }
    const tag = document.activeElement?.tagName || '';
    if (event.key === '/' && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) {
      event.preventDefault();
      open();
    }
  });

  input.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(run, 120);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const first = list.querySelector('a[href]');
    if (!first) return;
    event.preventDefault();
    window.location.assign(first.href);
  });

  modalEl.addEventListener('hidden.bs.modal', () => {
    if (controller) controller.abort();
    input.value = '';
    clearResults();
    status.textContent = '';
  });
})();
