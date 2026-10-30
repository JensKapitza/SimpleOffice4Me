(() => {
  'use strict';
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
  const shortBatch = (id) => (id ? `Buchgruppe ${String(id).slice(0, 8)}` : 'Einzelfoto');
  const updateBatchStatus = () => {
    if (!batchStatus) return;
    batchStatus.textContent = currentBatch
      ? `Aktuell: ${shortBatch(currentBatch)} — weitere Fotos gehören zu diesem Buch.`
      : 'Aktuell: Einzelfotos (keine Buchgruppe).';
  };
  const cleanText = (value) => String(value || '').trim();
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
      mlLine.textContent = 'ML-Status unbekannt';
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
      badge.textContent = `Zugeordnet: #${assignment.display_id || ''} ${assignment.name || ''}`.trim();
    } else {
      badge.className = 'badge text-bg-warning me-1';
      badge.textContent = 'Noch nicht zugeordnet';
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
      setGlobal(cleanText(payload && payload.error) || 'Umgruppieren fehlgeschlagen.', 'danger');
      button.disabled = false;
    }
  };

  const ROLE_LABELS = {title: 'Titelblatt', imprint: 'Impressum', spine: 'Buchrücken', other: 'Sonstiges'};

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
          warn.textContent = 'Rolle im Eingangskorb gespeichert, am vorhandenen Foto noch nicht aktualisiert – erneut Rolle wählen zum Wiederholen.';
          state.appendChild(warn);
          const retry = document.createElement('button');
          retry.type = 'button';
          retry.className = 'btn btn-sm btn-outline-warning ms-1';
          retry.textContent = 'Wiederholen';
          retry.addEventListener('click', () => saveRole(entry, select, state));
          state.appendChild(retry);
        } else {
          state.textContent = payload.attached_role_updated === true
            ? '✓ (auch am zugeordneten Objekt aktualisiert)'
            : '✓';
        }
      } else {
        select.value = entry.photo_role || '';
        state.textContent = cleanText(payload && payload.error) || 'Speichern fehlgeschlagen.';
      }
    } catch (_error) {
      select.value = entry.photo_role || '';
      state.textContent = 'Netzwerkfehler — Rolle nicht gespeichert, bitte erneut versuchen.';
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
    img.alt = 'Gespeicherte Aufnahme';
    img.loading = 'lazy';
    img.src = entry.preview_url || '';
    card.appendChild(img);
    const body = document.createElement('div');
    body.className = 'card-body';
    const state = document.createElement('p');
    state.className = 'small text-secondary';
    state.appendChild(statusBadge(entry));
    const meta = document.createElement('span');
    const taken = cleanText(entry.created_at).replace('T', ' ').slice(0, 16);
    const size = entry.original && entry.original.size ? ` · ${Math.round(entry.original.size / 1024)} KB` : '';
    meta.textContent = `Aufgenommen ${taken}${size}`;
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
      join.textContent = 'Zum aktuellen Buch';
      join.addEventListener('click', () => moveEntry(entry, currentBatch, join));
      batchLine.appendChild(join);
    }
    if (entry.batch_id) {
      const leave = document.createElement('button');
      leave.type = 'button';
      leave.className = 'btn btn-sm btn-outline-secondary';
      leave.textContent = 'Aus Gruppe lösen';
      leave.addEventListener('click', () => moveEntry(entry, '', leave));
      batchLine.appendChild(leave);
    }
    body.appendChild(batchLine);
    const roleLine = document.createElement('p');
    roleLine.className = 'small mb-1';
    const roleLabel = document.createElement('label');
    roleLabel.className = 'form-label small mb-0 me-1';
    roleLabel.textContent = 'Fotorolle: ';
    const roleSelect = document.createElement('select');
    roleSelect.className = 'form-select form-select-sm d-inline-block w-auto';
    roleSelect.setAttribute('aria-label', 'Fotorolle');
    [['', 'Ohne Rolle'], ['title', 'Titelblatt'], ['imprint', 'Impressum'], ['spine', 'Buchrücken'], ['other', 'Sonstiges']].forEach(([value, text]) => {
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
      note.textContent = 'Analyse ausstehend oder fehlgeschlagen.';
      body.appendChild(note);
      if (entry.analyze_url) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'btn btn-sm btn-outline-primary';
        retry.textContent = 'Erneut analysieren';
        retry.addEventListener('click', async () => {
          retry.disabled = true;
          const data = new FormData();
          data.append('_csrf_token', csrf);
          const response = await fetch(entry.analyze_url, {method: 'POST', body: data, cache: 'no-store'});
          const payload = await safeJson(response);
          if (response.ok && payload && payload.ok) {
            col.replaceWith(renderEntry(payload.entry));
          } else {
            note.textContent = cleanText(payload && payload.error) || 'Analyse fehlgeschlagen.';
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
    badge.textContent = analysis.kind === 'book' ? 'Buch' : 'Gegenstand';
    kindLine.appendChild(badge);
    kindLine.appendChild(document.createTextNode(cleanText(analysis.kind_reason)));
    body.appendChild(kindLine);
    const backends = document.createElement('p');
    backends.className = 'small text-secondary mb-1';
    const ocr = analysis.ocr || {};
    const ml = analysis.ml || {};
    backends.textContent = `OCR: ${ocr.engine || '–'} (${ocr.status || '–'}) · `
      + (ml.available ? `ML: ${cleanText(ml.label)} · ${cleanText(ml.provider_label)} · ${cleanText(ml.model)}` : `ML: ${cleanText(ml.error) || 'nicht verfügbar'}`);
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
      note.textContent = `Bereits vorhanden: #${dup.display_id} ${dup.name} `;
      const link = document.createElement('a');
      link.href = dup.url;
      link.textContent = 'Öffnen';
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
      addField('Titel', 'title', prefill.title);
      addField('Autoren', 'authors', (prefill.authors || []).join(', '));
      addField('Jahr', 'published_date', prefill.year);
      addField('Verlag', 'publisher', prefill.publisher);
      addField('ISBN', 'isbn', prefill.isbn);
      (prefill.title_lines || []).slice(0, 3).forEach((line) => {
        if (!line || line === prefill.title) return;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'btn btn-sm btn-outline-secondary me-1 mb-1';
        chip.textContent = `Titel: ${line.slice(0, 60)}`;
        chip.addEventListener('click', () => { inputs.title.value = line; });
        form.appendChild(chip);
      });
    } else {
      addField('Name', 'title', prefill.name);
    }
    const missing = (analysis.missing_fields || []).join(', ');
    if (missing) {
      const hint = document.createElement('p');
      hint.className = 'small text-warning mb-0';
      hint.textContent = `Bitte ergänzen: ${missing}`;
      form.appendChild(hint);
    }
    const actions = document.createElement('div');
    actions.className = 'col-12 d-grid gap-2';
    const adopt = document.createElement('button');
    adopt.type = 'button';
    adopt.className = 'btn btn-success';
    adopt.textContent = analysis.kind === 'book' ? 'Als Buch übernehmen' : 'Als Gegenstand übernehmen';
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
        state.textContent = `${cleanText(result && result.error) || 'Speichern fehlgeschlagen.'}${fields}`;
        button.disabled = false;
        return;
      }
      if (result.duplicate) {
        state.textContent = '';
        const note = document.createElement('span');
        note.textContent = `${cleanText(result.message) || 'Bereits vorhanden – kein Duplikat angelegt.'} `;
        if (result.object_id && entry.assign_url) {
          const linkExisting = document.createElement('button');
          linkExisting.type = 'button';
          linkExisting.className = 'btn btn-sm btn-outline-success ms-1';
          linkExisting.textContent = 'Trotzdem zuordnen';
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
      done.textContent = `Gespeichert: #${result.display_id || ''} ${result.name || ''} `;
      const link = document.createElement('a');
      link.href = result.detail_url || (dataset.detailPattern || '').replace('__OBJECT__', result.object_id || '');
      link.textContent = 'Öffnen';
      done.appendChild(link);
      if (linked.ok && linkResult && linkResult.ok) {
        const extra = linkResult.attached
          ? ', Foto übernommen.'
          : ((linkResult.notices || [])[0] ? `, ${(linkResult.notices || [])[0]}` : '.');
        done.appendChild(document.createTextNode(` · Eintrag zugeordnet${extra}`));
      } else {
        done.appendChild(document.createTextNode(' · Zuordnung fehlgeschlagen, Original bleibt im Korb.'));
      }
      state.appendChild(done);
    } catch (_error) {
      state.textContent = 'Speichern fehlgeschlagen (Netzwerk).';
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
        state.textContent = `${cleanText(linked && linked.error) || 'Zuordnung fehlgeschlagen.'} Original bleibt im Korb.`;
        linkButton.disabled = false;
        return;
      }
      const extra = linked.attached ? ', Foto übernommen.' : '';
      state.textContent = `Zugeordnet: #${result.display_id || ''} ${result.name || ''}${extra} `;
      const open = document.createElement('a');
      open.href = result.detail_url || '';
      open.textContent = 'Öffnen';
      state.appendChild(open);
    } catch (_error) {
      state.textContent = 'Zuordnung fehlgeschlagen (Netzwerk). Original bleibt im Korb.';
      linkButton.disabled = false;
    }
  };

  const uploadPhoto = async (file, clientRequestId) => {
    const data = new FormData();
    data.append('photo', file, file.name || 'foto.jpg');
    data.append('client_request_id', clientRequestId);
    if (currentBatch) data.append('batch_id', currentBatch);
    data.append('_csrf_token', csrf);
    const response = await fetch(dataset.uploadUrl, {method: 'POST', body: data, cache: 'no-store'});
    const payload = await safeJson(response);
    if (!response.ok || !payload || payload.ok !== true) {
      throw new Error(cleanText(payload && payload.error) || `Upload fehlgeschlagen (${response.status}).`);
    }
    return payload;
  };

  const renderBatches = () => {
    if (!batchBox) return;
    batchBox.textContent = '';
    const groups = new Map();
    knownEntries.forEach((entry) => {
      if (!entry.batch_id) return;
      if (!groups.has(entry.batch_id)) groups.set(entry.batch_id, []);
      groups.get(entry.batch_id).push(entry);
    });
    groups.forEach((members, batchId) => {
      const col = document.createElement('div');
      col.className = 'col-12';
      const card = document.createElement('div');
      card.className = 'card shadow-sm border-success';
      const body = document.createElement('div');
      body.className = 'card-body';
      const title = document.createElement('h2');
      title.className = 'h5 mb-1';
      title.textContent = `${shortBatch(batchId)} · ${members.length} Foto(s)`;
      body.appendChild(title);
      const hint = document.createElement('p');
      hint.className = 'small text-secondary mb-2';
      hint.textContent = 'Gemeinsam prüfen, korrigieren und als ein Buch übernehmen.';
      body.appendChild(hint);
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'btn btn-success';
      open.textContent = 'Buchgruppe prüfen und übernehmen';
      open.addEventListener('click', () => openBatchReview(batchId, body, open));
      body.appendChild(open);
      card.appendChild(body);
      col.appendChild(card);
      batchBox.appendChild(col);
    });
  };

  const refreshAssignedCards = async (batchId) => {
    const fresh = await fetch(batchUrlFor(batchId), {cache: 'no-store'});
    const reloaded = await safeJson(fresh);
    if (!fresh.ok || !reloaded || reloaded.ok !== true) {
      throw new Error('Aktualisierung fehlgeschlagen');
    }
    (reloaded.entries || []).forEach((entry) => {
      knownEntries.set(entry.entry_id, entry);
      const col = cards.querySelector(`[data-entry-id="${entry.entry_id}"]`);
      if (col) col.replaceWith(renderEntry(entry));
    });
  };

  const openBatchReview = async (batchId, body, button) => {
    button.disabled = true;
    const url = batchUrlFor(batchId);
    if (!url) {
      setGlobal('Buchgruppen-Ansicht ist nicht verfügbar.', 'danger');
      button.disabled = false;
      return;
    }
    const response = await fetch(url, {cache: 'no-store'});
    const payload = await safeJson(response);
    if (!response.ok || !payload || payload.ok !== true) {
      setGlobal(cleanText(payload && payload.error) || 'Buchgruppe konnte nicht geladen werden.', 'danger');
      button.disabled = false;
      return;
    }
    body.appendChild(renderBatchReview(payload));
    button.remove();
  };

  const renderBatchReview = (payload) => {
    const wrap = document.createElement('div');
    wrap.className = 'mt-3';
    const combined = payload.combined || {};
    const prefill = combined.prefill || {};
    const kindLine = document.createElement('p');
    kindLine.className = 'small mb-1';
    const badge = document.createElement('span');
    badge.className = `badge ${combined.kind === 'book' ? 'text-bg-primary' : 'text-bg-secondary'} me-1`;
    badge.textContent = combined.kind === 'book' ? 'Buch' : 'Gegenstand';
    kindLine.appendChild(badge);
    kindLine.appendChild(document.createTextNode(cleanText(combined.kind_reason)));
    wrap.appendChild(kindLine);
    const models = document.createElement('p');
    models.className = 'small text-secondary mb-1';
    const modelList = (combined.ml_models || []).join(', ');
    models.textContent = `Fotos: ${combined.analyzed_count || 0}/${combined.photo_count || 0} analysiert`
      + (modelList ? ` · ML-Modelle: ${modelList}` : ' · ML: nicht verfügbar');
    wrap.appendChild(models);
    (combined.warnings || []).forEach((text) => {
      const note = document.createElement('div');
      note.className = 'alert alert-warning py-1 small mb-1';
      note.textContent = text;
      wrap.appendChild(note);
    });
    if (combined.isbn_conflict) {
      const note = document.createElement('div');
      note.className = 'alert alert-danger py-1 small mb-1';
      note.textContent = `Unterschiedliche ISBNs: ${(combined.isbn_candidates || []).join(' · ')} — bitte korrekte ISBN unten wählen.`;
      wrap.appendChild(note);
    }
    const rolesByPhoto = {};
    (payload.entries || []).forEach((item) => {
      if (item && item.entry_id) rolesByPhoto[item.entry_id] = item.photo_role || '';
    });
    (combined.photos || []).forEach((photo) => {
      const line = document.createElement('p');
      line.className = 'small text-secondary mb-0';
      const role = rolesByPhoto[photo.photo_id] || '';
      line.textContent = `Foto ${String(photo.photo_id || '').slice(0, 8)}: `
        + `${photo.kind || '–'}${photo.isbn ? ` · ISBN ${photo.isbn}` : ''}`
        + `${role && ROLE_LABELS[role] ? ` · Rolle: ${ROLE_LABELS[role]}` : ''} · `
        + (photo.ml_available
          ? `ML ${photo.ml_label || ''} · ${photo.ml_provider_label || ''} · ${photo.ml_model || ''}`
          : `ML: ${photo.ml_error || 'nicht verfügbar'}`);
      wrap.appendChild(line);
    });
    const form = document.createElement('div');
    form.className = 'row g-2 mt-2';
    const inputs = {};
    const addField = (label, name, value) => {
      const row = fieldRow(label, name, value);
      form.appendChild(row.wrap);
      inputs[name] = row.input;
    };
    if (combined.kind === 'book') {
      addField('Titel', 'title', prefill.title);
      addField('Autoren', 'authors', (prefill.authors || []).join(', '));
      addField('Jahr', 'published_date', prefill.year);
      addField('Verlag', 'publisher', prefill.publisher);
      addField('ISBN', 'isbn', prefill.isbn);
    } else {
      addField('Name', 'title', prefill.name);
    }
    const missing = (combined.missing_fields || []).join(', ');
    if (missing) {
      const hint = document.createElement('p');
      hint.className = 'small text-warning mb-0';
      hint.textContent = `Bitte ergänzen: ${missing}`;
      form.appendChild(hint);
    }
    const state = document.createElement('p');
    state.className = 'small mt-2 mb-0';
    state.setAttribute('role', 'status');
    const actions = document.createElement('div');
    actions.className = 'col-12 d-grid gap-2';
    const analyze = document.createElement('button');
    analyze.type = 'button';
    analyze.className = 'btn btn-outline-primary';
    analyze.textContent = 'Gruppe erneut analysieren';
    analyze.addEventListener('click', async () => {
      analyze.disabled = true;
      const data = new FormData();
      if (consentBox && consentBox.checked) data.append('consent_meta', '1');
      data.append('_csrf_token', csrf);
      const target = `${batchUrlFor(payload.batch_id)}/analyze`;
      const response = await fetch(target, {method: 'POST', body: data, cache: 'no-store'});
      const result = await safeJson(response);
      if (response.ok && result && result.ok) {
        (result.entries || []).forEach((entry) => {
          knownEntries.set(entry.entry_id, entry);
          const col = cards.querySelector(`[data-entry-id="${entry.entry_id}"]`);
          if (col) col.replaceWith(renderEntry(entry));
        });
        wrap.replaceWith(renderBatchReview(result));
      } else {
        state.textContent = cleanText(result && result.error) || 'Analyse fehlgeschlagen.';
        analyze.disabled = false;
      }
    });
    actions.appendChild(analyze);
    const adopt = document.createElement('button');
    adopt.type = 'button';
    adopt.className = 'btn btn-success';
    adopt.textContent = combined.kind === 'book' ? 'Als ein Buch übernehmen' : 'Als Gegenstand übernehmen';
    adopt.addEventListener('click', async () => {
      adopt.disabled = true;
      const data = new FormData();
      const get = (name) => (inputs[name] ? cleanText(inputs[name].value) : '');
      data.append('title', get('title'));
      if (combined.kind === 'book') {
        data.append('isbn', get('isbn') || combined.isbn || '');
        data.append('authors', get('authors'));
        data.append('published_date', get('published_date'));
        data.append('publisher', get('publisher'));
        data.append('item_type', 'book');
      } else {
        data.append('item_type', 'object');
      }
      data.append('capture_request_id', nextToken());
      data.append('_csrf_token', csrf);
      const target = `${batchUrlFor(payload.batch_id)}/adopt`;
      const response = await fetch(target, {method: 'POST', body: data, cache: 'no-store'});
      const result = await safeJson(response);
      if (!response.ok || !result || result.ok !== true) {
        state.textContent = cleanText(result && result.error) || 'Speichern fehlgeschlagen.';
        adopt.disabled = false;
        return;
      }
      state.textContent = '';
      const done = document.createElement('span');
      done.textContent = `${cleanText(result.message) || 'Gespeichert.'} `;
      if (result.detail_url) {
        const link = document.createElement('a');
        link.href = result.detail_url;
        link.textContent = 'Öffnen';
        done.appendChild(link);
      }
      state.appendChild(done);
      if (!result.duplicate) {
        const fresh = await fetch(batchUrlFor(payload.batch_id), {cache: 'no-store'});
        const reloaded = await safeJson(fresh);
        if (fresh.ok && reloaded && reloaded.ok) {
          (reloaded.entries || []).forEach((entry) => {
            knownEntries.set(entry.entry_id, entry);
            const col = cards.querySelector(`[data-entry-id="${entry.entry_id}"]`);
            if (col) col.replaceWith(renderEntry(entry));
          });
        }
        renderBatches();
      }
    });
    actions.appendChild(adopt);
    if (combined.kind === 'book' && payload.assign_url) {
      const assignRow = document.createElement('div');
      assignRow.className = 'col-12 d-flex flex-wrap gap-2 align-items-end mt-2';
      const idWrap = document.createElement('div');
      const idLabel = document.createElement('label');
      idLabel.className = 'form-label small mb-1';
      idLabel.textContent = 'Vorhandene Buch-ID';
      const idInput = document.createElement('input');
      idInput.className = 'form-control form-control-sm';
      idInput.placeholder = 'z. B. #12';
      idInput.setAttribute('aria-label', 'Vorhandene Buch-ID');
      idWrap.appendChild(idLabel);
      idWrap.appendChild(idInput);
      assignRow.appendChild(idWrap);
      const assignExisting = document.createElement('button');
      assignExisting.type = 'button';
      assignExisting.className = 'btn btn-sm btn-outline-success';
      assignExisting.textContent = 'Ganze Gruppe vorhandenem Buch zuordnen';
      assignExisting.addEventListener('click', async () => {
        assignExisting.disabled = true;
        state.textContent = '';
        let result = null;
        try {
          const data = new FormData();
          data.append('object_id', cleanText(idInput.value));
          data.append('_csrf_token', csrf);
          const response = await fetch(payload.assign_url, {method: 'POST', body: data, cache: 'no-store'});
          result = await safeJson(response);
          if (!response.ok || !result || result.ok !== true) {
            state.textContent = cleanText(result && result.error) || 'Zuordnung fehlgeschlagen.';
            assignExisting.disabled = false;
            return;
          }
        } catch (_error) {
          state.textContent = 'Netzwerkfehler — Zuordnung unbekannt, bitte erneut versuchen.';
          assignExisting.disabled = false;
          return;
        }
        const done = document.createElement('span');
        done.textContent = `${cleanText(result.message) || 'Zugeordnet.'} `
          + `(${result.assigned || 0}/${result.photo_count || 0} Fotos, ${result.attached || 0} übernommen) `;
        if (result.detail_url) {
          const link = document.createElement('a');
          link.href = result.detail_url;
          link.textContent = 'Öffnen';
          done.appendChild(link);
        }
        state.appendChild(done);
        const notices = result.notices || [];
        notices.forEach((text) => {
          const note = document.createElement('div');
          note.className = 'alert alert-warning py-1 small mb-1 mt-1';
          note.textContent = text;
          state.appendChild(note);
        });
        if (notices.length > 0) {
          const retryHint = document.createElement('div');
          retryHint.className = 'alert alert-info py-1 small mb-1 mt-1';
          retryHint.textContent = 'Nicht alle Fotos wurden übernommen — bei Bedarf erneut auf „Zuordnen“ klicken.';
          state.appendChild(retryHint);
          try {
            await refreshAssignedCards(payload.batch_id);
          } catch (_error) {
            const warn = document.createElement('div');
            warn.className = 'alert alert-warning py-1 small mb-1 mt-1';
            warn.textContent = 'Anzeige konnte nicht aktualisiert werden — die Zuordnung wurde gespeichert.';
            state.appendChild(warn);
          }
          assignExisting.disabled = false;
          return;
        }
        try {
          await refreshAssignedCards(payload.batch_id);
        } catch (_error) {
          const warn = document.createElement('div');
          warn.className = 'alert alert-warning py-1 small mb-1 mt-1';
          warn.textContent = 'Anzeige konnte nicht aktualisiert werden — die Zuordnung wurde gespeichert, Seite ggf. neu laden.';
          state.appendChild(warn);
          assignExisting.disabled = false;
          return;
        }
        renderBatches();
      });
      assignRow.appendChild(assignExisting);
      form.appendChild(assignRow);
    }
    form.appendChild(actions);
    wrap.appendChild(form);
    wrap.appendChild(state);
    return wrap;
  };

  const queueFiles = async (files) => {
    const list = Array.from(files || []).filter((file) => file && file.size > 0);
    if (!list.length) return;
    setGlobal(`${list.length} Foto(s) werden gespeichert und analysiert …`, 'info');
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
        failures.push(`${cleanText(file.name) || 'Foto'}: ${error.message}`);
      }
    }
    if (failures.length) {
      setGlobal(`${stored} von ${list.length} Fotos gespeichert. Fehler: ${failures.join(' · ')}`, 'warning');
    } else if (duplicates) {
      setGlobal('Dieses Foto ist bereits im Eingangskorb – kein Duplikat angelegt.', 'warning');
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
    setGlobal('Neues Buch begonnen — die nächsten Fotos gehören zu dieser Buchgruppe.', 'success');
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
