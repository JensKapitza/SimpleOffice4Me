(() => {
  const DEBOUNCE_MS = 220;
  const DEFAULT_ENDPOINT = '/documents/contacts/address-search.json';
  const value = el => el ? el.value.trim() : '';
  const setValue = (el, v) => { if (el && v) el.value = v; };
  const isEnglish = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const message = (de, en) => isEnglish ? en : de;
  const normalized = input => String(input || '').trim().replace(/\s+/g, ' ').toLocaleLowerCase();

  function uniqueBy(items, identity) {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).filter(item => {
      const key = identity(item);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

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

  function candidateButton(item, apply) {
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
    return button;
  }

  function suggestionButton(suggestion, apply) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'list-group-item list-group-item-action py-2';
    button.textContent = suggestion.value;
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', () => apply(suggestion));
    return button;
  }

  function markField(input, kind) {
    if (!input) return;
    input.setAttribute(`data-address-${kind}`, '');
    input.dataset.addressField = kind;
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-haspopup', 'listbox');
  }

  function prepareLegacyCrm() {
    const helper = document.getElementById('osm-address-helper');
    if (!helper) return;
    helper.dataset.addressAutocomplete = '';
    helper.dataset.addressEndpoint = DEFAULT_ENDPOINT;
    const city = document.getElementById('osm-city');
    const postal = document.getElementById('osm-postal');
    const street = document.getElementById('osm-street');
    const state = document.getElementById('osm-state');
    const country = document.getElementById('osm-country');
    const status = document.getElementById('osm-status');
    markField(city, 'city'); markField(postal, 'postal'); markField(street, 'street'); markField(state, 'state'); markField(country, 'country');
    if (status) status.setAttribute('data-address-status', '');
    const row = city?.closest('.row');
    if (row) {
      const cityBox = city?.parentElement, postalBox = postal?.parentElement, countryBox = country?.parentElement, streetBox = street?.parentElement, stateBox = state?.parentElement;
      [cityBox, postalBox, stateBox, countryBox, streetBox].filter(Boolean).forEach(box => row.append(box));
    }
    const oldSearch = document.getElementById('osm-search');
    if (oldSearch) oldSearch.hidden = true;
  }

  function prepareGenericForms() {
    document.querySelectorAll('form').forEach(form => {
      if (form.closest('[data-address-autocomplete]')) return;
      const city = form.querySelector('[name="city"], [name$="_city"], [name="ort"], [name$="_ort"]');
      const postal = form.querySelector('[name="postal"], [name$="_postal"], [name="postal_code"], [name$="_postal_code"], [name="plz"], [name$="_plz"]');
      const street = form.querySelector('[name="street"], [name$="_street"], [name="strasse"], [name$="_strasse"]');
      if (!(city && postal && street)) return;
      form.dataset.addressAutocomplete = '';
      form.dataset.addressEndpoint = DEFAULT_ENDPOINT;
      markField(city, 'city'); markField(postal, 'postal'); markField(street, 'street');
      const country = form.querySelector('[name="country"], [name$="_country"], [name="land"], [name$="_land"]');
      markField(country, 'country');
    });
  }

  function initGroup(group) {
    if (group.dataset.addressAutocompleteReady === '1') return;
    group.dataset.addressAutocompleteReady = '1';
    const endpoint = group.dataset.addressEndpoint || DEFAULT_ENDPOINT;
    const city = group.querySelector('[data-address-city], [data-address-field="city"]');
    const postal = group.querySelector('[data-address-postal], [data-address-field="postal"]');
    const street = group.querySelector('[data-address-street], [data-address-field="street"]');
    const state = group.querySelector('[data-address-state], [data-address-field="state"]');
    const country = group.querySelector('[data-address-country], [data-address-field="country"]');
    const status = group.querySelector('[data-address-status]');
    const inputs = [city, postal, street, state].filter(Boolean);
    if (!inputs.length) return;
    group.closest('form')?.setAttribute('autocomplete', 'off');
    [[city, 'city'], [postal, 'postal'], [street, 'street'], [state, 'state'], [country, 'country']]
      .forEach(([input, kind]) => markField(input, kind));
    let timer = null;
    let serial = 0;

    const applyComplete = item => {
      setValue(city, item.city || ''); setValue(postal, item.postal || ''); setValue(street, item.street || '');
      setValue(state, item.state || value(state));
      setValue(country, (item.country || value(country) || 'DE').toUpperCase());
      hideAll();
      if (status) status.textContent = message('Eindeutige Adresse übernommen.', 'Unique address applied.');
      group.dispatchEvent(new CustomEvent('simpleoffice:address-selected', {bubbles: true, detail: item}));
    };

    const applySuggestion = (active, suggestion) => {
      if (!active || !suggestion?.value) return;
      active.value = suggestion.value;
      const fills = suggestion.fills && typeof suggestion.fills === 'object' ? suggestion.fills : {};
      if (suggestion.field === 'postal' && fills.city) setValue(city, fills.city);
      hideAll();
      if (status) status.textContent = message(
        fills.city
          ? 'PLZ und der eindeutig zugehörige Ort wurden übernommen.'
          : 'Nur das aktuell bearbeitete Feld wurde übernommen.',
        fills.city
          ? 'The postcode and its unambiguous city were applied.'
          : 'Only the currently edited field was applied.'
      );
      group.dispatchEvent(new CustomEvent('simpleoffice:address-selected', {
        bubbles: true,
        detail: {field: suggestion.field, value: suggestion.value, complete: false},
      }));
    };

    const enoughContext = () => [city, postal, street]
      .filter(input => value(input).length >= 3).length >= 2;

    const search = async active => {
      if (!active || value(active).length < 3) { hideAll(); return; }
      const requestId = ++serial;
      const menu = ensureMenu(active); menu.replaceChildren();
      const q = [value(city), value(postal), value(street), value(state)].filter(Boolean).join(' ');
      const field = active.dataset.addressField || '';
      const params = new URLSearchParams({q, country: (value(country) || 'DE').toLowerCase(), field});
      if (status) status.textContent = message('Lokale Adresssuche …', 'Searching the local address index …');
      try {
        const response = await fetch(`${endpoint}?${params}`, {headers: {'Accept': 'application/json'}});
        const payload = await response.json();
        if (requestId !== serial) return;
        if (!response.ok || !payload.ready) {
          if (status) status.textContent = payload.ready === false
            ? message('Lokaler Adressindex ist nicht bereit. Manuelle Eingabe bleibt möglich.', 'The local address index is not ready. Manual entry remains available.')
            : message('Adresssuche nicht verfügbar. Manuelle Eingabe bleibt möglich.', 'Address search is unavailable. Manual entry remains available.');
          return;
        }
        if (payload.unique && enoughContext()) { applyComplete(payload.unique); return; }
        const suggestions = uniqueBy(payload.suggestions, suggestion =>
          `${normalized(suggestion?.field)}\u0000${normalized(suggestion?.value)}`
        );
        if (!suggestions.length) {
          if (status) status.textContent = message(
            'Kein sicherer Vorschlag für dieses Feld. Manuelle Eingabe bleibt möglich.',
            'No safe suggestion for this field. Manual entry remains available.'
          );
          return;
        }
        if (status) status.textContent = Number.isFinite(Number(payload.index_count))
          ? message(
            `${suggestions.length} Feldvorschläge · Index enthält ${payload.index_count} Adressen`,
            `${suggestions.length} field suggestions · index contains ${payload.index_count} addresses`
          )
          : message(`${suggestions.length} Feldvorschläge`, `${suggestions.length} field suggestions`);
        suggestions.forEach(suggestion => menu.append(suggestionButton(suggestion, item => applySuggestion(active, item))));
      } catch (_error) {
        if (requestId === serial && status) status.textContent = message(
          'Adresssuche nicht verfügbar. Manuelle Eingabe bleibt möglich.',
          'Address search is unavailable. Manual entry remains available.'
        );
      }
    };

    inputs.forEach(input => {
      input.addEventListener('input', () => {
        window.clearTimeout(timer);
        if (value(input).length < 3) { ensureMenu(input).replaceChildren(); return; }
        timer = window.setTimeout(() => search(input), DEBOUNCE_MS);
      });
      input.addEventListener('focus', () => { if (value(input).length >= 3) search(input); });
    });
  }

  function initFreeform(input) {
    if (input.dataset.addressFreeformReady === '1') return;
    input.dataset.addressFreeformReady = '1';
    input.setAttribute('autocomplete', 'off');
    input.closest('form')?.setAttribute('autocomplete', 'off');
    let timer = null;
    let serial = 0;
    const apply = item => {
      input.value = [item.street, [item.postal, item.city].filter(Boolean).join(' ')].filter(Boolean).join(', ');
      hideAll();
    };
    const search = async () => {
      const q = value(input);
      if (q.length < 3) return;
      const requestId = ++serial;
      const menu = ensureMenu(input); menu.replaceChildren();
      try {
        const response = await fetch(`${DEFAULT_ENDPOINT}?${new URLSearchParams({q, country: 'de'})}`, {headers: {'Accept': 'application/json'}});
        const payload = await response.json();
        if (requestId !== serial || !response.ok || !payload.ready) return;
        if (payload.unique) { apply(payload.unique); return; }
        uniqueBy(payload.candidates, item =>
          [item?.street, item?.postal, item?.city, item?.state, item?.country].map(normalized).join('\u0000')
        ).forEach(item => menu.append(candidateButton(item, apply)));
      } catch (_error) { /* optional helper: keep manual entry usable */ }
    };
    input.addEventListener('input', () => {
      window.clearTimeout(timer);
      if (value(input).length < 3) { ensureMenu(input).replaceChildren(); return; }
      timer = window.setTimeout(search, DEBOUNCE_MS);
    });
    input.addEventListener('focus', () => { if (value(input).length >= 3) search(); });
  }

  prepareLegacyCrm();
  prepareGenericForms();
  document.querySelectorAll('[data-address-autocomplete]').forEach(initGroup);
  document.querySelectorAll('input[name="address"]').forEach(initFreeform);
  document.querySelectorAll('form[data-address-compose]').forEach(form => {
    form.addEventListener('submit', () => {
      const fieldValue = name => value(form.querySelector(`[data-address-field="${name}"]`));
      const target = form.querySelector('[data-address-composed]');
      if (target) {
        const country = fieldValue('country').toUpperCase();
        const street = fieldValue('street'), city = fieldValue('city');
        const postal = fieldValue('postal'), state = fieldValue('state');
        let rows;
        if (['US', 'CA', 'AU'].includes(country)) rows = [street, `${city}${city && state ? ', ' : ''}${state} ${postal}`.trim(), country];
        else if (country === 'JP') rows = [postal, `${state} ${city}`.trim(), street, country];
        else if (['GB', 'IE'].includes(country)) rows = [street, city, postal, country];
        else rows = [street, `${postal} ${city}`.trim(), state, country && country !== 'DE' ? country : ''];
        target.value = rows.filter(Boolean).join('\n');
      }
    });
  });
  document.addEventListener('click', event => {
    if (!event.target.closest('[data-address-autocomplete], input[name="address"]')) hideAll();
  });
})();
