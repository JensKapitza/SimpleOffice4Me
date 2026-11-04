(() => {
  'use strict';
  const root = document.getElementById('doc-image-engel');
  if (!root) return;
  const button = document.getElementById('doc-image-analyze');
  const status = document.getElementById('doc-image-status');
  const result = document.getElementById('doc-image-result');
  const suggestUrl = root.dataset.suggestUrl || '';
  const documentId = root.dataset.documentId || '';
  const csrf = root.dataset.csrf || '';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    analyzing: EN ? 'The angel is looking at the stored image …' : 'Der Engel schaut sich das gespeicherte Bild an …',
    failed: (code) => EN ? `Image analysis failed (${code}).` : `Bildanalyse fehlgeschlagen (${code}).`,
    timeoutHint: EN ? 'The model may still be loading — please try again later.' : 'Das Modell lädt möglicherweise noch — bitte später erneut versuchen.',
    ollamaHint: EN ? 'Please set up Ollama with a vision model on this machine.' : 'Bitte Ollama mit Bildmodell auf diesem Rechner einrichten.',
    noImage: EN ? 'Not an image document — nothing to analyze.' : 'Kein Bilddokument — nichts zu analysieren.',
    localModel: EN ? 'local model' : 'lokales Modell',
    unknownModel: EN ? 'unknown' : 'unbekannt',
    head: (provider, model) => EN
      ? `Analysis by ${provider} · Model ${model} · nothing stored`
      : `Analyse durch ${provider} · Modell ${model} · nichts gespeichert`,
    objectsFound: (names) => EN ? `Recognized object(s): ${names}` : `Erkanntes Objekt: ${names}`,
    objectsNone: EN ? 'No distinct object recognized.' : 'Kein eindeutiges Objekt erkannt.',
    ready: EN ? 'Image analysis ready.' : 'Bildanalyse bereit.',
    network: EN ? 'Image analysis failed (network).' : 'Bildanalyse fehlgeschlagen (Netzwerk).',
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
  const analyze = async () => {
    if (!suggestUrl || !documentId || !button) return;
    button.disabled = true;
    setStatus(STR.analyzing, 'info');
    if (result) { result.hidden = true; result.textContent = ''; }
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
        setStatus(hinted, response.status === 503 ? 'warning' : 'danger');
        return;
      }
      if (!payload.image) {
        setStatus(STR.noImage, 'warning');
        return;
      }
      const image = payload.image;
      const head = document.createElement('div');
      head.className = 'small text-secondary mb-1';
      head.textContent = STR.head(cleanText(payload.provider_label) || STR.localModel, cleanText(payload.model) || STR.unknownModel);
      result.appendChild(head);
      const desc = document.createElement('p');
      desc.className = 'mb-2';
      desc.textContent = cleanText(image.description);
      result.appendChild(desc);
      const objects = Array.isArray(image.objects) ? image.objects.map(cleanText).filter(Boolean) : [];
      const objs = document.createElement('p');
      objs.className = 'small mb-0';
      objs.textContent = objects.length ? STR.objectsFound(objects.join(', ')) : STR.objectsNone;
      result.appendChild(objs);
      result.hidden = false;
      setStatus(STR.ready, 'success');
    } catch (_error) {
      setStatus(STR.network, 'danger');
    } finally {
      button.disabled = false;
    }
  };
  button?.addEventListener('click', analyze);
})();
