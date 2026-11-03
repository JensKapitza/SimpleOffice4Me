(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    unreadable: EN ? 'Firewall response is unreadable.' : 'Firewall-Antwort ist nicht lesbar.',
    actionFailed: EN ? 'Firewall action failed.' : 'Firewall-Aktion fehlgeschlagen.',
    actionSlow: EN ? 'Firewall action is taking longer than expected. Reload the status.' : 'Firewall-Aktion läuft länger als erwartet. Status erneut einlesen.',
    actionRunning: EN ? 'Firewall action running …' : 'Firewall-Aktion läuft …',
    zonePrompt: (zones) => EN ? 'Multiple firewalld zones are active. Enter the target zone:\n' + zones.join(', ') : 'Mehrere firewalld-Zonen sind aktiv. Zielzone eingeben:\n' + zones.join(', '),
    zoneInvalid: EN ? 'Invalid or cancelled zone selection.' : 'Ungültige oder abgebrochene Zonenauswahl.',
    testPreparing: EN ? 'Preparing 20-second firewall test …' : '20-Sekunden-Firewalltest wird vorbereitet …',
    testActive: EN ? 'Test active. Check the connection and confirm within 20 seconds.' : 'Test aktiv. Verbindung prüfen und innerhalb von 20 Sekunden bestätigen.',
    enabled: EN ? 'enabled' : 'aktiviert', disabled: EN ? 'disabled' : 'deaktiviert',
    noBind: EN ? 'no fixed bind address' : 'keine feste Bind-Adresse',
    listenerOn: EN ? 'Listener active' : 'Listener aktiv', listenerOff: EN ? 'no listener' : 'kein Listener',
    testOpen: EN ? 'Test opening' : 'Öffnen testen', testBlock: EN ? 'Test blocking' : 'Sperren testen',
    noTests: EN ? 'No running tests.' : 'Keine laufenden Tests.',
    confirmChange: EN ? 'Confirm change' : 'Änderung bestätigen',
    rollbackNow: EN ? 'Roll back now' : 'Jetzt zurückrollen',
    confirming: EN ? 'Confirming firewall change …' : 'Firewalländerung wird bestätigt …',
    confirmed: EN ? 'Firewall change permanently confirmed.' : 'Firewalländerung dauerhaft bestätigt.',
    rollingBack: EN ? 'Rolling back firewall change …' : 'Firewalländerung wird zurückgerollt …',
    rolledBack: EN ? 'Firewall test rolled back.' : 'Firewalltest zurückgerollt.',
    rollbackFailed: EN ? 'Rollback failed – roll back again and check the agent log.' : 'Rollback fehlgeschlagen – erneut zurückrollen und Agent-Protokoll prüfen.',
    confirmingSafe: EN ? 'Confirmation is being applied safely …' : 'Bestätigung wird sicher übernommen …',
    preparingWatchdog: EN ? 'Test is being prepared; the rollback watchdog is authoritative.' : 'Test wird vorbereitet; Rollback-Watchdog ist maßgeblich.',
    autoRollback: (left) => EN ? `Automatic rollback in ${left} s` : `Automatischer Rollback in ${left} s`,
    rollbackRunning: EN ? 'Rollback running / refresh status' : 'Rollback läuft / Status aktualisieren',
    unknown: EN ? 'unknown' : 'unbekannt',
    cacheAge: (age) => EN ? `Cache age: ${age} s` : `Cache-Alter: ${age} s`,
    installed: (ufw, firewalld, nftables) => EN
      ? `UFW: ${ufw ? 'yes' : 'no'} · firewalld: ${firewalld ? 'yes' : 'no'} · nftables: ${nftables ? 'yes' : 'no'}`
      : `UFW: ${ufw ? 'ja' : 'nein'} · firewalld: ${firewalld ? 'ja' : 'nein'} · nftables: ${nftables ? 'ja' : 'nein'}`,
    activeZones: (zones) => EN ? `Active zones: ${zones}` : `Aktive Zonen: ${zones}`,
    noZones: EN ? 'No firewalld zone reported.' : 'Keine firewalld-Zone gemeldet.',
    lastAction: (action) => EN ? `Last action: ${action}` : `Letzte Aktion: ${action}`,
    lastActionNone: EN ? 'Last action: –' : 'Letzte Aktion: –',
    automatic: EN ? 'automatic' : 'automatisch',
    reloaded: EN ? 'Firewall status reloaded.' : 'Firewallstatus neu eingelesen.',
    manualRule: EN ? 'Manual rule' : 'Manuelle Regel',
    testActiveShort: EN ? 'Test active. Confirm within 20 seconds.' : 'Test aktiv. Innerhalb von 20 Sekunden bestätigen.',
    loaded: EN ? 'Firewall status loaded.' : 'Firewallstatus geladen.',
  };
  const root = document.getElementById('firewall-control');
  if (!root) return;
  const base = root.dataset.api;
  const feedback = document.getElementById('firewall-feedback');
  const tbody = document.getElementById('fw-services');
  const tests = document.getElementById('fw-tests');
  const zoneSelect = document.getElementById('fw-zone');
  let busy = false;
  let lastData = null;

  const token = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const report = (message, error = false) => {
    feedback.textContent = message;
    feedback.className = `alert alert-${error ? 'danger' : 'secondary'}`;
  };
  const request = async (path = '', method = 'GET', body) => {
    const response = await fetch(base + path, {method, credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token()},
      body: body === undefined ? undefined : JSON.stringify(body)});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error(STR.unreadable); }
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : STR.actionFailed);
    return data;
  };
  const waitOperation = async id => {
    for (let i = 0; i < 30; i++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const row = await request('/operations/' + encodeURIComponent(id));
      if (row.state === 'completed') return row.result || {};
      if (row.state === 'failed') throw new Error(row.result?.error || row.result?.message || STR.actionFailed);
    }
    throw new Error(STR.actionSlow);
  };
  const perform = async (path, body, message) => {
    if (busy) return null;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    try {
      report(message || STR.actionRunning);
      const queued = await request(path, 'POST', body || {});
      const result = queued.id ? await waitOperation(queued.id) : queued;
      await load(false);
      return result;
    } finally {
      busy = false;
      root.removeAttribute('aria-busy');
    }
  };
  const badge = value => {
    const map = {allowed: 'success', blocked: 'danger', partial: 'warning', unknown: 'secondary', inactive: 'secondary', 'local-only': 'info'};
    const span = document.createElement('span');
    span.className = `badge text-bg-${map[value] || 'secondary'}`;
    span.textContent = value;
    return span;
  };
  const portLabel = p => `${p.port_start}${p.port_end !== p.port_start ? '–' + p.port_end : ''}/${p.protocol.toUpperCase()}`;

  const serviceAction = async (service, effect) => {
    const zones = lastData?.snapshot?.active_zones || [];
    let zone = '';
    if (lastData?.snapshot?.backend === 'firewalld' && zones.length > 1) {
      zone = window.prompt(STR.zonePrompt(zones), zones[0]) || '';
      if (!zones.includes(zone)) {
        report(STR.zoneInvalid, true);
        return;
      }
    }
    try {
      const result = await perform('/test', {service: service.id, effect, zone}, STR.testPreparing);
      if (result?.test_id) report(STR.testActive);
    } catch (error) {
      report(error.message, true);
    }
  };

  const renderServices = services => {
    tbody.replaceChildren();
    services.forEach(service => {
      const tr = document.createElement('tr');

      const name = document.createElement('td');
      const strong = document.createElement('strong');
      strong.textContent = service.name;
      const note = document.createElement('div');
      note.className = 'small text-secondary';
      note.textContent = service.note || '';
      name.append(strong, note);

      const state = document.createElement('td');
      state.append(document.createTextNode(`${service.state} · ${service.enabled ? STR.enabled : STR.disabled}`));
      const bind = document.createElement('div');
      bind.className = 'small text-secondary';
      bind.textContent = service.bind || STR.noBind;
      state.appendChild(bind);

      const ports = document.createElement('td');
      (service.port_status || []).forEach(p => {
        const row = document.createElement('div');
        row.className = 'small mb-1';
        row.textContent = `${portLabel(p)} · ${p.listening ? STR.listenerOn : STR.listenerOff}`;
        ports.appendChild(row);
      });

      const fw = document.createElement('td');
      fw.appendChild(badge(service.firewall_state));
      const diagnosis = document.createElement('div');
      diagnosis.className = 'small fw-semibold mt-1';
      diagnosis.textContent = service.diagnosis || '';
      fw.appendChild(diagnosis);
      (service.port_status || []).forEach(p => {
        const detail = document.createElement('div');
        detail.className = 'small text-secondary mt-1';
        detail.textContent = `${portLabel(p)}: ${p.firewall.reason}`;
        fw.appendChild(detail);
      });

      const actions = document.createElement('td');
      actions.className = 'text-nowrap';
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'btn btn-sm btn-outline-success me-2';
      open.textContent = STR.testOpen;
      open.disabled = !lastData?.snapshot?.writable || service.firewall_state === 'local-only';
      open.addEventListener('click', () => serviceAction(service, 'allow'));
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn btn-sm btn-outline-danger';
      close.textContent = STR.testBlock;
      close.disabled = !lastData?.snapshot?.writable || service.critical || service.firewall_state === 'local-only';
      close.addEventListener('click', () => serviceAction(service, 'deny'));
      actions.append(open, close);

      tr.append(name, state, ports, fw, actions);
      tbody.appendChild(tr);
    });
  };

  const renderTests = rows => {
    tests.replaceChildren();
    if (!rows.length) {
      const p = document.createElement('p');
      p.className = 'text-secondary';
      p.textContent = STR.noTests;
      tests.appendChild(p);
      return;
    }
    rows.forEach(test => {
      const col = document.createElement('div');
      col.className = 'col-md-6';
      const card = document.createElement('article');
      card.className = 'card border-warning';
      const body = document.createElement('div');
      body.className = 'card-body';
      const title = document.createElement('h3');
      title.className = 'h5';
      title.textContent = `${test.backend} · Test ${String(test.id).slice(0, 8)} · ${test.status || 'pending'}`;
      const countdown = document.createElement('p');
      countdown.className = 'fw-semibold';
      countdown.dataset.expires = String(test.expires_at || 0);
      countdown.dataset.status = test.status || 'pending';
      const rule = document.createElement('p');
      rule.className = 'small';
      rule.textContent = (test.rules || []).map(portLabel).join(', ');
      const confirm = document.createElement('button');
      confirm.type = 'button';
      confirm.className = 'btn btn-success me-2';
      confirm.textContent = STR.confirmChange;
      confirm.disabled = (test.status || 'pending') !== 'pending';
      confirm.addEventListener('click', async () => {
        try {
          await perform(`/test/${encodeURIComponent(test.id)}/confirm`, {}, STR.confirming);
          report(STR.confirmed);
        } catch (error) {
          report(error.message, true);
        }
      });
      const rollback = document.createElement('button');
      rollback.type = 'button';
      rollback.className = 'btn btn-outline-danger';
      rollback.textContent = STR.rollbackNow;
      rollback.addEventListener('click', async () => {
        try {
          await perform(`/test/${encodeURIComponent(test.id)}/rollback`, {}, STR.rollingBack);
          report(STR.rolledBack);
        } catch (error) {
          report(error.message, true);
        }
      });
      body.append(title, countdown, rule, confirm, rollback);
      card.appendChild(body);
      col.appendChild(card);
      tests.appendChild(col);
    });
    updateCountdowns();
  };

  const updateCountdowns = () => {
    document.querySelectorAll('[data-expires]').forEach(node => {
      const status = node.dataset.status || 'pending';
      if (status === 'rollback_failed') {
        node.textContent = STR.rollbackFailed;
        return;
      }
      if (status === 'confirming') {
        node.textContent = STR.confirmingSafe;
        return;
      }
      if (status === 'preparing') {
        node.textContent = STR.preparingWatchdog;
        return;
      }
      const left = Math.max(0, Math.ceil(Number(node.dataset.expires) - Date.now() / 1000));
      node.textContent = left ? STR.autoRollback(left) : STR.rollbackRunning;
    });
  };

  const render = data => {
    lastData = data;
    const snapshot = data.snapshot || {};
    document.getElementById('fw-backend').textContent = snapshot.backend || STR.unknown;
    document.getElementById('fw-backend-detail').textContent =
      `${snapshot.message || ''} ${STR.cacheAge(snapshot.cache_age_seconds ?? '–')}`;
    const installed = snapshot.installed || {};
    document.getElementById('fw-installed').textContent =
      STR.installed(installed.ufw, installed.firewalld, installed.nftables);
    document.getElementById('fw-zones').textContent = (snapshot.active_zones || []).length
      ? STR.activeZones(snapshot.active_zones.join(', '))
      : STR.noZones;

    const last = data.last_operation;
    const lastResult = last?.result?.state || last?.result?.error || '';
    document.getElementById('fw-last-operation').textContent = last
      ? STR.lastAction(`${last.action} · ${last.state}${lastResult ? ' · ' + lastResult : ''}`)
      : STR.lastActionNone;

    zoneSelect.replaceChildren(new Option(STR.automatic, ''));
    (snapshot.active_zones || []).forEach(zone => zoneSelect.appendChild(new Option(zone, zone)));
    renderTests(snapshot.tests || []);
    renderServices(data.services || []);
    document.getElementById('fw-diagnostic').textContent =
      JSON.stringify({snapshot, listener_tool: data.listener_tool}, null, 2);
  };

  const load = async (refresh = true) => {
    if (refresh) {
      const queued = await request('/refresh', 'POST', {});
      if (queued.id) await waitOperation(queued.id);
    }
    render(await request(''));
  };

  document.getElementById('fw-refresh').addEventListener('click', async () => {
    try {
      await load(true);
      report(STR.reloaded);
    } catch (error) {
      report(error.message, true);
    }
  });

  document.getElementById('fw-manual').addEventListener('submit', async event => {
    event.preventDefault();
    const start = Number(document.getElementById('fw-port-start').value);
    const end = Number(document.getElementById('fw-port-end').value || start);
    const rule = {
      effect: document.getElementById('fw-effect').value,
      protocol: document.getElementById('fw-protocol').value,
      port_start: start,
      port_end: end,
      source: document.getElementById('fw-source').value.trim(),
      label: STR.manualRule
    };
    try {
      const result = await perform('/test', {rules: [rule], zone: zoneSelect.value}, STR.testPreparing);
      if (result?.test_id) report(STR.testActiveShort);
    } catch (error) {
      report(error.message, true);
    }
  });

  load(false).then(() => report(STR.loaded)).catch(error => report(error.message, true));
  setInterval(() => {
    updateCountdowns();
    if (!document.hidden && !busy) load(false).catch(() => {});
  }, 1000);
})();
