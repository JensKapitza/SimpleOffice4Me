/* Desktop file reveal: the main process resolves the path and keeps it out of
   renderer JavaScript. The button is presentation only, not an authorization. */
(() => {
  'use strict';
  const bridge = window.simpleOfficeDesktop;
  if (!bridge || bridge.desktop !== true || typeof bridge.revealDocument !== 'function') return;
  document.querySelectorAll('[data-desktop-reveal]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', async () => {
      const feedback = button.parentElement
        ? button.parentElement.querySelector('[data-desktop-reveal-feedback]')
        : null;
      button.disabled = true;
      try {
        await bridge.revealDocument(button.dataset.documentId);
        if (feedback) feedback.textContent = '';
      } catch {
        if (feedback) {
          const german = (document.documentElement.lang || 'de') !== 'en';
          feedback.textContent = german
            ? 'Datei ist hier nicht lokal verfügbar.'
            : 'File is not available locally here.';
        }
      } finally {
        button.disabled = false;
      }
    });
  });
})();
