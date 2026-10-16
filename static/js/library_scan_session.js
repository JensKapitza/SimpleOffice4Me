(() => {
  'use strict';

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
        status.textContent = 'Keine Scan-Sitzung aktiv.';
        history.replaceChildren();
        return;
      }
      if ([...location.options].some(option => option.value === session.location_id)) {
        location.value = session.location_id;
      }
      const stateLabel = active ? 'läuft' : 'pausiert';
      status.textContent = [
        `Sitzung ${stateLabel}: ${session.location_path || 'Standort'}`,
        `${session.total || 0} Scans`,
        `${session.assigned || 0} zugeordnet`,
        `${session.duplicates || 0} Dubletten`,
        `${session.missing || 0} offen`,
        `${session.errors || 0} Fehler`,
      ].join(' · ');
      history.replaceChildren();
      const labels = {
        assigned: 'Zugeordnet',
        duplicate: 'Bereits am Standort',
        missing: 'Noch nicht im Inventar',
        error: 'Fehler',
      };
      for (const row of [...(session.recent || [])].reverse()) {
        const label = labels[row.outcome] || 'Scan';
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
      if (!response.ok) throw new Error(payload.error || 'Scan-Sitzung konnte nicht aktualisiert werden.');
      render(payload.session);
      return payload.session;
    };

    buttons.start.addEventListener('click', async () => {
      if (!location.value) {
        status.textContent = 'Bitte zuerst ein Regal auswählen oder scannen.';
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
      if (!window.confirm('Scan-Sitzung abschließen? Der Sitzungsfortschritt wird danach verworfen.')) return;
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
          if (!response.ok) throw new Error(payload.error || 'Scan-Sitzung konnte nicht geladen werden.');
          render(payload.session);
        } catch (error) {
          status.textContent = error.message;
          input.disabled = true;
          scanButton.disabled = true;
        }
      },
      requireActive: () => {
        if (session?.status === 'active') return true;
        status.textContent = session ? 'Die Scan-Sitzung ist pausiert.' : 'Bitte zuerst eine Scan-Sitzung starten.';
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
