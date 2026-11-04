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
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    emptySuggestion: EN ? 'The suggestion was empty. Please try again.' : 'Der Vorschlag war leer. Bitte erneut versuchen.',
    subtitle: EN ? 'Subtitle: ' : 'Untertitel: ',
    author: EN ? 'Author: ' : 'Autor: ',
    needPhoto: EN ? 'Please select a photo first.' : 'Bitte zuerst ein Foto auswählen.',
    photoTooBig: EN ? 'The photo exceeds 5 MiB – please choose a smaller image.' : 'Das Foto ist größer als 5 MiB – bitte ein kleineres Bild wählen.',
    photoType: EN ? 'Only JPEG, PNG or WebP are allowed.' : 'Nur JPEG, PNG oder WebP sind erlaubt.',
    notAvailable: EN ? 'Suggestions are not available here.' : 'Vorschläge sind hier nicht verfügbar.',
    analyzing: EN ? 'Analyzing photo …' : 'Foto wird analysiert …',
    failed: (code) => EN ? `Suggestion failed (${code}).` : `Vorschlag fehlgeschlagen (${code}).`,
    unavailable503: EN ? 'Local object recognition is not set up. Please configure Ollama and a vision model.' : 'Lokale Objekterkennung ist nicht eingerichtet. Bitte Ollama und ein Vision-Modell konfigurieren.',
    ready: EN ? 'Suggestion ready. Please review and adopt if needed.' : 'Vorschlag bereit. Bitte prüfen und bei Bedarf übernehmen.',
    analysisFailed: EN ? 'Analysis failed.' : 'Die Analyse ist fehlgeschlagen.',
    applied: EN ? 'Suggestion applied. Please check the fields, then save.' : 'Vorschlag übernommen. Bitte Felder prüfen, dann speichern.',
  };

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
    if (!suggestion || !suggestionBox) { setStatus(STR.emptySuggestion, 'warning'); return; }
    pendingSuggestion = suggestion;
    if (suggestedName) suggestedName.textContent = cleanText(suggestion.object_name);
    if (suggestedType) suggestedType.textContent = suggestion.item_type && suggestion.item_type !== 'object' ? `(${suggestion.item_type})` : '';
    if (suggestedNote) suggestedNote.textContent = cleanText(suggestion.note);
    if (suggestedBookDetails) {
      const details = [];
      if (suggestion.subtitle) details.push(`${STR.subtitle}${cleanText(suggestion.subtitle)}`);
      if (Array.isArray(suggestion.authors) && suggestion.authors.length) {
        details.push(`${STR.author}${suggestion.authors.map(cleanText).filter(Boolean).join(', ')}`);
      }
      suggestedBookDetails.textContent = details.join(' · ');
    }
    if (suggestedTags) suggestedTags.textContent = suggestionHelpers ? suggestionHelpers.formatSuggestionTags(suggestion.tags) : '';
    if (suggestedProvider) suggestedProvider.textContent = cleanText(payload.provider_label);
    suggestionBox.hidden = false;
    suggestionBox.scrollIntoView({block: 'nearest'});
  };
  const analyzePhoto = async () => {
    if (!photo?.files?.length) { setStatus(STR.needPhoto, 'warning'); return; }
    const file = photo.files[0];
    if (file.size > 5 * 1024 * 1024) { setStatus(STR.photoTooBig, 'warning'); return; }
    if (!/^image\/(jpeg|png|webp)$/i.test(file.type || '')) { setStatus(STR.photoType, 'warning'); return; }
    if (!urls.suggest) { setStatus(STR.notAvailable, 'warning'); return; }
    const token = form?.querySelector('input[name="_csrf_token"]')?.value || '';
    const data = new FormData();
    data.append('photo', file, file.name || 'foto');
    data.append('_csrf_token', token);
    if (metaPhotoConsent?.checked) data.append('consent_meta', 'yes');
    const code = cleanText(barcode?.value); if (code) data.append('barcode', code.slice(0, 120));
    const isbnCode = cleanText(isbn?.value); if (isbnCode) data.append('isbn', isbnCode.slice(0, 120));
    if (analyzeButton) { analyzeButton.disabled = true; analyzeButton.setAttribute('aria-busy', 'true'); }
    setStatus(STR.analyzing, 'primary');
    if (analyzing) analyzing.abort(); analyzing = new AbortController();
    try {
      const response = await fetch(urls.suggest, { method: 'POST', body: data, cache: 'no-store', signal: analyzing.signal });
      const payload = await safeJson(response);
      if (!response.ok) {
        const serverMessage = cleanText(payload && payload.error);
        const fallback = response.status === 503
          ? STR.unavailable503
          : STR.failed(response.status);
        setStatus(serverMessage || fallback, response.status >= 500 ? 'danger' : 'warning');
        return;
      }
      showObjectSuggestion(payload);
      setStatus(STR.ready, 'success');
    } catch (error) {
      if (error?.name !== 'AbortError') setStatus(STR.analysisFailed, 'danger');
    } finally {
      if (analyzeButton) { analyzeButton.disabled = false; analyzeButton.removeAttribute('aria-busy'); }
    }
  };
  const adoptSuggestion = () => {
    if (!pendingSuggestion || !title) { hideObjectSuggestion(); return; }
    applySuggestionToForm(pendingSuggestion);
    hideObjectSuggestion();
    setStatus(STR.applied, 'success');
    title.focus();
  };

  photo?.addEventListener('change', hideObjectSuggestion);
  photo?.addEventListener('change', () => { if ($('analyze-photo')) analyzePhoto(); });
  analyzeButton?.addEventListener('click', analyzePhoto);
  $('adopt-suggestion')?.addEventListener('click', adoptSuggestion);
  $('dismiss-suggestion')?.addEventListener('click', hideObjectSuggestion);
  // Shared namespace for static/js/angel.js (loaded after this module):
  // the suggestion dialog (partial adopt, adopt-all, re-analyze, reject).
  if (typeof window !== 'undefined') {
    window.SimpleOfficeInventorySuggest = {
      pending: () => pendingSuggestion,
      adoptAll: adoptSuggestion,
      dismiss: hideObjectSuggestion,
      reanalyze: analyzePhoto,
    };
  }
})();
