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
  const recentFilter = $('recent-filter');
  const saveButton = $('inventory-save');
  const replaceMetadata = $('replace-metadata');

  const urls = {
    find: root.dataset.findUrl,
    lookup: root.dataset.lookupUrl,
    amazon: root.dataset.amazonUrl,
    marketplace: '/inventory/marketplace/search',
    objects: root.dataset.objectsUrl,
    detailPattern: root.dataset.detailPattern,
  };

  const fieldMap = {
    title: 'title', authors: 'authors', publisher: 'publisher', published_date: 'published-date',
    page_count: 'page-count', language: 'language', categories: 'categories', description: 'description',
    market_price: 'market-price', currency: 'currency', price_source: 'price-source',
    metadata_source: 'metadata-source', metadata_checked_at: 'metadata-checked-at',
  };

  let stream = null;
  let scanning = false;
  let detector = null;
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
    scanning = false;
    if (stream) { stream.getTracks().forEach((track) => track.stop()); stream = null; }
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
  const applyMetadata = (data) => {
    const overwrite = Boolean(replaceMetadata?.checked);
    Object.entries(fieldMap).forEach(([key, id]) => {
      const element = $(id); const value = data[key];
      if (!element || value === undefined || value === null || cleanText(value) === '') return;
      const shouldProtect = ['title', 'description', 'authors', 'publisher', 'published-date', 'categories'].includes(id);
      if (!overwrite && shouldProtect && cleanText(element.value)) return;
      element.value = String(value);
    });
  };
  const applyMarketplaceMetadata = (data) => {
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
      empty.textContent = data.configured === false
        ? `${providerName}-API ist noch nicht konfiguriert. Du kannst die normale Suche öffnen.`
        : `Keine passenden ${providerName}-Treffer gefunden.`;
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
      if (Array.isArray(data.results) && data.results.length) setStatus(`${data.results.length} Treffer geladen. Besten Treffer prüfen oder einen der drei auswählen.`, 'success');
      else if (data.configured === false) setStatus(`${provider === 'ebay' ? 'eBay' : 'Amazon'}-API noch nicht konfiguriert; direkte Suche ist verfügbar.`, 'warning');
      else setStatus(data.error || 'Keine Marketplace-Treffer gefunden.', 'secondary');
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
  $('restore-location')?.addEventListener('click', restoreLocation); photo?.addEventListener('change', previewPhoto); recentFilter?.addEventListener('input', filterRecent);

  amazonButton?.addEventListener('click', () => searchMarketplace('amazon'));

  $('start-barcode')?.addEventListener('click', async () => {
    if (!('BarcodeDetector' in window)) { setStatus('BarcodeDetector fehlt. Kennung kann manuell eingetragen werden.', 'warning'); barcode?.focus(); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setStatus('Kamera-Zugriff nicht verfügbar; HTTPS kann erforderlich sein.', 'warning'); return; }
    try {
      detector = new BarcodeDetector({ formats: ['ean_13', 'ean_8', 'code_128', 'code_39', 'qr_code'] });
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      const video = $('camera-preview'); video.srcObject = stream; $('camera-box').hidden = false; scanning = true; setStatus('Barcode vor die Kamera halten …', 'primary');
      const scan = async () => {
        if (!scanning) return;
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            const value = cleanText(codes[0].rawValue); barcode.value = value; const canonical = normalizeIsbn(value);
            if (canonical) { isbn.value = canonical; type.value = 'book'; objectTypeWasAutoBook = true; updateMode(); updateBookLinks(canonical); setStatus(`ISBN erkannt: ${canonical}. Lade Buchdaten …`, 'success'); stopCamera(); lookup(); return; }
            isbn.value = ''; updateMode(); setStatus(`Barcode erkannt: ${value}`, 'success'); stopCamera(); duplicateProbe(value, 'identifier'); return;
          }
        } catch (_error) {}
        requestAnimationFrame(scan);
      };
      requestAnimationFrame(scan);
    } catch (_error) { stopCamera(); setStatus('Kamera konnte nicht geöffnet werden.', 'danger'); }
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
