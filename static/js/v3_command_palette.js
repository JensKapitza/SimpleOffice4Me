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

  const escapeHtml = (value) => String(value ?? '').replace(
    /[&<>"']/g,
    (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    })[ch]
  );

  const render = (rows) => {
    if (!rows.length) {
      list.innerHTML = '<div class="list-group-item text-secondary">Keine Treffer.</div>';
      return;
    }
    list.innerHTML = rows.map((row, index) => {
      return '<a class="list-group-item list-group-item-action" href="' +
        escapeHtml(row.url) + '" data-v3-result="' + index + '">' +
        '<div class="d-flex justify-content-between gap-2"><strong>' +
        escapeHtml(row.title) + '</strong><span class="badge text-bg-light border">' +
        escapeHtml(row.kind) + '</span></div><div class="small text-secondary text-truncate">' +
        escapeHtml(row.subtitle) + '</div></a>';
    }).join('');
  };

  const run = async () => {
    const q = input.value.trim();
    if (controller) controller.abort();
    if (!q) {
      list.innerHTML = '';
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
        list.innerHTML = '';
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
    list.innerHTML = '';
    status.textContent = '';
  });
})();
