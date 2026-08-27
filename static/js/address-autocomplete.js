(() => {
  const DEBOUNCE_MS = 220;

  const value = el => el ? el.value.trim() : '';
  const setValue = (el, v) => { if (el && v) el.value = v; };

  function ensureMenu(input) {
    let menu = input.parentElement?.querySelector(':scope > .address-autocomplete-menu');
    if (!menu) {
      menu = document.createElement('div');
      menu.className = 'list-group address-autocomplete-menu position-absolute start-0 end-0 mt-1 shadow-sm';
      menu.style.zIndex = '1080';
      input.parentElement?.classList.add('position-relative');
      input.parentElement?.append(menu);
    }
    return menu;
  }

  function hideAll(except = null) {
    document.querySelectorAll('.address-autocomplete-menu').forEach(menu => {
      if (menu !== except) menu.replaceChildren();
    });
  }

  function initGroup(group) {
    const endpoint = group.dataset.addressEndpoint;
    if (!endpoint) return;
    const city = group.querySelector('[data-address-city]');
    const postal = group.querySelector('[data-address-postal]');
    const street = group.querySelector('[data-address-street]');
    const country = group.querySelector('[data-address-country]');
    const status = group.querySelector('[data-address-status]');
    const inputs = [city, postal, street].filter(Boolean);
    let timer = null;
    let serial = 0;

    const apply = item => {
      setValue(city, item.city || '');
      setValue(postal, item.postal || '');
      setValue(street, item.street || '');
      setValue(country, (item.country || value(country) || 'DE').toUpperCase());
      hideAll();
      if (status) status.textContent = 'Eindeutige Adresse übernommen.';
      group.dispatchEvent(new CustomEvent('simpleoffice:address-selected', {bubbles: true, detail: item}));
    };

    const search = async active => {
      if (!active || value(active).length < 3) { hideAll(); return; }
      const requestId = ++serial;
      const menu = ensureMenu(active);
      menu.replaceChildren();
      const params = new URLSearchParams({
        field: active.dataset.addressField || '',
        city: value(city),
        postal: value(postal),
        street: value(street),
        country: (value(country) || 'DE').toLowerCase(),
      });
      if (status) status.textContent = 'Lokale Adresssuche …';
      try {
        const response = await fetch(`${endpoint}?${params}`, {headers: {'Accept': 'application/json'}});
        const payload = await response.json();
        if (requestId !== serial) return;
        if (!response.ok || !payload.ready) {
          if (status) status.textContent = payload.ready === false ? 'Lokaler Adressindex ist nicht bereit.' : 'Adresssuche nicht verfügbar.';
          return;
        }
        if (payload.unique) { apply(payload.unique); return; }
        const candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
        if (!candidates.length) {
          if (status) status.textContent = 'Keine passende lokale Adresse gefunden.';
          return;
        }
        if (status) status.textContent = `${candidates.length} Treffer`;
        candidates.forEach(item => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'list-group-item list-group-item-action py-2';
          const main = document.createElement('div');
          main.textContent = [item.city, item.postal, item.street].filter(Boolean).join(' · ');
          const secondary = document.createElement('small');
          secondary.className = 'text-secondary';
          secondary.textContent = [item.state, item.country].filter(Boolean).join(' · ');
          button.append(main, secondary);
          button.addEventListener('mousedown', event => event.preventDefault());
          button.addEventListener('click', () => apply(item));
          menu.append(button);
        });
      } catch (_error) {
        if (requestId === serial && status) status.textContent = 'Adresssuche nicht verfügbar.';
      }
    };

    inputs.forEach(input => {
      input.dataset.addressField = input.hasAttribute('data-address-city') ? 'city' : input.hasAttribute('data-address-postal') ? 'postal' : 'street';
      input.setAttribute('autocomplete', 'off');
      input.addEventListener('input', () => {
        window.clearTimeout(timer);
        if (value(input).length < 3) { ensureMenu(input).replaceChildren(); return; }
        timer = window.setTimeout(() => search(input), DEBOUNCE_MS);
      });
      input.addEventListener('focus', () => { if (value(input).length >= 3) search(input); });
    });
  }

  document.addEventListener('click', event => {
    if (!event.target.closest('[data-address-autocomplete]')) hideAll();
  });
  document.querySelectorAll('[data-address-autocomplete]').forEach(initGroup);
})();
