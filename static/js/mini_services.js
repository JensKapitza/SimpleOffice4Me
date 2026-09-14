(() => {
  'use strict';
  const root = document.getElementById('mini-service-control');
  if (!root) return;
  const base = root.dataset.api;
  const feedback = document.getElementById('mini-feedback');
  const cards = document.getElementById('mini-control-cards');
  const labels = {unavailable: '○ Nicht erreichbar', stopped: '■ Gestoppt', starting: '↻ Startet',
    running: '● Läuft', degraded: '⚠ Eingeschränkt', stopping: '↻ Stoppt', failed: '⚠ Fehler',
    waiting: '◷ Wartet', disabled: '○ Deaktiviert', scanning: '↻ Sucht'};
  let busy = false;
  const views = new Map();
  const text = (tag, value, classes) => {
    const node = document.createElement(tag);
    node.textContent = value;
    if (classes) node.className = classes;
    return node;
  };
  const report = (message, error = false) => {
    feedback.textContent = message;
    feedback.className = `alert alert-${error ? 'danger' : 'secondary'}`;
  };
  const request = async (path, method = 'GET', body) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const token = document.querySelector('meta[name="csrf-token"]');
      const response = await fetch(base + path, {method, credentials: 'same-origin', cache: 'no-store',
        signal: controller.signal, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token ? token.content : ''},
        body: body === undefined ? undefined : JSON.stringify(body)});
      let data;
      try { data = await response.json(); } catch (_) { throw new Error('Antwort nicht lesbar. Anmeldung und Verbindung prüfen.'); }
      if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : (data.error && data.error.message) || 'Aktion fehlgeschlagen. Diagnose prüfen.');
      return data;
    } finally { clearTimeout(timer); }
  };
  const waitOperation = async (id) => {
    for (let attempt = 0; attempt < 40; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      const operation = await request('/operations/' + encodeURIComponent(id));
      if (operation.state === 'completed') return;
      if (operation.state === 'failed') throw new Error((operation.result && (operation.result.message || operation.result.error)) || 'Aktion fehlgeschlagen.');
    }
    throw new Error('Aktion dauert länger als erwartet. Status aktualisieren; der Befehl kann noch laufen.');
  };
  const perform = async (id, action, body) => {
    if (busy) return;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    root.querySelectorAll('button').forEach(button => { button.disabled = true; });
    report(action === 'scan' ? '↻ Geräte und Dienste werden gesucht …' : '↻ Aktion wird ausgeführt …');
    try {
      const result = await request('/' + id + '/' + action, 'POST', body || {});
      if (result.id) await waitOperation(result.id);
      await refresh();
      report(action === 'scan' ? `${result.count} Treffer. ${result.scope || ''}` : '✓ Aktion abgeschlossen.');
    } catch (error) { report(error.name === 'AbortError' ? 'Zeitüberschreitung. Verbindung prüfen und Status aktualisieren.' : error.message, true); }
    finally {
      busy = false;
      root.removeAttribute('aria-busy');
      root.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  };
  const createCard = (service) => {
    const col = text('div', '', 'col-12 col-md-6 col-xl-4');
    const article = text('article', '', 'card h-100');
    const body = text('div', '', 'card-body');
    body.appendChild(text('h3', service.name, 'h5'));
    const status = text('p', '');
    const health = text('p', '', 'small');
    const error = text('p', '', 'small');
    const scan = text('p', '', 'small');
    body.append(status, health, error, scan);
    const actions = text('div', '', 'd-flex flex-wrap gap-2 mb-3');
    [['start', 'Starten'], ['stop', 'Stoppen'], ['restart', 'Neustart'], ['scan', 'Suchen']].forEach(([key, label]) => {
      const button = text('button', label, 'btn btn-outline-primary');
      button.type = 'button'; button.setAttribute('aria-label', `${service.name}: ${label}`);
      button.addEventListener('click', () => perform(service.id, key)); actions.appendChild(button);
    });
    body.appendChild(actions);
    const settings = text('details', ''); settings.appendChild(text('summary', 'Einstellungen und Diagnose'));
    const inputs = {};
    [['enabled', 'Aktiviert'], ['autostart', 'Automatisch mit dem Worker starten']].forEach(([key, label]) => {
      const wrap = text('div', '', 'form-check my-2'); const input = document.createElement('input');
      input.type = 'checkbox'; input.className = 'form-check-input'; input.id = `mini-${service.id}-${key}`;
      const caption = text('label', label, 'form-check-label'); caption.htmlFor = input.id;
      wrap.append(input, caption); settings.appendChild(wrap); inputs[key] = input;
    });
    const save = text('button', 'Einstellungen speichern', 'btn btn-outline-primary'); save.type = 'button';
    save.addEventListener('click', () => perform(service.id, 'settings', {enabled: inputs.enabled.checked, autostart: inputs.autostart.checked}));
    const diagnosis = text('pre', '', 'small mt-2'); settings.append(save, diagnosis); body.appendChild(settings);
    article.appendChild(body); col.appendChild(article); cards.appendChild(col);
    const view = {status, health, error, scan, inputs, diagnosis}; views.set(service.id, view); return view;
  };
  const refresh = async () => {
    const data = await request('');
    data.services.forEach(service => {
      const view = views.get(service.id) || createCard(service);
      view.status.textContent = labels[service.state] || service.state;
      view.health.textContent = service.health ? service.health.message : '';
      view.error.textContent = service.last_error ? `${service.last_error.message} ${service.last_error.action}` : '';
      view.scan.textContent = service.scan.updated_at ? `${service.scan.count} Treffer · ${new Date(service.scan.updated_at * 1000).toLocaleString()}` : 'Noch keine Suche ausgeführt.';
      // Do not replace focused controls or a user's unsaved settings on polling.
      if (!view.initialized) { Object.keys(view.inputs).forEach(key => { view.inputs[key].checked = service.settings[key]; }); view.initialized = true; }
      view.diagnosis.textContent = JSON.stringify({config: service.config, health: service.health, error: service.last_error,
        retry: service.retry_in_seconds, scan: service.scan}, null, 2);
    });
  };
  document.getElementById('mini-refresh').addEventListener('click', () => refresh().then(() => report('✓ Status aktualisiert.')).catch(error => report(error.message, true)));
  refresh().then(() => report('Status geladen.')).catch(error => report(error.message, true));
  const poll = async () => {
    if (!document.hidden && !busy) {
      try { await refresh(); } catch (error) { report(error.message, true); }
    }
    setTimeout(poll, 5000);
  };
  setTimeout(poll, 5000);
})();
