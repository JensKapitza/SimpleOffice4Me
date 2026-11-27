/* Preview the capture before the server acknowledgement; no analysis. */
(() => {
  const form = document.querySelector('#intake-capture');
  const input = document.querySelector('#intake-photo');
  const pending = document.querySelector('#intake-pending');
  if (!form || !input || !pending) return;
  const picture = pending.querySelector('[data-intake-pending-photo]');
  const barcode = form.querySelector('#intake-barcode');
  const batch = document.querySelector('#intake-batch-location');
  const scan = form.querySelector('[data-intake-native-scan]');
  const code = pending.querySelector('[data-intake-pending-barcode]');
  const openSaved = pending.querySelector('[data-intake-open-saved]');
  const status = pending.querySelector('[role="status"]');
  const submit = form.querySelector('[type="submit"]');
  const nativeSaveLimitMs = 8000;
  const initialSubmitLabel = submit.textContent;
  let nativeCapture = null;
  let nativeDestinationEditable = false;
  let previewVersion = 0;
  let uploading = false;
  let saved = false;
  const batchKey = 'simpleoffice.intake.batch-location';
  if (batch) {
    const scope = batch.dataset.intakeBatchScope;
    try {
      const choice = JSON.parse(window.sessionStorage.getItem(batchKey));
      if (choice?.scope === scope && Array.from(batch.options).some(option => option.value === choice.location)) {
        batch.value = choice.location;
      }
    } catch (_) { /* Storage can be unavailable in private or managed browsers. */ }
    batch.addEventListener('change', () => {
      if (nativeDestinationEditable) nativeCapture?.set('location_id', batch.value);
      try {
        if (batch.value) window.sessionStorage.setItem(batchKey, JSON.stringify({scope, location: batch.value}));
        else window.sessionStorage.removeItem(batchKey);
      } catch (_) { /* The current capture still uses the selected destination. */ }
    });
  }
  const showBarcode = () => {
    const value = !input.files[0] ? barcode.value.trim() : '';
    code.hidden = !value;
    code.querySelector('code').textContent = value;
  };
  const lockCapture = (locked) => {
    submit.disabled = locked;
    input.disabled = scan.disabled = barcode.readOnly = locked || Boolean(nativeCapture);
    if (batch) batch.disabled = locked || (Boolean(nativeCapture) && !nativeDestinationEditable);
  };
  const sendCapture = async (data, bounded) => {
    const controller = bounded && typeof AbortController === 'function' ? new AbortController() : null;
    const send = async () => {
      const response = await window.fetch(form.action, {
        method: 'POST', credentials: 'same-origin', headers: {Accept: 'application/json'}, body: data,
        ...(controller ? {signal: controller.signal} : {}),
      });
      return {response, parsed: await response.json()};
    };
    if (!bounded) return send();
    let timer;
    try {
      return await Promise.race([send(), new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('Native barcode acknowledgement timed out'));
          controller?.abort();
        }, nativeSaveLimitMs);
      })]);
    } finally {
      clearTimeout(timer);
    }
  };
  const retryNote = 'Speicherung nicht bestätigt. Bitte denselben Scan erneut speichern.';

  openSaved.addEventListener('click', () => window.location.reload());
  form.addEventListener('intake:native-scan', () => {
    if (input.files[0] || uploading || saved || nativeCapture) return;
    // Reuse the original payload, including its capture token, until confirmed.
    nativeCapture = new FormData(form);
    form.requestSubmit();
  });
  input.addEventListener('change', async () => {
    const version = ++previewVersion;
    const photo = input.files[0];
    pending.hidden = !photo;
    picture.hidden = !photo;
    code.hidden = true;
    if (!photo) {
      picture.replaceChildren();
      return;
    }
    picture.replaceChildren();
    status.textContent = 'Lokale Foto-Vorschau wird erstellt …';
    try {
      // Decode to pixels and paint a canvas. Never interpret the selected file
      // as markup or assign its bytes to a navigable DOM URL.
      if (typeof window.createImageBitmap !== 'function') throw new Error('Preview decoder unavailable');
      const bitmap = await window.createImageBitmap(photo);
      try {
        if (version !== previewVersion) return;
        const scale = Math.min(1, 1200 / Math.max(bitmap.width, bitmap.height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        canvas.className = 'object-fit-cover rounded';
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', 'Vorschau des neuen Fotos');
        const context = canvas.getContext('2d');
        if (!context) throw new Error('Canvas preview unavailable');
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        picture.replaceChildren(canvas);
        status.textContent = 'Neues Foto · Noch nicht gespeichert';
      } finally {
        bitmap.close?.();
      }
    } catch (_) {
      if (version === previewVersion) {
        picture.replaceChildren();
        status.textContent = 'Foto-Vorschau nicht verfügbar · Das Foto kann trotzdem gespeichert werden';
      }
    }
    if (photo.size <= 12 * 1024 * 1024 && form.requestSubmit) form.requestSubmit();
  });
  form.addEventListener('submit', async event => {
    if (uploading || saved) {
      event.preventDefault();
      return;
    }
    pending.hidden = false;
    picture.hidden = !input.files[0];
    showBarcode();
    status.textContent = 'Privater Entwurf wird gespeichert …';
    if (!window.fetch) return;
    event.preventDefault();
    uploading = true;
    const data = nativeCapture || new FormData(form);
    nativeDestinationEditable = false;
    lockCapture(true);
    try {
      const {response, parsed} = await sendCapture(data, Boolean(nativeCapture));
      if (!response.ok || !parsed.ok || !/^[0-9a-f]{32}$/.test(parsed.draft?.id)) {
        if (response.status === 400 && parsed?.error_kind === 'location_unavailable' && batch) {
          // The server rejected the location before writing. Retry the same capture
          // token after correcting only the destination; unknown outcomes stay locked.
          const option = Array.from(batch.options).find(row => row.value === data.get('location_id'));
          if (option?.value) option.remove();
          batch.value = '';
          batch.dispatchEvent(new Event('change'));
          if (nativeCapture) {
            nativeDestinationEditable = true;
            nativeCapture.set('location_id', '');
          }
        }
        status.textContent = typeof parsed?.error === 'string' ? parsed.error
          : nativeCapture ? retryNote : 'Speicherung nicht bestätigt. Bitte erneut speichern.';
        return;
      }
      status.textContent = 'Privater Entwurf gespeichert';
      saved = true;
      window.location.hash = 'draft-' + parsed.draft.id;
      if (document.querySelector('[data-intake-editor][data-so-dirty="1"]')) {
        status.textContent += ' · Andere Entwürfe enthalten noch ungespeicherte Änderungen.';
        openSaved.hidden = false;
      } else {
        window.location.reload();
      }
    } catch (_) {
      status.textContent = nativeCapture ? retryNote
        : 'Speicherung nicht bestätigt. Eingabe bleibt erhalten; bitte erneut speichern.';
    } finally {
      uploading = false;
      if (!saved) {
        submit.textContent = nativeCapture ? 'Scan erneut speichern' : initialSubmitLabel;
        lockCapture(false);
      }
    }
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
    editor.dataset.soDirty = dirty ? '1' : '0';
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
