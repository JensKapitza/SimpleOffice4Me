(() => {
  document.querySelectorAll('[data-company-autocomplete]').forEach((group) => {
    const input = group.querySelector('[data-company-name]');
    const hidden = group.querySelector('[data-company-id]');
    const results = group.querySelector('[data-company-results]');
    const status = group.querySelector('[data-company-status]');
    const endpoint = group.dataset.companyEndpoint;
    const exclude = group.dataset.companyExclude || '';
    let timer;
    let controller;

    const close = () => {
      results.replaceChildren();
      results.hidden = true;
    };
    const choose = (item) => {
      input.value = item.company_name;
      hidden.value = item.contact_id;
      status.textContent = `${item.display_name} als Firma ausgewählt.`;
      close();
    };
    const search = async () => {
      const query = input.value.trim();
      hidden.value = '';
      if (query.length < 2) {
        close();
        status.textContent = query ? 'Bitte mindestens zwei Zeichen eingeben.' : '';
        return;
      }
      controller?.abort();
      controller = new AbortController();
      status.textContent = 'Kontakte werden gesucht …';
      try {
        const url = new URL(endpoint, window.location.origin);
        url.searchParams.set('q', query);
        if (exclude) url.searchParams.set('exclude', exclude);
        const response = await fetch(url, {headers: {Accept: 'application/json'}, signal: controller.signal});
        if (!response.ok) throw new Error('search failed');
        const payload = await response.json();
        close();
        payload.items.forEach((item) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'list-group-item list-group-item-action';
          const title = document.createElement('strong');
          title.textContent = item.company_name;
          const detail = document.createElement('div');
          detail.className = 'small text-secondary';
          detail.textContent = [item.display_name !== item.company_name ? item.display_name : '', item.email].filter(Boolean).join(' · ');
          button.append(title, detail);
          button.addEventListener('click', () => choose(item));
          results.append(button);
        });
        results.hidden = payload.items.length === 0;
        status.textContent = payload.items.length ? `${payload.items.length} Kontakt${payload.items.length === 1 ? '' : 'e'} gefunden.` : 'Keine passenden Kontakte gefunden.';
      } catch (error) {
        if (error.name !== 'AbortError') {
          close();
          status.textContent = 'Kontaktsuche ist derzeit nicht verfügbar.';
        }
      }
    };
    input.addEventListener('input', () => {
      clearTimeout(timer);
      timer = window.setTimeout(search, 200);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') close();
      if (event.key === 'ArrowDown' && !results.hidden) {
        event.preventDefault();
        results.querySelector('button')?.focus();
      }
    });
    document.addEventListener('click', (event) => {
      if (!group.contains(event.target)) close();
    });
  });
})();
