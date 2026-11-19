/* Task document picker: live document search plus attach action.
   Progressive enhancement; the board stays fully usable without JavaScript. */
(() => {
  'use strict';
  document.querySelectorAll('[data-link-picker]').forEach(picker => {
    const input = picker.querySelector('[data-link-search]');
    const results = picker.querySelector('[data-link-results]');
    const searchUrl = picker.dataset.searchUrl;
    const attachUrl = picker.dataset.attachUrl;
    if (!input || !results || !searchUrl || !attachUrl) return;
    let timer = 0;
    let requestId = 0;

    function clearResults() {
      results.replaceChildren();
    }

    async function search(query, run) {
      try {
        const params = new URLSearchParams({q: query, type: 'document'});
        const response = await fetch(`${searchUrl}?${params.toString()}`, {headers: {Accept: 'application/json'}});
        if (!response.ok) return;
        const data = await response.json();
        if (run !== requestId) return;
        clearResults();
        (data.matches || []).slice(0, 8).forEach(match => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'list-group-item list-group-item-action small text-break';
          button.textContent = match.label || '';
          button.setAttribute('role', 'option');
          button.addEventListener('click', async () => {
            const form = new FormData();
            form.set('document_id', match.ref || '');
            await fetch(attachUrl, {method: 'POST', body: form});
            window.location.reload();
          });
          results.append(button);
        });
        if (!results.children.length) {
          const empty = document.createElement('div');
          empty.className = 'list-group-item small text-secondary';
          empty.textContent = '—';
          results.append(empty);
        }
      } catch {
        clearResults();
      }
    }

    input.addEventListener('input', () => {
      window.clearTimeout(timer);
      const query = input.value.trim();
      if (query.length < 2) {
        clearResults();
        return;
      }
      timer = window.setTimeout(() => search(query, ++requestId), 250);
    });
  });
})();
