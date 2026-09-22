(() => {
  'use strict';
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
    try { data = await response.json(); } catch (_) { throw new Error('Firewall-Antwort ist nicht lesbar.'); }
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : 'Firewall-Aktion fehlgeschlagen.');
    return data;
  };
  const waitOperation = async id => {
    for (let i = 0; i < 30; i++) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const row = await request('/operations/' + encodeURIComponent(id));
      if (row.state === 'completed') return row.result || {};
      if (row.state === 'failed') throw new Error(row.result?.error || row.result?.message || 'Firewall-Aktion fehlgeschlagen.');
    }
    throw new Error('Firewall-Aktion läuft länger als erwartet. Status erneut einlesen.');
  };
  const perform = async (path, body, message) => {
    if (busy) return null;
    busy = true;
    root.setAttribute('aria-busy', 'true');
    try {
      report(message || 'Firewall-Aktion läuft …');
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
      zone = window.prompt('Mehrere firewalld-Zonen sind aktiv. Zielzone eingeben:\n' + zones.join(', '), zones[0]) || '';
      if (!zones.includes(zone)) {
        report('Ungültige oder abgebrochene Zonenauswahl.', true);
        return;
      }
    }
    try {
      const result = await perform('/test', {service: service.id, effect, zone}, '20-Sekunden-Firewalltest wird vorbereitet …');
      if (result?.test_id) report('Test aktiv. Verbindung prüfen und innerhalb von 20 Sekunden bestätigen.');
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
      state.append(document.createTextNode(`${service.state} · ${service.enabled ? 'aktiviert' : 'deaktiviert'}`));
      const bind = document.createElement('div');
      bind.className = 'small text-secondary';
      bind.textContent = service.bind || 'keine feste Bind-Adresse';
      state.appendChild(bind);

      const ports = document.createElement('td');
      (service.port_status || []).forEach(p => {
        const row = document.createElement('div');
        row.className = 'small mb-1';
        row.textContent = `${portLabel(p)} · ${p.listening ? 'Listener aktiv' : 'kein Listener'}`;
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
      open.textContent = 'Öffnen testen';
      open.disabled = !lastData?.snapshot?.writable || service.firewall_state === 'local-only';
      open.addEventListener('click', () => serviceAction(service, 'allow'));
      const close = document.createElement('button');
      close.type = 'button';
      close.className = 'btn btn-sm btn-outline-danger';
      close.textContent = 'Sperren testen';
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
      p.textContent = 'Keine laufenden Tests.';
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
      title.textContent = `${test.backend} · Test ${String(test.id).slice(0, 8)}`;
      const countdown = document.createElement('p');
      countdown.className = 'fw-semibold';
      countdown.dataset.expires = String(test.expires_at || 0);
      const rule = document.createElement('p');
      rule.className = 'small';
      rule.textContent = (test.rules || []).map(portLabel).join(', ');
      const confirm = document.createElement('button');
      confirm.type = 'button';
      confirm.className = 'btn btn-success me-2';
      confirm.textContent = 'Änderung bestätigen';
      confirm.addEventListener('click', async () => {
        try {
          await perform(`/test/${encodeURIComponent(test.id)}/confirm`, {}, 'Firewalländerung wird bestätigt …');
          report('Firewalländerung dauerhaft bestätigt.');
        } catch (error) {
          report(error.message, true);
        }
      });
      const rollback = document.createElement('button');
      rollback.type = 'button';
      rollback.className = 'btn btn-outline-danger';
      rollback.textContent = 'Jetzt zurückrollen';
      rollback.addEventListener('click', async () => {
        try {
          await perform(`/test/${encodeURIComponent(test.id)}/rollback`, {}, 'Firewalländerung wird zurückgerollt …');
          report('Firewalltest zurückgerollt.');
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
      const left = Math.max(0, Math.ceil(Number(node.dataset.expires) - Date.now() / 1000));
      node.textContent = left ? `Automatischer Rollback in ${left} s` : 'Rollback läuft / Status aktualisieren';
    });
  };

  const render = data => {
    lastData = data;
    const snapshot = data.snapshot || {};
    document.getElementById('fw-backend').textContent = snapshot.backend || 'unbekannt';
    document.getElementById('fw-backend-detail').textContent =
      `${snapshot.message || ''} Cache-Alter: ${snapshot.cache_age_seconds ?? '–'} s`;
    const installed = snapshot.installed || {};
    document.getElementById('fw-installed').textContent =
      `UFW: ${installed.ufw ? 'ja' : 'nein'} · firewalld: ${installed.firewalld ? 'ja' : 'nein'} · nftables: ${installed.nftables ? 'ja' : 'nein'}`;
    document.getElementById('fw-zones').textContent = (snapshot.active_zones || []).length
      ? `Aktive Zonen: ${snapshot.active_zones.join(', ')}`
      : 'Keine firewalld-Zone gemeldet.';

    zoneSelect.replaceChildren(new Option('automatisch', ''));
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
      report('Firewallstatus neu eingelesen.');
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
      label: 'Manuelle Regel'
    };
    try {
      const result = await perform('/test', {rules: [rule], zone: zoneSelect.value}, '20-Sekunden-Firewalltest wird vorbereitet …');
      if (result?.test_id) report('Test aktiv. Innerhalb von 20 Sekunden bestätigen.');
    } catch (error) {
      report(error.message, true);
    }
  });

  load(false).then(() => report('Firewallstatus geladen.')).catch(error => report(error.message, true));
  setInterval(() => {
    updateCountdowns();
    if (!document.hidden && !busy) load(false).catch(() => {});
  }, 1000);
})();
