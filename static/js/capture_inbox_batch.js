(() => {
  'use strict';
  // Split out of static/js/capture_inbox.js to keep that module under the
  // file-size gate: book-group rendering, review, adopt and assign flows.
  // Shared state arrives via ctx (same pattern as capture_marketplace.js).
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    open: EN ? 'Open' : 'Öffnen',
    kindBook: EN ? 'Book' : 'Buch', kindObject: EN ? 'Object' : 'Gegenstand',
    analysisFailed: EN ? 'Analysis failed.' : 'Analyse fehlgeschlagen.',
    saveFailed: EN ? 'Saving failed.' : 'Speichern fehlgeschlagen.',
    groupPhotos: (label, n) => EN ? `${label} · ${n} photo(s)` : `${label} · ${n} Foto(s)`,
    groupHint: EN ? 'Review together, correct, and adopt as one book.' : 'Gemeinsam prüfen, korrigieren und als ein Buch übernehmen.',
    groupReview: EN ? 'Review and adopt book group' : 'Buchgruppe prüfen und übernehmen',
    groupResume: EN ? 'Resume (more photos)' : 'Fortsetzen (weitere Fotos)',
    groupResumed: (id) => EN ? `Book group ${id.slice(0, 8)} resumed — further photos belong to this book.` : `Buchgruppe ${id.slice(0, 8)} fortgesetzt — weitere Fotos gehören zu diesem Buch.`,
    refreshFailed: EN ? 'Refresh failed' : 'Aktualisierung fehlgeschlagen',
    groupViewMissing: EN ? 'Book group view is not available.' : 'Buchgruppen-Ansicht ist nicht verfügbar.',
    groupLoadFailed: EN ? 'Could not load book group.' : 'Buchgruppe konnte nicht geladen werden.',
    photosAnalyzed: (a, p) => EN ? `Photos: ${a}/${p} analyzed` : `Fotos: ${a}/${p} analysiert`,
    mlModels: (list) => EN ? ` · ML models: ${list}` : ` · ML-Modelle: ${list}`,
    mlNone: EN ? ' · ML: not available' : ' · ML: nicht verfügbar',
    mlUnavailable: EN ? 'not available' : 'nicht verfügbar',
    isbnConflict: (ids) => EN ? `Different ISBNs: ${ids} — please choose the correct ISBN below.` : `Unterschiedliche ISBNs: ${ids} — bitte korrekte ISBN unten wählen.`,
    alreadyThere: (id, name) => EN ? `Already exists: #${id} ${name} ` : `Bereits vorhanden: #${id} ${name} `,
    groupAssign: EN ? 'Assign group to this book' : 'Gruppe diesem Buch zuordnen',
    photoLine: (id, kind, isbn, role, ml, note, tags) => EN ? `Photo ${String(id).slice(0, 8)}: ${kind}${isbn}${role}${ml}${note}${tags}` : `Foto ${String(id).slice(0, 8)}: ${kind}${isbn}${role}${ml}${note}${tags}`,
    rolePart: (label) => EN ? ` · Role: ${label}` : ` · Rolle: ${label}`,
    mlNote: (note) => EN ? ` · Note: ${note.slice(0, 120)}` : ` · Notiz: ${note.slice(0, 120)}`,
    mlTags: (tags) => EN ? ` · Tags: ${tags.slice(0, 120)}` : ` · Tags: ${tags.slice(0, 120)}`,
    fieldTitle: EN ? 'Title' : 'Titel', fieldAuthors: EN ? 'Authors' : 'Autoren',
    fieldYear: EN ? 'Year' : 'Jahr', fieldPublisher: EN ? 'Publisher' : 'Verlag',
    fieldIsbn: EN ? 'ISBN' : 'ISBN', fieldName: EN ? 'Name' : 'Name',
    fieldDescription: EN ? 'Description' : 'Beschreibung', fieldTags: EN ? 'Tags (comma-separated)' : 'Tags (Komma-getrennt)',
    fieldPrice: EN ? 'Price (EUR guide)' : 'Preis (EUR-Richtwert)',
    fieldCurrency: EN ? 'Currency' : 'Währung', fieldPriceSource: EN ? 'Price source' : 'Preisquelle',
    fieldDescription: EN ? 'Description' : 'Beschreibung', fieldTags: EN ? 'Tags (comma-separated)' : 'Tags (Komma-getrennt)',
    completeHint: (missing) => EN ? `Please complete: ${missing}` : `Bitte ergänzen: ${missing}`,
    completeHint: (missing) => EN ? `Please complete: ${missing}` : `Bitte ergänzen: ${missing}`,
    shelfLabel: EN ? 'Shelf/location: ' : 'Regal/Standort: ',
    shelfAria: EN ? 'Choose shelf or location' : 'Regal oder Standort wählen',
    shelfNone: EN ? 'Adopt without shelf' : 'Ohne Regal übernehmen',
    shelfHint: EN ? 'No shelves yet — create one in the library first, or adopt without a shelf.' : 'Keine Regale vorhanden — erst in der Bibliothek anlegen, oder ohne Regal übernehmen.',
    shelfRetry: EN ? ' Click “Adopt” again to retry.' : ' Zum Wiederholen erneut auf „Übernehmen“ klicken.',
    shelfSet: (code, path) => EN ? `Shelf: ${code || '–'}${path ? ` — ${path}` : ''}` : `Regal: ${code || '–'}${path ? ` — ${path}` : ''}`,
    shelfKept: EN ? 'Shelf unchanged (book already exists).' : 'Regal nicht geändert (Buch existiert bereits).',
    shelfSkipped: EN ? 'Adopted without shelf.' : 'Ohne Regal übernommen.',
    groupAnalyze: EN ? 'Analyze group again' : 'Gruppe erneut analysieren',
    adoptOneBook: EN ? 'Adopt as one book' : 'Als ein Buch übernehmen',
    adoptObject: EN ? 'Adopt as object' : 'Als Gegenstand übernehmen',
    networkRetry: EN ? 'Network error — please try again.' : 'Netzwerkfehler — bitte erneut versuchen.',
    savedFallback: EN ? 'Saved.' : 'Gespeichert.',
    displayNotUpdated: EN ? 'Display could not be refreshed — the adoption was saved, reload the page if needed.' : 'Anzeige konnte nicht aktualisiert werden — die Übernahme wurde gespeichert, Seite ggf. neu laden.',
    existingBookId: EN ? 'Existing book ID' : 'Vorhandene Buch-ID',
    idPlaceholder: EN ? 'e.g. #12' : 'z. B. #12',
    assignWhole: EN ? 'Assign whole group to existing book' : 'Ganze Gruppe vorhandenem Buch zuordnen',
    assignFailed: EN ? 'Assignment failed.' : 'Zuordnung fehlgeschlagen.',
    assignNetwork: EN ? 'Network error — assignment unknown, please try again.' : 'Netzwerkfehler — Zuordnung unbekannt, bitte erneut versuchen.',
    assignedStats: (msg, a, p, t) => EN ? `${msg} (${a}/${p} photos, ${t} adopted) ` : `${msg} (${a}/${p} Fotos, ${t} übernommen) `,
    assignedFallback: EN ? 'Assigned.' : 'Zugeordnet.',
    notAllAdopted: EN ? 'Not all photos were adopted — click “Assign” again if needed.' : 'Nicht alle Fotos wurden übernommen — bei Bedarf erneut auf „Zuordnen“ klicken.',
    displayKeptAssign: EN ? 'Display could not be refreshed — the assignment was saved.' : 'Anzeige konnte nicht aktualisiert werden — die Zuordnung wurde gespeichert.',
    displayKeptAssignReload: EN ? 'Display could not be refreshed — the assignment was saved, reload the page if needed.' : 'Anzeige konnte nicht aktualisiert werden — die Zuordnung wurde gespeichert, Seite ggf. neu laden.',
  };
  const renderBatches = (ctx) => {
    const {batchBox, knownEntries, cards, setGlobal, shortBatch, renderEntry, openBatchReview, setCurrentBatch, updateBatchStatus} = ctx;
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
      title.textContent = STR.groupPhotos(shortBatch(batchId), members.length);
      body.appendChild(title);
      const hint = document.createElement('p');
      hint.className = 'small text-secondary mb-2';
      hint.textContent = STR.groupHint;
      body.appendChild(hint);
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'btn btn-success';
      open.textContent = STR.groupReview;
      open.addEventListener('click', () => openBatchReview(ctx, batchId, body, open));
      body.appendChild(open);
      const resume = document.createElement('button');
      resume.type = 'button';
      resume.className = 'btn btn-outline-success ms-2';
      resume.textContent = STR.groupResume;
      resume.addEventListener('click', () => {
        setCurrentBatch(batchId);
        updateBatchStatus();
        setGlobal(STR.groupResumed(batchId), 'success');
        knownEntries.forEach((entry) => {
          const col = cards.querySelector(`[data-entry-id="${entry.entry_id}"]`);
          if (col) col.replaceWith(renderEntry(entry));
        });
      });
      body.appendChild(resume);
      card.appendChild(body);
      col.appendChild(card);
      batchBox.appendChild(col);
    });
  };

  const refreshAssignedCards = async (ctx, batchId) => {
    const {batchUrlFor, safeJson, knownEntries, cards, renderEntry} = ctx;
    const fresh = await fetch(batchUrlFor(batchId), {cache: 'no-store'});
    const reloaded = await safeJson(fresh);
    if (!fresh.ok || !reloaded || reloaded.ok !== true) {
      throw new Error(STR.refreshFailed);
    }
    (reloaded.entries || []).forEach((entry) => {
      knownEntries.set(entry.entry_id, entry);
      const col = cards.querySelector(`[data-entry-id="${entry.entry_id}"]`);
      if (col) col.replaceWith(renderEntry(entry));
    });
  };

  const openBatchReview = async (ctx, batchId, body, button) => {
    const {batchUrlFor, safeJson, setGlobal, cleanText} = ctx;
    button.disabled = true;
    const url = batchUrlFor(batchId);
    if (!url) {
      setGlobal(STR.groupViewMissing, 'danger');
      button.disabled = false;
      return;
    }
    const response = await fetch(url, {cache: 'no-store'});
    const payload = await safeJson(response);
    if (!response.ok || !payload || payload.ok !== true) {
      setGlobal(cleanText(payload && payload.error) || STR.groupLoadFailed, 'danger');
      button.disabled = false;
      return;
    }
    body.appendChild(renderBatchReview(ctx, payload));
    button.remove();
  };

  const renderBatchReview = (ctx, payload) => {
    const {cleanText, fieldRow, ROLE_LABELS, cards, knownEntries, csrf, dataset, consentBox, setGlobal, nextToken, batchUrlFor, safeJson, shortBatch} = ctx;
    const InboxBatch = window.SimpleOfficeCaptureInboxBatch;
    const wrap = document.createElement('div');
    wrap.className = 'mt-3';
    const combined = payload.combined || {};
    const prefill = combined.prefill || {};
    const kindLine = document.createElement('p');
    kindLine.className = 'small mb-1';
    const badge = document.createElement('span');
    badge.className = `badge ${combined.kind === 'book' ? 'text-bg-primary' : 'text-bg-secondary'} me-1`;
    badge.textContent = combined.kind === 'book' ? STR.kindBook : STR.kindObject;
    kindLine.appendChild(badge);
    kindLine.appendChild(document.createTextNode(cleanText(combined.kind_reason)));
    wrap.appendChild(kindLine);
    const models = document.createElement('p');
    models.className = 'small text-secondary mb-1';
    const modelList = (combined.ml_models || []).join(', ');
    models.textContent = STR.photosAnalyzed(combined.analyzed_count || 0, combined.photo_count || 0)
      + (modelList ? STR.mlModels(modelList) : STR.mlNone);
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
      note.textContent = STR.isbnConflict((combined.isbn_candidates || []).join(' · '));
      wrap.appendChild(note);
    }
    (combined.duplicates || []).forEach((dup) => {
      const note = document.createElement('div');
      note.className = 'alert alert-warning py-1 small mb-1';
      note.textContent = STR.alreadyThere(dup.display_id || '', dup.name || '');
      if (dup.url) {
        const link = document.createElement('a');
        link.href = dup.url;
        link.textContent = STR.open;
        note.appendChild(link);
        note.appendChild(document.createTextNode(' '));
      }
      if (payload.assign_url && dup.object_id) {
        const linkBtn = document.createElement('button');
        linkBtn.type = 'button';
        linkBtn.className = 'btn btn-sm btn-outline-success ms-1';
        linkBtn.textContent = STR.groupAssign;
        linkBtn.addEventListener('click', () => {
          if (assignIdInput && assignExistingBtn) {
            assignIdInput.value = dup.object_id || '';
            assignExistingBtn.click();
          }
        });
        note.appendChild(linkBtn);
      }
      wrap.appendChild(note);
    });
    const rolesByPhoto = {};
    (payload.entries || []).forEach((item) => {
      if (item && item.entry_id) rolesByPhoto[item.entry_id] = item.photo_role || '';
    });
    (combined.photos || []).forEach((photo) => {
      const line = document.createElement('p');
      line.className = 'small text-secondary mb-0';
      const role = rolesByPhoto[photo.photo_id] || '';
      const photoTags = Array.isArray(photo.ml_tags) ? photo.ml_tags.filter((tag) => cleanText(tag)).join(', ') : '';
      line.textContent = STR.photoLine(
        photo.photo_id,
        `${photo.kind || '–'}${photo.isbn ? ` · ISBN ${photo.isbn}` : ''}`,
        role && ROLE_LABELS[role] ? STR.rolePart(ROLE_LABELS[role]) : '',
        photo.ml_available
          ? `ML ${photo.ml_label || ''} · ${photo.ml_provider_label || ''} · ${photo.ml_model || ''}`
          : `ML: ${photo.ml_error || STR.mlUnavailable}`,
        photo.ml_note ? STR.mlNote(cleanText(photo.ml_note)) : '',
        photoTags ? STR.mlTags(photoTags) : '',
      );
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
      addField(STR.fieldTitle, 'title', prefill.title);
      addField(STR.fieldAuthors, 'authors', (prefill.authors || []).join(', '));
      addField(STR.fieldYear, 'published_date', prefill.year);
      addField(STR.fieldPublisher, 'publisher', prefill.publisher);
      addField(STR.fieldIsbn, 'isbn', prefill.isbn);
    } else {
      addField(STR.fieldName, 'title', prefill.name);
    }
    addField(STR.fieldDescription, 'description', prefill.description);
    addField(STR.fieldTags, 'tags', Array.isArray(prefill.tags) ? prefill.tags.join(', ') : '');
    addField(STR.fieldPrice, 'market_price', '');
    addField(STR.fieldCurrency, 'currency', 'EUR');
    addField(STR.fieldPriceSource, 'price_source', '');
    if (inputs.currency) inputs.currency.maxLength = 3;
    if (inputs.market_price) inputs.market_price.setAttribute('inputmode', 'decimal');
    if (window.SimpleOfficeCaptureMarketplace) {
      window.SimpleOfficeCaptureMarketplace.renderMarketplaceSection({
        inputs, combined, form, dataset, cleanText, safeJson,
      });
    }
    if (window.SimpleOfficeCapturePeerStock) {
      window.SimpleOfficeCapturePeerStock.renderPeerStockSection({
        inputs, combined, form, dataset, csrf, cleanText, safeJson,
      });
      window.SimpleOfficeCapturePeerStock.renderPeerInventorySection({
        inputs, combined, form, dataset, csrf, cleanText, safeJson,
      });
    }
    const missing = (combined.missing_fields || []).join(', ');
    if (missing) {
      const hint = document.createElement('p');
      hint.className = 'small text-warning mb-0';
      hint.textContent = STR.completeHint(missing);
      form.appendChild(hint);
    }
    let shelfSelect = null;
    let assignIdInput = null;
    let assignExistingBtn = null;
    if (combined.kind === 'book') {
      const shelfWrap = document.createElement('div');
      shelfWrap.className = 'col-12';
      const shelfLabel = document.createElement('label');
      shelfLabel.className = 'form-label small mb-1';
      shelfLabel.textContent = STR.shelfLabel;
      shelfSelect = document.createElement('select');
      shelfSelect.className = 'form-select';
      shelfSelect.setAttribute('aria-label', STR.shelfAria);
      const none = document.createElement('option');
      none.value = '';
      none.textContent = STR.shelfNone;
      shelfSelect.appendChild(none);
      (payload.shelves || []).forEach((row) => {
        if (!row || !row.location_id) return;
        const option = document.createElement('option');
        option.value = row.location_id;
        const code = cleanText(row.code);
        const path = cleanText(row.path) || cleanText(row.name);
        option.textContent = code ? `${code} — ${path || code}` : (path || row.location_id);
        shelfSelect.appendChild(option);
      });
      const preselect = cleanText(payload.default_location_id);
      if (preselect) {
        const known = Array.from(shelfSelect.options).some((option) => option.value === preselect);
        if (known) shelfSelect.value = preselect;
      }
      shelfLabel.appendChild(shelfSelect);
      shelfWrap.appendChild(shelfLabel);
      if ((payload.shelves || []).length === 0) {
        const hint = document.createElement('p');
        hint.className = 'small text-secondary mb-0';
        hint.textContent = STR.shelfHint;
        shelfWrap.appendChild(hint);
      }
      form.appendChild(shelfWrap);
    }
    const renderShelfState = (result) => {
      if (!result || combined.kind !== 'book') return;
      if (result.shelf_error) {
        const warn = document.createElement('div');
        warn.className = 'alert alert-warning py-1 small mb-1 mt-1';
        warn.textContent = `${cleanText(result.shelf_error)}${STR.shelfRetry}`;
        state.appendChild(warn);
        return;
      }
      const shelf = result.shelf || null;
      const line = document.createElement('div');
      line.className = `alert py-1 small mb-1 mt-1 ${shelf ? 'alert-success' : 'alert-info'}`;
      if (shelf) {
        const code = cleanText(shelf.code);
        const path = cleanText(shelf.path);
        line.textContent = STR.shelfSet(code, path);
      } else {
        line.textContent = result.duplicate ? STR.shelfKept : STR.shelfSkipped;
      }
      state.appendChild(line);
    };
    const state = document.createElement('p');
    state.className = 'small mt-2 mb-0';
    state.setAttribute('role', 'status');
    const actions = document.createElement('div');
    actions.className = 'col-12 d-grid gap-2';
    const analyze = document.createElement('button');
    analyze.type = 'button';
    analyze.className = 'btn btn-outline-primary';
    analyze.textContent = STR.groupAnalyze;
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
        wrap.replaceWith(InboxBatch.renderBatchReview(ctx, result));
      } else {
        state.textContent = cleanText(result && result.error) || STR.analysisFailed;
        analyze.disabled = false;
      }
    });
    actions.appendChild(analyze);
    const adopt = document.createElement('button');
    adopt.type = 'button';
    adopt.className = 'btn btn-success';
    adopt.textContent = combined.kind === 'book' ? STR.adoptOneBook : STR.adoptObject;
    const adoptToken = nextToken();
    adopt.addEventListener('click', async () => {
      adopt.disabled = true;
      state.textContent = '';
      const data = new FormData();
      const get = (name) => (inputs[name] ? cleanText(inputs[name].value) : '');
      data.append('title', get('title'));
      if (combined.kind === 'book') {
        data.append('isbn', get('isbn') || combined.isbn || '');
        data.append('authors', get('authors'));
        data.append('published_date', get('published_date'));
        data.append('publisher', get('publisher'));
        data.append('item_type', 'book');
        data.append('location_id', shelfSelect ? cleanText(shelfSelect.value) : '');
      } else {
        data.append('item_type', 'object');
      }
      data.append('description', get('description'));
      data.append('tags', get('tags'));
      data.append('market_price', get('market_price'));
      data.append('currency', get('currency'));
      data.append('price_source', get('price_source'));
      data.append('capture_request_id', adoptToken);
      data.append('_csrf_token', csrf);
      const target = `${batchUrlFor(payload.batch_id)}/adopt`;
      let result = null;
      try {
        const response = await fetch(target, {method: 'POST', body: data, cache: 'no-store'});
        result = await safeJson(response);
        if (!response.ok || !result || result.ok !== true) {
          state.textContent = cleanText(result && result.error) || STR.saveFailed;
          adopt.disabled = false;
          return;
        }
      } catch (_error) {
        state.textContent = STR.networkRetry;
        adopt.disabled = false;
        return;
      }
      state.textContent = '';
      const done = document.createElement('span');
      done.textContent = `${cleanText(result.message) || STR.savedFallback} `;
      if (result.detail_url) {
        const link = document.createElement('a');
        link.href = result.detail_url;
        link.textContent = STR.open;
        done.appendChild(link);
      }
      if (result.duplicate && result.object_id && assignIdInput && assignExistingBtn) {
        const linkExisting = document.createElement('button');
        linkExisting.type = 'button';
        linkExisting.className = 'btn btn-sm btn-outline-success ms-1';
        linkExisting.textContent = STR.groupAssign;
        linkExisting.addEventListener('click', () => {
          assignIdInput.value = result.object_id || '';
          assignExistingBtn.click();
        });
        done.appendChild(linkExisting);
      }
      state.appendChild(done);
      renderShelfState(result);
      if (result.shelf_error) {
        adopt.disabled = false;
        return;
      }
      if (!result.duplicate) {
        try {
          await InboxBatch.refreshAssignedCards(ctx, payload.batch_id);
        } catch (_error) {
          const warn = document.createElement('div');
          warn.className = 'alert alert-warning py-1 small mb-1 mt-1';
          warn.textContent = STR.displayNotUpdated;
          state.appendChild(warn);
          adopt.disabled = false;
          return;
        }
        renderBatches(ctx);
      }
    });
    actions.appendChild(adopt);
    if (combined.kind === 'book' && payload.assign_url) {
      const assignRow = document.createElement('div');
      assignRow.className = 'col-12 d-flex flex-wrap gap-2 align-items-end mt-2';
      const idWrap = document.createElement('div');
      const idLabel = document.createElement('label');
      idLabel.className = 'form-label small mb-1';
      idLabel.textContent = STR.existingBookId;
      const idInput = document.createElement('input');
      idInput.className = 'form-control form-control-sm';
      idInput.placeholder = STR.idPlaceholder;
      idInput.setAttribute('aria-label', STR.existingBookId);
      idWrap.appendChild(idLabel);
      idWrap.appendChild(idInput);
      assignRow.appendChild(idWrap);
      const assignExisting = document.createElement('button');
      assignExisting.type = 'button';
      assignExisting.className = 'btn btn-sm btn-outline-success';
      assignExisting.textContent = STR.assignWhole;
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
            state.textContent = cleanText(result && result.error) || STR.assignFailed;
            assignExisting.disabled = false;
            return;
          }
        } catch (_error) {
          state.textContent = STR.assignNetwork;
          assignExisting.disabled = false;
          return;
        }
        const done = document.createElement('span');
        done.textContent = STR.assignedStats(cleanText(result.message) || STR.assignedFallback, result.assigned || 0, result.photo_count || 0, result.attached || 0);
        if (result.detail_url) {
          const link = document.createElement('a');
          link.href = result.detail_url;
          link.textContent = STR.open;
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
          retryHint.textContent = STR.notAllAdopted;
          state.appendChild(retryHint);
          try {
            await InboxBatch.refreshAssignedCards(ctx, payload.batch_id);
          } catch (_error) {
            const warn = document.createElement('div');
            warn.className = 'alert alert-warning py-1 small mb-1 mt-1';
            warn.textContent = STR.displayKeptAssign;
            state.appendChild(warn);
          }
          assignExisting.disabled = false;
          return;
        }
        try {
          await InboxBatch.refreshAssignedCards(ctx, payload.batch_id);
        } catch (_error) {
          const warn = document.createElement('div');
          warn.className = 'alert alert-warning py-1 small mb-1 mt-1';
          warn.textContent = STR.displayKeptAssignReload;
          state.appendChild(warn);
          assignExisting.disabled = false;
          return;
        }
        renderBatches(ctx);
      });
      assignIdInput = idInput;
      assignExistingBtn = assignExisting;
      assignRow.appendChild(assignExisting);
      form.appendChild(assignRow);
    }
    form.appendChild(actions);
    wrap.appendChild(form);
    wrap.appendChild(state);
    return wrap;
  };
  window.SimpleOfficeCaptureInboxBatch = {renderBatches, refreshAssignedCards, openBatchReview, renderBatchReview};
})();
