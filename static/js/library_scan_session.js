(() => {
  'use strict';

  const tx = (german, english) => window.SimpleOfficeI18n?.text(german, english) ?? german;

  const textNode = (text, className = '') => {
    const node = document.createElement('div');
    node.className = className;
    node.textContent = String(text ?? '');
    return node;
  };

  const requestId = () => {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
    const bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return [...bytes].map(value => value.toString(16).padStart(2, '0')).join('');
  };

  const create = options => {
    let session = null;
    const status = options.status;
    const history = options.history;
    const location = options.location;
    const input = options.input;
    const scanButton = options.scanButton;
    const buttons = options.buttons;

    const render = value => {
      session = value && typeof value === 'object' ? value : null;
      const active = session?.status === 'active';
      const paused = session?.status === 'paused';
      buttons.start.hidden = Boolean(session);
      buttons.pause.hidden = !active;
      buttons.resume.hidden = !paused;
      buttons.finish.hidden = !session;
      location.disabled = Boolean(session);
      input.disabled = !active;
      scanButton.disabled = !active;
      if (!session) {
        status.textContent = tx('Keine Scan-Sitzung aktiv.', 'No scan session is active.');
        history.replaceChildren();
        return;
      }
      if ([...location.options].some(option => option.value === session.location_id)) {
        location.value = session.location_id;
      }
      const stateLabel = active ? tx('läuft', 'running') : tx('pausiert', 'paused');
      status.textContent = [
        `${tx('Sitzung', 'Session')} ${stateLabel}: ${session.location_path || tx('Standort', 'Location')}`,
        `${session.total || 0} ${tx('Scans', 'scans')}`,
        `${session.assigned || 0} ${tx('zugeordnet', 'assigned')}`,
        `${session.duplicates || 0} ${tx('Dubletten', 'duplicates')}`,
        `${session.missing || 0} ${tx('offen', 'open')}`,
        `${session.errors || 0} ${tx('Fehler', 'errors')}`,
      ].join(' · ');
      history.replaceChildren();
      const labels = {
        assigned: tx('Zugeordnet', 'Assigned'),
        duplicate: tx('Bereits am Standort', 'Already at this location'),
        missing: tx('Noch nicht im Inventar', 'Not in inventory yet'),
        error: tx('Fehler', 'Error'),
      };
      for (const row of [...(session.recent || [])].reverse()) {
        const label = labels[row.outcome] || tx('Scan', 'Scan');
        const identity = row.display_id ? ` #${row.display_id}` : '';
        const name = row.name ? ` ${row.name}` : '';
        history.append(textNode(`${label}:${identity}${name}`, 'list-group-item py-2'));
      }
    };

    const action = async (name, extra = {}) => {
      const form = new FormData();
      form.set('_csrf_token', options.csrf);
      form.set('action', name);
      for (const [key, value] of Object.entries(extra)) form.set(key, value);
      const response = await fetch(options.url, {
        method: 'POST',
        body: form,
        headers: {'Accept': 'application/json'},
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || tx('Scan-Sitzung konnte nicht aktualisiert werden.', 'Could not update the scan session.'));
      render(payload.session);
      return payload.session;
    };

    buttons.start.addEventListener('click', async () => {
      if (!location.value) {
        status.textContent = tx('Bitte zuerst ein Regal auswählen oder scannen.', 'Select or scan a shelf first.');
        return;
      }
      try {
        await action('start', {location_id: location.value});
        input.focus();
      } catch (error) {
        status.textContent = error.message;
      }
    });
    buttons.pause.addEventListener('click', () => action('pause').catch(error => { status.textContent = error.message; }));
    buttons.resume.addEventListener('click', async () => {
      try {
        await action('resume');
        input.focus();
      } catch (error) {
        status.textContent = error.message;
      }
    });
    buttons.finish.addEventListener('click', async () => {
      if (!window.confirm(tx('Scan-Sitzung abschließen? Der Sitzungsfortschritt wird danach verworfen.', 'Finish the scan session? Its progress will be discarded.'))) return;
      try {
        await action('finish');
      } catch (error) {
        status.textContent = error.message;
      }
    });

    return Object.freeze({
      load: async () => {
        try {
          const response = await fetch(options.url, {headers: {'Accept': 'application/json'}});
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(payload.error || tx('Scan-Sitzung konnte nicht geladen werden.', 'Could not load the scan session.'));
          render(payload.session);
        } catch (error) {
          status.textContent = error.message;
          input.disabled = true;
          scanButton.disabled = true;
        }
      },
      requireActive: () => {
        if (session?.status === 'active') return true;
        status.textContent = session ? tx('Die Scan-Sitzung ist pausiert.', 'The scan session is paused.') : tx('Bitte zuerst eine Scan-Sitzung starten.', 'Start a scan session first.');
        return false;
      },
      addRequestFields: form => {
        form.set('scan_session_id', session.session_id);
        form.set('scan_id', requestId());
      },
      accept: render,
    });
  };

  window.SimpleOfficeLibraryScanSession = Object.freeze({create});
})();
