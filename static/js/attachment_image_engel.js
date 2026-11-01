(() => {
  'use strict';
  const cleanText = (value) => String(value || '').trim();
  const safeJson = async (response) => { try { return await response.json(); } catch (_error) { return {}; } };
  const renderResult = (slot, text, kind) => {
    if (!slot) return;
    slot.textContent = '';
    slot.hidden = false;
    const note = document.createElement('div');
    note.className = `alert alert-${kind || 'secondary'} py-1 small mb-0`;
    note.textContent = text;
    slot.appendChild(note);
  };
  document.querySelectorAll('.attachment-image-analyze').forEach((button) => {
    button.addEventListener('click', async () => {
      const suggestUrl = button.dataset.suggestUrl || '';
      const documentId = button.dataset.documentId || '';
      const csrf = button.dataset.csrf || '';
      const slot = button.parentElement?.querySelector('.attachment-image-result');
      if (!suggestUrl || !documentId || !button) return;
      button.disabled = true;
      renderResult(slot, 'Der Engel schaut sich den freigegebenen Anhang an …', 'info');
      try {
        const data = new FormData();
        data.append('document_id', documentId);
        data.append('_csrf_token', csrf);
        const response = await fetch(suggestUrl, { method: 'POST', body: data, cache: 'no-store' });
        const payload = await safeJson(response);
        if (!response.ok || !payload || payload.ok !== true) {
          const message = cleanText(payload && payload.error) || `Bildanalyse fehlgeschlagen (${response.status}).`;
          const hinted = response.status === 503
            ? (payload && payload.timeout === true
              ? `${message} Das Modell lädt möglicherweise noch — bitte später erneut versuchen.`
              : `${message} Bitte Ollama mit Bildmodell auf diesem Rechner einrichten.`)
            : message;
          renderResult(slot, hinted, response.status === 503 ? 'warning' : 'danger');
          return;
        }
        if (!payload.image) {
          renderResult(slot, 'Kein Bilddokument — nichts zu analysieren.', 'warning');
          return;
        }
        const objects = Array.isArray(payload.image.objects) ? payload.image.objects.map(cleanText).filter(Boolean) : [];
        renderResult(
          slot,
          `${cleanText(payload.image.description)} — ${cleanText(payload.provider_label) || 'lokales Modell'} · ${cleanText(payload.model) || 'unbekannt'}${objects.length ? ` · Objekt: ${objects.join(', ')}` : ''}`,
          'success',
        );
      } catch (_error) {
        renderResult(slot, 'Bildanalyse fehlgeschlagen (Netzwerk).', 'danger');
      } finally {
        button.disabled = false;
      }
    });
  });
})();
