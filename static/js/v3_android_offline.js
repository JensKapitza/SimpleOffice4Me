(() => {
  'use strict';

  const text = (german, english) => window.SimpleOfficeI18n?.text(german, english) ?? english;

  const root = document.querySelector('[data-android-offline-app]');
  if (!root || root.dataset.enabled !== '1') return;

  let bridge = window.SimpleOfficeOffline || null;
  const message = root.querySelector('[data-offline-message]');
  const cached = root.querySelector('[data-offline-cached]');
  const queue = root.querySelector('[data-offline-queue]');
  const nativeState = root.querySelector('[data-offline-native]');
  const itemState = root.querySelector('[data-offline-items]');
  const outboxState = root.querySelector('[data-offline-outbox]');
  const bytesState = root.querySelector('[data-offline-bytes]');
  const worksets = root.querySelector('[data-offline-worksets]');
  const syncButton = root.querySelector('[data-offline-sync]');
  const switchButton = root.querySelector('[data-offline-switch-account]');
  const clearButton = root.querySelector('[data-offline-clear]');
  let syncing = false;
  let workspaceRevision = 0;
  const binding = () => ({bridge, revision: workspaceRevision});
  const isCurrent = active =>
    active.bridge === bridge && active.bridge === window.SimpleOfficeOffline && active.revision === workspaceRevision;
  const SYNC_TIMEOUT_MS = 30000;
  const offline = () => document.documentElement.dataset.soNetwork === 'offline' || navigator.onLine === false;
  const updateSyncControls = () => {
    [syncButton, switchButton, clearButton].forEach(button => {
      if (button) button.disabled = !bridge || syncing;
    });
  };


  const show = (text, level = 'info') => {
    if (!message) return;
    message.className = `alert alert-${level}`;
    message.textContent = text;
  };
  const hideMessage = () => {
    if (message) message.className = 'alert alert-info d-none';
  };
  const formatBytes = value => {
    let bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes < 0) bytes = 0;
    const units = ['B', 'KiB', 'MiB', 'GiB'];
    let unit = 0;
    while (bytes >= 1024 && unit < units.length - 1) {
      bytes /= 1024;
      unit += 1;
    }
    return `${bytes.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  };
  const jsonFetch = async (url, options = {}) => {
    const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
    if (options.method && options.method !== 'GET') {
      const token = document.querySelector('meta[name="csrf-token"]');
      if (token && token.content) headers['X-CSRF-Token'] = token.content;
    }
    const response = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...options,
      headers,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  };
  const taskUrl = id => root.dataset.taskApiPrefix + encodeURIComponent(id);
  const serverTask = (id, signal) => jsonFetch(taskUrl(id), {signal});

  const cacheTask = async id => {
    const active = binding();
    const task = await serverTask(id);
    if (!isCurrent(active)) return false;
    const result = active.bridge.cache(
      task.id,
      'task',
      task.version,
      JSON.stringify(task),
      7 * 24 * 60 * 60,
      'tasks'
    );
    if (result !== 'ok') throw new Error(text(`Lokales Speichern fehlgeschlagen: ${result}`, `Could not save locally: ${result}`));
    await render();
    return true;
  };

  const parseCachedTask = metadata => {
    const stored = bridge.read(metadata.id, 'task');
    if (!stored || stored.status !== 'ok') return null;
    try {
      const task = JSON.parse(stored.payload);
      return {metadata, stored, task};
    } catch (_) {
      return null;
    }
  };

  const statusSelect = current => {
    const select = document.createElement('select');
    select.className = 'form-select form-select-sm';
    select.setAttribute('aria-label', text('Aufgabenstatus', 'Task status'));
    [
      ['needs-action', text('Offen', 'Open')],
      ['in-process', text('In Bearbeitung', 'In progress')],
      ['completed', text('Erledigt', 'Completed')],
      ['cancelled', text('Abgebrochen', 'Cancelled')],
    ].forEach(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === current;
      select.append(option);
    });
    return select;
  };

  const renderCached = (items, operations) => {
    if (!cached) return;
    cached.replaceChildren();
    const tasks = items.filter(item => item.kind === 'task');
    if (!tasks.length) {
      const empty = document.createElement('div');
      empty.className = 'list-group-item text-secondary';
      empty.textContent = text('Keine Aufgaben offline gespeichert.', 'No tasks are saved for offline use.');
      cached.append(empty);
      return;
    }

    tasks.forEach(metadata => {
      const entry = parseCachedTask(metadata);
      if (!entry) return;
      const {task} = entry;
      const row = document.createElement('div');
      row.className = 'list-group-item';

      const head = document.createElement('div');
      head.className = 'd-flex justify-content-between gap-2';
      const title = document.createElement('div');
      title.className = 'fw-semibold text-break';
      title.textContent = task.title || task.id;
      const unresolved = operations.some(op =>
        op.targetId === metadata.id && ['pending', 'conflict', 'rejected'].includes(op.status)
      );
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn btn-sm btn-outline-danger';
      remove.textContent = unresolved ? text('Änderung zuerst klären', 'Resolve the change first') : text('Entfernen', 'Remove');
      remove.disabled = unresolved;
      remove.addEventListener('click', async () => {
        if (unresolved) return;
        const result = bridge.remove(metadata.id, 'task');
        if (result !== 'ok') {
          show(text(`Offline-Aufgabe konnte nicht entfernt werden: ${result}`, `Could not remove offline task: ${result}`), 'warning');
          return;
        }
        await render();
      });
      head.append(title, remove);

      const controls = document.createElement('div');
      controls.className = 'd-flex flex-wrap gap-2 align-items-center mt-2';
      const select = statusSelect(task.status || 'needs-action');
      select.style.maxWidth = '13rem';
      const enqueue = document.createElement('button');
      enqueue.type = 'button';
      enqueue.className = 'btn btn-sm btn-outline-primary';
      enqueue.textContent = text('Status offline vormerken', 'Queue status change offline');
      enqueue.disabled = unresolved;
      enqueue.addEventListener('click', async () => {
        hideMessage();
        const operation = bridge.enqueue(
          'task_status',
          metadata.id,
          metadata.version,
          JSON.stringify({status: select.value})
        );
        if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(operation || '')) {
          show(text(`Änderung konnte nicht vorgemerkt werden: ${operation || 'unbekannt'}`, `Could not queue the change: ${operation || 'unknown'}`), 'warning');
          return;
        }
        show(text('Statusänderung lokal vorgemerkt.', 'Status change queued locally.'), 'success');
        await render();
      });
      controls.append(select, enqueue);

      const meta = document.createElement('div');
      meta.className = 'small text-secondary mt-2';
      meta.textContent = text(`Arbeitsbereich ${metadata.workset || 'default'} · Version ${String(metadata.version || '').slice(0, 20)}…`, `Workset ${metadata.workset || 'default'} · Version ${String(metadata.version || '').slice(0, 20)}…`);
      row.append(head, controls, meta);
      cached.append(row);
    });
  };

  const refreshTaskFromServer = async (id, active, signal) => {
    const task = await serverTask(id, signal);
    if (!isCurrent(active) || signal?.aborted) return false;
    if (task.id !== id || typeof task.version !== 'string' || !task.version) {
      throw new Error(text('Serverstand nicht verfügbar.', 'Server version unavailable.'));
    }
    const result = active.bridge.cache(task.id, 'task', task.version, JSON.stringify(task), 7 * 24 * 60 * 60, 'tasks');
    if (result !== 'ok') throw new Error(text(`Serverstand konnte lokal nicht gespeichert werden: ${result}`, `Could not save server state locally: ${result}`));
    return true;
  };
  const acknowledge = (operationId, status) => {
    const result = bridge.ack(operationId, status);
    if (result !== 'ok') throw new Error(text(`Offline-Status konnte nicht gespeichert werden: ${result}`, `Could not save offline status: ${result}`));
  };

  const renderQueue = operations => {
    if (!queue) return;
    queue.replaceChildren();
    if (!operations.length) {
      const empty = document.createElement('div');
      empty.className = 'list-group-item text-secondary';
      empty.textContent = text('Keine offenen Offline-Änderungen.', 'No pending offline changes.');
      queue.append(empty);
      return;
    }

    operations.forEach(op => {
      const row = document.createElement('div');
      row.className = 'list-group-item';
      const label = document.createElement('div');
      label.className = 'd-flex flex-wrap justify-content-between gap-2';
      const description = document.createElement('span');
      description.textContent = text(`Aufgabe ${op.targetId} · Statusänderung`, `Task ${op.targetId} · status change`);
      const badge = document.createElement('span');
      badge.className = `badge ${op.status === 'conflict' ? 'text-bg-warning' : op.status === 'rejected' ? 'text-bg-danger' : 'text-bg-secondary'}`;
      badge.textContent = ({pending: text('Ausstehend', 'Pending'), conflict: text('Konflikt', 'Conflict'), rejected: text('Abgelehnt', 'Rejected')})[op.status] || op.status || text('Ausstehend', 'Pending');
      label.append(description, badge);
      row.append(label);

      if (op.status === 'conflict') {
        const note = document.createElement('p');
        note.className = 'small mt-2 mb-2';
        note.textContent = text('Der Serverstand wurde seit dem Offline-Speichern geändert. Es erfolgt kein automatisches Überschreiben.', 'The server data changed after this item was saved offline. Nothing will be overwritten automatically.');
        const accept = document.createElement('button');
        accept.type = 'button';
        accept.className = 'btn btn-sm btn-outline-primary';
        accept.textContent = text('Serverstand übernehmen', 'Use server version');
        accept.addEventListener('click', async () => {
          try {
            const active = binding();
            if (!await refreshTaskFromServer(op.targetId, active) || !isCurrent(active)) return;
            acknowledge(op.operationId, 'discarded');
            show(text('Konflikt verworfen und aktueller Serverstand geladen.', 'Conflict discarded and current server version loaded.'), 'success');
            await render();
          } catch (error) {
            show(error.message || String(error), 'warning');
          }
        });
        row.append(note, accept);
      } else if (op.status === 'rejected') {
        const discard = document.createElement('button');
        discard.type = 'button';
        discard.className = 'btn btn-sm btn-outline-danger mt-2';
        discard.textContent = text('Verworfene Änderung entfernen', 'Remove rejected change');
        discard.addEventListener('click', async () => {
          try {
            acknowledge(op.operationId, 'discarded');
            await render();
          } catch (error) {
            show(error.message || String(error), 'warning');
          }
        });
        row.append(discard);
      }
      queue.append(row);
    });
  };

  const render = async () => {
    if (!bridge) {
      if (nativeState) nativeState.textContent = text('Nicht verfügbar', 'Unavailable');
      const unavailable = (target, message) => {
        if (!target) return;
        const row = document.createElement('div');
        row.className = 'list-group-item text-secondary';
        row.textContent = message;
        target.replaceChildren(row);
      };
      unavailable(cached, text('Offline-Arbeitsdaten sind nur in der Android-App verfügbar.', 'Offline data is available only in the Android app.'));
      unavailable(queue, text('Kein nativer Offline-Speicher verfügbar.', 'No native offline storage is available.'));
      return;
    }
    if (nativeState) nativeState.textContent = text('Verfügbar', 'Available');
    const status = bridge.status();
    const items = bridge.items();
    const operations = bridge.outbox();
    if (itemState) itemState.textContent = String(status.items || 0);
    if (outboxState) outboxState.textContent = String(status.outbox || 0);
    if (bytesState) bytesState.textContent = `${formatBytes(status.bytes)} / ${formatBytes(status.maxBytes)}`;
    if (worksets) {
      worksets.replaceChildren();
      (status.worksets || []).forEach(group => {
        const badge = document.createElement('span');
        badge.className = 'badge text-bg-light border me-2 mb-1';
        badge.textContent = `${group.name}: ${group.items} · ${formatBytes(group.bytes)}`;
        worksets.append(badge);
      });
    }
    renderCached(items, operations);
    renderQueue(operations);
  };

  function matchedResults(results, pending) {
    const byId = new Map(pending.map(op => [op.operationId, op]));
    const seen = new Map();
    for (const result of Array.isArray(results) ? results : []) {
      const op = byId.get(result?.operationId);
      if (!op) continue;
      if (seen.has(op.operationId) || result.targetId !== op.targetId || result.baseVersion !== op.baseVersion ||
          !['synced', 'conflict', 'rejected'].includes(result.status)) {
        seen.set(op.operationId, null);
      } else {
        seen.set(op.operationId, result);
      }
    }
    return Array.from(seen.values()).filter(Boolean);
  }

  const syncOutbox = async (manual = false) => {
    if (syncing || !bridge) return;
    if (offline()) {
      if (manual) show(text('Offline. Änderungen bleiben im Ausgang.', 'Offline. Changes stay in the outbox.'), 'warning');
      return;
    }
    syncing = true;
    const active = binding();
    const controller = new AbortController();
    const deadline = window.setTimeout(() => controller.abort(), SYNC_TIMEOUT_MS);
    updateSyncControls();
    try {
      const pending = active.bridge.outbox().filter(op => (op.status || 'pending') === 'pending');
      if (!pending.length) {
        if (manual) show(text('Keine Änderungen zur Synchronisation ausstehend.', 'No changes are waiting to synchronize.'));
        return;
      }
      show(text('Offline-Änderungen werden synchronisiert …', 'Synchronizing offline changes …'));
      const payload = await jsonFetch(root.dataset.syncApi, {
        method: 'POST',
        signal: controller.signal,
        body: JSON.stringify({
          operations: pending.map(op => ({
            operationId: op.operationId,
            mutationType: op.type,
            targetId: op.targetId,
            baseVersion: op.baseVersion,
            payload: op.payload,
          })),
        }),
      });
      const counts = {synced: 0, conflict: 0, rejected: 0};
      for (const result of matchedResults(payload.results, pending)) {
        if (!isCurrent(active)) return;
        if (controller.signal.aborted) break;
        try {
          if (result.status === 'synced' && !await refreshTaskFromServer(result.targetId, active, controller.signal)) continue;
          if (!isCurrent(active)) return;
          if (controller.signal.aborted) break;
          acknowledge(result.operationId, result.status);
          counts[result.status]++;
        } catch (_) {
          // A missing reply, refresh or local acknowledgement is not a completed operation.
          // Leave it pending; the original operation ID remains safe to retry later.
        }
      }
      if (!isCurrent(active)) return;
      const pendingCount = active.bridge.outbox().filter(op => (op.status || 'pending') === 'pending').length;
      const {synced, conflict, rejected} = counts;
      const status = text(
        `Synchronisiert: ${synced} · Konflikte: ${conflict} · Abgelehnt: ${rejected} · Weiter ausstehend: ${pendingCount}`,
        `Synced: ${synced} · Conflicts: ${conflict} · Rejected: ${rejected} · Still pending: ${pendingCount}`
      );
      show(status, conflict || rejected || pendingCount ? 'warning' : 'success');
    } catch (_) {
      if (isCurrent(active)) {
        show(text('Synchronisation nicht möglich. Änderungen bleiben im Ausgang.',
          'Could not synchronize. Changes stay in the outbox.'), 'warning');
      }
    } finally {
      window.clearTimeout(deadline);
      syncing = false;
      updateSyncControls();
      await render();
    }
  };

  root.querySelectorAll('[data-offline-cache-task]').forEach(button => {
    button.addEventListener('click', async () => {
      if (!bridge) {
        show(text('Offline-Speichern ist nur in der Android-App verfügbar.', 'Offline saving is available only in the Android app.'), 'warning');
        return;
      }
      try {
        hideMessage();
        if (!await cacheTask(button.dataset.offlineCacheTask)) return;
        show(text('Aufgabe wurde für die Offline-Nutzung gespeichert.', 'Task saved for offline use.'), 'success');
      } catch (error) {
        show(error.message || String(error), 'warning');
      }
    });
  });

  if (syncButton) syncButton.addEventListener('click', () => syncOutbox(true));
  if (switchButton) {
    switchButton.addEventListener('click', () => {
      if (!bridge || syncing) return;
      if (!window.confirm(text('Android-Konto wechseln? Lokale Offline-Daten und vorgemerkte Änderungen werden vorher gelöscht.', 'Switch Android account? Local offline data and queued changes will be deleted first.'))) return;
      workspaceRevision++;
      const result = bridge.switchAccount();
      if (result !== 'ok') show(text(`Kontowechsel nicht möglich: ${result}`, `Could not switch account: ${result}`), 'warning');
    });
  }
  if (clearButton) clearButton.addEventListener('click', async () => {
    if (!bridge || syncing) return;
    if (!window.confirm(text('Alle lokalen Offline-Arbeitsdaten und vorgemerkten Änderungen löschen?', 'Delete all local offline data and queued changes?'))) return;
    workspaceRevision++;
    const result = bridge.clear();
    show(result === 'ok' ? text('Lokale Offline-Daten wurden gelöscht.', 'Local offline data was deleted.') : text(`Löschen fehlgeschlagen: ${result}`, `Could not delete data: ${result}`), result === 'ok' ? 'success' : 'warning');
    await render();
  });

  window.addEventListener('simpleoffice:native-ready', () => {
    const next = window.SimpleOfficeOffline || null;
    if (bridge !== next) hideMessage();
    bridge = next;
    updateSyncControls();
    render().then(() => syncOutbox());
  });
  window.addEventListener('simpleoffice:network', event => {
    if (event.detail && event.detail.online) syncOutbox();
  });
  window.addEventListener('online', () => syncOutbox());

  updateSyncControls();
  render().then(() => syncOutbox());
})();
