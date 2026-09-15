(() => {
  'use strict';
  const root = document.getElementById('audio-announcements');
  if (!root) return;
  const get = id => document.getElementById(id);
  let busy = false;
  const show = (message, error) => {
    get('announcement-status').textContent = message;
    get('announcement-status').className = 'alert alert-' + (error ? 'danger' : 'secondary');
  };
  const request = async (path, body) => {
    const token = document.querySelector('meta[name="csrf-token"]');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(root.dataset.api + path, {method: body === undefined ? 'GET' : 'POST',
        credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token ? token.content : ''},
        body: body === undefined ? undefined : JSON.stringify(body)});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Antwort nicht lesbar. Anmeldung prüfen.'); }
      if (!response.ok) throw new Error(data.error || 'Audio-Aktion fehlgeschlagen.');
      return data;
    } finally { clearTimeout(timer); }
  };
  const refresh = async () => {
    const data = await request('');
    const select = get('announcement-target'), current = select.value;
    const previous = JSON.stringify(Array.from(select.options).slice(1).map(option => [option.value, option.text]));
    const options = data.outputs.filter(output => output.node_id === 'local').map(output => ({id: output.output_id, name: output.name + (output.online ? '' : ' (offline)')}));
    data.groups.forEach(group => options.push({id: group.group_id, name: 'Gruppe: ' + group.name}));
    if (JSON.stringify(options.map(option => [option.id, option.name])) !== previous) {
      while (select.firstChild) select.removeChild(select.firstChild);
      select.add(new Option('Ausgang oder Gruppe wählen', ''));
      options.forEach(option => select.add(new Option(option.name, option.id)));
      if (options.some(option => option.id === current)) select.value = current;
      else if (options.length === 1) select.value = options[0].id;
    }
    const history = get('announcement-history');
    const historyFocused = history.contains(document.activeElement);
    if (!historyFocused) {
    while (history.firstChild) history.removeChild(history.firstChild);
    const labels = {queued: '◷ Wartet', playing: '● Spielt', done: '✓ Abgeschlossen', failed: '⚠ Fehlgeschlagen', cancelled: '■ Abgebrochen'};
    data.queue.slice(0, 20).forEach(job => {
      const row = document.createElement('p');
      row.textContent = '#' + job.id + ' · ' + (labels[job.state] || job.state) + ' · ' + job.kind + (job.error ? ' · ' + job.error : '');
      if (job.state === 'queued') {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-sm btn-outline-secondary ms-2';
        button.textContent = 'Abbrechen'; button.addEventListener('click', () => act('/queue/' + job.id + '/cancel', {})); row.appendChild(button);
      }
      history.appendChild(row);
    });
    }
    const states = {unavailable: '○ Nicht erreichbar', stopped: '■ Gestoppt', starting: '↻ Startet', running: '● Läuft', degraded: '⚠ Eingeschränkt', stopping: '↻ Stoppt', failed: '⚠ Fehler', waiting: '◷ Wartet', disabled: '○ Deaktiviert'};
    if (!busy) show((states[data.service.state] || data.service.state) + ' · ' + data.service.health.message);
  };
  const act = async (path, body) => {
    if (busy) return;
    busy = true; root.setAttribute('aria-busy', 'true');
    try { const data = await request(path, body); show(data.id ? 'Auftrag #' + data.id + ' eingereiht.' : 'Aktion ausgeführt.'); await refresh(); }
    catch (error) { show(error.name === 'AbortError' ? 'Zeitüberschreitung. Status prüfen.' : error.message, true); }
    finally { busy = false; root.removeAttribute('aria-busy'); }
  };
  root.querySelectorAll('[data-audio-control]').forEach(button => button.addEventListener('click', () => act('/' + button.dataset.audioControl, {})));
  const queue = kind => {
    const target = get('announcement-target').value;
    if (!target) { show('Bitte einen Ausgang oder eine Gruppe wählen.', true); return; }
    act('/' + kind, kind === 'say' ? {targets: [target], text: get('announcement-text').value} : {targets: [target], preset: get('announcement-preset').value});
  };
  get('announcement-say').addEventListener('click', () => queue('say'));
  get('announcement-sound').addEventListener('click', () => queue('sound'));
  get('announcement-save').addEventListener('click', () => act('/settings', {enabled: get('announcement-enabled').checked, autostart: get('announcement-autostart').checked, retry_limit: Number(get('announcement-retries').value)}));
  request('/settings').then(data => { get('announcement-enabled').checked = data.enabled; get('announcement-autostart').checked = data.autostart; get('announcement-retries').value = data.retry_limit; }).catch(error => show(error.message, true));
  const poll = async () => { if (!busy && !document.hidden) { try { await refresh(); } catch (error) { show(error.message, true); } } setTimeout(poll, 3000); };
  poll();
})();
