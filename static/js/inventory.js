(() => {
  'use strict';

  const root = document.getElementById('inventory-app');
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const status = $('scan-status');
  const isbn = $('isbn');
  const barcode = $('barcode');
  const nfc = $('nfc-id');
  const type = $('item-type');
  const title = $('title');
  const form = $('inventory-form');
  const findForm = $('inventory-find-form');
  const findInput = $('inventory-find');
  const findResults = $('inventory-find-results');
  const lookupButton = $('lookup-book');
  const amazonButton = $('amazon-search');
  const cover = $('book-cover');
  const coverWrap = $('book-cover-wrap');
  const openLibraryLink = $('openlibrary-link');
  const photo = $('photo');
  const photoPreview = $('photo-preview');
  const takePhoto = $('take-photo');
  const choosePhoto = $('choose-photo');
  const recentFilter = $('recent-filter');
  const saveButton = $('inventory-save');
  const replaceMetadata = $('replace-metadata');
  const metadataProvenance = $('metadata-provenance');

  const urls = {
    find: root.dataset.findUrl,
    lookup: root.dataset.lookupUrl,
    amazon: root.dataset.amazonUrl,
    marketplace: '/inventory/marketplace/search',
    objects: root.dataset.objectsUrl,
    detailPattern: root.dataset.detailPattern,
    suggest: root.dataset.suggestUrl,
    match: root.dataset.matchUrl,
    attach: root.dataset.attachUrl,
  };

  const fieldMap = {
    title: 'title', authors: 'authors', publisher: 'publisher', published_date: 'published-date',
    page_count: 'page-count', language: 'book-language', categories: 'categories', description: 'description',
    market_price: 'market-price', currency: 'currency', price_source: 'price-source',
    metadata_source: 'metadata-source', metadata_checked_at: 'metadata-checked-at',
  };

  let stream = null;
  let scanning = false;
  let detector = null;
  let cameraGeneration = 0;
  let lookupCooldown = null;
  let lookupAbort = null;
  let finderAbort = null;
  let marketplaceAbort = null;
  let objectTypeWasAutoBook = false;
  let ebayButton = null;
  let marketplaceResults = null;
  let actionBar = null;

  const setStatus = (text, kind = 'secondary') => {
    if (!status) return;
    status.className = `alert alert-${kind} py-2 small`;
    status.textContent = text;
  };
  const cleanText = (value) => String(value || '').trim();
  const LOCAL_UNAVAILABLE_MESSAGE = 'Lokale Objekterkennung ist nicht eingerichtet. Bitte Ollama und ein Vision-Modell konfigurieren.';

  // Navigation targets can come from JSON responses or data attributes. Never allow
  // javascript:, data:, cross-origin, or malformed URLs to reach location APIs.
  const safeLocalUrl = (value) => {
    try {
      const parsed = new URL(String(value || ''), window.location.origin);
      if (parsed.origin !== window.location.origin) return '';
      if (!['http:', 'https:'].includes(parsed.protocol)) return '';
      return parsed.href;
    } catch (_error) {
      return '';
    }
  };
  const safeMarketplaceUrl = (value) => {
    try {
      const parsed = new URL(String(value || ''));
      if (parsed.protocol !== 'https:') return '';
      const host = parsed.hostname.toLowerCase();
      const allowed = host === 'amazon.de' || host.endsWith('.amazon.de') || host === 'ebay.de' || host.endsWith('.ebay.de');
      return allowed ? parsed.href : '';
    } catch (_error) {
      return '';
    }
  };
  const navigateLocal = (value) => {
    const target = safeLocalUrl(value);
    if (!target) {
      setStatus('Unsicheres Navigationsziel wurde blockiert.', 'danger');
      return false;
    }
    window.location.assign(target);
    return true;
  };

  const isbn13CheckDigit = (prefix) => {
    let total = 0;
    for (let i = 0; i < prefix.length; i += 1) total += Number(prefix[i]) * (i % 2 === 0 ? 1 : 3);
    return String((10 - (total % 10)) % 10);
  };
  const normalizeIsbn = (value) => {
    const raw = cleanText(value).replace(/^ISBN(?:-1[03])?\s*:?\s*/i, '').replace(/[^0-9Xx]/g, '').toUpperCase();
    if (raw.length === 13 && /^97[89]\d{10}$/.test(raw)) return isbn13CheckDigit(raw.slice(0, 12)) === raw[12] ? raw : '';
    if (raw.length === 10 && /^\d{9}[\dX]$/.test(raw)) {
      let sum = 0;
      for (let i = 0; i < 10; i += 1) sum += (10 - i) * (raw[i] === 'X' ? 10 : Number(raw[i]));
      if (sum % 11 !== 0) return '';
      const prefix = `978${raw.slice(0, 9)}`;
      return `${prefix}${isbn13CheckDigit(prefix)}`;
    }
    return '';
  };

  const updateMode = () => {
    const book = cleanText(type?.value).toLowerCase() === 'book' || Boolean(cleanText(isbn?.value));
    document.querySelectorAll('.book-only').forEach((element) => { element.hidden = !book; });
    if (amazonButton) { amazonButton.hidden = false; amazonButton.disabled = false; }
    if (ebayButton) ebayButton.hidden = false;
  };
  const setBookModeFromIsbn = () => {
    if (!isbn || !type) return;
    if (cleanText(isbn.value) && cleanText(type.value).toLowerCase() === 'object') {
      type.value = 'book'; objectTypeWasAutoBook = true;
    } else if (!cleanText(isbn.value) && objectTypeWasAutoBook && cleanText(type.value).toLowerCase() === 'book') {
      type.value = 'object'; objectTypeWasAutoBook = false;
    }
    updateMode();
  };
  const stopCamera = () => {
    cameraGeneration += 1;
    scanning = false;
    if (stream) { stream.getTracks().forEach((track) => track.stop()); stream = null; }
    const video = $('camera-preview'); if (video) video.srcObject = null;
    const box = $('camera-box'); if (box) box.hidden = true;
  };
  const safeJson = async (response) => { try { return await response.json(); } catch (_error) { return {}; } };

  const setLookupCooldown = (seconds) => {
    if (!lookupButton) return;
    if (lookupCooldown) clearInterval(lookupCooldown);
    let remaining = Math.max(0, Number(seconds) || 0);
    const original = lookupButton.dataset.label || 'Buchdaten laden'; lookupButton.dataset.label = original;
    const paint = () => {
      lookupButton.disabled = remaining > 0;
      lookupButton.textContent = remaining > 0 ? `Erneut in ${remaining}s` : original;
      remaining -= 1;
      if (remaining < 0 && lookupCooldown) { clearInterval(lookupCooldown); lookupCooldown = null; }
    };
    paint(); if (remaining >= 0) lookupCooldown = window.setInterval(paint, 1000);
  };

  const updateBookLinks = (canonicalIsbn) => {
    if (!canonicalIsbn) { if (coverWrap) coverWrap.hidden = true; if (openLibraryLink) openLibraryLink.hidden = true; return; }
    if (cover && coverWrap) {
      cover.src = `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(canonicalIsbn)}-M.jpg?default=false`;
      cover.alt = `Buchcover für ISBN ${canonicalIsbn}`; coverWrap.hidden = false;
    }
    if (openLibraryLink) { openLibraryLink.href = `https://openlibrary.org/isbn/${encodeURIComponent(canonicalIsbn)}`; openLibraryLink.hidden = false; }
  };
  const renderMetadataProvenance = (data) => {
    if (!metadataProvenance) return;
    const candidates = Array.isArray(data.metadata_candidates) ? data.metadata_candidates : [];
    metadataProvenance.replaceChildren();
    if (!candidates.length) { metadataProvenance.hidden = true; return; }
    const titles = [...new Set(candidates.map((candidate) => cleanText(candidate.title)).filter(Boolean))];
    const sources = candidates.map((candidate) => cleanText(candidate.metadata_source)).filter(Boolean);
    const conflict = titles.length > 1;
    metadataProvenance.textContent = conflict
      ? `Konflikt zwischen Quellen: ${sources.join(', ')}. Übernommene Werte bitte prüfen; nichts wird automatisch gespeichert.`
      : `Metadatenquelle: ${sources.join(', ')}. Werte bitte prüfen; nichts wird automatisch gespeichert.`;
    metadataProvenance.className = `col-12 small ${conflict ? 'text-warning-emphasis' : 'text-secondary'}`;
    metadataProvenance.hidden = false;
  };

  const applyMetadata = (data) => {
    const overwrite = Boolean(replaceMetadata?.checked);
    Object.entries(fieldMap).forEach(([key, id]) => {
      const element = $(id); const value = data[key];
      if (!element || value === undefined || value === null || cleanText(value) === '') return;
      const shouldProtect = ['title', 'description', 'authors', 'publisher', 'published-date', 'categories'].includes(id);
      if (!overwrite && shouldProtect && cleanText(element.value)) return;
      element.value = String(value);
    });
    renderMetadataProvenance(data);
  };
  const applyMarketplaceMetadata = (data) => {
    const selectedTitle = cleanText(data.title);
    if (selectedTitle && title) title.value = selectedTitle;
    applyMetadata(data);
    const overwrite = Boolean(replaceMetadata?.checked);
    const simpleFields = {
      manufacturer: 'manufacturer', model: 'model', barcode: 'barcode', isbn: 'isbn',
    };
    Object.entries(simpleFields).forEach(([key, id]) => {
      const element = $(id); const value = cleanText(data[key]);
      if (!element || !value || (!overwrite && cleanText(element.value))) return;
      element.value = value;
    });
    const canonical = normalizeIsbn(isbn?.value);
    if (canonical && isbn && type) {
      isbn.value = canonical;
      if (barcode && !cleanText(barcode.value)) barcode.value = canonical;
      type.value = 'book'; objectTypeWasAutoBook = true; updateBookLinks(canonical);
    }
    if ($('metadata-checked-at')) $('metadata-checked-at').value = new Date().toISOString();
    updateMode();
    setStatus(`Daten von ${data.provider === 'ebay' ? 'eBay' : 'Amazon'} übernommen. Bitte kurz prüfen.`, 'success');
  };

  const lookup = async () => {
    if (!isbn || !lookupButton) return;
    const entered = cleanText(isbn.value) || cleanText(barcode?.value);
    if (!entered) { setStatus('Bitte zuerst eine ISBN eingeben oder scannen.', 'warning'); isbn?.focus(); return; }
    const canonical = normalizeIsbn(entered);
    if (!canonical) { setStatus('Die ISBN ist ungültig. ISBN-10 und ISBN-13 mit oder ohne Bindestriche werden unterstützt.', 'warning'); isbn?.focus(); isbn?.select(); return; }
    isbn.value = canonical; if (barcode && !cleanText(barcode.value)) barcode.value = canonical;
    type.value = 'book'; objectTypeWasAutoBook = true; updateMode(); updateBookLinks(canonical);
    if (lookupAbort) lookupAbort.abort(); lookupAbort = new AbortController();
    lookupButton.disabled = true; lookupButton.setAttribute('aria-busy', 'true'); setStatus(`Suche Buchdaten für ISBN ${canonical} …`, 'primary');
    try {
      const response = await fetch(`${urls.lookup}?isbn=${encodeURIComponent(canonical)}`, { headers: { Accept: 'application/json' }, cache: 'no-store', signal: lookupAbort.signal });
      const data = await safeJson(response);
      if (!response.ok) {
        const retryAfter = Number(data.retry_after || response.headers.get('Retry-After') || 0);
        if (response.status === 429 && retryAfter) setLookupCooldown(retryAfter);
        setStatus(data.error || `Buchdaten konnten nicht geladen werden (${response.status}).`, response.status >= 500 ? 'danger' : 'warning'); return;
      }
      applyMetadata(data); isbn.value = data.isbn || canonical; if (barcode && !cleanText(barcode.value)) barcode.value = isbn.value;
      type.value = 'book'; updateMode(); updateBookLinks(isbn.value);
      const source = data.metadata_source || 'Open Library';
      const errors = Array.isArray(data.lookup_errors) && data.lookup_errors.length ? ` Hinweis: ${data.lookup_errors.join(', ')}` : '';
      setStatus(`Buchdaten geladen: ${source}. Bitte kurz prüfen.${errors}`, 'success'); setLookupCooldown(5);
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus(`Buchdatenabruf ist fehlgeschlagen.${navigator.onLine === false ? ' Das Gerät ist offline.' : ''}`, 'danger');
    } finally { lookupButton.removeAttribute('aria-busy'); if (!lookupCooldown) lookupButton.disabled = false; }
  };

  const marketplaceQuery = () => {
    const canonical = normalizeIsbn(isbn?.value);
    return canonical || cleanText(barcode?.value) || cleanText(title?.value);
  };
  const openMarketplace = (value) => {
    const target = safeMarketplaceUrl(value);
    if (!target) { setStatus('Unsicheres Marketplace-Ziel wurde blockiert.', 'danger'); return; }
    window.open(target, '_blank', 'noopener,noreferrer');
  };
  const renderMarketplaceResults = (provider, data) => {
    if (!marketplaceResults) return;
    marketplaceResults.replaceChildren();
    const providerName = provider === 'ebay' ? 'eBay' : 'Amazon';
    const rows = Array.isArray(data.results) ? data.results.slice(0, 3) : [];
    if (!rows.length) {
      const empty = document.createElement('div'); empty.className = 'alert alert-secondary mb-2';
      empty.textContent = cleanText(data.warning) || `Keine automatisch lesbaren ${providerName}-Treffer gefunden. Die normale Suche kann geöffnet werden.`;
      marketplaceResults.appendChild(empty);
      const fallback = safeMarketplaceUrl(data.fallback_url);
      if (fallback) {
        const open = document.createElement('button'); open.type = 'button'; open.className = 'btn btn-outline-secondary w-100';
        open.textContent = `${providerName}-Suche öffnen`; open.addEventListener('click', () => openMarketplace(fallback));
        marketplaceResults.appendChild(open);
      }
      marketplaceResults.hidden = false; return;
    }
    const heading = document.createElement('div'); heading.className = 'fw-semibold mb-2';
    heading.textContent = `${providerName}: beste ${rows.length} Treffer`; marketplaceResults.appendChild(heading);
    const list = document.createElement('div'); list.className = 'list-group';
    rows.forEach((row, index) => {
      const item = document.createElement('div'); item.className = 'list-group-item';
      const top = document.createElement('div'); top.className = 'd-flex justify-content-between gap-2 align-items-start';
      const text = document.createElement('div'); text.className = 'flex-grow-1';
      const name = document.createElement('div'); name.className = 'fw-semibold'; name.textContent = cleanText(row.title) || `${providerName}-Treffer ${index + 1}`;
      text.appendChild(name);
      if (index === 0) { const badge = document.createElement('span'); badge.className = 'badge text-bg-success ms-2'; badge.textContent = 'Bester Treffer'; name.appendChild(badge); }
      const details = [cleanText(row.authors), cleanText(row.manufacturer), cleanText(row.categories)].filter(Boolean).join(' · ');
      if (details) { const meta = document.createElement('div'); meta.className = 'small text-secondary'; meta.textContent = details; text.appendChild(meta); }
      const price = [cleanText(row.market_price), cleanText(row.currency)].filter(Boolean).join(' ');
      if (price) { const amount = document.createElement('div'); amount.className = 'small fw-semibold mt-1'; amount.textContent = price; text.appendChild(amount); }
      top.appendChild(text); item.appendChild(top);
      const controls = document.createElement('div'); controls.className = 'd-grid d-sm-flex gap-2 mt-2';
      const take = document.createElement('button'); take.type = 'button'; take.className = `btn ${index === 0 ? 'btn-success' : 'btn-outline-success'} btn-sm`;
      take.textContent = 'Daten übernehmen'; take.addEventListener('click', () => applyMarketplaceMetadata(row)); controls.appendChild(take);
      const target = safeMarketplaceUrl(row.url);
      if (target) { const view = document.createElement('button'); view.type = 'button'; view.className = 'btn btn-outline-secondary btn-sm'; view.textContent = 'Angebot ansehen'; view.addEventListener('click', () => openMarketplace(target)); controls.appendChild(view); }
      item.appendChild(controls); list.appendChild(item);
    });
    marketplaceResults.appendChild(list); marketplaceResults.hidden = false;
  };
  const searchMarketplace = async (provider) => {
    const query = marketplaceQuery();
    if (!query) { setStatus('Für die Marketplace-Suche zuerst ISBN, Barcode oder Objektname erfassen.', 'warning'); title?.focus(); return; }
    const button = provider === 'ebay' ? ebayButton : amazonButton;
    if (marketplaceAbort) marketplaceAbort.abort(); marketplaceAbort = new AbortController();
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    setStatus(`Suche ${provider === 'ebay' ? 'eBay' : 'Amazon'} nach „${query}“ …`, 'primary');
    try {
      const response = await fetch(`${urls.marketplace}?provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(query)}`, {
        headers: { Accept: 'application/json' }, cache: 'no-store', signal: marketplaceAbort.signal,
      });
      const data = await safeJson(response);
      if (!response.ok && !data.fallback_url) { setStatus(data.error || 'Marketplace-Suche fehlgeschlagen.', 'warning'); return; }
      renderMarketplaceResults(provider, data);
      if (Array.isArray(data.results) && data.results.length) {
        const source = data.source === 'api' ? 'API-Fallback' : 'öffentliche Suche';
        setStatus(`${data.results.length} Treffer über ${source} geladen. Treffer prüfen und auswählen.`, 'success');
      } else {
        setStatus(cleanText(data.warning || data.error) || 'Keine Marketplace-Treffer gefunden.', 'secondary');
      }
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus(`Marketplace-Suche nicht erreichbar.${navigator.onLine === false ? ' Das Gerät ist offline.' : ''}`, 'danger');
    } finally { if (button) { button.disabled = false; button.removeAttribute('aria-busy'); } }
  };

  const prepareMarketplaceUi = () => {
    if (!saveButton) return;
    actionBar = saveButton.parentElement;
    if (!actionBar) return;
    actionBar.classList.add('flex-column', 'flex-sm-row');
    actionBar.style.zIndex = '1035';
    actionBar.style.boxShadow = '0 -0.3rem 0.8rem rgba(0,0,0,.12)';
    actionBar.style.paddingBottom = 'max(.65rem, env(safe-area-inset-bottom, 0px))';
    saveButton.classList.add('w-100');
    if (amazonButton) {
      amazonButton.classList.remove('book-only'); amazonButton.hidden = false; amazonButton.disabled = false;
      amazonButton.classList.add('w-100'); amazonButton.innerHTML = '<i class="fab fa-amazon me-2"></i>Amazon Daten suchen';
    }
    ebayButton = document.createElement('button'); ebayButton.type = 'button'; ebayButton.id = 'ebay-search';
    ebayButton.className = 'btn btn-outline-primary w-100'; ebayButton.innerHTML = '<i class="fas fa-tags me-2"></i>eBay Daten suchen';
    actionBar.appendChild(ebayButton);
    marketplaceResults = document.createElement('div'); marketplaceResults.id = 'marketplace-results';
    marketplaceResults.className = 'col-12 border rounded p-2 bg-body mb-2'; marketplaceResults.hidden = true;
    actionBar.parentElement?.insertBefore(marketplaceResults, actionBar);
    if (form) form.style.paddingBottom = 'calc(7rem + env(safe-area-inset-bottom, 0px))';
    ebayButton.addEventListener('click', () => searchMarketplace('ebay'));
  };
  const syncActionBarInset = () => {
    if (!actionBar) return;
    const viewport = window.visualViewport;
    const obscured = viewport ? Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop) : 0;
    actionBar.style.bottom = `${Math.round(obscured)}px`;
  };

  const renderFinderMatches = (matches, query) => {
    if (!findResults) return;
    findResults.replaceChildren();
    if (!matches.length) { findResults.hidden = true; return; }
    const heading = document.createElement('div'); heading.className = 'small text-secondary mb-1'; heading.textContent = `Lokale Treffer für „${query}“`; findResults.appendChild(heading);
    const group = document.createElement('div'); group.className = 'list-group';
    matches.slice(0, 8).forEach((row) => {
      const target = safeLocalUrl(row.dataset.url); if (!target) return;
      const link = document.createElement('a'); link.className = 'list-group-item list-group-item-action py-2'; link.href = target;
      link.textContent = row.dataset.label || row.textContent.trim(); group.appendChild(link);
    });
    findResults.appendChild(group); findResults.hidden = false;
  };
  const localFinderMatches = (query) => {
    const needle = cleanText(query).toLowerCase(); if (!needle) return [];
    return Array.from(document.querySelectorAll('[data-inventory-search]')).filter((row) => cleanText(row.dataset.inventorySearch).toLowerCase().includes(needle));
  };

  const quickFind = async () => {
    if (!findInput) return;
    const raw = cleanText(findInput.value); if (!raw) { findInput.focus(); return; }
    const localMatches = localFinderMatches(raw); renderFinderMatches(localMatches, raw);
    const canonicalIsbn = normalizeIsbn(raw); const query = canonicalIsbn || raw.replace(/^#\s*/, '').trim();
    if (finderAbort) finderAbort.abort(); finderAbort = new AbortController(); findInput.setAttribute('aria-busy', 'true'); setStatus(`Suche Inventar nach „${raw}“ …`, 'primary');
    try {
      const response = await fetch(`${urls.find}?identifier=${encodeURIComponent(query)}&nfc=${encodeURIComponent(query)}`, { headers: { Accept: 'application/json' }, cache: 'no-store', signal: finderAbort.signal });
      const data = await safeJson(response);
      if (response.ok && data.found && data.url && navigateLocal(data.url)) return;
      const numeric = raw.replace(/^#\s*/, '').trim();
      if (/^\d+$/.test(numeric) && urls.detailPattern) {
        const detailUrl = safeLocalUrl(urls.detailPattern.replace('__OBJECT__', encodeURIComponent(numeric)));
        if (detailUrl) {
          const detailResponse = await fetch(detailUrl, { headers: { Accept: 'text/html' }, cache: 'no-store', signal: finderAbort.signal });
          if (detailResponse.ok && navigateLocal(detailUrl)) return;
        }
      }
      if (localMatches.length === 1 && navigateLocal(localMatches[0].dataset.url)) return;
      const fallback = `${urls.objects}?q=${encodeURIComponent(raw)}`;
      setStatus(localMatches.length ? `${localMatches.length} lokale Treffer gefunden. Für die vollständige Suche wird die Objektliste verwendet.` : `Kein exakter Kennungstreffer. Öffne die vollständige Inventarsuche für „${raw}“.`, 'secondary');
      navigateLocal(fallback);
    } catch (error) {
      if (error?.name !== 'AbortError') { setStatus('Schnellsuche nicht erreichbar – öffne die vollständige Objektliste.', 'warning'); navigateLocal(`${urls.objects}?q=${encodeURIComponent(raw)}`); }
    } finally { findInput.removeAttribute('aria-busy'); }
  };

  const duplicateProbe = async (value, source) => {
    const raw = cleanText(value); if (!raw || !urls.find) return; const query = normalizeIsbn(raw) || raw;
    try {
      const response = await fetch(`${urls.find}?identifier=${encodeURIComponent(source === 'nfc' ? '' : query)}&nfc=${encodeURIComponent(source === 'nfc' ? query : '')}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      const data = await safeJson(response); if (response.ok && data.found) setStatus(`Bereits vorhanden: #${data.display_id || ''} ${data.name || ''}.`, 'warning');
    } catch (_error) {}
  };

  const analyzeButton = $('analyze-photo');
  const suggestionBox = $('object-suggestion');
  const suggestedName = $('suggested-name');
  const suggestedType = $('suggested-type');
  const suggestedNote = $('suggested-note');
  const suggestedProvider = $('suggested-provider');
  const isbnTitleHint = $('isbn-title-hint');
  let pendingSuggestion = null;
  let analyzing = null;

  const hideObjectSuggestion = () => { pendingSuggestion = null; if (suggestionBox) suggestionBox.hidden = true; };
  const showObjectSuggestion = (payload) => {
    const suggestion = payload && payload.suggestion ? payload.suggestion : null;
    if (!suggestion || !suggestionBox) { setStatus('Der Vorschlag war leer. Bitte erneut versuchen.', 'warning'); return; }
    pendingSuggestion = suggestion;
    if (suggestedName) suggestedName.textContent = cleanText(suggestion.object_name);
    if (suggestedType) suggestedType.textContent = suggestion.item_type && suggestion.item_type !== 'object' ? `(${suggestion.item_type})` : '';
    if (suggestedNote) suggestedNote.textContent = cleanText(suggestion.note);
    if (suggestedProvider) suggestedProvider.textContent = cleanText(payload.provider_label);
    suggestionBox.hidden = false;
    suggestionBox.scrollIntoView({block: 'nearest'});
  };
  const analyzePhoto = async () => {
    if (!photo?.files?.length) { setStatus('Bitte zuerst ein Foto auswählen.', 'warning'); return; }
    const file = photo.files[0];
    if (file.size > 5 * 1024 * 1024) { setStatus('Das Foto ist größer als 5 MiB – bitte ein kleineres Bild wählen.', 'warning'); return; }
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type || '')) { setStatus('Nur JPEG, PNG oder WebP sind erlaubt.', 'warning'); return; }
    if (!urls.suggest) { setStatus('Vorschläge sind hier nicht verfügbar.', 'warning'); return; }
    const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
    const data = new FormData();
    data.append('photo', file, file.name || 'foto');
    data.append('_csrf_token', token);
    const code = cleanText(barcode?.value); if (code) data.append('barcode', code.slice(0, 120));
    const isbnCode = cleanText(isbn?.value); if (isbnCode) data.append('isbn', isbnCode.slice(0, 120));
    if (analyzeButton) { analyzeButton.disabled = true; analyzeButton.setAttribute('aria-busy', 'true'); }
    setStatus('Foto wird analysiert …', 'primary');
    if (analyzing) analyzing.abort(); analyzing = new AbortController();
    try {
      const response = await fetch(urls.suggest, { method: 'POST', body: data, cache: 'no-store', signal: analyzing.signal });
      const payload = await safeJson(response);
      if (!response.ok) {
        const serverMessage = cleanText(payload && payload.error);
        const fallback = response.status === 503
          ? LOCAL_UNAVAILABLE_MESSAGE
          : `Vorschlag fehlgeschlagen (${response.status}).`;
        setStatus(serverMessage || fallback, response.status >= 500 ? 'danger' : 'warning');
        return;
      }
      showObjectSuggestion(payload);
      setStatus('Vorschlag bereit. Bitte prüfen und bei Bedarf übernehmen.', 'success');
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus('Die Analyse ist fehlgeschlagen.', 'danger');
    } finally {
      if (analyzeButton) { analyzeButton.disabled = false; analyzeButton.removeAttribute('aria-busy'); }
    }
  };
  const adoptSuggestion = () => {
    if (!pendingSuggestion || !title) { hideObjectSuggestion(); return; }
    title.value = cleanText(pendingSuggestion.object_name);
    const suggested = cleanText(pendingSuggestion.item_type);
    if (suggested && suggested !== 'object' && type && (!cleanText(type.value) || cleanText(type.value) === 'object')) {
      type.value = suggested; objectTypeWasAutoBook = false; updateMode();
    }
    hideObjectSuggestion();
    setStatus('Vorschlag übernommen. Bitte Felder prüfen, dann speichern.', 'success');
    title.focus();
  };
  const checkIsbnInTitle = () => {
    if (!isbnTitleHint || !title || !isbn) return;
    const canonical = normalizeIsbn(title.value);
    isbnTitleHint.hidden = !(canonical && !cleanText(isbn.value));
  };
  const adoptIsbnFromTitle = () => {
    if (!title || !isbn) return;
    const canonical = normalizeIsbn(title.value);
    if (!canonical) { if (isbnTitleHint) isbnTitleHint.hidden = true; return; }
    isbn.value = canonical; title.value = '';
    setBookModeFromIsbn(); updateBookLinks(canonical);
    if (isbnTitleHint) isbnTitleHint.hidden = true;
    lookup();
  };

  const previewPhoto = async () => {
    if (!photoPreview || !photo?.files?.length) return; const file = photo.files[0];
    if (file.size > 12 * 1024 * 1024) { photo.value = ''; photoPreview.hidden = true; setStatus('Das Foto ist größer als 12 MiB.', 'warning'); return; }
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) { photo.value = ''; photoPreview.hidden = true; setStatus('Nur JPEG, PNG oder WebP sind erlaubt.', 'warning'); return; }
    try {
      const bitmap = await createImageBitmap(file); const maxSide = 1600; const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext('2d'); if (!context) throw new Error('canvas unavailable'); context.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
      photoPreview.src = canvas.toDataURL('image/jpeg', 0.85); photoPreview.hidden = false;
    } catch (_error) { photoPreview.removeAttribute('src'); photoPreview.hidden = true; setStatus('Das Bild konnte nicht sicher als Vorschau dekodiert werden.', 'warning'); }
  };

  const rememberLocation = () => { const location = $('location'); if (!location) return; const value = cleanText(location.value); try { if (value) localStorage.setItem('simpleoffice.inventory.lastLocation', value); } catch (_error) {} };
  const restoreLocation = () => { const location = $('location'); if (!location || cleanText(location.value)) return; try { const saved = localStorage.getItem('simpleoffice.inventory.lastLocation'); if (saved) location.value = saved; } catch (_error) {} };
  const clearBookMetadata = () => {
    ['authors', 'publisher', 'published-date', 'page-count', 'language', 'categories', 'description', 'market-price', 'price-source', 'metadata-source', 'metadata-checked-at'].forEach((id) => { const element = $(id); if (element) element.value = ''; });
    if ($('currency')) $('currency').value = 'EUR'; setStatus('Geladene Buchdaten wurden aus dem Formular entfernt. ISBN und Barcode bleiben erhalten.', 'secondary');
  };
  const updateConnectivity = () => { const badge = $('inventory-network'); if (!badge) return; const online = navigator.onLine !== false; badge.textContent = online ? 'online' : 'offline'; badge.className = `badge ${online ? 'text-bg-success' : 'text-bg-warning'}`; };
  const filterRecent = () => { const needle = cleanText(recentFilter?.value).toLowerCase(); document.querySelectorAll('[data-recent-row]').forEach((row) => { row.hidden = Boolean(needle) && !cleanText(row.dataset.inventorySearch).toLowerCase().includes(needle); }); };

  findForm?.addEventListener('submit', (event) => { event.preventDefault(); quickFind(); });
  findInput?.addEventListener('input', () => renderFinderMatches(localFinderMatches(findInput.value), findInput.value));
  type?.addEventListener('input', () => { objectTypeWasAutoBook = false; updateMode(); });
  isbn?.addEventListener('input', () => { setBookModeFromIsbn(); const canonical = normalizeIsbn(isbn.value); if (canonical) updateBookLinks(canonical); });
  isbn?.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); lookup(); } });
  isbn?.addEventListener('blur', () => duplicateProbe(isbn.value, 'identifier'));
  barcode?.addEventListener('blur', () => duplicateProbe(barcode.value, 'identifier'));
  nfc?.addEventListener('blur', () => duplicateProbe(nfc.value, 'nfc'));
  lookupButton?.addEventListener('click', lookup); $('clear-book-data')?.addEventListener('click', clearBookMetadata);
  $('book-mode')?.addEventListener('click', () => { type.value = 'book'; objectTypeWasAutoBook = false; updateMode(); isbn.focus(); });
  $('object-mode')?.addEventListener('click', () => { type.value = 'object'; objectTypeWasAutoBook = false; updateMode(); title.focus(); });
  $('restore-location')?.addEventListener('click', restoreLocation); photo?.addEventListener('change', previewPhoto);
  photo?.addEventListener('change', hideObjectSuggestion);
  photo?.addEventListener('change', () => { if ($('analyze-photo')) analyzePhoto(); });
  // Cancel-safe: opening the chooser never clears the input, so a cancelled
  // dialog keeps the attached file; both actions reuse the same #photo field.
  takePhoto?.addEventListener('click', () => { if (!photo) return; photo.setAttribute('capture', 'environment'); photo.click(); });
  choosePhoto?.addEventListener('click', () => { if (!photo) return; photo.removeAttribute('capture'); photo.click(); });
  const SERIES_MAX_ITEMS = 8;
  const SERIES_MAX_BYTES = 5 * 1024 * 1024;
  const seriesInput = $('photo-series-input');
  const queueList = $('queue-list');
  const queueHint = $('queue-hint');
  let seriesItems = [];
  let seriesSeq = 0;
  let seriesBusy = false;
  const seriesShelf = $('series-shelf');
  let seriesActiveId = null;
  const seriesToken = () => { try { const bytes = new Uint8Array(16); crypto.getRandomValues(bytes); return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join(''); } catch (_error) { let out = ''; while (out.length < 32) out += Math.floor(Math.random() * 4294967295).toString(16).padStart(8, '0'); return out.slice(0, 32); } };
  const seriesActive = () => seriesItems.find((entry) => entry.id === seriesActiveId && !entry.saved) || null;
  const seriesFocusField = (key) => { const map = { title: 'title', isbn: 'isbn', barcode: 'barcode', library_location_id: 'series-shelf', market_price: 'market-price', purchase_date: 'purchase-date' }; const id = map[key || '']; if (id) $(id)?.focus(); };
  const seriesUpdateHint = () => { if (queueHint) queueHint.textContent = `Serie: ${seriesItems.length}/${SERIES_MAX_ITEMS} Fotos. Jedes Foto wird einzeln lokal analysiert — nichts wird automatisch gespeichert.`; };
  const seriesValidFile = (file) => file && /^image\/(jpeg|png|webp)$/i.test(file.type || '') && file.size > 0 && file.size <= SERIES_MAX_BYTES;
  const seriesRenderItem = (item) => {
    let card = document.getElementById(`series-card-${item.id}`);
    if (!card) {
      card = document.createElement('div');
      card.className = 'card';
      card.id = `series-card-${item.id}`;
      card.innerHTML = '<div class="card-body d-flex gap-2 align-items-start"><img width="96" height="96" style="width:96px;height:96px;object-fit:cover" class="rounded border flex-shrink-0" alt=""><div class="flex-grow-1"><div class="fw-semibold small text-break" data-part="name"></div><div class="small" data-part="status" role="status"></div><div class="small text-secondary" data-part="suggestion" hidden></div><div class="d-flex flex-wrap gap-2 mt-1 align-items-center" data-part="isbn-row" hidden><span class="small text-secondary" data-part="isbn"></span><button type="button" class="btn btn-sm btn-outline-secondary" data-part="adopt-isbn">ISBN übernehmen</button></div><input class="form-control form-control-sm mt-2" data-part="identifier" placeholder="Barcode / ISBN (optional)" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="120"><div class="d-flex flex-wrap gap-2 mt-2"><button type="button" class="btn btn-sm btn-outline-primary" data-part="check">Im Inventar prüfen</button><button type="button" class="btn btn-sm btn-success" data-part="attach" hidden>Foto anhängen</button></div><div class="small mt-1" data-part="match" hidden></div><ul class="small mt-2 mb-0 ps-3" data-part="checklist" hidden></ul><div class="small mt-1" data-part="save" hidden></div><div class="d-flex flex-wrap gap-2 mt-2"><button type="button" class="btn btn-sm btn-outline-primary" data-part="transfer" hidden>Ins Formular übernehmen</button><button type="button" class="btn btn-sm btn-outline-secondary" data-part="retry" hidden>Erneut analysieren</button><button type="button" class="btn btn-sm btn-outline-danger" data-part="remove">Entfernen</button></div></div></div>';
      card.querySelector('[data-part="transfer"]').addEventListener('click', () => seriesTransfer(item.id));
      card.querySelector('[data-part="retry"]').addEventListener('click', () => seriesRetry(item.id));
      card.querySelector('[data-part="remove"]').addEventListener('click', () => seriesRemove(item.id));
      const identifierInput = card.querySelector('[data-part="identifier"]');
      identifierInput.setAttribute('aria-label', `Kennung für Serienfoto ${item.id} (optional)`);
      identifierInput.addEventListener('input', (event) => { item.identifier = event.target.value; item.match = null; item.attached = false; seriesRenderItem(item); });
      card.querySelector('[data-part="check"]').addEventListener('click', () => seriesCheck(item.id));
      card.querySelector('[data-part="attach"]').addEventListener('click', () => seriesAttach(item.id));
      card.querySelector('[data-part="adopt-isbn"]').addEventListener('click', () => seriesAdoptIsbn(item.id));
      queueList?.appendChild(card);
    }
    const img = card.querySelector('img');
    img.hidden = !item.url;
    if (item.url && img.getAttribute('src') !== item.url) { img.setAttribute('src', item.url); img.setAttribute('alt', `Serienfoto ${item.id}`); }
    card.querySelector('[data-part="name"]').textContent = `#${item.id} · ${item.file.name || 'Foto'} (${Math.max(1, Math.round(item.file.size / 1024))} KB)`;
    card.querySelector('[data-part="status"]').textContent = item.statusText;
    const boxEl = card.querySelector('[data-part="suggestion"]');
    if (item.suggestion) {
      boxEl.hidden = false;
      boxEl.textContent = `${cleanText(item.suggestion.object_name)}${item.suggestion.item_type && item.suggestion.item_type !== 'object' ? ` (${item.suggestion.item_type})` : ''}${item.suggestion.note ? ` – ${cleanText(item.suggestion.note)}` : ''}`;
    } else { boxEl.hidden = true; boxEl.textContent = ''; }
    const isbnRow = card.querySelector('[data-part="isbn-row"]');
    const isbnValue = item.suggestion ? cleanText(item.suggestion.isbn) : '';
    if (isbnValue) {
      isbnRow.hidden = false;
      card.querySelector('[data-part="isbn"]').textContent = `ISBN im Foto: ${isbnValue}`;
    } else { isbnRow.hidden = true; }
    card.querySelector('[data-part="transfer"]').hidden = !(item.status === 'ready' || item.status === 'transferred');
    card.querySelector('[data-part="retry"]').hidden = !(item.status === 'error' || item.status === 'ready');
    const matchEl = card.querySelector('[data-part="match"]');
    const attachBtn = card.querySelector('[data-part="attach"]');
    if (item.match) { matchEl.hidden = false; matchEl.textContent = item.match.text; }
    else { matchEl.hidden = true; matchEl.textContent = ''; }
    attachBtn.hidden = !(item.match && item.match.status === 'unique' && !item.attached);
    seriesRenderChecklist(item, card);
    const saveEl = card.querySelector('[data-part="save"]');
    if (item.saveInfo) { saveEl.hidden = false; saveEl.textContent = ''; const span = document.createElement('span'); span.textContent = item.saveInfo.text; saveEl.appendChild(span); const target = safeLocalUrl(item.saveInfo.url); if (target) { saveEl.appendChild(document.createTextNode(' ')); const link = document.createElement('a'); link.href = target; link.textContent = 'Eintrag öffnen'; saveEl.appendChild(link); } }
    else { saveEl.hidden = true; saveEl.textContent = ''; }
  };
  const seriesProcessNext = async () => {
    // Sequential: one request at a time bounds Ollama memory/load.
    if (seriesBusy || !queueList) return;
    const item = seriesItems.find((entry) => entry.status === 'pending');
    if (!item) return;
    if (!urls.suggest) { item.status = 'error'; item.statusText = 'Vorschläge sind hier nicht verfügbar.'; seriesRenderItem(item); return; }
    seriesBusy = true;
    item.status = 'working'; item.statusText = 'Wird analysiert …'; item.controller = new AbortController();
    seriesRenderItem(item);
    try {
      const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
      const data = new FormData();
      data.append('photo', item.file, item.file.name || 'foto');
      data.append('_csrf_token', token);
      // Item-local identifier only: never copy the main form's global barcode/ISBN here.
      const ownCode = cleanText(item.identifier);
      if (ownCode) { const ownIsbn = normalizeIsbn(ownCode); data.append(ownIsbn ? 'isbn' : 'barcode', (ownIsbn || ownCode).slice(0, 120)); }
      const response = await fetch(urls.suggest, { method: 'POST', body: data, cache: 'no-store', signal: item.controller.signal });
      const payload = await safeJson(response);
      if (!seriesItems.includes(item)) { seriesBusy = false; seriesProcessNext(); return; }
      if (!response.ok) {
        const serverMessage = cleanText(payload && payload.error);
        const fallback = response.status === 503 ? LOCAL_UNAVAILABLE_MESSAGE : `Vorschlag fehlgeschlagen (${response.status}).`;
        item.status = 'error'; item.statusText = serverMessage || fallback;
      } else if (!payload || !payload.suggestion) {
        item.status = 'error'; item.statusText = 'Der Vorschlag war leer. Bitte erneut versuchen.';
      } else {
        item.status = 'ready'; item.suggestion = payload.suggestion;
        const label = cleanText(payload.provider_label);
        item.statusText = label ? `Vorschlag bereit (${label}).` : 'Vorschlag bereit.';
      }
    } catch (error) {
      if (error?.name === 'AbortError') { seriesBusy = false; seriesProcessNext(); return; }
      item.status = 'error'; item.statusText = 'Die Analyse ist fehlgeschlagen.';
    }
    seriesBusy = false;
    seriesRenderItem(item);
    seriesProcessNext();
  };
  const seriesAddFiles = (files) => {
    if (!queueList || !files?.length) return;
    // No dedupe: identical files stay independent items (never merge by similarity).
    for (const file of files) {
      if (seriesItems.length >= SERIES_MAX_ITEMS) { setStatus(`Maximal ${SERIES_MAX_ITEMS} Fotos je Serie – weitere wurden ignoriert.`, 'warning'); break; }
      seriesSeq += 1;
      const item = { id: seriesSeq, file, identifier: '', url: '', status: 'pending', statusText: 'Wartet auf Analyse …', suggestion: null, controller: null, match: null, attached: false, captureToken: seriesToken(), saved: false, saving: false, saveInfo: null };
      if (!seriesValidFile(file)) {
        item.status = 'error';
        item.statusText = !/^image\/(jpeg|png|webp)$/i.test(file.type || '') ? 'Nur JPEG, PNG oder WebP sind erlaubt.' : 'Das Foto ist leer oder größer als 5 MiB.';
      } else {
        try { item.url = URL.createObjectURL(file); } catch (_error) { item.status = 'error'; item.statusText = 'Das Foto konnte nicht gelesen werden.'; }
      }
      seriesItems.push(item);
      seriesRenderItem(item);
    }
    seriesUpdateHint();
    seriesProcessNext();
  };
  const seriesRetry = (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    if (!item || item.status === 'working') return;
    if (!seriesValidFile(item.file)) { item.status = 'error'; item.statusText = 'Datei weiterhin ungültig (JPEG/PNG/WebP, max. 5 MiB).'; seriesRenderItem(item); return; }
    item.status = 'pending'; item.statusText = 'Wartet auf Analyse …'; item.suggestion = null;
    seriesRenderItem(item);
    seriesProcessNext();
  };
  const seriesRemove = (id) => {
    const index = seriesItems.findIndex((entry) => entry.id === id);
    if (index < 0) return;
    const [item] = seriesItems.splice(index, 1);
    if (seriesActiveId === id) seriesActiveId = null;
    try { if (item.controller) item.controller.abort(); } catch (_error) {}
    try { if (item.url) URL.revokeObjectURL(item.url); } catch (_error) {}
    document.getElementById(`series-card-${id}`)?.remove();
    seriesUpdateHint();
  };
  const seriesTransfer = (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    if (!item || !item.suggestion || !title || !photo || item.saved) return;
    title.value = cleanText(item.suggestion.object_name);
    const suggested = cleanText(item.suggestion.item_type);
    if (suggested && suggested !== 'object' && type && (!cleanText(type.value) || cleanText(type.value) === 'object')) {
      type.value = suggested; objectTypeWasAutoBook = false; updateMode();
    }
    const ownCode = cleanText(item.identifier);
    const ownIsbn = normalizeIsbn(ownCode);
    if (isbn) isbn.value = '';
    if (barcode) barcode.value = '';
    if (ownIsbn && isbn) { isbn.value = ownIsbn; setBookModeFromIsbn(); }
    else if (ownCode && barcode) { barcode.value = ownCode; }
    try {
      const carry = new DataTransfer();
      carry.items.add(item.file);
      photo.files = carry.files;
    } catch (_error) { setStatus('Das Serienfoto konnte nicht ins Formular übernommen werden.', 'danger'); return; }
    previewPhoto();
    item.status = 'transferred'; item.statusText = 'Ins Formular übernommen – bitte prüfen, dann speichern (Seite und Serie bleiben erhalten).';
    seriesActiveId = item.id;
    seriesRenderItem(item);
    setStatus('Serienvorschlag übernommen. Bitte Felder prüfen, dann speichern – die Seite bleibt erhalten.', 'success');
    if (ownIsbn) lookup();
    title.focus();
  };
  const seriesAdoptIsbn = (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    const isbn = item && item.suggestion ? cleanText(item.suggestion.isbn) : '';
    if (!item || !isbn) return;
    const card = document.getElementById(`series-card-${id}`);
    const field = card ? card.querySelector('[data-part="identifier"]') : null;
    if (!field) return;
    field.value = isbn;
    item.identifier = isbn;
    item.match = null;
    item.attached = false;
    seriesRenderItem(item);
    const checkBtn = card.querySelector('[data-part="check"]');
    if (checkBtn) checkBtn.focus();
  };
  const seriesCheck = async (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    if (!item || item.checking) return;
    const ownCode = cleanText(item.identifier);
    if (!ownCode || !urls.match) { item.match = { status: 'idle', text: ownCode ? 'Prüfung hier nicht verfügbar.' : 'Bitte zuerst eine Kennung eintragen.' }; seriesRenderItem(item); return; }
    item.checking = true;
    try {
      const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
      const data = new FormData();
      data.append('identifier', ownCode);
      data.append('_csrf_token', token);
      const response = await fetch(urls.match, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok) {
        item.match = { status: 'error', text: cleanText(payload && payload.error) || `Prüfung fehlgeschlagen (${response.status}).` };
      } else if (!payload || (payload.status !== 'unique' && payload.status !== 'none' && payload.status !== 'ambiguous')) {
        item.match = { status: 'error', text: 'Die Prüfung war unverständlich. Bitte erneut versuchen.' };
      } else if (payload.status === 'unique') {
        const hit = (payload.matches || [])[0] || {};
        item.match = { status: 'unique', text: `Treffer: ${cleanText(hit.name) || 'Objekt'} (${String(hit.object_id || '').slice(0, 8)}…)`, target: { object_id: String(hit.object_id || ''), name: cleanText(hit.name) } };
      } else if (payload.status === 'ambiguous') {
        const names = (payload.matches || []).slice(0, 3).map((hit) => cleanText(hit.name) || 'Objekt').join(', ');
        item.match = { status: 'ambiguous', text: `Mehrdeutig: ${payload.count || (payload.matches || []).length} Treffer (${names}) – bitte manuell prüfen.` };
      } else {
        item.match = { status: 'none', text: 'Kein Treffer – per „Ins Formular übernehmen“ als neues Objekt anlegen.' };
      }
    } catch (_error) {
      item.match = { status: 'error', text: 'Die Prüfung ist fehlgeschlagen.' };
    }
    item.checking = false;
    seriesRenderItem(item);
  };
  const seriesAttach = async (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    if (!item || !item.match || item.match.status !== 'unique' || !item.match.target || item.attaching) return;
    if (!urls.attach) { item.match = { status: 'error', text: 'Anhängen hier nicht verfügbar.' }; seriesRenderItem(item); return; }
    item.attaching = true;
    try {
      const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
      const data = new FormData();
      data.append('photo', item.file, item.file.name || 'foto');
      data.append('_csrf_token', token);
      data.append('item_id', item.match.target.object_id);
      data.append('identifier', cleanText(item.identifier));
      const response = await fetch(urls.attach, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok) {
        item.match = { status: 'error', text: cleanText(payload && payload.error) || `Anhängen fehlgeschlagen (${response.status}). Bitte erneut prüfen.` };
      } else {
        item.attached = true;
        item.statusText = 'Foto angehängt.';
      }
    } catch (_error) {
      item.match = { status: 'error', text: 'Das Anhängen ist fehlgeschlagen. Bitte erneut prüfen.' };
    }
    item.attaching = false;
    seriesRenderItem(item);
  };
  seriesInput?.addEventListener('change', () => { seriesAddFiles(seriesInput.files); seriesInput.value = ''; });
  $('series-add')?.addEventListener('click', () => { if (!seriesInput) return; seriesInput.removeAttribute('capture'); seriesInput.setAttribute('multiple', ''); seriesInput.click(); });
  $('series-capture')?.addEventListener('click', () => { if (!seriesInput) return; seriesInput.setAttribute('capture', 'environment'); seriesInput.removeAttribute('multiple'); seriesInput.click(); });
  seriesUpdateHint();
  // Series completion (own section after the queue wiring): submits the reviewed
  // form per card via JSON. Unlike transfer/check/attach above, saving intentionally
  // consumes the current form values — the isolation region ends at the queue wiring.
  const seriesIsBook = (item) => {
    const transferred = item.status === 'transferred';
    const kind = transferred ? cleanText(type?.value) : cleanText(item.suggestion?.item_type);
    if (kind.toLowerCase() === 'book' || kind.toLowerCase() === 'buch') return true;
    const code = transferred ? (cleanText(isbn?.value) || cleanText(barcode?.value)) : (cleanText(item.suggestion?.isbn) || cleanText(item.identifier));
    return Boolean(normalizeIsbn(code));
  };
  const seriesChecklist = (item) => {
    const transferred = item.status === 'transferred';
    const book = seriesIsBook(item);
    const code = transferred ? (normalizeIsbn(isbn?.value) || cleanText(barcode?.value)) : cleanText(item.identifier);
    const shelf = cleanText(seriesShelf?.value);
    return [
      { label: 'Titel/Typ', detail: transferred ? cleanText(title?.value) : cleanText(item.suggestion?.object_name), ok: Boolean(transferred ? cleanText(title?.value) : cleanText(item.suggestion?.object_name)), action: transferred ? 'title' : 'transfer' },
      book
        ? { label: 'Kennung (ISBN/Barcode)', detail: code, ok: Boolean(code), action: transferred ? 'isbn' : 'transfer' }
        : { label: 'Kennung (optional)', detail: code, ok: true, action: transferred ? 'isbn' : 'transfer' },
      book
        ? { label: 'Regal (Serie)', detail: cleanText(seriesShelf?.selectedOptions?.[0]?.textContent), ok: Boolean(shelf), action: 'shelf' }
        : { label: 'Regal (nur für Bücher)', detail: '', ok: true, action: 'shelf' },
      { label: 'Preis (Empfehlung)', detail: cleanText($('market-price')?.value), ok: Boolean(cleanText($('market-price')?.value)), action: 'price' },
    ];
  };
  const seriesChecklistAction = (id, action) => {
    if (action === 'transfer') { seriesTransfer(id); return; }
    ({ title: () => title?.focus(), isbn: () => isbn?.focus(), shelf: () => seriesShelf?.focus(), price: () => $('market-price')?.focus() })[action]?.();
  };
  const seriesRenderChecklist = (item, card) => {
    const list = card.querySelector('[data-part="checklist"]');
    if (!item.suggestion || item.saved) { list.hidden = true; list.textContent = ''; return; }
    list.hidden = false; list.textContent = '';
    for (const row of seriesChecklist(item)) {
      const li = document.createElement('li');
      li.textContent = `${row.ok ? '✓' : '○'} ${row.label}${row.detail ? `: ${row.detail}` : ''} `;
      const btn = document.createElement('button'); btn.type = 'button'; btn.className = 'btn btn-sm btn-link p-0';
      btn.textContent = row.action === 'transfer' ? 'Übernehmen' : 'Anzeigen';
      btn.addEventListener('click', () => seriesChecklistAction(item.id, row.action));
      li.appendChild(btn); list.appendChild(li);
    }
  };
  const seriesRefreshChecklists = () => { for (const entry of seriesItems) { if (entry.suggestion && !entry.saved && document.getElementById(`series-card-${entry.id}`)) seriesRenderItem(entry); } };
  const seriesSaveInfo = (payload, fallback) => {
    const extra = (Array.isArray(payload.notices) ? payload.notices : []).filter((note) => note && note !== payload.message);
    return { text: `${cleanText(payload.message) || fallback}${extra.length ? ` Hinweis: ${extra.join(' ')}` : ''}`, url: cleanText(payload.detail_url) };
  };
  const seriesResetForm = () => {
    if (title) title.value = ''; if (isbn) isbn.value = ''; if (barcode) barcode.value = ''; if (nfc) nfc.value = '';
    if (photo) photo.value = ''; if (photoPreview) { photoPreview.hidden = true; photoPreview.removeAttribute('src'); }
    const allow = $('allow-duplicate'); if (allow) allow.checked = false;
    ['manufacturer', 'model', 'serial-number', 'purchase-date', 'location', 'tags', 'description', 'authors', 'publisher', 'published-date', 'page-count', 'book-language', 'categories', 'market-price', 'price-source', 'metadata-source', 'metadata-checked-at'].forEach((id) => { const el = $(id); if (el) el.value = ''; });
    const currency = $('currency'); if (currency) currency.value = 'EUR';
    if (metadataProvenance) { metadataProvenance.hidden = true; metadataProvenance.textContent = ''; }
    ['inspection-name', 'inspection-due', 'inspection-interval', 'inspection-responsible', 'inspection-note'].forEach((id) => { const el = $(id); if (el) el.value = ''; });
    const unit = $('inspection-unit'); if (unit) unit.value = 'months';
    if (type) { type.value = 'object'; objectTypeWasAutoBook = false; updateMode(); }
  };
  const seriesSave = async (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    if (!item || item.saved || item.saving || !form) return;
    if (item.status !== 'transferred') { item.statusText = 'Bitte zuerst ins Formular übernehmen und prüfen.'; seriesRenderItem(item); return; }
    const book = seriesIsBook(item);
    const shelf = cleanText(seriesShelf?.value);
    if (!cleanText(title?.value) || (book && !shelf)) { setStatus(book ? 'Bitte Titel und Regal im Formular prüfen.' : 'Bitte einen Titel im Formular eingeben.', 'warning'); seriesRenderItem(item); seriesFocusField(!cleanText(title?.value) ? 'title' : 'library_location_id'); return; }
    item.saving = true;
    if (saveButton) { saveButton.disabled = true; saveButton.setAttribute('aria-busy', 'true'); }
    rememberLocation();
    try {
      const data = new FormData(form);
      data.set('response_format', 'json');
      data.set('capture_request_id', item.captureToken);
      if (book) {
        data.set('library_return', '1');
        data.set('library_location_id', shelf);
        data.set('item_type', 'book');
        if (!cleanText(data.get('barcode')) && cleanText(data.get('isbn'))) data.set('barcode', cleanText(data.get('isbn')));
      } else {
        data.delete('library_return');
        data.delete('library_location_id');
      }
      const response = await fetch(form.action, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok || !payload || payload.ok !== true) {
        const fields = payload && typeof payload.fields === 'object' ? payload.fields : {};
        if (fields.capture_request_id) item.captureToken = seriesToken();
        item.saveInfo = null;
        item.statusText = cleanText(payload && payload.error) || `Speichern fehlgeschlagen (${response.status}).`;
        setStatus(item.statusText, 'danger');
        seriesRenderItem(item);
        seriesFocusField(Object.keys(fields)[0] || '');
        return;
      }
      if (payload.saved) {
        item.saved = true; item.status = 'saved';
        item.saveInfo = seriesSaveInfo(payload, 'Gespeichert.');
        item.statusText = `Gespeichert: #${cleanText(payload.display_id)} ${cleanText(payload.name)}`.trim();
        seriesActiveId = null;
        seriesResetForm();
        setStatus('Serienfoto gespeichert. Regal bleibt – nächstes Foto übernehmen.', 'success');
      } else if (payload.retried) {
        item.saved = true; item.status = 'saved';
        item.saveInfo = seriesSaveInfo(payload, 'Bereits erfasst.');
        item.statusText = 'Bereits erfasst (Wiederholung) – kein Duplikat angelegt.';
        seriesActiveId = null;
        seriesResetForm();
        setStatus(item.statusText, 'success');
      } else if (payload.duplicate) {
        item.captureToken = seriesToken();
        item.saveInfo = seriesSaveInfo(payload, 'Bereits vorhanden.');
        item.saveInfo.text += ' Für ein weiteres Exemplar „Trotz gleicher Kennung …“ aktivieren und erneut speichern.';
        item.statusText = 'Bereits vorhanden – kein Duplikat angelegt.';
        setStatus(item.statusText, 'warning');
      } else {
        item.statusText = 'Unverständliche Antwort – nichts gespeichert.';
        setStatus(item.statusText, 'danger');
      }
    } catch (_error) {
      item.statusText = 'Speichern fehlgeschlagen (Netzwerk). Erneut versuchen – die Kennung verhindert Duplikate.';
      setStatus(item.statusText, 'danger');
    } finally {
      item.saving = false;
      if (saveButton) { saveButton.disabled = false; saveButton.removeAttribute('aria-busy'); }
      seriesRenderItem(item);
    }
  };
  form?.addEventListener('submit', (event) => {
    if (!seriesActive()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    seriesSave(seriesActiveId);
  });
  ['title', 'isbn', 'barcode', 'market-price'].forEach((id) => $(id)?.addEventListener('input', seriesRefreshChecklists));
  seriesShelf?.addEventListener('change', seriesRefreshChecklists);
  analyzeButton?.addEventListener('click', analyzePhoto);
  $('adopt-suggestion')?.addEventListener('click', adoptSuggestion);
  $('dismiss-suggestion')?.addEventListener('click', hideObjectSuggestion);
  title?.addEventListener('input', checkIsbnInTitle);
  $('adopt-isbn-title')?.addEventListener('click', adoptIsbnFromTitle);
  recentFilter?.addEventListener('input', filterRecent);

  amazonButton?.addEventListener('click', () => searchMarketplace('amazon'));

  $('start-barcode')?.addEventListener('click', async () => {
    stopCamera();
    const generation = cameraGeneration;
    if (typeof window.BarcodeDetector !== 'function' && typeof window.jsQR !== 'function') { setStatus('Kein Scanner verfügbar. Kennung bitte manuell eingeben.', 'warning'); barcode?.focus(); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setStatus('Kamera-Zugriff nicht verfügbar; HTTPS kann erforderlich sein.', 'warning'); return; }
    try {
      try { detector = typeof window.BarcodeDetector === 'function' ? new BarcodeDetector({ formats: ['ean_13', 'ean_8', 'code_128', 'code_39', 'qr_code'] }) : null; } catch (_error) { detector = null; }
      if (!detector && typeof window.jsQR !== 'function') throw new Error('No available decoder');
      const requestedStream = await window.SimpleOfficeCamera.openCameraStream(
        navigator.mediaDevices,
        () => generation === cameraGeneration,
      );
      if (!requestedStream) return;
      if (generation !== cameraGeneration) { requestedStream.getTracks().forEach(track => track.stop()); return; }
      stream = requestedStream;
      const video = $('camera-preview'); video.srcObject = stream; $('camera-box').hidden = false;
      try { await video.play(); } catch (_error) { throw new Error('Camera preview could not start'); }
      if (generation !== cameraGeneration) return;
      scanning = true;
      setStatus(
        detector
          ? 'Barcode vor die Kamera halten …'
          : 'QR-Code vor die Kamera halten; EAN/Barcodes bitte manuell eingeben.',
        'primary',
      );
      const scan = async () => {
        if (!scanning || generation !== cameraGeneration) return;
        try {
          let codes = [];
          if (detector) { try { codes = await detector.detect(video); } catch (_error) { /* Try local QR decoding below. */ } }
          if (!codes.length && typeof window.jsQR === 'function') {
            const canvas = document.createElement('canvas');
            const width = video.videoWidth, height = video.videoHeight;
            if (width && height) {
              const scale = Math.min(1, 1200 / Math.max(width, height));
              canvas.width = Math.max(1, Math.round(width * scale)); canvas.height = Math.max(1, Math.round(height * scale));
              const ctx = canvas.getContext('2d', {willReadFrequently: true});
              if (ctx) { ctx.drawImage(video, 0, 0, canvas.width, canvas.height); const frame = ctx.getImageData(0, 0, canvas.width, canvas.height); const decoded = window.jsQR(frame.data, frame.width, frame.height, {inversionAttempts: 'attemptBoth'}); if (decoded) codes = [{rawValue: decoded.data}]; }
            }
          }
          if (generation !== cameraGeneration) return;
          if (codes.length) {
            const value = cleanText(codes[0].rawValue); barcode.value = value; const canonical = normalizeIsbn(value);
            if (canonical) { isbn.value = canonical; type.value = 'book'; objectTypeWasAutoBook = true; updateMode(); updateBookLinks(canonical); setStatus(`ISBN erkannt: ${canonical}. Lade Buchdaten …`, 'success'); stopCamera(); lookup(); return; }
            isbn.value = ''; updateMode(); setStatus($('analyze-photo') ? `Barcode erkannt: ${value}. Kein ISBN – bitte ein Foto aufnehmen, die Analyse startet automatisch.` : `Barcode erkannt: ${value}`, 'success'); stopCamera(); takePhoto?.focus(); duplicateProbe(value, 'identifier'); return;
          }
        } catch (_error) {}
        requestAnimationFrame(scan);
      };
      requestAnimationFrame(scan);
    } catch (error) { if (generation !== cameraGeneration) return; stopCamera(); setStatus(error?.name === 'NotAllowedError' ? 'Kamerazugriff verweigert. Bitte Berechtigung im Browser prüfen.' : 'Kamera konnte nicht geöffnet werden. Bitte Berechtigungen oder Kamera prüfen.', 'danger'); }
  });

  $('scan-nfc')?.addEventListener('click', async () => {
    if (!('NDEFReader' in window)) { setStatus('Web NFC wird nicht unterstützt. NFC-Wert kann manuell eingetragen werden.', 'warning'); nfc?.focus(); return; }
    try {
      const reader = new NDEFReader(); await reader.scan(); setStatus('NFC-Tag an das Handy halten …', 'primary');
      reader.addEventListener('reading', (event) => {
        const parts = []; if (event.serialNumber) parts.push(event.serialNumber);
        for (const record of event.message.records) { try { if (record.data) parts.push(new TextDecoder(record.encoding || 'utf-8').decode(record.data)); } catch (_error) {} }
        nfc.value = parts.filter(Boolean).join(' | ').slice(0, 240); setStatus('NFC wurde gelesen.', 'success'); duplicateProbe(nfc.value, 'nfc');
      }, { once: true });
    } catch (_error) { setStatus('NFC konnte nicht gelesen werden.', 'danger'); }
  });

  form?.addEventListener('submit', () => { rememberLocation(); if (saveButton) { saveButton.disabled = true; saveButton.setAttribute('aria-busy', 'true'); saveButton.textContent = 'Wird gespeichert …'; } });
  window.addEventListener('online', updateConnectivity); window.addEventListener('offline', updateConnectivity);
  window.addEventListener('resize', syncActionBarInset); window.visualViewport?.addEventListener('resize', syncActionBarInset); window.visualViewport?.addEventListener('scroll', syncActionBarInset);
  window.addEventListener('pagehide', () => { stopCamera(); const old = photoPreview?.dataset.objectUrl; if (old) URL.revokeObjectURL(old); });
  prepareMarketplaceUi(); syncActionBarInset(); updateConnectivity(); updateMode(); if (isbn && cleanText(isbn.value)) updateBookLinks(normalizeIsbn(isbn.value));
})();