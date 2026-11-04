(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    actionFailed: EN ? 'Action failed.' : 'Aktion fehlgeschlagen.',
    timeout: EN ? 'Timeout. Check the status and try again.' : 'Zeitüberschreitung. Status prüfen und erneut versuchen.',
    scanning: EN ? '↻ Searching for recipients …' : '↻ Empfänger werden gesucht …',
    pickRecipient: EN ? 'Select recipient' : 'Empfänger auswählen',
    activeRecipients: (n, time) => EN ? `${n} active recipients · ${time}` : `${n} aktive Empfänger · ${time}`,
    savedUnconfirmed: (name) => EN ? `✓ ${name} saved. Reception is not confirmed; send a test tone to verify.` : `✓ ${name} gespeichert. Empfang ist nicht bestätigt; zum Test einen Signalton senden.`,
  };
  const get = id => document.getElementById(id);
  const root = get('announcement-remote');
  if (!root) return;
  const status = get('announcement-remote-status'), select = get('announcement-remote-select');
  const scan = get('announcement-remote-scan'), form = get('announcement-remote-form');
  let targets = [], busy = false;
  const post = async (url, data, timeout) => {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
    try {
      const token = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch(url, {method: 'POST', credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token ? token.content : ''},
        body: JSON.stringify(data)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || STR.actionFailed);
      return result;
    } finally { clearTimeout(timer); }
  };
  const action = async callback => {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy', 'true');
    scan.disabled = true; form.querySelector('button').disabled = true;
    try { await callback(); }
    catch (error) { status.textContent = error.name === 'AbortError' ? STR.timeout : error.message; }
    finally { busy = false; scan.disabled = false; form.querySelector('button').disabled = false; root.removeAttribute('aria-busy'); }
  };
  scan.addEventListener('click', () => action(async () => {
    status.textContent = STR.scanning;
    targets = []; select.replaceChildren(new Option(STR.pickRecipient, ''));
    const data = await post(root.dataset.scanUrl, {}, 60000);
    targets = data.targets || [];
    targets.forEach((target, index) => select.add(new Option(target.label + ' · ' + target.id, String(index))));
    status.textContent = STR.activeRecipients(targets.length, new Date(data.updated_at * 1000).toLocaleTimeString());
  }));
  select.addEventListener('change', () => {
    if (select.value === '') return;
    const target = targets[Number(select.value)];
    if (!target) return;
    get('announcement-remote-host').value = target.host;
    get('announcement-remote-port').value = target.port;
    get('announcement-remote-name').value = target.label;
  });
  form.addEventListener('submit', event => {
    event.preventDefault();
    action(async () => {
      const host = get('announcement-remote-host').value.trim(), port = Number(get('announcement-remote-port').value);
      const result = await post(get('audio-announcements').dataset.api + '/outputs', {
        node_id: 'rtp-' + host, output_id: 'rtp-' + host + '-' + port,
        name: get('announcement-remote-name').value.trim(), volume: Number(get('announcement-remote-volume').value),
        transport: {kind: 'rtp-udp', host, port}
      }, 15000);
      status.textContent = STR.savedUnconfirmed(result.name);
      window.dispatchEvent(new Event('simpleoffice:audio-output-saved'));
    });
  });
})();
