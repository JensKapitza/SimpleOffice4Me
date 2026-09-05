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
    objects: root.dataset.objectsUrl,
    detailPattern: root.dataset.detailPattern,
  };

  const fieldMap = {
    title: 'title',
    authors: 'authors',
    publisher: 'publisher',
    published_date: 'published-date',
    page_count: 'page-count',
    language: 'language',
    categories: 'categories',
    description: 'description',
    market_price: 'market-price',
    currency: 'currency',
    price_source: 'price-source',
    metadata_source: 'metadata-source',
    metadata_checked_at: 'metadata-checked-at',
  };

  let stream = null;
  let scanning = false;
  let detector = null;
  let lookupCooldown = null;
  let lookupAbort = null;
  let finderAbort = null;
  let objectTypeWasAutoBook = false;

  const setStatus = (text, kind = 'secondary') => {
    if (!status) return;
    status.className = `alert alert-${kind} py-2 small`;
    status.textContent = text;
  };

  const cleanText = (value) => String(value || '').trim();

  const isbn13CheckDigit = (prefix) => {
    let total = 0;
    for (let i = 0; i < prefix.length; i += 1) {
      total += Number(prefix[i]) * (i % 2 === 0 ? 1 : 3);
    }
    return String((10 - (total % 10)) % 10);
  };

  const normalizeIsbn = (value) => {
    const raw = cleanText(value)
      .replace(/^ISBN(?:-1[03])?\s*:?\s*/i, '')
      .replace(/[^0-9Xx]/g, '')
      .toUpperCase();

    if (raw.length === 13 && /^97[89]\d{10}$/.test(raw)) {
      return isbn13CheckDigit(raw.slice(0, 12)) === raw[12] ? raw : '';
    }

    if (raw.length === 10 && /^\d{9}[\dX]$/.test(raw)) {
      let sum = 0;
      for (let i = 0; i < 10; i += 1) {
        const digit = raw[i] === 'X' ? 10 : Number(raw[i]);
        sum += (10 - i) * digit;
      }
      if (sum % 11 !== 0) return '';
      const prefix = `978${raw.slice(0, 9)}`;
      return `${prefix}${isbn13CheckDigit(prefix)}`;
    }
    return '';
  };

  const looksLikeBookCode = (value) => {
    const raw = cleanText(value).replace(/[^0-9Xx]/g, '');
    return raw.length === 10 || (raw.length === 13 && /^97[89]/.test(raw));
  };

  const updateMode = () => {
    const book = cleanText(type?.value).toLowerCase() === 'book' || Boolean(cleanText(isbn?.value));
    document.querySelectorAll('.book-only').forEach((element) => {
      element.hidden = !book;
    });
    if (amazonButton) amazonButton.disabled = !book;
  };

  const setBookModeFromIsbn = () => {
    if (!isbn || !type) return;
    if (cleanText(isbn.value)) {
      if (cleanText(type.value).toLowerCase() === 'object') {
        type.value = 'book';
        objectTypeWasAutoBook = true;
      }
    } else if (objectTypeWasAutoBook && cleanText(type.value).toLowerCase() === 'book') {
      type.value = 'object';
      objectTypeWasAutoBook = false;
    }
    updateMode();
  };

  const stopCamera = () => {
    scanning = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
    const box = $('camera-box');
    if (box) box.hidden = true;
  };

  const safeJson = async (response) => {
    try {
      return await response.json();
    } catch (_error) {
      return {};
    }
  };

  const setLookupCooldown = (seconds) => {
    if (!lookupButton) return;
    if (lookupCooldown) clearInterval(lookupCooldown);
    let remaining = Math.max(0, Number(seconds) || 0);
    const original = lookupButton.dataset.label || 'Buchdaten laden';
    lookupButton.dataset.label = original;
    const paint = () => {
      lookupButton.disabled = remaining > 0;
      lookupButton.textContent = remaining > 0 ? `Erneut in ${remaining}s` : original;
      remaining -= 1;
      if (remaining < 0 && lookupCooldown) {
        clearInterval(lookupCooldown);
        lookupCooldown = null;
      }
    };
    paint();
    if (remaining >= 0) lookupCooldown = window.setInterval(paint, 1000);
  };

  const updateBookLinks = (canonicalIsbn) => {
    if (!canonicalIsbn) {
      if (coverWrap) coverWrap.hidden = true;
      if (openLibraryLink) openLibraryLink.hidden = true;
      return;
    }
    if (cover && coverWrap) {
      cover.src = `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(canonicalIsbn)}-M.jpg?default=false`;
      cover.alt = `Buchcover für ISBN ${canonicalIsbn}`;
      coverWrap.hidden = false;
    }
    if (openLibraryLink) {
      openLibraryLink.href = `https://openlibrary.org/isbn/${encodeURIComponent(canonicalIsbn)}`;
      openLibraryLink.hidden = false;
    }
  };

  const applyMetadata = (data) => {
    const overwrite = Boolean(replaceMetadata?.checked);
    Object.entries(fieldMap).forEach(([key, id]) => {
      const element = $(id);
      const value = data[key];
      if (!element || value === undefined || value === null || cleanText(value) === '') return;
      const shouldProtect = ['title', 'description', 'authors', 'publisher', 'published-date', 'categories'].includes(id);
      if (!overwrite && shouldProtect && cleanText(element.value)) return;
      element.value = String(value);
    });
  };

  const lookup = async () => {
    if (!isbn || !lookupButton) return;
    const entered = cleanText(isbn.value) || cleanText(barcode?.value);
    if (!entered) {
      setStatus('Bitte zuerst eine ISBN eingeben oder scannen.', 'warning');
      isbn?.focus();
      return;
    }

    const canonical = normalizeIsbn(entered);
    if (!canonical) {
      setStatus('Die ISBN ist ungültig. ISBN-10 und ISBN-13 mit oder ohne Bindestriche werden unterstützt.', 'warning');
      isbn?.focus();
      isbn?.select();
      return;
    }

    isbn.value = canonical;
    if (barcode && !cleanText(barcode.value)) barcode.value = canonical;
    type.value = 'book';
    objectTypeWasAutoBook = true;
    updateMode();
    updateBookLinks(canonical);

    if (lookupAbort) lookupAbort.abort();
    lookupAbort = new AbortController();
    lookupButton.disabled = true;
    lookupButton.setAttribute('aria-busy', 'true');
    setStatus(`Suche Buchdaten für ISBN ${canonical} …`, 'primary');

    try {
      const response = await fetch(`${urls.lookup}?isbn=${encodeURIComponent(canonical)}`, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
        signal: lookupAbort.signal,
      });
      const data = await safeJson(response);
      if (!response.ok) {
        const retryAfter = Number(data.retry_after || response.headers.get('Retry-After') || 0);
        if (response.status === 429 && retryAfter) setLookupCooldown(retryAfter);
        setStatus(data.error || `Buchdaten konnten nicht geladen werden (${response.status}).`, response.status >= 500 ? 'danger' : 'warning');
        return;
      }

      applyMetadata(data);
      isbn.value = data.isbn || canonical;
      if (barcode && !cleanText(barcode.value)) barcode.value = isbn.value;
      type.value = 'book';
      updateMode();
      updateBookLinks(isbn.value);
      const source = data.metadata_source || 'Open Library';
      const errors = Array.isArray(data.lookup_errors) && data.lookup_errors.length
        ? ` Hinweis: ${data.lookup_errors.join(', ')}`
        : '';
      setStatus(`Buchdaten geladen: ${source}. Bitte kurz prüfen.${errors}`, 'success');
      setLookupCooldown(5);
    } catch (error) {
      if (error?.name !== 'AbortError') {
        const offline = navigator.onLine === false ? ' Das Gerät ist offline.' : '';
        setStatus(`Buchdatenabruf ist fehlgeschlagen.${offline}`, 'danger');
      }
    } finally {
      lookupButton.removeAttribute('aria-busy');
      if (!lookupCooldown) lookupButton.disabled = false;
    }
  };

  const renderFinderMatches = (matches, query) => {
    if (!findResults) return;
    findResults.innerHTML = '';
    if (!matches.length) {
      findResults.hidden = true;
      return;
    }
    const heading = document.createElement('div');
    heading.className = 'small text-secondary mb-1';
    heading.textContent = `Lokale Treffer für „${query}“`;
    findResults.appendChild(heading);
    const group = document.createElement('div');
    group.className = 'list-group';
    matches.slice(0, 8).forEach((row) => {
      const link = document.createElement('a');
      link.className = 'list-group-item list-group-item-action py-2';
      link.href = row.dataset.url;
      link.textContent = row.dataset.label || row.textContent.trim();
      group.appendChild(link);
    });
    findResults.appendChild(group);
    findResults.hidden = false;
  };

  const localFinderMatches = (query) => {
    const needle = cleanText(query).toLowerCase();
    if (!needle) return [];
    return Array.from(document.querySelectorAll('[data-inventory-search]')).filter((row) =>
      cleanText(row.dataset.inventorySearch).toLowerCase().includes(needle)
    );
  };

  const quickFind = async () => {
    if (!findInput) return;
    const raw = cleanText(findInput.value);
    if (!raw) {
      findInput.focus();
      return;
    }

    const localMatches = localFinderMatches(raw);
    renderFinderMatches(localMatches, raw);

    const canonicalIsbn = normalizeIsbn(raw);
    const query = canonicalIsbn || raw.replace(/^#\s*/, '').trim();
    if (finderAbort) finderAbort.abort();
    finderAbort = new AbortController();
    findInput.setAttribute('aria-busy', 'true');
    setStatus(`Suche Inventar nach „${raw}“ …`, 'primary');

    try {
      const response = await fetch(
        `${urls.find}?identifier=${encodeURIComponent(query)}&nfc=${encodeURIComponent(query)}`,
        { headers: { Accept: 'application/json' }, cache: 'no-store', signal: finderAbort.signal },
      );
      const data = await safeJson(response);
      if (response.ok && data.found && data.url) {
        window.location.assign(data.url);
        return;
      }

      const numeric = raw.replace(/^#\s*/, '').trim();
      if (/^\d+$/.test(numeric) && urls.detailPattern) {
        const detailUrl = urls.detailPattern.replace('__OBJECT__', encodeURIComponent(numeric));
        const detailResponse = await fetch(detailUrl, {
          headers: { Accept: 'text/html' },
          cache: 'no-store',
          signal: finderAbort.signal,
        });
        if (detailResponse.ok) {
          window.location.assign(detailUrl);
          return;
        }
      }

      if (localMatches.length === 1) {
        window.location.assign(localMatches[0].dataset.url);
        return;
      }

      const fallback = `${urls.objects}?q=${encodeURIComponent(raw)}`;
      setStatus(localMatches.length
        ? `${localMatches.length} lokale Treffer gefunden. Für die vollständige Suche wird die Objektliste verwendet.`
        : `Kein exakter Kennungstreffer. Öffne die vollständige Inventarsuche für „${raw}“.`, 'secondary');
      window.location.assign(fallback);
    } catch (error) {
      if (error?.name !== 'AbortError') {
        const fallback = `${urls.objects}?q=${encodeURIComponent(raw)}`;
        setStatus('Schnellsuche nicht erreichbar – öffne die vollständige Objektliste.', 'warning');
        window.location.assign(fallback);
      }
    } finally {
      findInput.removeAttribute('aria-busy');
    }
  };

  const duplicateProbe = async (value, source) => {
    const raw = cleanText(value);
    if (!raw || !urls.find) return;
    const query = normalizeIsbn(raw) || raw;
    try {
      const response = await fetch(
        `${urls.find}?identifier=${encodeURIComponent(source === 'nfc' ? '' : query)}&nfc=${encodeURIComponent(source === 'nfc' ? query : '')}`,
        { headers: { Accept: 'application/json' }, cache: 'no-store' },
      );
      const data = await safeJson(response);
      if (response.ok && data.found) {
        setStatus(`Bereits vorhanden: #${data.display_id || ''} ${data.name || ''}.`, 'warning');
      }
    } catch (_error) {
      // Duplicate probing is opportunistic and must never block capture.
    }
  };

  const previewPhoto = () => {
    if (!photoPreview || !photo?.files?.length) return;
    const file = photo.files[0];
    if (file.size > 12 * 1024 * 1024) {
      photo.value = '';
      photoPreview.hidden = true;
      setStatus('Das Foto ist größer als 12 MiB.', 'warning');
      return;
    }
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) {
      photo.value = '';
      photoPreview.hidden = true;
      setStatus('Nur JPEG, PNG oder WebP sind erlaubt.', 'warning');
      return;
    }
    const old = photoPreview.dataset.objectUrl;
    if (old) URL.revokeObjectURL(old);
    const objectUrl = URL.createObjectURL(file);
    photoPreview.dataset.objectUrl = objectUrl;
    photoPreview.src = objectUrl;
    photoPreview.hidden = false;
  };

  const rememberLocation = () => {
    const location = $('location');
    if (!location) return;
    const value = cleanText(location.value);
    try {
      if (value) localStorage.setItem('simpleoffice.inventory.lastLocation', value);
    } catch (_error) {}
  };

  const restoreLocation = () => {
    const location = $('location');
    if (!location || cleanText(location.value)) return;
    try {
      const saved = localStorage.getItem('simpleoffice.inventory.lastLocation');
      if (saved) location.value = saved;
    } catch (_error) {}
  };

  const clearBookMetadata = () => {
    ['authors', 'publisher', 'published-date', 'page-count', 'language', 'categories', 'description', 'market-price', 'price-source', 'metadata-source', 'metadata-checked-at']
      .forEach((id) => {
        const element = $(id);
        if (element) element.value = '';
      });
    if ($('currency')) $('currency').value = 'EUR';
    setStatus('Geladene Buchdaten wurden aus dem Formular entfernt. ISBN und Barcode bleiben erhalten.', 'secondary');
  };

  const updateConnectivity = () => {
    const badge = $('inventory-network');
    if (!badge) return;
    const online = navigator.onLine !== false;
    badge.textContent = online ? 'online' : 'offline';
    badge.className = `badge ${online ? 'text-bg-success' : 'text-bg-warning'}`;
  };

  const filterRecent = () => {
    const needle = cleanText(recentFilter?.value).toLowerCase();
    document.querySelectorAll('[data-recent-row]').forEach((row) => {
      row.hidden = Boolean(needle) && !cleanText(row.dataset.inventorySearch).toLowerCase().includes(needle);
    });
  };

  findForm?.addEventListener('submit', (event) => {
    event.preventDefault();
    quickFind();
  });
  findInput?.addEventListener('input', () => renderFinderMatches(localFinderMatches(findInput.value), findInput.value));

  type?.addEventListener('input', () => {
    objectTypeWasAutoBook = false;
    updateMode();
  });
  isbn?.addEventListener('input', () => {
    setBookModeFromIsbn();
    const canonical = normalizeIsbn(isbn.value);
    if (canonical) updateBookLinks(canonical);
  });
  isbn?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      lookup();
    }
  });
  isbn?.addEventListener('blur', () => duplicateProbe(isbn.value, 'identifier'));
  barcode?.addEventListener('blur', () => duplicateProbe(barcode.value, 'identifier'));
  nfc?.addEventListener('blur', () => duplicateProbe(nfc.value, 'nfc'));
  lookupButton?.addEventListener('click', lookup);
  $('clear-book-data')?.addEventListener('click', clearBookMetadata);
  $('book-mode')?.addEventListener('click', () => {
    type.value = 'book';
    objectTypeWasAutoBook = false;
    updateMode();
    isbn.focus();
  });
  $('object-mode')?.addEventListener('click', () => {
    type.value = 'object';
    objectTypeWasAutoBook = false;
    updateMode();
    title.focus();
  });
  $('restore-location')?.addEventListener('click', restoreLocation);
  photo?.addEventListener('change', previewPhoto);
  recentFilter?.addEventListener('input', filterRecent);

  amazonButton?.addEventListener('click', () => {
    const q = cleanText(isbn?.value) || cleanText(barcode?.value) || cleanText(title?.value);
    if (!q) {
      setStatus('Für die Amazon-Suche zuerst ISBN, Barcode oder Titel erfassen.', 'warning');
      return;
    }
    window.open(`${urls.amazon}?q=${encodeURIComponent(q)}`, '_blank', 'noopener,noreferrer');
  });

  $('start-barcode')?.addEventListener('click', async () => {
    if (!('BarcodeDetector' in window)) {
      setStatus('BarcodeDetector fehlt. Kennung kann manuell eingetragen werden.', 'warning');
      barcode?.focus();
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus('Kamera-Zugriff nicht verfügbar; HTTPS kann erforderlich sein.', 'warning');
      return;
    }
    try {
      detector = new BarcodeDetector({ formats: ['ean_13', 'ean_8', 'code_128', 'code_39', 'qr_code'] });
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      const video = $('camera-preview');
      video.srcObject = stream;
      $('camera-box').hidden = false;
      scanning = true;
      setStatus('Barcode vor die Kamera halten …', 'primary');
      const scan = async () => {
        if (!scanning) return;
        try {
          const codes = await detector.detect(video);
          if (codes.length) {
            const value = cleanText(codes[0].rawValue);
            barcode.value = value;
            const canonical = normalizeIsbn(value);
            if (canonical) {
              isbn.value = canonical;
              type.value = 'book';
              objectTypeWasAutoBook = true;
              updateMode();
              updateBookLinks(canonical);
              setStatus(`ISBN erkannt: ${canonical}. Lade Buchdaten …`, 'success');
              stopCamera();
              lookup();
              return;
            }
            isbn.value='';
            updateMode();
            setStatus(`Barcode erkannt: ${value}`, 'success');
            stopCamera();
            duplicateProbe(value, 'identifier');
            return;
          }
        } catch (_error) {}
        requestAnimationFrame(scan);
      };
      requestAnimationFrame(scan);
    } catch (_error) {
      stopCamera();
      setStatus('Kamera konnte nicht geöffnet werden.', 'danger');
    }
  });

  $('scan-nfc')?.addEventListener('click', async () => {
    if (!('NDEFReader' in window)) {
      setStatus('Web NFC wird nicht unterstützt. NFC-Wert kann manuell eingetragen werden.', 'warning');
      nfc?.focus();
      return;
    }
    try {
      const reader = new NDEFReader();
      await reader.scan();
      setStatus('NFC-Tag an das Handy halten …', 'primary');
      reader.addEventListener('reading', (event) => {
        const parts = [];
        if (event.serialNumber) parts.push(event.serialNumber);
        for (const record of event.message.records) {
          try {
            if (record.data) parts.push(new TextDecoder(record.encoding || 'utf-8').decode(record.data));
          } catch (_error) {}
        }
        nfc.value = parts.filter(Boolean).join(' | ').slice(0, 240);
        setStatus('NFC wurde gelesen.', 'success');
        duplicateProbe(nfc.value, 'nfc');
      }, { once: true });
    } catch (_error) {
      setStatus('NFC konnte nicht gelesen werden.', 'danger');
    }
  });

  form?.addEventListener('submit', () => {
    rememberLocation();
    if (saveButton) {
      saveButton.disabled = true;
      saveButton.setAttribute('aria-busy', 'true');
      saveButton.textContent = 'Wird gespeichert …';
    }
  });

  window.addEventListener('online', updateConnectivity);
  window.addEventListener('offline', updateConnectivity);
  window.addEventListener('pagehide', () => {
    stopCamera();
    const old = photoPreview?.dataset.objectUrl;
    if (old) URL.revokeObjectURL(old);
  });

  updateConnectivity();
  updateMode();
  if (isbn && cleanText(isbn.value)) updateBookLinks(normalizeIsbn(isbn.value));
})();
