/* Desktop file reveal: show local files in the OS file manager.
   The button only appears inside the first-party desktop shell, which proves
   same-machine execution. The server only resolves document IDs to validated
   local paths and never launches processes. */
(() => {
  'use strict';
  const bridge = window.simpleOfficeDesktop;
  if (!bridge || bridge.desktop !== true || typeof bridge.revealFile !== 'function') return;
  document.querySelectorAll('[data-desktop-reveal]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', async () => {
      const feedback = button.parentElement
        ? button.parentElement.querySelector('[data-desktop-reveal-feedback]')
        : null;
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.localPathUrl, {headers: {Accept: 'application/json'}});
        if (!response.ok) throw new Error(`local-path ${response.status}`);
        const data = await response.json();
        if (!data || data.available !== true || typeof data.path !== 'string') {
          throw new Error((data && data.reason) || 'unavailable');
        }
        await bridge.revealFile(data.path);
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
