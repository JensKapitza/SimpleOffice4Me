(() => {
  'use strict';

  const root = document.getElementById('audio-streamer-app');
  if (!root) return;

  const base = root.dataset.baseUrl || '';
  const select = document.getElementById('speaker-select');
  const search = document.getElementById('speaker-search');
  const status = document.getElementById('speaker-scan-status');
  const manual = document.getElementById('speaker-device');
  const scanButton = document.getElementById('speaker-scan');
  let outputs = [];

  const render = () => {
    if (!select) return;
    const current = select.value;
    const query = String(search?.value || '').trim().toLocaleLowerCase();
    const visible = outputs.filter((item) => {
      const text = `${item.id || ''} ${item.driver || ''} ${item.state || ''}`.toLocaleLowerCase();
      return !query || text.includes(query);
    });

    select.replaceChildren();
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'Keine lokale Wiedergabe';
    select.appendChild(none);

    visible.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.id;
      const meta = [item.default ? 'Standard' : '', item.state || ''].filter(Boolean).join(' · ');
      option.textContent = meta ? `${item.id} (${meta})` : item.id;
      select.appendChild(option);
    });

    if (visible.some((item) => item.id === current)) select.value = current;
    if (status) status.textContent = `${visible.length} von ${outputs.length} Ausgängen angezeigt.`;
  };

  const scan = async () => {
    if (scanButton) scanButton.disabled = true;
    if (status) status.textContent = 'Audio-Ausgänge werden gesucht …';
    try {
      const response = await fetch(base + '/outputs', {cache: 'no-store', credentials: 'same-origin'});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      outputs = Array.isArray(data.outputs) ? data.outputs : [];
      render();
      const preferred = outputs.find((item) => item.default) || outputs[0];
      if (preferred && select) {
        select.value = preferred.id;
        if (manual) manual.value = preferred.id;
      }
    } catch (error) {
      if (status) status.textContent = error.message || 'Scan fehlgeschlagen.';
    } finally {
      if (scanButton) scanButton.disabled = false;
    }
  };

  scanButton?.addEventListener('click', scan);
  search?.addEventListener('input', render);
  select?.addEventListener('change', () => {
    if (manual) manual.value = select.value;
  });
})();
