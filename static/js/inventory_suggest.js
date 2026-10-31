// Single-photo suggestion workflow for the inventory form (analyze, review,
// adopt). Split out of static/js/inventory.js to keep that module under the
// file-size gate; loaded after it (see templates/inventory/index.html) and
// wired through window.SimpleOfficeInventoryUi. No autosave: the user always
// reviews the suggestion card and saves manually.
(() => {
  'use strict';
  const ui = (typeof window !== 'undefined' && window.SimpleOfficeInventoryUi) || null;
  if (!ui || !ui.form) return;
  const {
    $, cleanText, setStatus, safeJson, urls, LOCAL_UNAVAILABLE_MESSAGE,
    form, photo, barcode, isbn, title, applySuggestionToForm,
  } = ui;
  const suggestionHelpers = (typeof window !== 'undefined' && window.SimpleOfficeSuggestionFields) || null;

  const analyzeButton = $('analyze-photo');
  const suggestionBox = $('object-suggestion');
  const suggestedName = $('suggested-name');
  const suggestedType = $('suggested-type');
  const suggestedNote = $('suggested-note');
  const suggestedBookDetails = $('suggested-book-details');
  const suggestedTags = $('suggested-tags');
  const suggestedProvider = $('suggested-provider');
  const metaPhotoConsent = $('meta-photo-consent');
  let pendingSuggestion = null;
  let analyzing = null;

  const hideObjectSuggestion = () => { pendingSuggestion = null; if (suggestionBox) suggestionBox.hidden = true; };
  const showObjectSuggestion = (payload) => {
    const suggestion = payload && payload.suggestion ? payload.suggestion : null;
    if (!suggestion || !suggestionBox) { setStatus('Der Vorschlag war leer. Bitte erneut versuchen.', 'warning'); return; }
    pendingSuggestion = suggestion;
    if (suggestedName) suggestedName.textContent = cleanText(suggestion.object_name);
    if (suggestedType) suggestedType.textContent = suggestion.item_type && suggestion.item_type !== 'object' ? `(${suggestion.item_type})` : '';
    if (suggestedNote) suggestedNote.textContent = cleanText(suggestion.note);
    if (suggestedBookDetails) {
      const details = [];
      if (suggestion.subtitle) details.push(`Untertitel: ${cleanText(suggestion.subtitle)}`);
      if (Array.isArray(suggestion.authors) && suggestion.authors.length) {
        details.push(`Autor: ${suggestion.authors.map(cleanText).filter(Boolean).join(', ')}`);
      }
      suggestedBookDetails.textContent = details.join(' · ');
    }
    if (suggestedTags) suggestedTags.textContent = suggestionHelpers ? suggestionHelpers.formatSuggestionTags(suggestion.tags) : '';
    if (suggestedProvider) suggestedProvider.textContent = cleanText(payload.provider_label);
    suggestionBox.hidden = false;
    suggestionBox.scrollIntoView({block: 'nearest'});
  };
  const analyzePhoto = async () => {
    if (!photo?.files?.length) { setStatus('Bitte zuerst ein Foto auswählen.', 'warning'); return; }
    const file = photo.files[0];
    if (file.size > 5 * 1024 * 1024) { setStatus('Das Foto ist größer als 5 MiB – bitte ein kleineres Bild wählen.', 'warning'); return; }
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type || '')) { setStatus('Nur JPEG, PNG oder WebP sind erlaubt.', 'warning'); return; }
    if (!urls.suggest) { setStatus('Vorschläge sind hier nicht verfügbar.', 'warning'); return; }
    const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
    const data = new FormData();
    data.append('photo', file, file.name || 'foto');
    data.append('_csrf_token', token);
    if (metaPhotoConsent?.checked) data.append('consent_meta', 'yes');
    const code = cleanText(barcode?.value); if (code) data.append('barcode', code.slice(0, 120));
    const isbnCode = cleanText(isbn?.value); if (isbnCode) data.append('isbn', isbnCode.slice(0, 120));
    if (analyzeButton) { analyzeButton.disabled = true; analyzeButton.setAttribute('aria-busy', 'true'); }
    setStatus('Foto wird analysiert …', 'primary');
    if (analyzing) analyzing.abort(); analyzing = new AbortController();
    try {
      const response = await fetch(urls.suggest, { method: 'POST', body: data, cache: 'no-store', signal: analyzing.signal });
      const payload = await safeJson(response);
      if (!response.ok) {
        const serverMessage = cleanText(payload && payload.error);
        const fallback = response.status === 503
          ? LOCAL_UNAVAILABLE_MESSAGE
          : `Vorschlag fehlgeschlagen (${response.status}).`;
        setStatus(serverMessage || fallback, response.status >= 500 ? 'danger' : 'warning');
        return;
      }
      showObjectSuggestion(payload);
      setStatus('Vorschlag bereit. Bitte prüfen und bei Bedarf übernehmen.', 'success');
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus('Die Analyse ist fehlgeschlagen.', 'danger');
    } finally {
      if (analyzeButton) { analyzeButton.disabled = false; analyzeButton.removeAttribute('aria-busy'); }
    }
  };
  const adoptSuggestion = () => {
    if (!pendingSuggestion || !title) { hideObjectSuggestion(); return; }
    applySuggestionToForm(pendingSuggestion);
    hideObjectSuggestion();
    setStatus('Vorschlag übernommen. Bitte Felder prüfen, dann speichern.', 'success');
    title.focus();
  };

  photo?.addEventListener('change', hideObjectSuggestion);
  photo?.addEventListener('change', () => { if ($('analyze-photo')) analyzePhoto(); });
  analyzeButton?.addEventListener('click', analyzePhoto);
  $('adopt-suggestion')?.addEventListener('click', adoptSuggestion);
  $('dismiss-suggestion')?.addEventListener('click', hideObjectSuggestion);
})();
