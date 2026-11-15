(() => {
  const input = document.querySelector('[data-shopping-share-url]');
  const button = document.querySelector('[data-shopping-copy]');
  if (!input || !button) return;

  button.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(input.value);
      button.textContent = 'Kopiert';
    } catch (_error) {
      input.focus();
      input.select();
      const copied = document.execCommand('copy');
      button.textContent = copied ? 'Kopiert' : 'Bitte manuell kopieren';
    }
  });
})();
