(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    analyzing: EN ? 'The angel is looking at the released attachment …' : 'Der Engel schaut sich den freigegebenen Anhang an …',
    failed: (code) => EN ? `Image analysis failed (${code}).` : `Bildanalyse fehlgeschlagen (${code}).`,
    timeoutHint: EN ? 'The model may still be loading — please try again later.' : 'Das Modell lädt möglicherweise noch — bitte später erneut versuchen.',
    ollamaHint: EN ? 'Please set up Ollama with a vision model on this machine.' : 'Bitte Ollama mit Bildmodell auf diesem Rechner einrichten.',
    noImage: EN ? 'Not an image document — nothing to analyze.' : 'Kein Bilddokument — nichts zu analysieren.',
    localModel: EN ? 'local model' : 'lokales Modell',
    unknownModel: EN ? 'unknown' : 'unbekannt',
    objectsFound: (names) => EN ? ` · Object(s): ${names}` : ` · Objekt: ${names}`,
    network: EN ? 'Image analysis failed (network).' : 'Bildanalyse fehlgeschlagen (Netzwerk).',
  };
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
      renderResult(slot, STR.analyzing, 'info');
      try {
        const data = new FormData();
        data.append('document_id', documentId);
        data.append('_csrf_token', csrf);
        const response = await fetch(suggestUrl, { method: 'POST', body: data, cache: 'no-store' });
        const payload = await safeJson(response);
        if (!response.ok || !payload || payload.ok !== true) {
          const message = cleanText(payload && payload.error) || STR.failed(response.status);
          const hinted = response.status === 503
            ? (payload && payload.timeout === true
              ? `${message} ${STR.timeoutHint}`
              : `${message} ${STR.ollamaHint}`)
            : message;
          renderResult(slot, hinted, response.status === 503 ? 'warning' : 'danger');
          return;
        }
        if (!payload.image) {
          renderResult(slot, STR.noImage, 'warning');
          return;
        }
        const objects = Array.isArray(payload.image.objects) ? payload.image.objects.map(cleanText).filter(Boolean) : [];
        renderResult(
          slot,
          `${cleanText(payload.image.description)} — ${cleanText(payload.provider_label) || STR.localModel} · ${cleanText(payload.model) || STR.unknownModel}${objects.length ? STR.objectsFound(objects.join(', ')) : ''}`,
          'success',
        );
      } catch (_error) {
        renderResult(slot, STR.network, 'danger');
      } finally {
        button.disabled = false;
      }
    });
  });
})();
