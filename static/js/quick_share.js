(() => {
  'use strict';

  const form = document.getElementById('quick-share-form');
  const kindSelect = document.getElementById('quick-share-kind');
  const sections = [...document.querySelectorAll('[data-kind-section]')];

  function updateKind() {
    const activeKind = kindSelect?.value || 'text';
    sections.forEach((section) => {
      const active = section.dataset.kindSection === activeKind;
      section.hidden = !active;
      section.querySelectorAll('input, textarea').forEach((input) => {
        input.disabled = !active;
        input.required = active && ['quick-share-text', 'quick-share-url', 'quick-share-event-title', 'quick-share-start', 'quick-share-end'].includes(input.id);
      });
    });
  }

  kindSelect?.addEventListener('change', updateKind);
  updateKind();

  document.querySelectorAll('[data-copy-value]').forEach((button) => {
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyValue || '');
        const original = button.textContent;
        button.textContent = document.documentElement.lang === 'en' ? 'Copied' : 'Kopiert';
        window.setTimeout(() => { button.textContent = original; }, 1400);
      } catch (_error) {
        const details = button.closest('.card-body')?.querySelector('[data-qr-payload]');
        if (details) {
          const selection = window.getSelection();
          const range = document.createRange();
          range.selectNodeContents(details);
          selection?.removeAllRanges();
          selection?.addRange(range);
        }
      }
    });
  });
})();
