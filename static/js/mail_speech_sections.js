(() => {
  'use strict';

  for (const select of document.querySelectorAll('[data-mail-speech-section]')) {
    const reader = select.closest('[data-document-reader]');
    const start = reader?.querySelector('[data-speech-start]');
    if (!start || start.disabled) continue;

    select.disabled = false;
    select.addEventListener('change', () => {
      const page = Number(select.value);
      if (!Number.isInteger(page) || page < 1 || page > select.options.length) return;
      // The shared reader stops playback or aborts a pending response.
      // It sends no request until the user explicitly clicks Read aloud.
      reader.dispatchEvent(new CustomEvent('reader:page-changed', {detail: {page}}));
    });
  }
})();
