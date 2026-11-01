// Büroengel avatar dialogs: dashboard hint dialogs plus the inventory
// suggestion dialog (partial adopt, adopt-all, re-analyze, reject).
// Loaded after inventory_suggest.js; degrades gracefully on pages
// without angel markup. No autosave anywhere: the dialog only fills
// the form, saving stays manual.
(() => {
  'use strict';

  // Dashboard: each avatar button opens its per-card native dialog.
  document.querySelectorAll('[data-engel-dialog]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = document.getElementById(button.getAttribute('data-engel-dialog'));
      if (dialog && typeof dialog.showModal === 'function') dialog.showModal();
    });
  });
  document.querySelectorAll('[data-engel-close]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = button.closest('dialog');
      if (dialog && typeof dialog.close === 'function' && dialog.open) dialog.close();
    });
  });

  // Inventory: suggestion dialog with per-part checkboxes.
  const suggest = (typeof window !== 'undefined' && window.SimpleOfficeInventorySuggest) || null;
  const ui = (typeof window !== 'undefined' && window.SimpleOfficeInventoryUi) || null;
  const helpers = (typeof window !== 'undefined' && window.SimpleOfficeSuggestionFields) || null;
  const dialog = document.getElementById('engel-suggest-dialog');
  if (!suggest || !ui || !dialog || typeof dialog.showModal !== 'function') return;

  const $ = ui.$;
  const cleanText = ui.cleanText || ((value) => String(value || '').trim());
  const setStatus = ui.setStatus || (() => {});
  const parts = {
    name: {box: $('engel-part-name'), preview: $('engel-preview-name')},
    type: {box: $('engel-part-type'), preview: $('engel-preview-type')},
    note: {box: $('engel-part-note'), preview: $('engel-preview-note')},
    tags: {box: $('engel-part-tags'), preview: $('engel-preview-tags')},
  };
  const partValue = (suggestion, key) => {
    if (!suggestion) return '';
    if (key === 'name') return cleanText(suggestion.object_name);
    if (key === 'type') return cleanText(suggestion.item_type);
    if (key === 'note') {
      const bits = [suggestion.note];
      if (suggestion.subtitle) bits.push(`Untertitel: ${suggestion.subtitle}`);
      return bits.map(cleanText).filter(Boolean).join(' · ');
    }
    if (key === 'tags') return helpers ? cleanText(helpers.formatSuggestionTags(suggestion.tags)) : '';
    return '';
  };
  const openDialog = () => {
    const suggestion = suggest.pending();
    if (!suggestion) {
      setStatus('Kein Vorschlag vorhanden. Bitte zuerst analysieren.', 'warning');
      return;
    }
    Object.keys(parts).forEach((key) => {
      const part = parts[key];
      const value = partValue(suggestion, key);
      if (part.preview) part.preview.textContent = value ? `\u201E${value}\u201C` : '\u2013';
      if (part.box) {
        part.box.checked = Boolean(value);
        part.box.disabled = !value;
      }
    });
    dialog.showModal();
  };
  const adoptParts = () => {
    const suggestion = suggest.pending();
    if (!suggestion) {
      dialog.close();
      return;
    }
    const selected = (key) => {
      const box = parts[key].box;
      return Boolean(box && box.checked && !box.disabled);
    };
    if (!selected('name') && !selected('type') && !selected('note') && !selected('tags')) {
      setStatus('Nichts ausgewählt \u2013 bitte mindestens einen Teil anhaken.', 'warning');
      return;
    }
    // Granular writes mirror applySuggestionToForm field by field, so only
    // checked parts touch the form. The type guard matches that function;
    // book/object mode sync stays with the full "Alles übernehmen" path.
    if (selected('name') && ui.title) ui.title.value = cleanText(suggestion.object_name);
    if (selected('type')) {
      const field = $('item-type');
      const value = cleanText(suggestion.item_type);
      const current = field ? cleanText(field.value) : 'object';
      if (field && value && value !== 'object' && (!current || current === 'object')) field.value = value;
    }
    if (selected('note') && helpers) {
      const field = $('description');
      if (field) field.value = helpers.suggestionDescription(field.value, partValue(suggestion, 'note'));
    }
    if (selected('tags') && helpers) {
      const field = $('tags');
      if (field) field.value = helpers.mergeTags(field.value, suggestion.tags, field.maxLength);
    }
    dialog.close();
    suggest.dismiss();
    setStatus('Ausgewählte Teile übernommen. Bitte Felder prüfen, dann speichern.', 'success');
    if (ui.title) ui.title.focus();
  };

  $('engel-details')?.addEventListener('click', openDialog);
  $('engel-adopt-parts')?.addEventListener('click', adoptParts);
  $('engel-adopt-all')?.addEventListener('click', () => { dialog.close(); suggest.adoptAll(); });
  $('engel-reanalyze')?.addEventListener('click', () => { dialog.close(); suggest.reanalyze(); });
  $('engel-reject')?.addEventListener('click', () => {
    dialog.close();
    suggest.dismiss();
    setStatus('Vorschlag abgelehnt.', 'info');
  });
  if (typeof window !== 'undefined') window.SimpleOfficeAngel = {openDialog, adoptParts};
})();
