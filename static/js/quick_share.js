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
    const card = button.closest('.card-body');
    const payload = card.querySelector('[data-qr-payload]');
    const status = card.querySelector('[data-copy-status]');
    button.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(button.dataset.copyValue);
        status.textContent = status.dataset.copied;
      } catch {
        payload.closest('details').open = true;
        const selection = window.getSelection();
        if (selection) {
          const range = document.createRange();
          range.selectNodeContents(payload);
          selection.removeAllRanges();
          selection.addRange(range);
        }
        status.textContent = status.dataset.manual;
      }
    });
  });
})();
