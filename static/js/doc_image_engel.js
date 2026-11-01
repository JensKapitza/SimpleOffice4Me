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
    setStatus('Der Engel schaut sich das gespeicherte Bild an …', 'info');
    if (result) { result.hidden = true; result.textContent = ''; }
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
        setStatus(hinted, response.status === 503 ? 'warning' : 'danger');
        return;
      }
      if (!payload.image) {
        setStatus('Kein Bilddokument — nichts zu analysieren.', 'warning');
        return;
      }
      const image = payload.image;
      const head = document.createElement('div');
      head.className = 'small text-secondary mb-1';
      head.textContent = `Analyse durch ${cleanText(payload.provider_label) || 'lokales Modell'} · Modell ${cleanText(payload.model) || 'unbekannt'} · nichts gespeichert`;
      result.appendChild(head);
      const desc = document.createElement('p');
      desc.className = 'mb-2';
      desc.textContent = cleanText(image.description);
      result.appendChild(desc);
      const objects = Array.isArray(image.objects) ? image.objects.map(cleanText).filter(Boolean) : [];
      const objs = document.createElement('p');
      objs.className = 'small mb-0';
      objs.textContent = objects.length ? `Erkanntes Objekt: ${objects.join(', ')}` : 'Kein eindeutiges Objekt erkannt.';
      result.appendChild(objs);
      result.hidden = false;
      setStatus('Bildanalyse bereit.', 'success');
    } catch (_error) {
      setStatus('Bildanalyse fehlgeschlagen (Netzwerk).', 'danger');
    } finally {
      button.disabled = false;
    }
  };
  button?.addEventListener('click', analyze);
})();
