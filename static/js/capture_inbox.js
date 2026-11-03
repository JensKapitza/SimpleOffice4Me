(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    bookGroup: (id) => EN ? `Book group ${String(id).slice(0, 8)}` : `Buchgruppe ${String(id).slice(0, 8)}`,
    singlePhoto: EN ? 'Single photo' : 'Einzelfoto',
    batchCurrent: (label) => EN ? `Current: ${label} — further photos belong to this book.` : `Aktuell: ${label} — weitere Fotos gehören zu diesem Buch.`,
    batchSingles: EN ? 'Current: single photos (no book group).' : 'Aktuell: Einzelfotos (keine Buchgruppe).',
    mlUnknown: EN ? 'ML status unknown' : 'ML-Status unbekannt',
    assignedTo: (id, name) => EN ? `Assigned: #${id || ''} ${name || ''}`.trim() : `Zugeordnet: #${id || ''} ${name || ''}`.trim(),
    notAssigned: EN ? 'Not assigned yet' : 'Noch nicht zugeordnet',
    regroupFailed: EN ? 'Regrouping failed.' : 'Umgruppieren fehlgeschlagen.',
    roleTitle: EN ? 'Title page' : 'Titelblatt', roleImprint: EN ? 'Imprint' : 'Impressum',
    roleSpine: EN ? 'Book spine' : 'Buchrücken', roleOther: EN ? 'Other' : 'Sonstiges',
    roleStoredWarn: EN ? 'Role stored in the inbox, not yet updated on the existing photo — choose the role again to retry.' : 'Rolle im Eingangskorb gespeichert, am vorhandenen Foto noch nicht aktualisiert – erneut Rolle wählen zum Wiederholen.',
    retry: EN ? 'Retry' : 'Wiederholen',
    roleUpdated: EN ? '✓ (also updated on the assigned object)' : '✓ (auch am zugeordneten Objekt aktualisiert)',
    saveFailed: EN ? 'Saving failed.' : 'Speichern fehlgeschlagen.',
    networkRole: EN ? 'Network error — role not saved, please try again.' : 'Netzwerkfehler — Rolle nicht gespeichert, bitte erneut versuchen.',
    storedShot: EN ? 'Stored capture' : 'Gespeicherte Aufnahme',
    takenAt: (taken, size) => EN ? `Captured ${taken}${size}` : `Aufgenommen ${taken}${size}`,
    joinCurrent: EN ? 'To current book' : 'Zum aktuellen Buch',
    leaveGroup: EN ? 'Remove from group' : 'Aus Gruppe lösen',
    photoRole: EN ? 'Photo role: ' : 'Fotorolle: ',
    noRole: EN ? 'No role' : 'Ohne Rolle',
    analysisPending: EN ? 'Analysis pending or failed.' : 'Analyse ausstehend oder fehlgeschlagen.',
    analyzeAgain: EN ? 'Analyze again' : 'Erneut analysieren',
    analysisFailed: EN ? 'Analysis failed.' : 'Analyse fehlgeschlagen.',
    kindBook: EN ? 'Book' : 'Buch', kindObject: EN ? 'Object' : 'Gegenstand',
    mlUnavailable: EN ? 'not available' : 'nicht verfügbar',
    alreadyThere: (id, name) => EN ? `Already exists: #${id} ${name} ` : `Bereits vorhanden: #${id} ${name} `,
    open: EN ? 'Open' : 'Öffnen',
    fieldTitle: EN ? 'Title' : 'Titel', fieldAuthors: EN ? 'Authors' : 'Autoren',
    fieldYear: EN ? 'Year' : 'Jahr', fieldPublisher: EN ? 'Publisher' : 'Verlag',
    fieldIsbn: EN ? 'ISBN' : 'ISBN', fieldName: EN ? 'Name' : 'Name',
    fieldDescription: EN ? 'Description' : 'Beschreibung', fieldTags: EN ? 'Tags (comma-separated)' : 'Tags (Komma-getrennt)',
    titleChip: (line) => EN ? `Title: ${line.slice(0, 60)}` : `Titel: ${line.slice(0, 60)}`,
    completeHint: (missing) => EN ? `Please complete: ${missing}` : `Bitte ergänzen: ${missing}`,
    adoptBook: EN ? 'Adopt as book' : 'Als Buch übernehmen',
    adoptObject: EN ? 'Adopt as object' : 'Als Gegenstand übernehmen',
    saveNetwork: EN ? 'Saving failed (network).' : 'Speichern fehlgeschlagen (Netzwerk).',
    alreadyNoDup: EN ? 'Already exists – no duplicate created.' : 'Bereits vorhanden – kein Duplikat angelegt.',
    linkAnyway: EN ? 'Link anyway' : 'Trotzdem zuordnen',
    savedAs: (id, name) => EN ? `Saved: #${id || ''} ${name || ''} ` : `Gespeichert: #${id || ''} ${name || ''} `,
    photoAdopted: EN ? ', photo adopted.' : ', Foto übernommen.',
    entryLinked: EN ? ' · Entry linked' : ' · Eintrag zugeordnet',
    linkFailedKept: EN ? ' · Linking failed, original stays in the inbox.' : ' · Zuordnung fehlgeschlagen, Original bleibt im Korb.',
    linkFailedKept2: (err) => EN ? `${err} Original stays in the inbox.` : `${err} Original bleibt im Korb.`,
    linkNetworkKept: EN ? 'Linking failed (network). Original stays in the inbox.' : 'Zuordnung fehlgeschlagen (Netzwerk). Original bleibt im Korb.',
    linkedTo: (id, name, extra) => EN ? `Assigned: #${id || ''} ${name || ''}${extra} ` : `Zugeordnet: #${id || ''} ${name || ''}${extra} `,
    uploadFailed: (code) => EN ? `Upload failed (${code}).` : `Upload fehlgeschlagen (${code}).`,
  };
  const root = document.getElementById('capture-app');
  if (!root) return;
  const dataset = root.dataset;
  const csrf = dataset.csrf || '';
  const cards = document.getElementById('capture-cards');
  const globalStatus = document.getElementById('capture-global-status');
  const mlLine = document.getElementById('capture-ml-status');
  const batchBox = document.getElementById('capture-batches');
  const batchStatus = document.getElementById('capture-batch-status');
  const consentBox = document.getElementById('capture-consent-meta');
  let currentBatch = '';
  const knownEntries = new Map();
  const batchUrlFor = (id) => String(dataset.batchPattern || '').replace('__BATCH__', id);
  const shortBatch = (id) => (id ? STR.bookGroup(id) : STR.singlePhoto);
  const updateBatchStatus = () => {
    if (!batchStatus) return;
    batchStatus.textContent = currentBatch
      ? STR.batchCurrent(shortBatch(currentBatch))
      : STR.batchSingles;
  };
  const cleanText = (value) => String(value || '').trim();
  // Allowlist for server-provided preview URLs: relative paths plus
  // http(s)/blob/data-image only. Anything else (javascript:, data
  // HTML, protocol-relative) fails closed to an empty image source.
  const safePreviewUrl = (value) => {
    const url = String(value || '').trim();
    if (url.startsWith('/') && !url.startsWith('//')) return url;
    const lower = url.toLowerCase();
    if (lower.startsWith('http://') || lower.startsWith('https://')) return url;
    if (lower.startsWith('blob:')) return url;
    if (lower.startsWith('data:image/')) return url;
    return '';
  };
  const nextToken = () => {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  };
  const setGlobal = (text, kind) => {
    if (!globalStatus) return;
    globalStatus.textContent = '';
    if (!text) return;
    const note = document.createElement('div');
    note.className = `alert alert-${kind || 'secondary'} py-2 small mb-0`;
    note.textContent = text;
    globalStatus.appendChild(note);
  };
  const safeJson = async (response) => { try { return await response.json(); } catch (_error) { return {}; } };
  const initialEntries = (() => {
    try {
      return JSON.parse(document.getElementById('capture-entries')?.textContent || '[]');
    } catch (_error) {
      return [];
    }
  })();

  const loadMlStatus = async () => {
    if (!mlLine || !dataset.mlStatusUrl) return;
    try {
      const response = await fetch(dataset.mlStatusUrl, {cache: 'no-store'});
      const payload = await safeJson(response);
      if (!response.ok || !payload || payload.ok !== true) throw new Error('status');
      mlLine.textContent = window.SimpleOfficeCaptureMl.formatMlStatus(payload);
    } catch (_error) {
      mlLine.textContent = STR.mlUnknown;
    }
  };

  const fieldRow = (label, name, value) => {
    const wrap = document.createElement('div');
    wrap.className = 'col-12';
    const tag = document.createElement('label');
    tag.className = 'form-label small mb-1';
    tag.textContent = label;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control';
    input.name = name;
    input.value = cleanText(value);
    wrap.appendChild(tag);
    wrap.appendChild(input);
    return {wrap, input};
  };

  const statusBadge = (entry) => {
    const badge = document.createElement('span');
    const assignment = entry.assignment || {};
    if (entry.status === 'assigned' && assignment.object_id) {
      badge.className = 'badge text-bg-success me-1';
      badge.textContent = STR.assignedTo(assignment.display_id, assignment.name);
    } else {
      badge.className = 'badge text-bg-warning me-1';
      badge.textContent = STR.notAssigned;
    }
    return badge;
  };

  const refreshEntry = (entry) => {
    if (entry && entry.entry_id) knownEntries.set(entry.entry_id, entry);
    renderBatches();
  };

  const moveEntry = async (entry, batchId, button) => {
    if (!entry.batch_url) return;
    button.disabled = true;
    const data = new FormData();
    data.append('batch_id', batchId);
    data.append('_csrf_token', csrf);
    const response = await fetch(entry.batch_url, {method: 'POST', body: data, cache: 'no-store'});
    const payload = await safeJson(response);
    if (response.ok && payload && payload.ok) {
      const col = cards.querySelector(`[data-entry-id="${entry.entry_id}"]`);
      if (col) col.replaceWith(renderEntry(payload.entry));
      else refreshEntry(payload.entry);
    } else {
      setGlobal(cleanText(payload && payload.error) || STR.regroupFailed, 'danger');
      button.disabled = false;
    }
  };

  const ROLE_LABELS = {title: STR.roleTitle, imprint: STR.roleImprint, spine: STR.roleSpine, other: STR.roleOther};

  const saveRole = async (entry, select, state) => {
    if (!entry.role_url) return;
    select.disabled = true;
    state.textContent = '…';
    try {
      const data = new FormData();
      data.append('role', select.value);
      data.append('_csrf_token', csrf);
      const response = await fetch(entry.role_url, {method: 'POST', body: data, cache: 'no-store'});
      const payload = await safeJson(response);
      if (response.ok && payload && payload.ok) {
        if (payload.entry) {
          knownEntries.set(payload.entry.entry_id, payload.entry);
          entry.photo_role = payload.entry.photo_role || '';
        }
        if (payload.attached_role_error) {
          state.textContent = '';
          const warn = document.createElement('span');
          warn.className = 'fw-bold text-warning';
          warn.textContent = STR.roleStoredWarn;
          state.appendChild(warn);
          const retry = document.createElement('button');
          retry.type = 'button';
          retry.className = 'btn btn-sm btn-outline-warning ms-1';
          retry.textContent = STR.retry;
          retry.addEventListener('click', () => saveRole(entry, select, state));
          state.appendChild(retry);
        } else {
          state.textContent = payload.attached_role_updated === true
            ? STR.roleUpdated
            : '✓';
        }
      } else {
        select.value = entry.photo_role || '';
        state.textContent = cleanText(payload && payload.error) || STR.saveFailed;
      }
    } catch (_error) {
      select.value = entry.photo_role || '';
      state.textContent = STR.networkRole;
    } finally {
      select.disabled = false;
    }
  };

  const renderEntry = (entry) => {
    if (entry && entry.entry_id) knownEntries.set(entry.entry_id, entry);
    const col = document.createElement('div');
    col.className = 'col-12 col-md-6 col-xl-4';
    col.dataset.entryId = entry.entry_id || '';
    const card = document.createElement('div');
    card.className = 'card shadow-sm h-100';
    const img = document.createElement('img');
    img.className = 'card-img-top';
    img.alt = STR.storedShot;
    img.loading = 'lazy';
    // Scheme-allowlisted above; encodeURI keeps the URL intact while
    // neutralizing any residual metacharacters.
    img.src = encodeURI(safePreviewUrl(entry.preview_url));
    card.appendChild(img);
    const body = document.createElement('div');
    body.className = 'card-body';
    const state = document.createElement('p');
    state.className = 'small text-secondary';
    state.appendChild(statusBadge(entry));
    const meta = document.createElement('span');
    const taken = cleanText(entry.created_at).replace('T', ' ').slice(0, 16);
    const size = entry.original && entry.original.size ? ` · ${Math.round(entry.original.size / 1024)} KB` : '';
    meta.textContent = STR.takenAt(taken, size);
    state.appendChild(meta);
    body.appendChild(state);
    const batchLine = document.createElement('p');
    batchLine.className = 'small mb-1';
    const batchBadge = document.createElement('span');
    batchBadge.className = `badge ${entry.batch_id ? 'text-bg-info' : 'text-bg-light'} me-1`;
    batchBadge.textContent = shortBatch(entry.batch_id);
    batchLine.appendChild(batchBadge);
    if (currentBatch && entry.batch_id !== currentBatch) {
      const join = document.createElement('button');
      join.type = 'button';
      join.className = 'btn btn-sm btn-outline-success me-1';
      join.textContent = STR.joinCurrent;
      join.addEventListener('click', () => moveEntry(entry, currentBatch, join));
      batchLine.appendChild(join);
    }
    if (entry.batch_id) {
      const leave = document.createElement('button');
      leave.type = 'button';
      leave.className = 'btn btn-sm btn-outline-secondary';
      leave.textContent = STR.leaveGroup;
      leave.addEventListener('click', () => moveEntry(entry, '', leave));
      batchLine.appendChild(leave);
    }
    body.appendChild(batchLine);
    const roleLine = document.createElement('p');
    roleLine.className = 'small mb-1';
    const roleLabel = document.createElement('label');
    roleLabel.className = 'form-label small mb-0 me-1';
    roleLabel.textContent = STR.photoRole;
    const roleSelect = document.createElement('select');
    roleSelect.className = 'form-select form-select-sm d-inline-block w-auto';
    roleSelect.setAttribute('aria-label', EN ? 'Photo role' : 'Fotorolle');
    [['', STR.noRole], ['title', STR.roleTitle], ['imprint', STR.roleImprint], ['spine', STR.roleSpine], ['other', STR.roleOther]].forEach(([value, text]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = text;
      roleSelect.appendChild(option);
    });
    roleSelect.value = entry.photo_role || '';
    const roleState = document.createElement('span');
    roleState.className = 'small text-secondary ms-1';
    roleSelect.addEventListener('change', () => saveRole(entry, roleSelect, roleState));
    roleLabel.appendChild(roleSelect);
    roleLine.appendChild(roleLabel);
    roleLine.appendChild(roleState);
    body.appendChild(roleLine);
    card.appendChild(body);
    col.appendChild(card);
    const analysis = entry.analysis && entry.analysis.ok ? entry.analysis : null;
    if (!analysis) {
      const note = document.createElement('p');
      note.className = 'small text-warning';
      note.textContent = STR.analysisPending;
      body.appendChild(note);
      if (entry.analyze_url) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'btn btn-sm btn-outline-primary';
        retry.textContent = STR.analyzeAgain;
        retry.addEventListener('click', async () => {
          retry.disabled = true;
          const data = new FormData();
          if (consentBox && consentBox.checked) data.append('consent_meta', '1');
          data.append('_csrf_token', csrf);
          const response = await fetch(entry.analyze_url, {method: 'POST', body: data, cache: 'no-store'});
          const payload = await safeJson(response);
          if (response.ok && payload && payload.ok) {
            col.replaceWith(renderEntry(payload.entry));
          } else {
            note.textContent = cleanText(payload && payload.error) || STR.analysisFailed;
            retry.disabled = false;
          }
        });
        body.appendChild(retry);
      }
      return col;
    }
    const kindLine = document.createElement('p');
    kindLine.className = 'small mb-1';
    const badge = document.createElement('span');
    badge.className = `badge ${analysis.kind === 'book' ? 'text-bg-primary' : 'text-bg-secondary'} me-1`;
    badge.textContent = analysis.kind === 'book' ? STR.kindBook : STR.kindObject;
    kindLine.appendChild(badge);
    kindLine.appendChild(document.createTextNode(cleanText(analysis.kind_reason)));
    body.appendChild(kindLine);
    const backends = document.createElement('p');
    backends.className = 'small text-secondary mb-1';
    const ocr = analysis.ocr || {};
    const ml = analysis.ml || {};
    backends.textContent = `OCR: ${ocr.engine || '–'} (${ocr.status || '–'}) · `
      + (ml.available ? `ML: ${cleanText(ml.label)} · ${cleanText(ml.provider_label)} · ${cleanText(ml.model)}` : `ML: ${cleanText(ml.error) || STR.mlUnavailable}`);
    body.appendChild(backends);
    if (analysis.isbn) {
      const isbnLine = document.createElement('p');
      isbnLine.className = 'small mb-1';
      isbnLine.textContent = `ISBN: ${analysis.isbn}`;
      body.appendChild(isbnLine);
    }
    (analysis.duplicates || []).forEach((dup) => {
      const note = document.createElement('div');
      note.className = 'alert alert-warning py-1 small mb-1';
      note.textContent = STR.alreadyThere(dup.display_id, dup.name);
      const link = document.createElement('a');
      link.href = dup.url;
      link.textContent = STR.open;
      note.appendChild(link);
      body.appendChild(note);
    });
    const form = document.createElement('div');
    form.className = 'row g-2 mt-1';
    const inputs = {};
    const prefill = analysis.prefill || {};
    const addField = (label, name, value) => {
      const row = fieldRow(label, name, value);
      form.appendChild(row.wrap);
      inputs[name] = row.input;
    };
    if (analysis.kind === 'book') {
      addField(STR.fieldTitle, 'title', prefill.title);
      addField(STR.fieldAuthors, 'authors', (prefill.authors || []).join(', '));
      addField(STR.fieldYear, 'published_date', prefill.year);
      addField(STR.fieldPublisher, 'publisher', prefill.publisher);
      addField(STR.fieldIsbn, 'isbn', prefill.isbn);
      (prefill.title_lines || []).slice(0, 3).forEach((line) => {
        if (!line || line === prefill.title) return;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'btn btn-sm btn-outline-secondary me-1 mb-1';
        chip.textContent = STR.titleChip(line);
        chip.addEventListener('click', () => { inputs.title.value = line; });
        form.appendChild(chip);
      });
    } else {
      addField(STR.fieldName, 'title', prefill.name);
    }
    addField(STR.fieldDescription, 'description', prefill.description);
    addField(STR.fieldTags, 'tags', Array.isArray(prefill.tags) ? prefill.tags.join(', ') : '');
    const missing = (analysis.missing_fields || []).join(', ');
    if (missing) {
      const hint = document.createElement('p');
      hint.className = 'small text-warning mb-0';
      hint.textContent = STR.completeHint(missing);
      form.appendChild(hint);
    }
    const actions = document.createElement('div');
    actions.className = 'col-12 d-grid gap-2';
    const adopt = document.createElement('button');
    adopt.type = 'button';
    adopt.className = 'btn btn-success';
    adopt.textContent = analysis.kind === 'book' ? STR.adoptBook : STR.adoptObject;
    adopt.addEventListener('click', () => adoptDraft(entry, analysis, inputs, adopt, state));
    actions.appendChild(adopt);
    form.appendChild(actions);
    body.appendChild(form);
    return col;
  };

  const adoptDraft = async (entry, analysis, inputs, button, state) => {
    button.disabled = true;
    const data = new FormData();
    const get = (name) => (inputs[name] ? cleanText(inputs[name].value) : '');
    data.append('title', get('title'));
    if (analysis.kind === 'book') {
      data.append('isbn', get('isbn') || analysis.isbn || '');
      data.append('authors', get('authors'));
      data.append('published_date', get('published_date'));
      data.append('publisher', get('publisher'));
      data.append('item_type', 'book');
      data.append('metadata_source', 'capture-inbox');
    } else {
      data.append('item_type', 'object');
    }
    data.append('description', get('description'));
    data.append('tags', get('tags'));
    data.append('response_format', 'json');
    data.append('capture_request_id', nextToken());
    data.append('_csrf_token', csrf);
    data.append('library_return', '');
    try {
      const target = analysis.kind === 'book' ? dataset.booksUrl : dataset.itemsUrl;
      const response = await fetch(target, {
        method: 'POST', body: data, cache: 'no-store', headers: {Accept: 'application/json'},
      });
      const result = await safeJson(response);
      if (!response.ok || !result || result.ok !== true) {
        const fields = result && result.fields ? ` (${Object.keys(result.fields).join(', ')})` : '';
        state.textContent = `${cleanText(result && result.error) || STR.saveFailed}${fields}`;
        button.disabled = false;
        return;
      }
      if (result.duplicate) {
        state.textContent = '';
        const note = document.createElement('span');
        note.textContent = `${cleanText(result.message) || STR.alreadyNoDup} `;
        if (result.object_id && entry.assign_url) {
          const linkExisting = document.createElement('button');
          linkExisting.type = 'button';
          linkExisting.className = 'btn btn-sm btn-outline-success ms-1';
          linkExisting.textContent = STR.linkAnyway;
          linkExisting.addEventListener('click', () => linkDuplicate(entry, result, analysis, linkExisting, state));
          note.appendChild(linkExisting);
        } else {
          button.disabled = false;
        }
        state.appendChild(note);
        return;
      }
      const linkData = new FormData();
      linkData.append('kind', analysis.kind === 'book' ? 'book' : 'object');
      linkData.append('object_id', result.object_id || '');
      linkData.append('_csrf_token', csrf);
      const linked = await fetch(entry.assign_url, {method: 'POST', body: linkData, cache: 'no-store'});
      const linkResult = await safeJson(linked);
      state.textContent = '';
      const done = document.createElement('span');
      done.textContent = STR.savedAs(result.display_id, result.name);
      const link = document.createElement('a');
      link.href = result.detail_url || (dataset.detailPattern || '').replace('__OBJECT__', result.object_id || '');
      link.textContent = STR.open;
      done.appendChild(link);
      if (linked.ok && linkResult && linkResult.ok) {
        const extra = linkResult.attached
          ? STR.photoAdopted
          : ((linkResult.notices || [])[0] ? `, ${(linkResult.notices || [])[0]}` : '.');
        done.appendChild(document.createTextNode(`${STR.entryLinked}${extra}`));
      } else {
        done.appendChild(document.createTextNode(STR.linkFailedKept));
      }
      state.appendChild(done);
    } catch (_error) {
      state.textContent = STR.saveNetwork;
      button.disabled = false;
    }
  };

  const linkDuplicate = async (entry, result, analysis, linkButton, state) => {
    linkButton.disabled = true;
    const data = new FormData();
    data.append('kind', analysis.kind === 'book' ? 'book' : 'object');
    data.append('object_id', result.object_id || '');
    data.append('_csrf_token', csrf);
    try {
      const response = await fetch(entry.assign_url, {method: 'POST', body: data, cache: 'no-store'});
      const linked = await safeJson(response);
      if (!response.ok || !linked || linked.ok !== true) {
        state.textContent = STR.linkFailedKept2(`${cleanText(linked && linked.error) || (EN ? 'Linking failed.' : 'Zuordnung fehlgeschlagen.')} `);
        linkButton.disabled = false;
        return;
      }
      const extra = linked.attached ? STR.photoAdopted : '';
      state.textContent = STR.linkedTo(result.display_id, result.name, extra);
      const open = document.createElement('a');
      open.href = result.detail_url || '';
      open.textContent = STR.open;
      state.appendChild(open);
    } catch (_error) {
      state.textContent = STR.linkNetworkKept;
      linkButton.disabled = false;
    }
  };

  const uploadPhoto = async (file, clientRequestId) => {
    const data = new FormData();
    data.append('photo', file, file.name || 'foto.jpg');
    data.append('client_request_id', clientRequestId);
    if (currentBatch) data.append('batch_id', currentBatch);
    if (consentBox && consentBox.checked) data.append('consent_meta', '1');
    data.append('_csrf_token', csrf);
    const response = await fetch(dataset.uploadUrl, {method: 'POST', body: data, cache: 'no-store'});
    const payload = await safeJson(response);
    if (!response.ok || !payload || payload.ok !== true) {
      throw new Error(cleanText(payload && payload.error) || STR.uploadFailed(response.status));
    }
    return payload;
  };

  const batchCtx = () => ({
    batchBox, knownEntries, cards, csrf, dataset, consentBox, cleanText, safeJson,
    fieldRow, ROLE_LABELS, setGlobal, nextToken, batchUrlFor, shortBatch,
    renderEntry, setCurrentBatch: (id) => { currentBatch = id; }, updateBatchStatus,
  });
  const batchApi = () => window.SimpleOfficeCaptureInboxBatch;
  const renderBatches = () => batchApi().renderBatches(batchCtx());
  const refreshAssignedCards = (batchId) => batchApi().refreshAssignedCards(batchCtx(), batchId);
  const openBatchReview = (batchId, body, button) => batchApi().openBatchReview(batchCtx(), batchId, body, button);
  const queueFiles = async (files) => {
    const list = Array.from(files || []).filter((file) => file && file.size > 0);
    if (!list.length) return;
    setGlobal(STR.queueSaving(list.length), 'info');
    const failures = [];
    let stored = 0;
    let duplicates = 0;
    for (const file of list) {
      try {
        const payload = await uploadPhoto(file, nextToken());
        cards.prepend(renderEntry(payload.entry));
        renderBatches();
        stored += 1;
        if (payload.duplicate) duplicates += 1;
      } catch (error) {
        failures.push(`${cleanText(file.name) || STR.photoFallback}: ${error.message}`);
      }
    }
    if (failures.length) {
      setGlobal(STR.queuePartial(stored, list.length, failures.join(' · ')), 'warning');
    } else if (duplicates) {
      setGlobal(STR.queueDup, 'warning');
    } else {
      setGlobal('', '');
    }
  };

  const takeInput = document.getElementById('capture-take-input');
  const chooseInput = document.getElementById('capture-choose-input');
  document.getElementById('capture-take')?.addEventListener('click', () => takeInput?.click());
  document.getElementById('capture-choose')?.addEventListener('click', () => chooseInput?.click());
  document.getElementById('capture-new-book')?.addEventListener('click', () => {
    currentBatch = nextToken();
    updateBatchStatus();
    setGlobal(STR.batchStarted, 'success');
  });
  document.getElementById('capture-single')?.addEventListener('click', () => {
    currentBatch = '';
    updateBatchStatus();
    setGlobal('', '');
  });
  takeInput?.addEventListener('change', () => { queueFiles(takeInput.files); takeInput.value = ''; });
  chooseInput?.addEventListener('change', () => { queueFiles(chooseInput.files); chooseInput.value = ''; });
  initialEntries.forEach((entry) => cards.appendChild(renderEntry(entry)));
  updateBatchStatus();
  renderBatches();
  loadMlStatus();
})();
