/* Preview the selected photo before the server acknowledgement; no analysis. */
(() => {
  const form = document.querySelector('#intake-capture');
  const input = document.querySelector('#intake-photo');
  const pending = document.querySelector('#intake-pending');
  if (!form || !input || !pending || !window.URL?.createObjectURL) return;
  const picture = pending.querySelector('[data-intake-pending-photo]');
  const status = pending.querySelector('[role="status"]');
  let preview = '';
  let uploading = false;
  input.addEventListener('change', () => {
    if (preview) URL.revokeObjectURL(preview);
    const photo = input.files[0];
    pending.hidden = !photo;
    picture.hidden = !photo;
    if (!photo) {
      picture.replaceChildren();
      preview = '';
      return;
    }
    preview = URL.createObjectURL(photo);
    const image = document.createElement('img');
    image.src = preview;
    image.alt = 'Vorschau des neuen Fotos';
    image.className = 'object-fit-cover rounded';
    image.loading = 'eager';
    image.decoding = 'async';
    image.fetchPriority = 'high';
    picture.replaceChildren(image);
    status.textContent = 'Neues Foto · Noch nicht gespeichert';
    if (photo.size <= 12 * 1024 * 1024 && form.requestSubmit) form.requestSubmit();
  });
  form.addEventListener('submit', async event => {
    pending.hidden = false;
    picture.hidden = !input.files[0];
    status.textContent = 'Privater Entwurf wird gespeichert …';
    if (!window.fetch) return;
    event.preventDefault();
    if (uploading) return;
    uploading = true;
    const data = new FormData(form);
    const submit = form.querySelector('[type="submit"]');
    input.disabled = submit.disabled = true;
    try {
      const response = await window.fetch(form.action, {
        method: 'POST', credentials: 'same-origin', headers: {Accept: 'application/json'}, body: data,
      });
      const parsed = await response.json();
      if (!response.ok || !parsed.ok || !/^[0-9a-f]{32}$/.test(parsed.draft?.id)) {
        status.textContent = typeof parsed.error === 'string'
          ? parsed.error : 'Speicherung nicht bestätigt. Bitte erneut speichern.';
        return;
      }
      status.textContent = 'Privater Entwurf gespeichert';
      window.location.hash = 'draft-' + parsed.draft.id;
      window.location.reload();
    } catch (_) {
      status.textContent = 'Speicherung nicht bestätigt. Foto bleibt ausgewählt; bitte erneut speichern.';
    } finally {
      input.disabled = submit.disabled = false;
      uploading = false;
    }
  });
  window.addEventListener('pagehide', () => {
    if (preview) URL.revokeObjectURL(preview);
  });
})();

/* Other actions must not silently discard unsaved edits on a card. */
document.querySelectorAll('[data-intake-editor]').forEach(editor => {
  const card = editor.closest('article');
  const actions = Array.from(card.querySelectorAll('form:not([data-intake-editor]) button'))
    .filter(button => !button.disabled);
  const note = editor.querySelector('[data-intake-dirty]');
  const initial = new URLSearchParams(new FormData(editor)).toString();
  const update = () => {
    const dirty = initial !== new URLSearchParams(new FormData(editor)).toString();
    actions.forEach(button => { button.disabled = dirty; });
    note.hidden = !dirty;
  };
  editor.addEventListener('input', update);
  editor.addEventListener('change', update);
});

/* Native browser-only barcode detection. If unsupported, typing still works. */
document.querySelectorAll('[data-intake-detect]').forEach(button => {
  if (typeof window.BarcodeDetector !== 'function') return;
  const card = button.closest('article');
  const input = card.querySelector('input[name="barcode"]');
  const note = card.querySelector('[data-intake-detect-note]');
  const image = card.querySelector('img');
  button.hidden = false;
  button.addEventListener('click', async () => {
    button.disabled = true;
    const before = input.value;
    try {
      const supported = await window.BarcodeDetector.getSupportedFormats();
      const formats = ['ean_8', 'ean_13', 'upc_a', 'upc_e'].filter(format => supported.includes(format));
      if (!formats.length) throw new Error('no_format');
      await image.decode();
      const rows = await new window.BarcodeDetector({formats}).detect(image);
      const codes = [...new Set(rows.map(row => row.rawValue).filter(value => /^[0-9]{8,14}$/.test(value)))];
      if (input.value !== before) return;
      if (codes.length === 1 && (!before || before === codes[0])) {
        input.value = codes[0];
        input.dispatchEvent(new Event('input', {bubbles: true}));
        note.textContent = 'Code erkannt; bitte im Entwurf speichern.';
      } else {
        note.textContent = codes.length > 0
          ? 'Mehrere oder abweichende Codes gefunden; bitte den passenden Barcode eingeben.'
          : 'Kein Code erkannt; bitte Barcode manuell eingeben.';
      }
    } catch (_) {
      note.textContent = 'Barcode-Erkennung nicht verfügbar; bitte manuell eingeben.';
    } finally {
      button.disabled = false;
    }
  });
});
