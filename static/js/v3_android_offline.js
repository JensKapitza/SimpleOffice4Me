(() => {
  'use strict';

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
  const serverTask = id => jsonFetch(taskUrl(id));

  const cacheTask = async id => {
    const task = await serverTask(id);
    const result = bridge.cache(
      task.id,
      'task',
      task.version,
      JSON.stringify(task),
      7 * 24 * 60 * 60,
      'tasks'
    );
    if (result !== 'ok') throw new Error(`Lokales Speichern fehlgeschlagen: ${result}`);
    await render();
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
    select.setAttribute('aria-label', 'Aufgabenstatus');
    [
      ['needs-action', 'Offen'],
      ['in-process', 'In Bearbeitung'],
      ['completed', 'Erledigt'],
      ['cancelled', 'Abgebrochen'],
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
      empty.textContent = 'Keine Aufgaben offline gespeichert.';
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
      remove.textContent = unresolved ? 'Änderung zuerst klären' : 'Entfernen';
      remove.disabled = unresolved;
      remove.addEventListener('click', async () => {
        if (unresolved) return;
        const result = bridge.remove(metadata.id, 'task');
        if (result !== 'ok') {
          show(`Offline-Aufgabe konnte nicht entfernt werden: ${result}`, 'warning');
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
      enqueue.textContent = 'Status offline vormerken';
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
          show(`Änderung konnte nicht vorgemerkt werden: ${operation || 'unbekannt'}`, 'warning');
          return;
        }
        show('Statusänderung lokal vorgemerkt.', 'success');
        await render();
      });
      controls.append(select, enqueue);

      const meta = document.createElement('div');
      meta.className = 'small text-secondary mt-2';
      meta.textContent = `Workset ${metadata.workset || 'default'} · Version ${String(metadata.version || '').slice(0, 20)}…`;
      row.append(head, controls, meta);
      cached.append(row);
    });
  };

  const refreshTaskFromServer = async id => {
    const task = await serverTask(id);
    const result = bridge.cache(task.id, 'task', task.version, JSON.stringify(task), 7 * 24 * 60 * 60, 'tasks');
    if (result !== 'ok') throw new Error(`Serverstand konnte lokal nicht gespeichert werden: ${result}`);
  };
  const acknowledge = (operationId, status) => {
    const result = bridge.ack(operationId, status);
    if (result !== 'ok') throw new Error(`Offline-Status konnte nicht gespeichert werden: ${result}`);
  };

  const renderQueue = operations => {
    if (!queue) return;
    queue.replaceChildren();
    if (!operations.length) {
      const empty = document.createElement('div');
      empty.className = 'list-group-item text-secondary';
      empty.textContent = 'Keine offenen Offline-Änderungen.';
      queue.append(empty);
      return;
    }

    operations.forEach(op => {
      const row = document.createElement('div');
      row.className = 'list-group-item';
      const label = document.createElement('div');
      label.className = 'd-flex flex-wrap justify-content-between gap-2';
      const description = document.createElement('span');
      description.textContent = `Aufgabe ${op.targetId} · Statusänderung`;
      const badge = document.createElement('span');
      badge.className = `badge ${op.status === 'conflict' ? 'text-bg-warning' : op.status === 'rejected' ? 'text-bg-danger' : 'text-bg-secondary'}`;
      badge.textContent = op.status || 'pending';
      label.append(description, badge);
      row.append(label);

      if (op.status === 'conflict') {
        const note = document.createElement('p');
        note.className = 'small mt-2 mb-2';
        note.textContent = 'Der Serverstand wurde seit dem Offline-Speichern geändert. Es erfolgt kein automatisches Überschreiben.';
        const accept = document.createElement('button');
        accept.type = 'button';
        accept.className = 'btn btn-sm btn-outline-primary';
        accept.textContent = 'Serverstand übernehmen';
        accept.addEventListener('click', async () => {
          try {
            await refreshTaskFromServer(op.targetId);
            acknowledge(op.operationId, 'discarded');
            show('Konflikt verworfen und aktueller Serverstand geladen.', 'success');
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
        discard.textContent = 'Verworfene Änderung entfernen';
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
      if (nativeState) nativeState.textContent = 'Nicht verfügbar';
      const unavailable = (target, message) => {
        if (!target) return;
        const row = document.createElement('div');
        row.className = 'list-group-item text-secondary';
        row.textContent = message;
        target.replaceChildren(row);
      };
      unavailable(cached, 'Offline-Arbeitsdaten sind nur in der Android-App verfügbar.');
      unavailable(queue, 'Kein nativer Offline-Speicher verfügbar.');
      return;
    }
    if (nativeState) nativeState.textContent = 'Verfügbar';
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

  const syncOutbox = async () => {
    if (!bridge) return;
    const pending = bridge.outbox().filter(op => (op.status || 'pending') === 'pending');
    if (!pending.length) {
      await render();
      return;
    }
    try {
      const payload = await jsonFetch(root.dataset.syncApi, {
        method: 'POST',
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
      for (const result of payload.results || []) {
        if (result.status === 'synced') {
          try {
            await refreshTaskFromServer(result.targetId);
          } catch (_) {
            continue;
          }
          acknowledge(result.operationId, 'synced');
        } else if (result.status === 'conflict') {
          acknowledge(result.operationId, 'conflict');
        } else {
          acknowledge(result.operationId, 'rejected');
        }
      }
      show('Offline-Änderungen wurden mit dem Server abgeglichen.', 'success');
    } catch (error) {
      show(`Synchronisation nicht möglich: ${error.message || error}`, 'warning');
    }
    await render();
  };

  root.querySelectorAll('[data-offline-cache-task]').forEach(button => {
    button.addEventListener('click', async () => {
      if (!bridge) {
        show('Offline-Speichern ist nur in der Android-App verfügbar.', 'warning');
        return;
      }
      try {
        hideMessage();
        await cacheTask(button.dataset.offlineCacheTask);
        show('Aufgabe wurde für die Offline-Nutzung gespeichert.', 'success');
      } catch (error) {
        show(error.message || String(error), 'warning');
      }
    });
  });

  const syncButton = root.querySelector('[data-offline-sync]');
  if (syncButton) syncButton.addEventListener('click', syncOutbox);
  const switchButton = root.querySelector('[data-offline-switch-account]');
  if (switchButton) {
    switchButton.disabled = !bridge;
    switchButton.addEventListener('click', () => {
      if (!bridge) return;
      if (!window.confirm('Android-Konto wechseln? Lokale Offline-Daten und vorgemerkte Änderungen werden vorher gelöscht.')) return;
      const result = bridge.switchAccount();
      if (result !== 'ok') show(`Kontowechsel nicht möglich: ${result}`, 'warning');
    });
  }
  const clearButton = root.querySelector('[data-offline-clear]');
  if (clearButton) clearButton.addEventListener('click', async () => {
    if (!bridge) return;
    if (!window.confirm('Alle lokalen Offline-Arbeitsdaten und vorgemerkten Änderungen löschen?')) return;
    const result = bridge.clear();
    show(result === 'ok' ? 'Lokale Offline-Daten wurden gelöscht.' : `Löschen fehlgeschlagen: ${result}`, result === 'ok' ? 'success' : 'warning');
    await render();
  });

  window.addEventListener('simpleoffice:native-ready', () => {
    bridge = window.SimpleOfficeOffline || null;
    if (switchButton) switchButton.disabled = !bridge;
    render().then(() => {
      if (bridge && document.documentElement.dataset.soNetwork !== 'offline' && navigator.onLine !== false) syncOutbox();
    });
  });
  window.addEventListener('simpleoffice:network', event => {
    if (event.detail && event.detail.online) syncOutbox();
  });
  window.addEventListener('online', syncOutbox);

  render().then(() => {
    if (document.documentElement.dataset.soNetwork !== 'offline' && navigator.onLine !== false) syncOutbox();
  });
})();
