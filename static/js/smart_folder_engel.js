(() => {
  'use strict';
  const root = document.getElementById('doc-engel');
  if (!root) return;
  const $ = (id) => document.getElementById(id);
  const suggestBtn = $('doc-engel-suggest');
  const status = $('doc-engel-status');
  const result = $('doc-engel-result');
  const tagsPara = $('doc-engel-tags');
  const suggestUrl = root.dataset.suggestUrl || '';
  const confirmUrl = root.dataset.confirmUrl || '';
  const imageUrl = root.dataset.imageUrl || '';
  const documentId = root.dataset.documentId || '';
  const folder = root.dataset.folder || '';
  const csrf = root.dataset.csrf || '';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    currentTags: EN ? 'Current tags: ' : 'Aktuelle Tags: ',
    none: EN ? 'none' : 'keine',
    alreadyPresent: EN ? 'already present' : 'schon vorhanden',
    pickAtLeastOne: EN ? 'Please select at least one suggestion.' : 'Bitte mindestens einen Vorschlag auswählen.',
    applying: EN ? 'Applying tags …' : 'Tags werden übernommen …',
    applyFailed: (code) => EN ? `Apply failed (${code}).` : `Übernahme fehlgeschlagen (${code}).`,
    applied: (added, skipped) => EN
      ? `${added} tag(s) applied${skipped ? `, ${skipped} already present` : ''}.`
      : `${added} Tag(s) übernommen${skipped ? `, ${skipped} schon vorhanden` : ''}.`,
    applyNetwork: EN ? 'Apply failed (network).' : 'Übernahme fehlgeschlagen (Netzwerk).',
    angelSees: EN ? 'The angel sees:' : 'Der Engel sieht:',
    objectHint: EN ? 'Object hint from the image' : 'Objekthinweis aus dem Bild',
    stillLooking: EN ? 'The angel is still looking at the image …' : 'Der Engel schaut sich noch das Bild an …',
    imageFailed: (code) => EN ? `Image analysis failed (${code}).` : `Bildanalyse fehlgeschlagen (${code}).`,
    imageNetwork: EN ? 'Image analysis failed (network).' : 'Bildanalyse fehlgeschlagen (Netzwerk).',
    noSuggestions: EN ? 'No suggestions.' : 'Keine Vorschläge.',
    suggestFailed: (code) => EN ? `Suggestion failed (${code}).` : `Vorschlag fehlgeschlagen (${code}).`,
    timeoutHint: EN ? 'The model may still be loading — please try again later.' : 'Das Modell lädt möglicherweise noch — bitte später erneut versuchen.',
    ollamaHint: EN ? 'Please set up Ollama with a text model on this machine.' : 'Bitte Ollama mit Textmodell auf diesem Rechner einrichten.',
    noVerifiable: EN ? 'No verifiable tag suggestions found.' : 'Keine belegbaren Tag-Vorschläge gefunden.',
    readyWho: (who) => EN ? `Suggestions ready (${who}). Please review and confirm.` : `Vorschläge bereit (${who}). Bitte prüfen und bestätigen.`,
    ready: EN ? 'Suggestions ready. Please review and confirm.' : 'Vorschläge bereit. Bitte prüfen und bestätigen.',
    suggestNetwork: EN ? 'Suggestion failed (network).' : 'Vorschlag fehlgeschlagen (Netzwerk).',
    pondering: EN ? 'The angel is pondering metadata and text …' : 'Der Engel grübelt über Metadaten und Text …',
    suggests: EN ? 'The angel suggests:' : 'Der Engel schlägt vor:',
    creating: EN ? 'Creating suggestions …' : 'Vorschläge werden erstellt …',
    applySelected: EN ? 'Apply selected' : 'Ausgewählte übernehmen',
  };
  const cleanText = (value) => String(value || '').trim();
  const setStatus = (text, kind) => {
    if (!status) return;
    status.textContent = '';
    if (!text) return;
    const note = document.createElement('div');
    note.className = `alert alert-${kind || 'secondary'} py-2 small mb-0`;
    note.textContent = text;
    status.appendChild(note);
  };
  const safeJson = async (response) => { try { return await response.json(); } catch (_error) { return {}; } };
  const renderBadges = (tags) => {
    if (!tagsPara) return;
    tagsPara.textContent = STR.currentTags;
    const list = Array.isArray(tags) ? tags : [];
    if (!list.length) { const none = document.createElement('span'); none.className = 'text-secondary'; none.textContent = STR.none; tagsPara.appendChild(none); return; }
    list.forEach((tag) => { const badge = document.createElement('span'); badge.className = 'badge text-bg-secondary me-1'; badge.textContent = cleanText(tag); tagsPara.appendChild(badge); });
  };
  const pickRow = (tag, reason, already) => {
    const wrap = document.createElement('label');
    wrap.className = 'form-check border rounded px-2 py-1';
    const box = document.createElement('input');
    box.type = 'checkbox'; box.className = 'form-check-input me-2'; box.dataset.engelPick = '1'; box.value = tag;
    box.checked = true; box.disabled = Boolean(already);
    wrap.appendChild(box);
    const name = document.createElement('strong'); name.textContent = tag; wrap.appendChild(name);
    if (already) { const note = document.createElement('span'); note.className = 'badge text-bg-secondary ms-2'; note.textContent = STR.alreadyPresent; wrap.appendChild(note); }
    if (reason) { const why = document.createElement('div'); why.className = 'small text-secondary'; why.textContent = reason; wrap.appendChild(why); }
    return wrap;
  };
  const confirmSelection = async () => {
    const picked = [...result.querySelectorAll('input[data-engel-pick]:checked:not(:disabled)')]
      .map((box) => cleanText(box.value)).filter(Boolean);
    if (!picked.length) { setStatus(STR.pickAtLeastOne, 'warning'); return; }
    setStatus(STR.applying, 'info');
    try {
      const data = new FormData();
      data.append('document_id', documentId);
      data.append('_csrf_token', csrf);
      picked.forEach((tag) => data.append('tags', tag));
      const response = await fetch(confirmUrl, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok || !payload || payload.ok !== true) {
        setStatus(cleanText(payload && payload.error) || STR.applyFailed(response.status), 'danger');
        return;
      }
      const added = Array.isArray(payload.added) ? payload.added.length : 0;
      const skipped = Array.isArray(payload.skipped) ? payload.skipped.length : 0;
      renderBadges(payload.tags);
      setStatus(STR.applied(added, skipped), 'success');
      result.hidden = true; result.textContent = '';
    } catch (_error) { setStatus(STR.applyNetwork, 'danger'); }
  };
  const renderImageInto = (slot, image) => {
    const seen = document.createElement('div');
    seen.className = 'fw-semibold mt-3 mb-1'; seen.textContent = STR.angelSees;
    slot.appendChild(seen);
    const desc = document.createElement('p'); desc.className = 'small mb-2'; desc.textContent = cleanText(image.description);
    slot.appendChild(desc);
    (Array.isArray(image.objects) ? image.objects : []).forEach((name) => {
      slot.appendChild(pickRow(cleanText(name), STR.objectHint, false));
    });
  };
  const renderImageWarning = (slot, message) => {
    const warn = document.createElement('div'); warn.className = 'alert alert-warning py-2 small mt-2 mb-0'; warn.textContent = message;
    slot.appendChild(warn);
  };
  const fetchImage = async (slot) => {
    if (!imageUrl || !slot) return;
    const pending = document.createElement('p');
    pending.className = 'small text-secondary mt-3 mb-0';
    pending.textContent = STR.stillLooking;
    slot.appendChild(pending);
    try {
      const data = new FormData();
      data.append('document_id', documentId);
      data.append('_csrf_token', csrf);
      const response = await fetch(imageUrl, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      pending.remove();
      if (!response.ok || !payload || payload.ok !== true) {
        renderImageWarning(slot, cleanText(payload && payload.error) || STR.imageFailed(response.status));
        return;
      }
      if (!payload.image) return;
      renderImageInto(slot, payload.image);
    } catch (_error) {
      pending.remove();
      renderImageWarning(slot, STR.imageNetwork);
    }
  };
  const renderTagRows = (slot, payload) => {
    slot.textContent = '';
    const rows = Array.isArray(payload.suggestions) ? payload.suggestions : [];
    if (!rows.length) { const none = document.createElement('p'); none.className = 'small text-secondary mb-0'; none.textContent = STR.noSuggestions; slot.appendChild(none); return; }
    rows.forEach((row) => slot.appendChild(pickRow(cleanText(row.tag), cleanText(row.reason), row.already)));
  };
  const renderTagsWarning = (slot, message) => {
    slot.textContent = '';
    const warn = document.createElement('div'); warn.className = 'alert alert-danger py-2 small mb-0'; warn.textContent = message;
    slot.appendChild(warn);
  };
  const fetchTags = async (slot) => {
    try {
      const data = new FormData();
      data.append('document_id', documentId);
      data.append('folder', folder);
      data.append('_csrf_token', csrf);
      const response = await fetch(suggestUrl, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok || !payload || payload.ok !== true) {
        const message = cleanText(payload && payload.error) || STR.suggestFailed(response.status);
        let hinted = message;
        if (response.status === 503) {
          hinted = payload && payload.timeout === true
            ? `${message} ${STR.timeoutHint}`
            : `${message} ${STR.ollamaHint}`;
        }
        setStatus(hinted, response.status === 503 ? 'warning' : 'danger');
        renderTagsWarning(slot, hinted);
        return;
      }
      const rows = Array.isArray(payload.suggestions) ? payload.suggestions : [];
      if (!rows.length) {
        setStatus(STR.noVerifiable, 'warning');
      } else {
        const who = [cleanText(payload.provider_label), cleanText(payload.model)].filter(Boolean).join(' · ');
        setStatus(who ? STR.readyWho(who) : STR.ready, 'success');
      }
      renderTagRows(slot, payload);
    } catch (_error) {
      setStatus(STR.suggestNetwork, 'danger');
      renderTagsWarning(slot, STR.suggestNetwork);
    }
  };
  const suggest = async () => {
    if (!suggestUrl || !suggestBtn) return;
    suggestBtn.disabled = true;
    setStatus(STR.pondering, 'info');
    result.hidden = false; result.textContent = '';
    const box = document.createElement('div');
    box.className = 'border rounded p-2';
    const head = document.createElement('div');
    head.className = 'fw-semibold mb-2'; head.textContent = STR.suggests;
    box.appendChild(head);
    const tagsSlot = document.createElement('div');
    const tagsPending = document.createElement('p');
    tagsPending.className = 'small text-secondary mb-0'; tagsPending.textContent = STR.creating;
    tagsSlot.appendChild(tagsPending);
    box.appendChild(tagsSlot);
    const imageSlot = document.createElement('div');
    box.appendChild(imageSlot);
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button'; confirmBtn.className = 'btn btn-success mt-2'; confirmBtn.textContent = STR.applySelected;
    confirmBtn.addEventListener('click', confirmSelection);
    box.appendChild(confirmBtn);
    result.appendChild(box);
    await Promise.allSettled([fetchTags(tagsSlot), fetchImage(imageSlot)]);
    suggestBtn.disabled = false;
  };
  suggestBtn?.addEventListener('click', suggest);
})();
