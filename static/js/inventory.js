(() => {
  'use strict';

  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    unavailable503: EN ? 'Local object recognition is not set up. Please configure Ollama and a vision model.' : 'Lokale Objekterkennung ist nicht eingerichtet. Bitte Ollama und ein Vision-Modell konfigurieren.', loadBookData: EN ? 'Load book data' : 'Buchdaten laden',
    retryIn: (s) => EN ? `Retry in ${s}s` : `Erneut in ${s}s`, bookCover: (isbn) => EN ? `Book cover for ISBN ${isbn}` : `Buchcover für ISBN ${isbn}`,
    conflictSources: (sources) => EN ? `Conflicting sources: ${sources}. Adopted values need review; nothing is saved automatically.` : `Konflikt zwischen Quellen: ${sources}. Übernommene Werte bitte prüfen; nichts wird automatisch gespeichert.`, metaSource: (sources) => EN ? `Metadata source: ${sources}. Please review values; nothing is saved automatically.` : `Metadatenquelle: ${sources}. Werte bitte prüfen; nichts wird automatisch gespeichert.`,
    marketplaceAdopted: (provider) => EN ? `Data adopted from ${provider}. Please review briefly.` : `Daten von ${provider} übernommen. Bitte kurz prüfen.`, needIsbn: EN ? 'Please enter or scan an ISBN first.' : 'Bitte zuerst eine ISBN eingeben oder scannen.',
    isbnInvalid: EN ? 'The ISBN is invalid. ISBN-10 and ISBN-13 with or without hyphens are supported.' : 'Die ISBN ist ungültig. ISBN-10 und ISBN-13 mit oder ohne Bindestriche werden unterstützt.', searchingIsbn: (isbn) => EN ? `Looking up book data for ISBN ${isbn} …` : `Suche Buchdaten für ISBN ${isbn} …`,
    loadFailed: (code) => EN ? `Could not load book data (${code}).` : `Buchdaten konnten nicht geladen werden (${code}).`, bookLoaded: (source, errors) => EN ? `Book data loaded: ${source}. Please review briefly.${errors}` : `Buchdaten geladen: ${source}. Bitte kurz prüfen.${errors}`,
    notePrefix: EN ? ' Note: ' : ' Hinweis: ', fetchFailed: (offline) => EN ? `Book lookup failed.${offline ? ' The device is offline.' : ''}` : `Buchdatenabruf ist fehlgeschlagen.${offline ? ' Das Gerät ist offline.' : ''}`,
    marketplaceBlocked: EN ? 'Unsafe marketplace target was blocked.' : 'Unsicheres Marketplace-Ziel wurde blockiert.', noMarketHits: (provider) => EN ? `No automatically readable ${provider} hits found. The regular search can be opened.` : `Keine automatisch lesbaren ${provider}-Treffer gefunden. Die normale Suche kann geöffnet werden.`,
    openSearch: (provider) => EN ? `Open ${provider} search` : `${provider}-Suche öffnen`, bestHits: (provider, n) => EN ? `${provider}: best ${n} hits` : `${provider}: beste ${n} Treffer`,
    hitFallback: (provider, i) => EN ? `${provider} hit ${i + 1}` : `${provider}-Treffer ${i + 1}`, bestHit: EN ? 'Best match' : 'Bester Treffer',
    adoptData: EN ? 'Adopt data' : 'Daten übernehmen', viewOffer: EN ? 'View offer' : 'Angebot ansehen',
    needQuery: EN ? 'For the marketplace search, first capture ISBN, barcode or object name.' : 'Für die Marketplace-Suche zuerst ISBN, Barcode oder Objektname erfassen.', searchingMarketplace: (provider, query) => EN ? `Searching ${provider} for “${query}” …` : `Suche ${provider} nach „${query}“ …`,
    marketplaceFailed: EN ? 'Marketplace search failed.' : 'Marketplace-Suche fehlgeschlagen.', apiFallback: EN ? 'API fallback' : 'API-Fallback',
    publicSearch: EN ? 'public search' : 'öffentliche Suche', hitsLoaded: (n, source) => EN ? `${n} hits loaded via ${source}. Review and select hits.` : `${n} Treffer über ${source} geladen. Treffer prüfen und auswählen.`,
    noMarketHits2: EN ? 'No marketplace hits found.' : 'Keine Marketplace-Treffer gefunden.', searchOffline: (offline) => EN ? `Marketplace search unreachable.${offline ? ' The device is offline.' : ''}` : `Marketplace-Suche nicht erreichbar.${offline ? ' Das Gerät ist offline.' : ''}`,
    searchAmazon: EN ? 'Search Amazon data' : 'Amazon Daten suchen', searchEbay: EN ? 'Search eBay data' : 'eBay Daten suchen',
    localHits: (query) => EN ? `Local matches for “${query}”` : `Lokale Treffer für „${query}“`, searchingInventory: (raw) => EN ? `Searching inventory for “${raw}” …` : `Suche Inventar nach „${raw}“ …`,
    localHitsFallback: (n) => EN ? `${n} local matches found. The object list is used for the full search.` : `${n} lokale Treffer gefunden. Für die vollständige Suche wird die Objektliste verwendet.`, noExactHit: (raw) => EN ? `No exact ID match. Opening the full inventory search for “${raw}”.` : `Kein exakter Kennungstreffer. Öffne die vollständige Inventarsuche für „${raw}“.`,
    quickSearchOffline: EN ? 'Quick search unreachable – opening the full object list.' : 'Schnellsuche nicht erreichbar – öffne die vollständige Objektliste.', duplicateFound: (id, name) => EN ? `Already exists: #${id} ${name}.` : `Bereits vorhanden: #${id} ${name}.`,
    subtitleLabel: EN ? 'Subtitle: ' : 'Untertitel: ', photoTooBig12: EN ? 'The photo exceeds 12 MiB.' : 'Das Foto ist größer als 12 MiB.',
    photoType: EN ? 'Only JPEG, PNG or WebP are allowed.' : 'Nur JPEG, PNG oder WebP sind erlaubt.', previewFailed: EN ? 'The image could not be safely decoded as a preview.' : 'Das Bild konnte nicht sicher als Vorschau dekodiert werden.',
    bookDataRemoved: EN ? 'Loaded book data was removed from the form. ISBN and barcode are kept.' : 'Geladene Buchdaten wurden aus dem Formular entfernt. ISBN und Barcode bleiben erhalten.', seriesHint: (n, max) => EN ? `Series: ${n}/${max} photos. Each photo is analyzed locally one by one — nothing is saved automatically.` : `Serie: ${n}/${max} Fotos. Jedes Foto wird einzeln lokal analysiert — nichts wird automatisch gespeichert.`,
    notAvailable: EN ? 'Suggestions are not available here.' : 'Vorschläge sind hier nicht verfügbar.', analyzing: EN ? 'Analyzing …' : 'Wird analysiert …',
    suggestFailed: (code) => EN ? `Suggestion failed (${code}).` : `Vorschlag fehlgeschlagen (${code}).`, emptySuggestion: EN ? 'The suggestion was empty. Please try again.' : 'Der Vorschlag war leer. Bitte erneut versuchen.',
    readySuggestion: (label) => EN ? `Suggestion ready (${label}).` : `Vorschlag bereit (${label}).`, readySuggestionPlain: EN ? 'Suggestion ready.' : 'Vorschlag bereit.',
    analysisFailed: EN ? 'Analysis failed.' : 'Die Analyse ist fehlgeschlagen.', maxSeries: (max) => EN ? `At most ${max} photos per series – further ones were ignored.` : `Maximal ${max} Fotos je Serie – weitere wurden ignoriert.`,
    waitingAnalysis: EN ? 'Waiting for analysis …' : 'Wartet auf Analyse …', photoEmpty: EN ? 'The photo is empty or exceeds 5 MiB.' : 'Das Foto ist leer oder größer als 5 MiB.',
    photoUnreadable: EN ? 'The photo could not be read.' : 'Das Foto konnte nicht gelesen werden.', fileInvalid: EN ? 'File still invalid (JPEG/PNG/WebP, max. 5 MiB).' : 'Datei weiterhin ungültig (JPEG/PNG/WebP, max. 5 MiB).',
    transferFailed: EN ? 'The series photo could not be transferred into the form.' : 'Das Serienfoto konnte nicht ins Formular übernommen werden.', transferred: EN ? 'Adopted into the form – please review, then save (page and series are kept).' : 'Ins Formular übernommen – bitte prüfen, dann speichern (Seite und Serie bleiben erhalten).',
    seriesAdopted: EN ? 'Series suggestion adopted. Please check the fields, then save – the page is kept.' : 'Serienvorschlag übernommen. Bitte Felder prüfen, dann speichern – die Seite bleibt erhalten.', checkIdle: (hasCode) => EN ? (hasCode ? 'Check not available here.' : 'Please enter an ID first.') : (hasCode ? 'Prüfung hier nicht verfügbar.' : 'Bitte zuerst eine Kennung eintragen.'),
    checkFailed: (code) => EN ? `Check failed (${code}).` : `Prüfung fehlgeschlagen (${code}).`, checkUnclear: EN ? 'The check was inconclusive. Please try again.' : 'Die Prüfung war unverständlich. Bitte erneut versuchen.',
    hitFound: (name, id) => EN ? `Hit: ${name} (${id}…)` : `Treffer: ${name} (${id}…)`, objectFallback: EN ? 'Object' : 'Objekt',
    ambiguous: (n, names) => EN ? `Ambiguous: ${n} hits (${names}) – please check manually.` : `Mehrdeutig: ${n} Treffer (${names}) – bitte manuell prüfen.`, noHit: EN ? 'No hit – create as a new object via “Adopt into form”.' : 'Kein Treffer – per „Ins Formular übernehmen“ als neues Objekt anlegen.',
    checkFailed2: EN ? 'The check failed.' : 'Die Prüfung ist fehlgeschlagen.', attachUnavailable: EN ? 'Attaching not available here.' : 'Anhängen hier nicht verfügbar.',
    attachFailed: (code) => EN ? `Attaching failed (${code}). Please check again.` : `Anhängen fehlgeschlagen (${code}). Bitte erneut prüfen.`, attachFailed2: EN ? 'Attaching failed. Please check again.' : 'Das Anhängen ist fehlgeschlagen. Bitte erneut prüfen.',
    photoAttached: EN ? 'Photo attached.' : 'Foto angehängt.', openEntry: EN ? 'Open entry' : 'Eintrag öffnen',
    adoptIsbnBtn: EN ? 'Adopt ISBN' : 'ISBN übernehmen', checkInventoryBtn: EN ? 'Check in inventory' : 'Im Inventar prüfen',
    attachPhotoBtn: EN ? 'Attach photo' : 'Foto anhängen', adoptIntoFormBtn: EN ? 'Adopt into form' : 'Ins Formular übernehmen',
    analyzeAgainBtn: EN ? 'Analyze again' : 'Erneut analysieren', removeBtn: EN ? 'Remove' : 'Entfernen',
    identifierPlaceholder: EN ? 'Barcode / ISBN (optional)' : 'Barcode / ISBN (optional)', identifierAria: (id) => EN ? `ID for series photo ${id} (optional)` : `Kennung für Serienfoto ${id} (optional)`,
    seriesPhotoAlt: (id) => EN ? `Series photo ${id}` : `Serienfoto ${id}`, photoFallback: EN ? 'Photo' : 'Foto',
    isbnInPhoto: (isbn) => EN ? `ISBN in photo: ${isbn}` : `ISBN im Foto: ${isbn}`, checklistTitle: EN ? 'Title/Type' : 'Titel/Typ',
    checklistIdIsbn: EN ? 'ID (ISBN/barcode)' : 'Kennung (ISBN/Barcode)', checklistIdOptional: EN ? 'ID (optional)' : 'Kennung (optional)',
    checklistShelf: EN ? 'Shelf (series)' : 'Regal (Serie)', checklistShelfBooks: EN ? 'Shelf (books only)' : 'Regal (nur für Bücher)',
    checklistPrice: EN ? 'Price (recommendation)' : 'Preis (Empfehlung)', checklistShow: EN ? 'Show' : 'Anzeigen',
    saveFirst: EN ? 'Please adopt into the form and review first.' : 'Bitte zuerst ins Formular übernehmen und prüfen.', needTitleShelf: EN ? 'Please check title and shelf in the form.' : 'Bitte Titel und Regal im Formular prüfen.',
    needTitle: EN ? 'Please enter a title in the form.' : 'Bitte einen Titel im Formular eingeben.', saveFailed: (code) => EN ? `Saving failed (${code}).` : `Speichern fehlgeschlagen (${code}).`,
    savedFallback: EN ? 'Saved.' : 'Gespeichert.', savedOk: (id, name) => EN ? `Saved: #${id} ${name}`.trim() : `Gespeichert: #${id} ${name}`.trim(),
    seriesSaved: EN ? 'Series photo saved. Shelf stays – adopt the next photo.' : 'Serienfoto gespeichert. Regal bleibt – nächstes Foto übernehmen.', alreadyCaptured: EN ? 'Already captured.' : 'Bereits erfasst.',
    alreadyCapturedRepeat: EN ? 'Already captured (repeat) – no duplicate created.' : 'Bereits erfasst (Wiederholung) – kein Duplikat angelegt.', alreadyThere: EN ? 'Already exists – no duplicate created.' : 'Bereits vorhanden – kein Duplikat angelegt.',
    duplicateHint: EN ? ' To store another copy, allow duplicates and save again.' : ' Für ein weiteres Exemplar „Trotz gleicher Kennung …“ aktivieren und erneut speichern.', unclearResponse: EN ? 'Unclear response – nothing saved.' : 'Unverständliche Antwort – nichts gespeichert.',
    saveNetwork: EN ? 'Saving failed (network). Try again – the ID prevents duplicates.' : 'Speichern fehlgeschlagen (Netzwerk). Erneut versuchen – die Kennung verhindert Duplikate.', saving: EN ? 'Saving …' : 'Wird gespeichert …',
    noScanner: EN ? 'No scanner available. Please enter the ID manually.' : 'Kein Scanner verfügbar. Kennung bitte manuell eingeben.', noCamera: EN ? 'Camera access unavailable; HTTPS may be required.' : 'Kamera-Zugriff nicht verfügbar; HTTPS kann erforderlich sein.',
    holdBarcode: EN ? 'Hold the barcode in front of the camera …' : 'Barcode vor die Kamera halten …', holdQr: EN ? 'Hold the QR code in front of the camera; enter EAN/barcodes manually.' : 'QR-Code vor die Kamera halten; EAN/Barcodes bitte manuell eingeben.',
    isbnFound: (code) => EN ? `ISBN recognized: ${code}. Loading book data …` : `ISBN erkannt: ${code}. Lade Buchdaten …`, barcodeFoundPhoto: (value) => EN ? `Barcode recognized: ${value}. No ISBN – please take a photo, analysis starts automatically.` : `Barcode erkannt: ${value}. Kein ISBN – bitte ein Foto aufnehmen, die Analyse startet automatisch.`,
    barcodeFound: (value) => EN ? `Barcode recognized: ${value}` : `Barcode erkannt: ${value}`, camDenied: EN ? 'Camera access denied. Please check the browser permission.' : 'Kamerazugriff verweigert. Bitte Berechtigung im Browser prüfen.',
    camFailed: EN ? 'Could not open the camera. Please check permissions or camera.' : 'Kamera konnte nicht geöffnet werden. Bitte Berechtigungen oder Kamera prüfen.', nfcUnsupported: EN ? 'Web NFC is not supported. The NFC value can be entered manually.' : 'Web NFC wird nicht unterstützt. NFC-Wert kann manuell eingetragen werden.',
    nfcHold: EN ? 'Hold the NFC tag to the phone …' : 'NFC-Tag an das Handy halten …', nfcRead: EN ? 'NFC read.' : 'NFC wurde gelesen.',
    nfcFailed: EN ? 'Could not read NFC.' : 'NFC konnte nicht gelesen werden.',
  };

  const root = document.getElementById('inventory-app');
  if (!root) return;

  const $ = (id) => document.getElementById(id);
  const status = $('scan-status');
  const isbn = $('isbn');
  const barcode = $('barcode');
  const nfc = $('nfc-id');
  const type = $('item-type');
  const title = $('title');
  const description = $('description');
  const tags = $('tags');
  const suggestionHelpers = (typeof window !== 'undefined' && window.SimpleOfficeSuggestionFields) || null;
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
  const LOCAL_UNAVAILABLE_MESSAGE = STR.unavailable503;
  const navigation = window.SimpleOfficeInventoryNavigation;
  const {safeLocalUrl, safeMarketplaceUrl} = navigation;
  const navigateLocal = (value) => navigation.navigateLocal(value, setStatus);

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
    const original = lookupButton.dataset.label || STR.loadBookData; lookupButton.dataset.label = original;
    const paint = () => {
      lookupButton.disabled = remaining > 0;
      lookupButton.textContent = remaining > 0 ? STR.retryIn(remaining) : original;
      remaining -= 1;
      if (remaining < 0 && lookupCooldown) { clearInterval(lookupCooldown); lookupCooldown = null; }
    };
    paint(); if (remaining >= 0) lookupCooldown = window.setInterval(paint, 1000);
  };

  const updateBookLinks = (canonicalIsbn) => {
    if (!canonicalIsbn) { if (coverWrap) coverWrap.hidden = true; if (openLibraryLink) openLibraryLink.hidden = true; return; }
    if (cover && coverWrap) {
      cover.src = `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(canonicalIsbn)}-M.jpg?default=false`;
      cover.alt = STR.bookCover(canonicalIsbn); coverWrap.hidden = false;
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
      ? STR.conflictSources(sources.join(', '))
      : STR.metaSource(sources.join(', '));
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
    setStatus(STR.marketplaceAdopted(data.provider === 'ebay' ? 'eBay' : 'Amazon'), 'success');
  };

  const lookup = async () => {
    if (!isbn || !lookupButton) return;
    const entered = cleanText(isbn.value) || cleanText(barcode?.value);
    if (!entered) { setStatus(STR.needIsbn, 'warning'); isbn?.focus(); return; }
    const canonical = normalizeIsbn(entered);
    if (!canonical) { setStatus(STR.isbnInvalid, 'warning'); isbn?.focus(); isbn?.select(); return; }
    isbn.value = canonical; if (barcode && !cleanText(barcode.value)) barcode.value = canonical;
    type.value = 'book'; objectTypeWasAutoBook = true; updateMode(); updateBookLinks(canonical);
    if (lookupAbort) lookupAbort.abort(); lookupAbort = new AbortController();
    lookupButton.disabled = true; lookupButton.setAttribute('aria-busy', 'true'); setStatus(STR.searchingIsbn(canonical), 'primary');
    try {
      const response = await fetch(`${urls.lookup}?isbn=${encodeURIComponent(canonical)}`, { headers: { Accept: 'application/json' }, cache: 'no-store', signal: lookupAbort.signal });
      const data = await safeJson(response);
      if (!response.ok) {
        const retryAfter = Number(data.retry_after || response.headers.get('Retry-After') || 0);
        if (response.status === 429 && retryAfter) setLookupCooldown(retryAfter);
        setStatus(data.error || STR.loadFailed(response.status), response.status >= 500 ? 'danger' : 'warning'); return;
      }
      applyMetadata(data); isbn.value = data.isbn || canonical; if (barcode && !cleanText(barcode.value)) barcode.value = isbn.value;
      type.value = 'book'; updateMode(); updateBookLinks(isbn.value);
      const source = data.metadata_source || 'Open Library';
      const errors = Array.isArray(data.lookup_errors) && data.lookup_errors.length ? `${STR.notePrefix}${data.lookup_errors.join(', ')}` : '';
      setStatus(STR.bookLoaded(source, errors), 'success'); setLookupCooldown(5);
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus(STR.fetchFailed(typeof navigator !== 'undefined' && navigator.onLine === false), 'danger');
    } finally { lookupButton.removeAttribute('aria-busy'); if (!lookupCooldown) lookupButton.disabled = false; }
  };

  const marketplaceQuery = () => {
    const canonical = normalizeIsbn(isbn?.value);
    return canonical || cleanText(barcode?.value) || cleanText(title?.value);
  };
  const openMarketplace = (value) => {
    const target = safeMarketplaceUrl(value);
    if (!target) { setStatus(STR.marketplaceBlocked, 'danger'); return; }
    window.open(target, '_blank', 'noopener,noreferrer');
  };
  const renderMarketplaceResults = (provider, data) => {
    if (!marketplaceResults) return;
    marketplaceResults.replaceChildren();
    const providerName = provider === 'ebay' ? 'eBay' : 'Amazon';
    const rows = Array.isArray(data.results) ? data.results.slice(0, 3) : [];
    if (!rows.length) {
      const empty = document.createElement('div'); empty.className = 'alert alert-secondary mb-2';
      empty.textContent = cleanText(data.warning) || STR.noMarketHits(providerName);
      marketplaceResults.appendChild(empty);
      const fallback = safeMarketplaceUrl(data.fallback_url);
      if (fallback) {
        const open = document.createElement('button'); open.type = 'button'; open.className = 'btn btn-outline-secondary w-100';
        open.textContent = STR.openSearch(providerName); open.addEventListener('click', () => openMarketplace(fallback));
        marketplaceResults.appendChild(open);
      }
      marketplaceResults.hidden = false; return;
    }
    const heading = document.createElement('div'); heading.className = 'fw-semibold mb-2';
    heading.textContent = STR.bestHits(providerName, rows.length); marketplaceResults.appendChild(heading);
    const list = document.createElement('div'); list.className = 'list-group';
    rows.forEach((row, index) => {
      const item = document.createElement('div'); item.className = 'list-group-item';
      const top = document.createElement('div'); top.className = 'd-flex justify-content-between gap-2 align-items-start';
      const text = document.createElement('div'); text.className = 'flex-grow-1';
      const name = document.createElement('div'); name.className = 'fw-semibold'; name.textContent = cleanText(row.title) || STR.hitFallback(providerName, index);
      text.appendChild(name);
      if (index === 0) { const badge = document.createElement('span'); badge.className = 'badge text-bg-success ms-2'; badge.textContent = STR.bestHit; name.appendChild(badge); }
      const details = [cleanText(row.authors), cleanText(row.manufacturer), cleanText(row.categories)].filter(Boolean).join(' · ');
      if (details) { const meta = document.createElement('div'); meta.className = 'small text-secondary'; meta.textContent = details; text.appendChild(meta); }
      const price = [cleanText(row.market_price), cleanText(row.currency)].filter(Boolean).join(' ');
      if (price) { const amount = document.createElement('div'); amount.className = 'small fw-semibold mt-1'; amount.textContent = price; text.appendChild(amount); }
      top.appendChild(text); item.appendChild(top);
      const controls = document.createElement('div'); controls.className = 'd-grid d-sm-flex gap-2 mt-2';
      const take = document.createElement('button'); take.type = 'button'; take.className = `btn ${index === 0 ? 'btn-success' : 'btn-outline-success'} btn-sm`;
      take.textContent = STR.adoptData; take.addEventListener('click', () => applyMarketplaceMetadata(row)); controls.appendChild(take);
      const target = safeMarketplaceUrl(row.url);
      if (target) { const view = document.createElement('button'); view.type = 'button'; view.className = 'btn btn-outline-secondary btn-sm'; view.textContent = STR.viewOffer; view.addEventListener('click', () => openMarketplace(target)); controls.appendChild(view); }
      item.appendChild(controls); list.appendChild(item);
    });
    marketplaceResults.appendChild(list); marketplaceResults.hidden = false;
  };
  const searchMarketplace = async (provider) => {
    const query = marketplaceQuery();
    if (!query) { setStatus(STR.needQuery, 'warning'); title?.focus(); return; }
    const button = provider === 'ebay' ? ebayButton : amazonButton;
    if (marketplaceAbort) marketplaceAbort.abort(); marketplaceAbort = new AbortController();
    if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
    setStatus(STR.searchingMarketplace(provider === 'ebay' ? 'eBay' : 'Amazon', query), 'primary');
    try {
      const response = await fetch(`${urls.marketplace}?provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(query)}`, {
        headers: { Accept: 'application/json' }, cache: 'no-store', signal: marketplaceAbort.signal,
      });
      const data = await safeJson(response);
      if (!response.ok && !data.fallback_url) { setStatus(data.error || STR.marketplaceFailed, 'warning'); return; }
      renderMarketplaceResults(provider, data);
      if (Array.isArray(data.results) && data.results.length) {
        const source = data.source === 'api' ? STR.apiFallback : STR.publicSearch;
        setStatus(STR.hitsLoaded(data.results.length, source), 'success');
      } else {
        setStatus(cleanText(data.warning || data.error) || STR.noMarketHits2, 'secondary');
      }
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus(STR.searchOffline(typeof navigator !== 'undefined' && navigator.onLine === false), 'danger');
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
      amazonButton.classList.add('w-100'); amazonButton.innerHTML = `<i class="fab fa-amazon me-2"></i>${STR.searchAmazon}`;
    }
    ebayButton = document.createElement('button'); ebayButton.type = 'button'; ebayButton.id = 'ebay-search';
    ebayButton.className = 'btn btn-outline-primary w-100'; ebayButton.innerHTML = `<i class="fas fa-tags me-2"></i>${STR.searchEbay}`;
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
    const heading = document.createElement('div'); heading.className = 'small text-secondary mb-1'; heading.textContent = STR.localHits(query); findResults.appendChild(heading);
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
    if (finderAbort) finderAbort.abort(); finderAbort = new AbortController(); findInput.setAttribute('aria-busy', 'true'); setStatus(STR.searchingInventory(raw), 'primary');
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
      setStatus(localMatches.length ? STR.localHitsFallback(localMatches.length) : STR.noExactHit(raw), 'secondary');
      navigateLocal(fallback);
    } catch (error) {
      if (error?.name !== 'AbortError') { setStatus(STR.quickSearchOffline, 'warning'); navigateLocal(`${urls.objects}?q=${encodeURIComponent(raw)}`); }
    } finally { findInput.removeAttribute('aria-busy'); }
  };

  const duplicateProbe = async (value, source) => {
    const raw = cleanText(value); if (!raw || !urls.find) return; const query = normalizeIsbn(raw) || raw;
    try {
      const response = await fetch(`${urls.find}?identifier=${encodeURIComponent(source === 'nfc' ? '' : query)}&nfc=${encodeURIComponent(source === 'nfc' ? query : '')}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
      const data = await safeJson(response); if (response.ok && data.found) setStatus(STR.duplicateFound(data.display_id || '', data.name || ''), 'warning');
    } catch (_error) {}
  };

  // Single-photo suggestion elements/state live in static/js/inventory_suggest.js.
  const isbnTitleHint = $('isbn-title-hint');

  // Shared by single-photo adopt and series transfer: fills name/type plus
  // note-as-description (only into an empty field) and suggested tags
  // (deduplicated append). Never saves; the user reviews and saves manually.
  const applySuggestionToForm = (suggestion) => {
    if (!suggestion || !title) return;
    title.value = cleanText(suggestion.object_name);
    const suggested = cleanText(suggestion.item_type);
    if (suggested && suggested !== 'object' && type && (!cleanText(type.value) || cleanText(type.value) === 'object')) {
      type.value = suggested; objectTypeWasAutoBook = false; updateMode();
    }
    if (suggestionHelpers) {
      const descriptionParts = [suggestion.note];
      if (suggestion.subtitle) descriptionParts.push(`${STR.subtitleLabel}${suggestion.subtitle}`);
      if (description) description.value = suggestionHelpers.suggestionDescription(description.value, descriptionParts.filter(Boolean).join(' · '));
      if (tags) tags.value = suggestionHelpers.mergeTags(tags.value, suggestion.tags, tags.maxLength);
    }
    const authorsField = $('authors');
    if (authorsField && !cleanText(authorsField.value) && Array.isArray(suggestion.authors)) {
      authorsField.value = suggestion.authors.map(cleanText).filter(Boolean).join(', ');
    }
  };
  // Shared namespace for static/js/inventory_suggest.js (loaded after this
  // module): single-photo analyze/review/adopt plus its event wiring.
  window.SimpleOfficeInventoryUi = {
    $, cleanText, setStatus, safeJson, urls, LOCAL_UNAVAILABLE_MESSAGE,
    form, photo, barcode, isbn, title, applySuggestionToForm,
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
    if (file.size > 12 * 1024 * 1024) { photo.value = ''; photoPreview.hidden = true; setStatus(STR.photoTooBig12, 'warning'); return; }
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type)) { photo.value = ''; photoPreview.hidden = true; setStatus(STR.photoType, 'warning'); return; }
    try {
      const bitmap = await createImageBitmap(file); const maxSide = 1600; const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement('canvas'); canvas.width = Math.max(1, Math.round(bitmap.width * scale)); canvas.height = Math.max(1, Math.round(bitmap.height * scale));
      const context = canvas.getContext('2d'); if (!context) throw new Error('canvas unavailable'); context.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
      photoPreview.src = canvas.toDataURL('image/jpeg', 0.85); photoPreview.hidden = false;
    } catch (_error) { photoPreview.removeAttribute('src'); photoPreview.hidden = true; setStatus(STR.previewFailed, 'warning'); }
  };

  const rememberLocation = () => { const location = $('location'); if (!location) return; const value = cleanText(location.value); try { if (value) localStorage.setItem('simpleoffice.inventory.lastLocation', value); } catch (_error) {} };
  const restoreLocation = () => { const location = $('location'); if (!location || cleanText(location.value)) return; try { const saved = localStorage.getItem('simpleoffice.inventory.lastLocation'); if (saved) location.value = saved; } catch (_error) {} };
  const clearBookMetadata = () => {
    ['authors', 'publisher', 'published-date', 'page-count', 'language', 'categories', 'description', 'market-price', 'price-source', 'metadata-source', 'metadata-checked-at'].forEach((id) => { const element = $(id); if (element) element.value = ''; });
    if ($('currency')) $('currency').value = 'EUR'; setStatus(STR.bookDataRemoved, 'secondary');
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
  const seriesUpdateHint = () => { if (queueHint) queueHint.textContent = STR.seriesHint(seriesItems.length, SERIES_MAX_ITEMS); };
  const seriesValidFile = (file) => file && /^image\/(jpeg|png|webp)$/i.test(file.type || '') && file.size > 0 && file.size <= SERIES_MAX_BYTES;
  const seriesCardHtml = `<div class="card-body d-flex gap-2 align-items-start"><img width="96" height="96" style="width:96px;height:96px;object-fit:cover" class="rounded border flex-shrink-0" alt=""><div class="flex-grow-1"><div class="fw-semibold small text-break" data-part="name"></div><div class="small" data-part="status" role="status"></div><div class="small text-secondary" data-part="suggestion" hidden></div><div class="d-flex flex-wrap gap-2 mt-1 align-items-center" data-part="isbn-row" hidden><span class="small text-secondary" data-part="isbn"></span><button type="button" class="btn btn-sm btn-outline-secondary" data-part="adopt-isbn">${STR.adoptIsbnBtn}</button></div><input class="form-control form-control-sm mt-2" data-part="identifier" placeholder="${STR.identifierPlaceholder}" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="120"><div class="d-flex flex-wrap gap-2 mt-2"><button type="button" class="btn btn-sm btn-outline-primary" data-part="check">${STR.checkInventoryBtn}</button><button type="button" class="btn btn-sm btn-success" data-part="attach" hidden>${STR.attachPhotoBtn}</button></div><div class="small mt-1" data-part="match" hidden></div><ul class="small mt-2 mb-0 ps-3" data-part="checklist" hidden></ul><div class="small mt-1" data-part="save" hidden></div><div class="d-flex flex-wrap gap-2 mt-2"><button type="button" class="btn btn-sm btn-outline-primary" data-part="transfer" hidden>${STR.adoptIntoFormBtn}</button><button type="button" class="btn btn-sm btn-outline-secondary" data-part="retry" hidden>${STR.analyzeAgainBtn}</button><button type="button" class="btn btn-sm btn-outline-danger" data-part="remove">${STR.removeBtn}</button></div></div></div>`;
  const seriesRenderItem = (item) => {
    let card = document.getElementById(`series-card-${item.id}`);
    if (!card) {
      card = document.createElement('div');
      card.className = 'card';
      card.id = `series-card-${item.id}`;
      card.innerHTML = seriesCardHtml;
      card.querySelector('[data-part="transfer"]').addEventListener('click', () => seriesTransfer(item.id));
      card.querySelector('[data-part="retry"]').addEventListener('click', () => seriesRetry(item.id));
      card.querySelector('[data-part="remove"]').addEventListener('click', () => seriesRemove(item.id));
      const identifierInput = card.querySelector('[data-part="identifier"]');
      identifierInput.setAttribute('aria-label', STR.identifierAria(item.id));
      identifierInput.addEventListener('input', (event) => { item.identifier = event.target.value; item.match = null; item.attached = false; seriesRenderItem(item); });
      card.querySelector('[data-part="check"]').addEventListener('click', () => seriesCheck(item.id));
      card.querySelector('[data-part="attach"]').addEventListener('click', () => seriesAttach(item.id));
      card.querySelector('[data-part="adopt-isbn"]').addEventListener('click', () => seriesAdoptIsbn(item.id));
      queueList?.appendChild(card);
    }
    const img = card.querySelector('img');
    img.hidden = !item.url;
    if (item.url && img.getAttribute('src') !== item.url) { img.setAttribute('src', item.url); img.setAttribute('alt', STR.seriesPhotoAlt(item.id)); }
    card.querySelector('[data-part="name"]').textContent = `#${item.id} · ${item.file.name || STR.photoFallback} (${Math.max(1, Math.round(item.file.size / 1024))} KB)`;
    card.querySelector('[data-part="status"]').textContent = item.statusText;
    const boxEl = card.querySelector('[data-part="suggestion"]');
    if (item.suggestion) {
      boxEl.hidden = false;
      const tagLine = suggestionHelpers ? suggestionHelpers.formatSuggestionTags(item.suggestion.tags) : '';
      boxEl.textContent = `${cleanText(item.suggestion.object_name)}${item.suggestion.item_type && item.suggestion.item_type !== 'object' ? ` (${item.suggestion.item_type})` : ''}${item.suggestion.note ? ` – ${cleanText(item.suggestion.note)}` : ''}${tagLine ? ` · ${tagLine}` : ''}`;
    } else { boxEl.hidden = true; boxEl.textContent = ''; }
    const isbnRow = card.querySelector('[data-part="isbn-row"]');
    const isbnValue = item.suggestion ? cleanText(item.suggestion.isbn) : '';
    if (isbnValue) {
      isbnRow.hidden = false;
      card.querySelector('[data-part="isbn"]').textContent = STR.isbnInPhoto(isbnValue);
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
    if (item.saveInfo) { saveEl.hidden = false; saveEl.textContent = ''; const span = document.createElement('span'); span.textContent = item.saveInfo.text; saveEl.appendChild(span); const target = safeLocalUrl(item.saveInfo.url); if (target) { saveEl.appendChild(document.createTextNode(' ')); const link = document.createElement('a'); link.href = target; link.textContent = STR.openEntry; saveEl.appendChild(link); } }
    else { saveEl.hidden = true; saveEl.textContent = ''; }
  };
  const seriesProcessNext = async () => {
    // Sequential: one request at a time bounds Ollama memory/load.
    if (seriesBusy || !queueList) return;
    const item = seriesItems.find((entry) => entry.status === 'pending');
    if (!item) return;
    if (!urls.suggest) { item.status = 'error'; item.statusText = STR.notAvailable; seriesRenderItem(item); return; }
    seriesBusy = true;
    item.status = 'working'; item.statusText = STR.analyzing; item.controller = new AbortController();
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
        const fallback = response.status === 503 ? LOCAL_UNAVAILABLE_MESSAGE : STR.suggestFailed(response.status);
        item.status = 'error'; item.statusText = serverMessage || fallback;
      } else if (!payload || !payload.suggestion) {
        item.status = 'error'; item.statusText = STR.emptySuggestion;
      } else {
        item.status = 'ready'; item.suggestion = payload.suggestion;
        const label = cleanText(payload.provider_label);
        item.statusText = label ? STR.readySuggestion(label) : STR.readySuggestionPlain;
      }
    } catch (error) {
      if (error?.name === 'AbortError') { seriesBusy = false; seriesProcessNext(); return; }
      item.status = 'error'; item.statusText = STR.analysisFailed;
    }
    seriesBusy = false;
    seriesRenderItem(item);
    seriesProcessNext();
  };
  const seriesAddFiles = (files) => {
    if (!queueList || !files?.length) return;
    // No dedupe: identical files stay independent items (never merge by similarity).
    for (const file of files) {
      if (seriesItems.length >= SERIES_MAX_ITEMS) { setStatus(STR.maxSeries(SERIES_MAX_ITEMS), 'warning'); break; }
      seriesSeq += 1;
      const item = { id: seriesSeq, file, identifier: '', url: '', status: 'pending', statusText: STR.waitingAnalysis, suggestion: null, controller: null, match: null, attached: false, captureToken: seriesToken(), saved: false, saving: false, saveInfo: null };
      if (!seriesValidFile(file)) {
        item.status = 'error';
        item.statusText = !/^image\/(jpeg|png|webp)$/i.test(file.type || '') ? STR.photoType : STR.photoEmpty;
      } else {
        try { item.url = URL.createObjectURL(file); } catch (_error) { item.status = 'error'; item.statusText = STR.photoUnreadable; }
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
    if (!seriesValidFile(item.file)) { item.status = 'error'; item.statusText = STR.fileInvalid; seriesRenderItem(item); return; }
    item.status = 'pending'; item.statusText = STR.waitingAnalysis; item.suggestion = null;
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
    applySuggestionToForm(item.suggestion);
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
    } catch (_error) { setStatus(STR.transferFailed, 'danger'); return; }
    previewPhoto();
    item.status = 'transferred'; item.statusText = STR.transferred;
    seriesActiveId = item.id;
    seriesRenderItem(item);
    setStatus(STR.seriesAdopted, 'success');
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
    if (!ownCode || !urls.match) { item.match = { status: 'idle', text: STR.checkIdle(Boolean(ownCode)) }; seriesRenderItem(item); return; }
    item.checking = true;
    try {
      const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
      const data = new FormData();
      data.append('identifier', ownCode);
      data.append('_csrf_token', token);
      const response = await fetch(urls.match, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok) {
        item.match = { status: 'error', text: cleanText(payload && payload.error) || STR.checkFailed(response.status) };
      } else if (!payload || (payload.status !== 'unique' && payload.status !== 'none' && payload.status !== 'ambiguous')) {
        item.match = { status: 'error', text: STR.checkUnclear };
      } else if (payload.status === 'unique') {
        const hit = (payload.matches || [])[0] || {};
        item.match = { status: 'unique', text: STR.hitFound(cleanText(hit.name) || STR.objectFallback, String(hit.object_id || '').slice(0, 8)), target: { object_id: String(hit.object_id || ''), name: cleanText(hit.name) } };
      } else if (payload.status === 'ambiguous') {
        const names = (payload.matches || []).slice(0, 3).map((hit) => cleanText(hit.name) || STR.objectFallback).join(', ');
        item.match = { status: 'ambiguous', text: STR.ambiguous(payload.count || (payload.matches || []).length, names) };
      } else {
        item.match = { status: 'none', text: STR.noHit };
      }
    } catch (_error) {
      item.match = { status: 'error', text: STR.checkFailed2 };
    }
    item.checking = false;
    seriesRenderItem(item);
  };
  const seriesAttach = async (id) => {
    const item = seriesItems.find((entry) => entry.id === id);
    if (!item || !item.match || item.match.status !== 'unique' || !item.match.target || item.attaching) return;
    if (!urls.attach) { item.match = { status: 'error', text: STR.attachUnavailable }; seriesRenderItem(item); return; }
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
        item.match = { status: 'error', text: cleanText(payload && payload.error) || STR.attachFailed(response.status) };
      } else {
        item.attached = true;
        item.statusText = STR.photoAttached;
      }
    } catch (_error) {
      item.match = { status: 'error', text: STR.attachFailed2 };
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
      { label: STR.checklistTitle, detail: transferred ? cleanText(title?.value) : cleanText(item.suggestion?.object_name), ok: Boolean(transferred ? cleanText(title?.value) : cleanText(item.suggestion?.object_name)), action: transferred ? 'title' : 'transfer' },
      book
        ? { label: STR.checklistIdIsbn, detail: code, ok: Boolean(code), action: transferred ? 'isbn' : 'transfer' }
        : { label: STR.checklistIdOptional, detail: code, ok: true, action: transferred ? 'isbn' : 'transfer' },
      book
        ? { label: STR.checklistShelf, detail: cleanText(seriesShelf?.selectedOptions?.[0]?.textContent), ok: Boolean(shelf), action: 'shelf' }
        : { label: STR.checklistShelfBooks, detail: '', ok: true, action: 'shelf' },
      { label: STR.checklistPrice, detail: cleanText($('market-price')?.value), ok: Boolean(cleanText($('market-price')?.value)), action: 'price' },
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
      btn.textContent = row.action === 'transfer' ? STR.adoptData : STR.checklistShow;
      btn.addEventListener('click', () => seriesChecklistAction(item.id, row.action));
      li.appendChild(btn); list.appendChild(li);
    }
  };
  const seriesRefreshChecklists = () => { for (const entry of seriesItems) { if (entry.suggestion && !entry.saved && document.getElementById(`series-card-${entry.id}`)) seriesRenderItem(entry); } };
  const seriesSaveInfo = (payload, fallback) => {
    const extra = (Array.isArray(payload.notices) ? payload.notices : []).filter((note) => note && note !== payload.message);
    return { text: `${cleanText(payload.message) || fallback}${extra.length ? `${STR.notePrefix}${extra.join(' ')}` : ''}`, url: cleanText(payload.detail_url) };
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
    if (item.status !== 'transferred') { item.statusText = STR.saveFirst; seriesRenderItem(item); return; }
    const book = seriesIsBook(item);
    const shelf = cleanText(seriesShelf?.value);
    if (!cleanText(title?.value) || (book && !shelf)) { setStatus(book ? STR.needTitleShelf : STR.needTitle, 'warning'); seriesRenderItem(item); seriesFocusField(!cleanText(title?.value) ? 'title' : 'library_location_id'); return; }
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
        item.statusText = cleanText(payload && payload.error) || STR.saveFailed(response.status);
        setStatus(item.statusText, 'danger');
        seriesRenderItem(item);
        seriesFocusField(Object.keys(fields)[0] || '');
        return;
      }
      if (payload.saved) {
        item.saved = true; item.status = 'saved';
        item.saveInfo = seriesSaveInfo(payload, STR.savedFallback);
        item.statusText = STR.savedOk(cleanText(payload.display_id), cleanText(payload.name));
        seriesActiveId = null;
        seriesResetForm();
        setStatus(STR.seriesSaved, 'success');
      } else if (payload.retried) {
        item.saved = true; item.status = 'saved';
        item.saveInfo = seriesSaveInfo(payload, STR.alreadyCaptured);
        item.statusText = STR.alreadyCapturedRepeat;
        seriesActiveId = null;
        seriesResetForm();
        setStatus(item.statusText, 'success');
      } else if (payload.duplicate) {
        item.captureToken = seriesToken();
        item.saveInfo = seriesSaveInfo(payload, STR.alreadyThere);
        item.saveInfo.text += STR.duplicateHint;
        item.statusText = STR.alreadyThere;
        setStatus(item.statusText, 'warning');
      } else {
        item.statusText = STR.unclearResponse;
        setStatus(item.statusText, 'danger');
      }
    } catch (_error) {
      item.statusText = STR.saveNetwork;
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
  title?.addEventListener('input', checkIsbnInTitle);
  $('adopt-isbn-title')?.addEventListener('click', adoptIsbnFromTitle);
  recentFilter?.addEventListener('input', filterRecent);

  amazonButton?.addEventListener('click', () => searchMarketplace('amazon'));

  $('start-barcode')?.addEventListener('click', async () => {
    stopCamera();
    const generation = cameraGeneration;
    if (typeof window.BarcodeDetector !== 'function' && typeof window.jsQR !== 'function') { setStatus(STR.noScanner, 'warning'); barcode?.focus(); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setStatus(STR.noCamera, 'warning'); return; }
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
        detector ? STR.holdBarcode : STR.holdQr,
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
            if (canonical) { isbn.value = canonical; type.value = 'book'; objectTypeWasAutoBook = true; updateMode(); updateBookLinks(canonical); setStatus(STR.isbnFound(canonical), 'success'); stopCamera(); lookup(); return; }
            isbn.value = ''; updateMode(); setStatus($('analyze-photo') ? STR.barcodeFoundPhoto(value) : STR.barcodeFound(value), 'success'); stopCamera(); takePhoto?.focus(); duplicateProbe(value, 'identifier'); return;
          }
        } catch (_error) {}
        requestAnimationFrame(scan);
      };
      requestAnimationFrame(scan);
    } catch (error) { if (generation !== cameraGeneration) return; stopCamera(); setStatus(error?.name === 'NotAllowedError' ? STR.camDenied : STR.camFailed, 'danger'); }
  });

  $('scan-nfc')?.addEventListener('click', async () => {
    if (!('NDEFReader' in window)) { setStatus(STR.nfcUnsupported, 'warning'); nfc?.focus(); return; }
    try {
      const reader = new NDEFReader(); await reader.scan(); setStatus(STR.nfcHold, 'primary');
      reader.addEventListener('reading', (event) => {
        const parts = []; if (event.serialNumber) parts.push(event.serialNumber);
        for (const record of event.message.records) { try { if (record.data) parts.push(new TextDecoder(record.encoding || 'utf-8').decode(record.data)); } catch (_error) {} }
        nfc.value = parts.filter(Boolean).join(' | ').slice(0, 240); setStatus(STR.nfcRead, 'success'); duplicateProbe(nfc.value, 'nfc');
      }, { once: true });
    } catch (_error) { setStatus(STR.nfcFailed, 'danger'); }
  });

  form?.addEventListener('submit', () => { rememberLocation(); if (saveButton) { saveButton.disabled = true; saveButton.setAttribute('aria-busy', 'true'); saveButton.textContent = STR.saving; } });
  window.addEventListener('online', updateConnectivity); window.addEventListener('offline', updateConnectivity);
  window.addEventListener('resize', syncActionBarInset); window.visualViewport?.addEventListener('resize', syncActionBarInset); window.visualViewport?.addEventListener('scroll', syncActionBarInset);
  window.addEventListener('pagehide', () => { stopCamera(); const old = photoPreview?.dataset.objectUrl; if (old) URL.revokeObjectURL(old); });
  prepareMarketplaceUi(); syncActionBarInset(); updateConnectivity(); updateMode(); if (isbn && cleanText(isbn.value)) updateBookLinks(normalizeIsbn(isbn.value));
})();
