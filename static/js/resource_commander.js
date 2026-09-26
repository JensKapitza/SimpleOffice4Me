(() => {
  'use strict';

  const state = {
    active: 'left',
    left: {smart: false, selected: null, capabilities: null},
    right: {smart: false, selected: null, capabilities: null},
    providers: [],
    compare: false,
    compareBusy: false,
    compareMode: 'metadata',
    comparePending: false,
    lastComparison: null,
  };

  const pane = side => document.getElementById(side);
  const other = side => side === 'left' ? 'right' : 'left';
  const providerId = side => pane(side).querySelector('.provider').value || 'self';
  const statusNode = side => pane(side).querySelector('.resource-commander-status');
  const providerDescriptor = side => state.providers.find(
    provider => provider.provider_id === providerId(side)
  ) || null;
  const capabilities = side => state[side].capabilities
    || providerDescriptor(side)?.capabilities
    || {};

  function humanSize(value) {
    const n = Number(value || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1048576) return `${(n / 1024).toFixed(1)} KiB`;
    if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MiB`;
    return `${(n / 1073741824).toFixed(1)} GiB`;
  }

  async function json(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    const payload = await response.json().catch(() => ({error: response.statusText}));
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }

  function post(url, payload) {
    return json(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
  }

  function setStatus(side, message) {
    statusNode(side).textContent = message || '';
  }

  function addContextBadge(target, text, className = '') {
    const badge = document.createElement('span');
    badge.className = ['resource-commander-context-badge', className].filter(Boolean).join(' ');
    badge.textContent = text;
    target.append(badge);
  }

  function updateProviderContext(side) {
    const descriptor = providerDescriptor(side);
    const detail = pane(side).querySelector('.resource-commander-context-detail');
    detail.replaceChildren();
    if (!descriptor) {
      addContextBadge(detail, 'Provider unbekannt', 'is-warning');
      return;
    }

    const caps = capabilities(side);
    if (descriptor.kind === 'federation') {
      const federation = descriptor.federation || {};
      addContextBadge(detail, `Federation · ${descriptor.label}`, 'is-federation');
      if (federation.peer_id) addContextBadge(detail, federation.peer_id);
      addContextBadge(
        detail,
        federation.verification_state || 'KNOWN_UNVERIFIED',
        (federation.verification_state || 'KNOWN_UNVERIFIED') === 'KNOWN_UNVERIFIED' ? 'is-warning' : ''
      );
      addContextBadge(
        detail,
        `Trust ${federation.trust_level || 'NONE'}`,
        (federation.trust_level || 'NONE') === 'NONE' ? 'is-warning' : ''
      );
      if (federation.has_error) addContextBadge(detail, 'letzter Peer-Fehler', 'is-error');
    } else {
      addContextBadge(detail, descriptor.label || descriptor.provider_id);
    }

    const rights = [];
    if (caps.read) rights.push('lesen');
    if (caps.write) rights.push('schreiben');
    if (caps.search) rights.push('suchen');
    if (caps.delete) rights.push('löschen');
    addContextBadge(detail, rights.length ? rights.join(' · ') : 'keine Dateiaktionen', rights.length ? '' : 'is-warning');
  }

  function setActive(side) {
    state.active = side;
    for (const candidate of ['left', 'right']) {
      pane(candidate).classList.toggle('is-active', candidate === side);
    }
    updateActions();
  }

  function updateActions() {
    const from = state.active;
    const to = other(from);
    const source = capabilities(from);
    const target = capabilities(to);
    const sameProvider = providerId(from) === providerId(to) && !state[from].smart;
    const arrow = from === 'left' ? '→' : '←';

    const copyButton = document.getElementById('copy');
    copyButton.disabled = !(source.read && source.copy && target.write);
    copyButton.textContent = `F5 Kopieren ${arrow}`;
    copyButton.title = copyButton.disabled
      ? 'Quelle muss lesbar und Ziel schreibbar sein'
      : 'Ausgewählte Datei zum anderen Provider kopieren';

    const moveButton = document.getElementById('move');
    moveButton.disabled = sameProvider
      ? !source.move
      : !(source.read && source.copy && target.write);
    moveButton.textContent = sameProvider
      ? `F6 Verschieben ${arrow}`
      : `F6 Sichere Kopie ${arrow}`;
    moveButton.title = sameProvider
      ? 'Innerhalb desselben Providers verschieben'
      : 'Provider-übergreifend wird aus Sicherheitsgründen nur kopiert; die Quelle bleibt erhalten';

    document.getElementById('mkdir').disabled = !(source.write && source.folders);
    document.getElementById('delete').disabled = !source.delete;

    const bothReadable = Boolean(capabilities('left').read && capabilities('right').read);
    document.getElementById('compare').disabled = !bothReadable;
    document.getElementById('compare-mode').disabled = !bothReadable;
    document.getElementById('complete').disabled = !bothReadable;
  }

  function currentPath(side) {
    return pane(side).querySelector('.path').value.trim().replace(/^\/+|\/+$/g, '');
  }

  function setPath(side, value) {
    pane(side).querySelector('.path').value = value || '';
  }

  function makeRow(side, entry) {
    const row = document.createElement('div');
    row.className = 'resource-commander-row';
    row.dataset.id = entry.resource_id;
    row.dataset.kind = entry.kind;
    row.dataset.name = String(entry.name || '').toLocaleLowerCase();

    const icon = document.createElement('span');
    icon.textContent = entry.kind === 'folder' ? '📁' : '📄';
    const cmp = document.createElement('span');
    cmp.className = 'resource-commander-cmp';
    const name = document.createElement('span');
    name.className = 'resource-commander-name';
    name.textContent = entry.name || '';
    name.title = entry.path || '';
    const size = document.createElement('span');
    size.className = 'resource-commander-size';
    size.textContent = entry.kind === 'folder' ? '' : humanSize(entry.size);
    const modified = document.createElement('span');
    modified.className = 'resource-commander-modified';
    modified.textContent = entry.modified || '';
    row.append(icon, cmp, name, size, modified);

    row.addEventListener('click', () => {
      pane(side).querySelectorAll('.selected').forEach(item => item.classList.remove('selected'));
      row.classList.add('selected');
      state[side].selected = entry;
      setActive(side);
    });

    row.addEventListener('dblclick', () => {
      if (entry.kind === 'folder') {
        const base = currentPath(side);
        const child = String(entry.path || entry.name || '').split('/').filter(Boolean).pop() || '';
        setPath(side, [base, child].filter(Boolean).join('/'));
        load(side);
        return;
      }
      const params = new URLSearchParams({
        provider: providerId(side),
        id: entry.resource_id,
        smart: state[side].smart ? '1' : '0',
      });
      window.open(`/resource-commander/api/download?${params}`, '_blank', 'noopener,noreferrer');
    });
    return row;
  }

  function render(side, entries) {
    const box = pane(side).querySelector('.resource-commander-list');
    box.replaceChildren();
    state[side].selected = null;
    for (const entry of entries) box.append(makeRow(side, entry));
    setStatus(side, `${entries.length} Einträge`);
  }

  function clearMarks() {
    for (const side of ['left', 'right']) {
      pane(side).querySelectorAll('.resource-commander-row').forEach(row => {
        row.classList.remove('identical', 'probably_identical', 'different', 'left_only', 'right_only');
        row.querySelector('.resource-commander-cmp').textContent = '';
      });
    }
  }

  function mark(side, name, status, symbol, title = '') {
    const wanted = String(name || '').toLocaleLowerCase();
    const row = [...pane(side).querySelectorAll('.resource-commander-row')]
      .find(item => item.dataset.name === wanted);
    if (!row) return;
    row.classList.add(status);
    const cmp = row.querySelector('.resource-commander-cmp');
    cmp.textContent = symbol;
    cmp.title = title;
  }

  function applyComparison(data) {
    clearMarks();
    state.lastComparison = data;
    for (const row of data.rows || []) {
      const name = row.name;
      const status = row.status;
      if (status === 'identical') {
        mark('left', name, status, '=', 'Sicher identisch');
        mark('right', name, status, '=', 'Sicher identisch');
      } else if (status === 'probably_identical') {
        const title = row.confidence === 'metadata'
          ? 'Metadaten passen; Inhalt nicht gelesen'
          : 'Teilprüfung stimmt';
        mark('left', name, status, '~', title);
        mark('right', name, status, '~', title);
      } else if (status === 'different') {
        mark('left', name, status, '≠', 'Inhalt, Größe oder Typ unterschiedlich');
        mark('right', name, status, '≠', 'Inhalt, Größe oder Typ unterschiedlich');
      } else if (status === 'left_only') {
        mark('left', name, status, '→', 'Fehlt rechts');
      } else if (status === 'right_only') {
        mark('right', name, status, '←', 'Fehlt links');
      }
    }
    const totals = data.totals || {};
    const mode = data.mode || state.compareMode;
    setStatus('left', `Diff ${mode}: =${totals.identical || 0} ~${totals.probably_identical || 0} ≠${totals.different || 0} →${totals.left_only || 0}`);
    setStatus('right', `Diff ${mode}: =${totals.identical || 0} ~${totals.probably_identical || 0} ≠${totals.different || 0} ←${totals.right_only || 0}`);
  }

  function comparePayload() {
    return {
      left_provider: providerId('left'),
      right_provider: providerId('right'),
      left_smart: state.left.smart,
      right_smart: state.right.smart,
      left_path: currentPath('left'),
      right_path: currentPath('right'),
    };
  }

  async function runCompare() {
    if (!state.compare) return;
    if (state.compareBusy) {
      state.comparePending = true;
      return;
    }
    state.compareBusy = true;
    try {
      const data = await post('/resource-commander/api/compare-directory', {
        ...comparePayload(),
        mode: state.compareMode,
      });
      applyComparison(data);
    } catch (error) {
      setStatus('left', `Diff: ${error.message}`);
    } finally {
      state.compareBusy = false;
      if (state.comparePending) {
        state.comparePending = false;
        queueMicrotask(runCompare);
      }
    }
  }

  async function load(side) {
    const params = new URLSearchParams({
      provider: providerId(side),
      path: currentPath(side),
      smart: state[side].smart && providerId(side) === 'self' ? '1' : '0',
    });
    try {
      setStatus(side, 'Lade …');
      const data = await json(`/resource-commander/api/list?${params}`);
      state[side].capabilities = data.capabilities || null;
      render(side, data.entries || []);
      updateProviderContext(side);
      updateActions();
      if (state.compare) queueMicrotask(runCompare);
    } catch (error) {
      setStatus(side, error.message);
    }
  }

  async function search(side) {
    const query = pane(side).querySelector('.search').value.trim();
    if (!query) return load(side);
    const params = new URLSearchParams({
      provider: providerId(side),
      path: currentPath(side),
      q: query,
      smart: state[side].smart ? '1' : '0',
    });
    try {
      const data = await json(`/resource-commander/api/search?${params}`);
      state[side].capabilities = data.capabilities || state[side].capabilities;
      render(side, data.entries || []);
      updateProviderContext(side);
      updateActions();
    } catch (error) {
      setStatus(side, error.message);
    }
  }

  async function copy(from, to) {
    const entry = state[from].selected;
    if (!entry) return setStatus(from, 'Keine Ressource ausgewählt');
    if (entry.kind !== 'file') return setStatus(from, 'Ordnerkopie: Dateien einzeln auswählen');
    try {
      setStatus(from, 'Kopiere …');
      await post('/resource-commander/api/copy', {
        source_provider: providerId(from),
        source_smart: state[from].smart,
        id: entry.resource_id,
        target_provider: providerId(to),
        target_path: currentPath(to),
        name: entry.name,
      });
      await load(to);
      setStatus(from, 'Kopiert');
    } catch (error) {
      setStatus(from, error.message);
    }
  }

  async function move(from, to) {
    const entry = state[from].selected;
    if (!entry) return setStatus(from, 'Keine Ressource ausgewählt');
    if (providerId(from) !== providerId(to) || state[from].smart) {
      await copy(from, to);
      return setStatus(from, 'Provider-übergreifend sicher kopiert; Quelle bleibt erhalten.');
    }
    try {
      await post('/resource-commander/api/move', {
        provider: providerId(from),
        id: entry.resource_id,
        path: currentPath(to),
        name: entry.name,
      });
      await Promise.all([load(from), load(to)]);
    } catch (error) {
      setStatus(from, error.message);
    }
  }

  async function mkdir(side) {
    const name = window.prompt('Ordnername');
    if (!name) return;
    try {
      await post('/resource-commander/api/mkdir', {
        provider: providerId(side),
        path: currentPath(side),
        name,
      });
      await load(side);
    } catch (error) {
      setStatus(side, error.message);
    }
  }

  async function remove(side) {
    const entry = state[side].selected;
    if (!entry || !window.confirm(`${entry.name} löschen?`)) return;
    try {
      await post('/resource-commander/api/delete', {
        provider: providerId(side),
        id: entry.resource_id,
      });
      await load(side);
    } catch (error) {
      setStatus(side, error.message);
    }
  }

  async function completeness() {
    const from = state.active;
    let payload = comparePayload();
    if (from === 'right') {
      payload = {
        left_provider: payload.right_provider,
        right_provider: payload.left_provider,
        left_smart: payload.right_smart,
        right_smart: payload.left_smart,
        left_path: payload.right_path,
        right_path: payload.left_path,
      };
    }
    setStatus(from, 'Vollständigkeitsprüfung läuft …');
    try {
      const data = await post('/resource-commander/api/completeness', payload);
      const missing = (data.required_missing || []).length;
      const uncertain = (data.required_uncertain || []).length;
      setStatus(from, data.complete
        ? `Hab alles: ${data.checked_files || 0} Dateien vollständig geprüft. Zusätzliche Zieldateien erlaubt.`
        : `Nicht vollständig: ${missing} fehlen/abweichend, ${uncertain} unklar.`);
    } catch (error) {
      setStatus(from, error.message);
    }
  }

  function updateSmart(side) {
    const button = pane(side).querySelector('.resource-commander-smart');
    button.classList.toggle('on', providerId(side) === 'self');
    button.textContent = state[side].smart ? 'SmartView ✓' : 'SmartView';
  }

  function bind(side) {
    const element = pane(side);
    element.addEventListener('mousedown', () => setActive(side));
    element.querySelector('.provider').addEventListener('change', () => {
      state[side].smart = false;
      state[side].capabilities = null;
      updateSmart(side);
      updateProviderContext(side);
      updateActions();
      setPath(side, '');
      load(side);
    });
    element.querySelector('.resource-commander-smart').addEventListener('click', () => {
      state[side].smart = !state[side].smart;
      state[side].capabilities = null;
      setPath(side, '');
      updateSmart(side);
      updateProviderContext(side);
      updateActions();
      load(side);
    });
    element.querySelector('.reload').addEventListener('click', () => load(side));
    element.querySelector('.up').addEventListener('click', () => {
      const parts = currentPath(side).split('/').filter(Boolean);
      parts.pop();
      setPath(side, parts.join('/'));
      load(side);
    });
    element.querySelector('.path').addEventListener('keydown', event => {
      if (event.key === 'Enter') load(side);
    });
    element.querySelector('.do-search').addEventListener('click', () => search(side));
    element.querySelector('.search').addEventListener('keydown', event => {
      if (event.key === 'Enter') search(side);
    });
  }

  async function init() {
    const data = await json('/resource-commander/api/providers');
    state.providers = data.providers || [];
    for (const side of ['left', 'right']) {
      const select = pane(side).querySelector('.provider');
      for (const provider of state.providers) {
        const option = document.createElement('option');
        option.value = provider.provider_id;
        option.textContent = provider.kind === 'federation'
          ? `Federation: ${provider.label}`
          : provider.label;
        select.append(option);
      }
      bind(side);
      updateSmart(side);
      updateProviderContext(side);
    }
    const federationIndex = state.providers.findIndex(provider => provider.kind === 'federation');
    if (federationIndex >= 0) {
      pane('right').querySelector('.provider').selectedIndex = federationIndex;
    } else if (state.providers.length > 1) {
      pane('right').querySelector('.provider').selectedIndex = 1;
    }
    updateProviderContext('right');
    setActive('left');
    await Promise.all([load('left'), load('right')]);
  }

  document.getElementById('copy').addEventListener('click', () => copy(state.active, other(state.active)));
  document.getElementById('move').addEventListener('click', () => move(state.active, other(state.active)));
  document.getElementById('mkdir').addEventListener('click', () => mkdir(state.active));
  document.getElementById('delete').addEventListener('click', () => remove(state.active));
  document.getElementById('compare').addEventListener('click', () => {
    state.compare = !state.compare;
    const button = document.getElementById('compare');
    button.classList.toggle('resource-commander-active-toggle', state.compare);
    button.textContent = state.compare ? 'Diff aktiv' : 'Diff einschalten';
    if (state.compare) runCompare(); else clearMarks();
  });
  document.getElementById('compare-mode').addEventListener('change', event => {
    state.compareMode = event.target.value;
    if (state.compare) runCompare();
  });
  document.getElementById('complete').addEventListener('click', completeness);
  document.getElementById('swap').addEventListener('click', () => {
    const leftProvider = providerId('left');
    const rightProvider = providerId('right');
    const leftPath = currentPath('left');
    const rightPath = currentPath('right');
    pane('left').querySelector('.provider').value = rightProvider;
    pane('right').querySelector('.provider').value = leftProvider;
    setPath('left', rightPath);
    setPath('right', leftPath);
    state.left.smart = false;
    state.right.smart = false;
    state.left.capabilities = null;
    state.right.capabilities = null;
    updateSmart('left');
    updateSmart('right');
    updateProviderContext('left');
    updateProviderContext('right');
    updateActions();
    load('left');
    load('right');
  });

  document.addEventListener('keydown', event => {
    const mapping = {F5: 'copy', F6: 'move', F7: 'mkdir', F8: 'delete'};
    const button = mapping[event.key];
    if (!button) return;
    event.preventDefault();
    document.getElementById(button).click();
  });

  init().catch(error => setStatus('left', error.message));
})();
