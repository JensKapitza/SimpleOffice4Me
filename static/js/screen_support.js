(() => {
  'use strict';
  const el = id => document.getElementById(`screen-support-${id}`);
  if (!el('panel')) return;
  let active = null, timer = null, version = 0, after = 0, commandCursor = 0;
  let state = {permission: 'none'}, fieldSignature = '', locallyRevoked = false;
  let popups = [], renderedFields = '', failures = 0, shownPopup = null;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const labels = {request: 'Eingaben wurden angefordert', grant: 'Eingaben erlaubt', deny: 'Eingaben abgelehnt',
    revoke: 'Eingabefreigabe widerrufen', join: 'Gegenstelle verbunden', leave: 'Gegenstelle getrennt',
    ended: 'Sitzung beendet', end: 'Sitzung beendet', notice: 'Hinweis', ack: 'Zur Kenntnis genommen', input: 'Anwendungsfeld angepasst', 'input-result': 'Feldeingabe verarbeitet'};
  const error = err => { el('error').textContent = err?.message || 'Support-Verbindung fehlgeschlagen.'; };
  const api = async (options = {}, context = active) => {
    if (!context) throw new Error('Keine aktive Support-Sitzung.');
    const headers = {'X-Screen-Code': context.code || ''};
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      if (options.body) { headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = csrf; }
      const suffix = options.body ? '' : `?role=${context.role}&after=${after}&after_command=${commandCursor}`;
      const response = await fetch(`/screen/api/sessions/${encodeURIComponent(context.id)}/support${suffix}`, {
        ...options, headers, signal: controller.signal, credentials: 'same-origin'});
      if (!response.ok) throw new Error(`Support-Anfrage fehlgeschlagen (HTTP ${response.status}).`);
      return await response.json();
    } finally { window.clearTimeout(timeout); }
  };
  const post = (type, data = {}, context = active) => api({method: 'POST', body: JSON.stringify({role: context?.role, type, ...data})}, context);
  const update = () => {
    const sender = active?.role === 'sender', receiver = active?.role === 'receiver';
    el('request').disabled = !receiver || state.permission !== 'none';
    el('grant').disabled = !sender || state.permission !== 'requested';
    el('deny').disabled = !sender || state.permission !== 'requested';
    el('revoke').disabled = !sender || state.permission !== 'granted';
    el('send').disabled = !active;
    el('permission-popup').classList.toggle('d-none', !sender || state.permission !== 'requested');
    el('active-banner').classList.toggle('d-none', !sender || state.permission !== 'granted' || locallyRevoked);
    el('status').textContent = !active ? 'Keine aktive Support-Sitzung. Verlauf bleibt verfügbar.' :
      state.permission === 'requested' ? 'Eingaben wurden angefordert. Freigabe durch den Nutzer erforderlich.' :
      state.permission === 'granted' ? (sender && locallyRevoked ? 'Eingaben lokal gesperrt. Freigabe widerrufen und erneut anfordern.' : 'Eingabefreigabe aktiv – nur angebotene Anwendungsfelder.') : 'Nur Ansicht. Keine Eingabefreigabe.';
  };
  const popup = () => {
    const current = popups[0];
    el('popup').classList.toggle('d-none', !current);
    if (current && shownPopup !== current.seq) {
      shownPopup = current.seq; el('popup-text').textContent = current.text;
      if (document.fullscreenElement) document.exitFullscreen?.().catch(error);
      el('popup').focus();
    }
    if (!current) shownPopup = null;
  };
  const events = (items, historical = false) => {
    for (const item of items || []) {
      if (item.seq <= after) continue;
      const li = document.createElement('li');
      const stamp = new Date(Number(item.at || item.timestamp || 0) * 1000).toLocaleString();
      li.textContent = `${stamp} · ${item.from === 'sender' ? 'Nutzer' : 'Gegenstelle'} · ${labels[item.type] || item.type}${item.text ? ': ' + item.text : ''}${item.event_seq ? ' (Hinweis ' + item.event_seq + ')' : ''}${item.outcome ? (item.outcome === 'applied' ? ' · übernommen' : ' · verworfen') : ''}`;
      el('history').appendChild(li);
      if (!historical && active && item.from !== active.role && item.type === 'notice') popups.push(item);
      after = Math.max(after, Number(item.seq || 0));
    }
    popup();
  };
  const publishFields = async () => {
    if (active?.role !== 'sender') return;
    const data = window.SimpleOfficeScreenWorkspace?.fields() || {epoch: '', fields: []};
    const signature = JSON.stringify(data);
    if (signature !== fieldSignature) { await post('fields', data); fieldSignature = signature; }
  };
  const fieldsUI = data => {
    const signature = JSON.stringify([state.permission, state.generation, data.epoch, data.fields]);
    if (signature === renderedFields) return;
    renderedFields = signature; el('fields').replaceChildren();
    if (active?.role !== 'receiver' || state.permission !== 'granted') return;
    if (!data.fields?.length) { el('fields').textContent = 'Auf dieser Anwendungsseite sind keine freigegebenen Felder verfügbar.'; return; }
    for (const field of data.fields) {
      const row = document.createElement('div'), label = document.createElement('label');
      row.className = 'my-2'; label.className = 'form-label'; label.textContent = field.label;
      const input = document.createElement(field.type === 'select' ? 'select' : 'input');
      input.id = `screen-remote-${field.id}`; label.htmlFor = input.id;
      input.className = 'form-control';
      if (field.type === 'select') for (const choice of field.options || []) {
        const option = document.createElement('option'); option.value = choice.value; option.textContent = choice.label; input.appendChild(option);
      } else input.type = field.type === 'checkbox' ? 'checkbox' : 'text';
      const button = document.createElement('button'); button.type = 'button'; button.className = 'btn btn-outline-primary mt-1'; button.textContent = 'Feld anpassen';
      const generation = state.generation, epoch = data.epoch;
      button.addEventListener('click', async () => {
        button.disabled = true;
        try { await post('input', {epoch, field_id: field.id, generation, value: field.type === 'checkbox' ? input.checked : input.value}); el('error').textContent = ''; }
        catch (err) { error(err); } finally { button.disabled = false; }
      });
      row.append(label, input, button); el('fields').appendChild(row);
    }
  };
  const poll = async ticket => {
    if (!active || ticket !== version) return;
    try {
      await publishFields();
      const data = await api();
      if (ticket !== version) return;
      state = data.state; events(data.events); update(); fieldsUI(data);
      for (const command of data.commands || []) {
        if (command.seq <= commandCursor) continue;
        if (!locallyRevoked && state.permission === 'granted' && command.generation === state.generation) {
          let outcome = 'applied';
          try {
            if (!window.SimpleOfficeScreenWorkspace?.apply(command)) throw new Error('Feld oder Anwendungsseite ist nicht mehr verfügbar.');
          } catch (err) { outcome = 'rejected'; error(err); }
          await post('input-result', {command_seq: command.seq, outcome});
        }
        commandCursor = Math.max(commandCursor, Number(command.seq || 0));
      }
      failures = 0;
      if (data.closed) { active = null; update(); return; }
    } catch (err) {
      if (ticket !== version) return;
      error(err); failures++;
      if (active) post(active.role === 'sender' ? 'revoke' : 'leave').catch(() => {});
      // Do not apply any commands from an interrupted permission channel.
      locallyRevoked = true; update();
      if (failures >= 3) { state = {permission: 'none'}; update(); return; }
    }
    if (ticket === version && active) timer = window.setTimeout(() => poll(ticket), 900);
  };
  const action = async type => {
    if (type === 'revoke' || type === 'deny') { locallyRevoked = true; state.permission = 'none'; update(); }
    try {
      await post(type); el('error').textContent = '';
      if (type === 'grant') locallyRevoked = false;
      if (timer) window.clearTimeout(timer);
      await poll(++version);
    } catch (err) { error(err); }
  };
  for (const type of ['request', 'grant', 'deny', 'revoke']) el(type).addEventListener('click', () => action(type));
  el('popup-grant').addEventListener('click', () => action('grant'));
  el('popup-deny').addEventListener('click', () => action('deny'));
  el('banner-revoke').addEventListener('click', () => action('revoke'));
  el('notice-form').addEventListener('submit', async event => {
    event.preventDefault(); const text = el('notice').value.trim(); if (!text) return;
    try { await post('notice', {text}); el('notice').value = ''; el('error').textContent = ''; }
    catch (err) { error(err); }
  });
  el('ack').addEventListener('click', async () => {
    if (!popups.length) return;
    try { await post('ack', {event_seq: popups[0].seq}); popups.shift(); popup(); }
    catch (err) { error(err); }
  });
  window.SimpleOfficeScreenSupport = {
    async start(session, role, code) {
      if (timer) window.clearTimeout(timer); version++;
      active = {id: session.session_id, role, code}; after = 0; commandCursor = 0;
      fieldSignature = ''; renderedFields = ''; popups = []; failures = 0; locallyRevoked = true;
      state = {permission: 'none'}; el('history').replaceChildren(); update();
      if (role === 'sender') { try { localStorage.setItem('simpleoffice-screen-history', active.id); } catch (_) { /* optional browser storage */ } }
      try { if (role === 'receiver') await post('join'); await poll(version); } catch (err) { error(err); }
    },
    stop() {
      const context = active; active = null; locallyRevoked = true; version++;
      if (timer) window.clearTimeout(timer); timer = null; state = {permission: 'none'};
      el('fields').replaceChildren(); popups = []; popup(); update();
      if (context?.role === 'receiver') post('leave', {}, context).catch(error);
      // Best effort: signaling also closes/revokes the session; an already closed
      // session is expected here and must not replace the user's stop result.
      if (context?.role === 'sender') post('revoke', {}, context).catch(() => {});
    },
    disconnected() { locallyRevoked = true; if (active) post(active.role === 'sender' ? 'revoke' : 'leave').catch(error); }
  };
  window.addEventListener('screen-workspace-change', () => { fieldSignature = ''; });
  update();
  try {
    const id = localStorage.getItem('simpleoffice-screen-history');
    if (id) api({}, {id, role: 'sender', code: ''}).then(data => { if (!active) events(data.events, true); }).catch(error);
  } catch (_) { /* optional browser storage */ }
})();
