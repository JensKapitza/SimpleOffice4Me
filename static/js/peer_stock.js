(() => {
  'use strict';
  const root = document.getElementById('peer-stock');
  if (!root) return;
  const ask = document.getElementById('peer-stock-ask');
  const status = document.getElementById('peer-stock-status');
  const result = document.getElementById('peer-stock-result');
  const cleanText = (value) => String(value || '').trim();
  const setStatus = (text, kind) => {
    if (!status) return;
    status.textContent = '';
    if (!text) return;
    const note = document.createElement('div');
    note.className = `alert alert-${kind || 'secondary'} py-2 small mb-0`;
    note.textContent = text;
    status.appendChild(note);
  };
  const safeJson = async (response) => { try { return await response.json(); } catch (_error) { return {}; } };
  const askFriends = async () => {
    if (!ask) return;
    ask.disabled = true;
    setStatus('Frage geteilte Freundeskataloge (lokal) …', 'info');
    if (result) { result.hidden = true; result.textContent = ''; }
    try {
      const data = new FormData();
      data.append('identifier', root.dataset.identifier || '');
      data.append('_csrf_token', root.dataset.csrf || '');
      const response = await fetch(root.dataset.url || '', { method: 'POST', body: data, cache: 'no-store' });
      const payload = await safeJson(response);
      if (!response.ok || !payload || payload.ok !== true) {
        setStatus(cleanText(payload && payload.error) || `Prüfung fehlgeschlagen (${response.status}).`, 'danger');
        return;
      }
      const friends = Array.isArray(payload.friends) ? payload.friends : [];
      const hits = friends.filter((entry) => entry && entry.available);
      const missing = friends.filter((entry) => entry && entry.state && entry.state !== 'ok').length;
      let summary = `${payload.friends_checked} Freund(e) geprüft, ${hits.length} Treffer.`;
      if (missing) summary += ` ${missing} ohne gültigen Index.`;
      setStatus(summary, hits.length ? 'success' : 'secondary');
      if (!result) return;
      result.hidden = false;
      if (!friends.length) { result.textContent = 'Keine Freunde mit Datenfreigabe hinterlegt.'; return; }
      const list = document.createElement('ul');
      list.className = 'list-unstyled mb-0';
      friends.forEach((entry) => {
        const item = document.createElement('li');
        const count = Array.isArray(entry.matches) ? entry.matches.length : 0;
        const name = cleanText(entry.peer_id);
        if (entry.available) {
          item.textContent = `✓ ${name} (${count} Treffer)`;
        } else if (!entry.state || entry.state === 'ok') {
          item.textContent = `○ ${name} (kein Treffer, Index gültig)`;
        } else if (entry.state === 'not_synced') {
          item.textContent = `? ${name} (Katalog nie synchronisiert — keine Aussage)`;
        } else {
          item.textContent = `? ${name} (Katalogfehler — keine Aussage)`;
        }
        list.appendChild(item);
      });
      result.appendChild(list);
    } catch (_error) { setStatus('Prüfung fehlgeschlagen (Netzwerk).', 'danger'); }
    finally { ask.disabled = false; }
  };
  ask?.addEventListener('click', askFriends);
})();
