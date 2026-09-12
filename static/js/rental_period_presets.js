(() => {
  const page = document.querySelector('[data-rental-entry]');
  if (!page) return;

  const toIso = value => {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, '0');
    const day = String(value.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  };

  const today = () => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  };

  const endOfMonth = value => new Date(value.getFullYear(), value.getMonth() + 1, 0);

  const lastTwelveMonths = () => {
    const end = today();
    const start = new Date(end.getFullYear(), end.getMonth() - 11, 1);
    return [toIso(start), toIso(endOfMonth(end))];
  };

  page.addEventListener('click', event => {
    const button = event.target.closest('[data-rental-period]');
    if (!button) return;
    const form = button.closest('form');
    if (!form) return;
    const start = form.querySelector('[name="starts_on"], [name="valid_from"]');
    const end = form.querySelector('[name="ends_on"], [name="valid_to"]');
    if (!start || !end) return;

    const mode = button.dataset.rentalPeriod;
    if (mode === 'one-day') {
      const value = start.value || toIso(today());
      start.value = value;
      end.value = value;
    } else if (mode === 'last-12') {
      const values = lastTwelveMonths();
      start.value = values[0];
      end.value = values[1];
    } else if (mode === 'open') {
      if (!start.value) start.value = toIso(today());
      end.value = '';
    }
  });
})();
