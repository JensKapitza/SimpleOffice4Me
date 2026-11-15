(() => {
  const picker = document.querySelector('[data-letter-address-picker]');
  const recipient = document.querySelector('[data-letter-recipient]');
  if (!picker || !recipient) return;

  picker.addEventListener('change', () => {
    recipient.value = picker.selectedOptions[0]?.dataset.addressLabel || '';
  });
})();
