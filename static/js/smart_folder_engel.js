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
    tagsPara.textContent = 'Aktuelle Tags: ';
    const list = Array.isArray(tags) ? tags : [];
    if (!list.length) { const none = document.createElement('span'); none.className = 'text-secondary'; none.textContent = 'keine'; tagsPara.appendChild(none); return; }
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
    if (already) { const note = document.createElement('span'); note.className = 'badge text-bg-secondary ms-2'; note.textContent = 'schon vorhanden'; wrap.appendChild(note); }
    if (reason) { const why = document.createElement('div'); why.className = 'small text-secondary'; why.textContent = reason; wrap.appendChild(why); }
    return wrap;
  };
  const confirmSelection = async () => {
    const picked = [...result.querySelectorAll('input[data-engel-pick]:checked:not(:disabled)')]
      .map((box) => cleanText(box.value)).filter(Boolean);
    if (!picked.length) { setStatus('Bitte mindestens einen Vorschlag auswählen.', 'warning'); return; }
    setStatus('Tags werden übernommen …', 'info');
    try {
      const data = new FormData();
      data.append('document_id', documentId);
      data.append('_csrf_token', csrf);
      picked.forEach((tag) => data.append('tags', tag));
      const response = await fetch(confirmUrl, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok || !payload || payload.ok !== true) {
        setStatus(cleanText(payload && payload.error) || `Übernahme fehlgeschlagen (${response.status}).`, 'danger');
        return;
      }
      const added = Array.isArray(payload.added) ? payload.added.length : 0;
      const skipped = Array.isArray(payload.skipped) ? payload.skipped.length : 0;
      renderBadges(payload.tags);
      setStatus(`${added} Tag(s) übernommen${skipped ? `, ${skipped} schon vorhanden` : ''}.`, 'success');
      result.hidden = true; result.textContent = '';
    } catch (_error) { setStatus('Übernahme fehlgeschlagen (Netzwerk).', 'danger'); }
  };
  const renderImageInto = (slot, image) => {
    const seen = document.createElement('div');
    seen.className = 'fw-semibold mt-3 mb-1'; seen.textContent = 'Der Engel sieht:';
    slot.appendChild(seen);
    const desc = document.createElement('p'); desc.className = 'small mb-2'; desc.textContent = cleanText(image.description);
    slot.appendChild(desc);
    (Array.isArray(image.objects) ? image.objects : []).forEach((name) => {
      slot.appendChild(pickRow(cleanText(name), 'Objekthinweis aus dem Bild', false));
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
    pending.textContent = 'Der Engel schaut sich noch das Bild an …';
    slot.appendChild(pending);
    try {
      const data = new FormData();
      data.append('document_id', documentId);
      data.append('_csrf_token', csrf);
      const response = await fetch(imageUrl, { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      pending.remove();
      if (!response.ok || !payload || payload.ok !== true) {
        renderImageWarning(slot, cleanText(payload && payload.error) || `Bildanalyse fehlgeschlagen (${response.status}).`);
        return;
      }
      if (!payload.image) return;
      renderImageInto(slot, payload.image);
    } catch (_error) {
      pending.remove();
      renderImageWarning(slot, 'Bildanalyse fehlgeschlagen (Netzwerk).');
    }
  };
  const renderTagRows = (slot, payload) => {
    slot.textContent = '';
    const rows = Array.isArray(payload.suggestions) ? payload.suggestions : [];
    if (!rows.length) { const none = document.createElement('p'); none.className = 'small text-secondary mb-0'; none.textContent = 'Keine Vorschläge.'; slot.appendChild(none); return; }
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
        const message = cleanText(payload && payload.error) || `Vorschlag fehlgeschlagen (${response.status}).`;
        let hinted = message;
        if (response.status === 503) {
          hinted = payload && payload.timeout === true
            ? `${message} Das Modell lädt möglicherweise noch — bitte später erneut versuchen.`
            : `${message} Bitte Ollama mit Textmodell auf diesem Rechner einrichten.`;
        }
        setStatus(hinted, response.status === 503 ? 'warning' : 'danger');
        renderTagsWarning(slot, hinted);
        return;
      }
      const rows = Array.isArray(payload.suggestions) ? payload.suggestions : [];
      if (!rows.length) {
        setStatus('Keine belegbaren Tag-Vorschläge gefunden.', 'warning');
      } else {
        const who = [cleanText(payload.provider_label), cleanText(payload.model)].filter(Boolean).join(' · ');
        setStatus(who ? `Vorschläge bereit (${who}). Bitte prüfen und bestätigen.` : 'Vorschläge bereit. Bitte prüfen und bestätigen.', 'success');
      }
      renderTagRows(slot, payload);
    } catch (_error) {
      setStatus('Vorschlag fehlgeschlagen (Netzwerk).', 'danger');
      renderTagsWarning(slot, 'Vorschlag fehlgeschlagen (Netzwerk).');
    }
  };
  const suggest = async () => {
    if (!suggestUrl || !suggestBtn) return;
    suggestBtn.disabled = true;
    setStatus('Der Engel grübelt über Metadaten und Text …', 'info');
    result.hidden = false; result.textContent = '';
    const box = document.createElement('div');
    box.className = 'border rounded p-2';
    const head = document.createElement('div');
    head.className = 'fw-semibold mb-2'; head.textContent = 'Der Engel schlägt vor:';
    box.appendChild(head);
    const tagsSlot = document.createElement('div');
    const tagsPending = document.createElement('p');
    tagsPending.className = 'small text-secondary mb-0'; tagsPending.textContent = 'Vorschläge werden erstellt …';
    tagsSlot.appendChild(tagsPending);
    box.appendChild(tagsSlot);
    const imageSlot = document.createElement('div');
    box.appendChild(imageSlot);
    const confirmBtn = document.createElement('button');
    confirmBtn.type = 'button'; confirmBtn.className = 'btn btn-success mt-2'; confirmBtn.textContent = 'Ausgewählte übernehmen';
    confirmBtn.addEventListener('click', confirmSelection);
    box.appendChild(confirmBtn);
    result.appendChild(box);
    await Promise.allSettled([fetchTags(tagsSlot), fetchImage(imageSlot)]);
    suggestBtn.disabled = false;
  };
  suggestBtn?.addEventListener('click', suggest);
})();
