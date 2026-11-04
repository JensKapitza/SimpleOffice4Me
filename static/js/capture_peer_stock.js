(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    stockTitle: EN ? 'Shared documents: search only on click, no auto-query' : 'Geteilte Dokumente: nur auf Klick suchen, keine Auto-Abfrage',
    stockNote: EN ? 'Searches locally mirrored document directories of sharing-enabled friends by ISBN/barcode — before buying again. Hits are document hints, not proof of ownership: there is no inventory reconciliation with friends. Title queries are not supported — titles only match on exactly identical spelling, hence no reliable statement.' : 'Durchsucht lokal gespiegelte Dokumentenverzeichnisse freigegebener Freunde anhand der ISBN/des Barcodes — noch vor einem Nachkauf. Treffer sind Dokumenthinweise, kein Besitznachweis: Ein Inventarabgleich mit Freunden existiert nicht. Titelabfragen werden nicht unterstützt — Titel treffen nur bei exakt gleicher Schreibweise, daher keine verlässliche Aussage.',
    stockAsk: EN ? 'Search shared documents' : 'Geteilte Dokumente suchen',
    stockNeedId: EN ? 'Capture an ISBN/barcode first for the document search — title queries are not supported.' : 'Für die Dokumentensuche zuerst eine ISBN/einen Barcode erfassen — Titelabfragen werden nicht unterstützt.',
    stockAsking: EN ? 'Querying shared document directories (local) …' : 'Frage geteilte Dokumentenverzeichnisse (lokal) …',
    checkFailed: (code) => EN ? `Check failed (${code}).` : `Prüfung fehlgeschlagen (${code}).`,
    stockSummary: (checked, hits) => EN ? `${checked} friend(s) checked, ${hits} document hint(s).` : `${checked} Freund(e) geprüft, ${hits} Dokumenthinweis(e).`,
    stockUnknown: (n) => EN ? ` ${n} without a valid index — no statement.` : ` ${n} ohne gültigen Index — keine Aussage.`,
    stockNoFriends: EN ? 'No friends with data sharing registered.' : 'Keine Freunde mit Datenfreigabe hinterlegt.',
    stockHit: (name, count) => EN ? `✓ ${name} (${count} document hint(s), no proof of ownership)` : `✓ ${name} (${count} Dokumenthinweis(e), kein Besitznachweis)`,
    stockNoHit: (name) => EN ? `○ ${name} (no hit, index valid)` : `○ ${name} (kein Treffer, Index gültig)`,
    stockNotSynced: (name) => EN ? `? ${name} (catalog never synced — no statement)` : `? ${name} (Katalog nie synchronisiert — keine Aussage)`,
    stockCatalogError: (name) => EN ? `? ${name} (catalog error — no statement)` : `? ${name} (Katalogfehler — keine Aussage)`,
    checkNetwork: EN ? 'Check failed (network).' : 'Prüfung fehlgeschlagen (Netzwerk).',
    invTitle: EN ? 'Friends’ stock (inventory check): search only on click, no auto-query' : 'Freundesbestand (Inventarabgleich): nur auf Klick suchen, keine Auto-Abfrage',
    invNote: EN ? 'Checks locally synced inventory directories of explicitly sharing-enabled friends by ISBN/GTIN code — before buying again. Only exact matches count; unsynced/faulty directories count as no statement. Titles and barcodes without a checksum are not matched.' : 'Prüft lokal synchronisierte Inventarverzeichnisse ausdrücklich freigegebener Freunde anhand der ISBN/des GTIN-Codes — noch vor einem Nachkauf. Gezählt wird nur exakte Übereinstimmung; unsynced/fehlerhafte Verzeichnisse gelten als keine Aussage. Titel und Barcodes ohne Prüfsumme werden nicht abgeglichen.',
    invAsk: EN ? 'Check friends’ stock' : 'Freundesbestand prüfen',
    invNeedId: EN ? 'Capture an ISBN/GTIN code first for the inventory check — titles and plain barcodes are not matched.' : 'Für den Inventarabgleich zuerst eine ISBN/einen GTIN-Code erfassen — Titel und einfache Barcodes werden nicht abgeglichen.',
    invAsking: EN ? 'Querying synced inventory directories (local) …' : 'Frage synchronisierte Inventarverzeichnisse (lokal) …',
    invSummary: (checked, hits) => EN ? `${checked} friend(s) checked, ${hits} with stock.` : `${checked} Freund(e) geprüft, ${hits} mit Bestand.`,
    invUnknown: (n) => EN ? ` ${n} without a valid directory state — no statement.` : ` ${n} ohne gültigen Verzeichnisstand — keine Aussage.`,
    invNoFriends: EN ? 'No friends with inventory sharing registered.' : 'Keine Freunde mit Inventarfreigabe hinterlegt.',
    invHit: (name, label, stand) => EN ? `✓ ${name} has the title${label}${stand}` : `✓ ${name} hat den Titel${label}${stand}`,
    invNoHit: (name, stand) => EN ? `○ ${name} (no stock, directory valid${stand})` : `○ ${name} (kein Bestand, Verzeichnis gültig${stand})`,
    invNotSynced: (name) => EN ? `? ${name} (directory never synced — no statement)` : `? ${name} (Verzeichnis nie synchronisiert — keine Aussage)`,
    invDirError: (name) => EN ? `? ${name} (directory error — no statement)` : `? ${name} (Verzeichnisfehler — keine Aussage)`,
    standAsOf: (date) => EN ? ` — as of ${date}` : ` — Stand vom ${date}`,
  };
  const renderPeerStockSection = (ctx) => {
    const {inputs, combined, form, dataset, csrf, cleanText, safeJson} = ctx;
    if (!cleanText(dataset.peerStockUrl)) return;
    const peerBox = document.createElement('div');
    peerBox.className = 'col-12';
    const peerTitle = document.createElement('div');
    peerTitle.className = 'fw-semibold small mb-1';
    peerTitle.textContent = STR.stockTitle;
    peerBox.appendChild(peerTitle);
    const peerNote = document.createElement('p');
    peerNote.className = 'small text-secondary mb-1';
    peerNote.textContent = STR.stockNote;
    peerBox.appendChild(peerNote);
    const peerStatus = document.createElement('p');
    peerStatus.className = 'small mt-1 mb-0';
    peerStatus.setAttribute('role', 'status');
    const peerResults = document.createElement('div');
    peerResults.className = 'mt-1';
    const peerStockUrl = cleanText(dataset.peerStockUrl);
    const askButton = document.createElement('button');
    askButton.type = 'button';
    askButton.className = 'btn btn-sm btn-outline-primary';
    askButton.textContent = STR.stockAsk;
    const askPeerStock = async () => {
      const identifier = cleanText(inputs.isbn ? inputs.isbn.value : '') || cleanText(combined.isbn || '');
      if (!identifier) {
        peerStatus.textContent = STR.stockNeedId;
        return;
      }
      askButton.disabled = true;
      peerStatus.textContent = STR.stockAsking;
      peerResults.replaceChildren();
      try {
        const data = new FormData();
        data.append('identifier', identifier);
        data.append('_csrf_token', csrf);
        const response = await fetch(peerStockUrl, {method: 'POST', body: data, cache: 'no-store'});
        const payload = await safeJson(response);
        if (!response.ok || !payload || payload.ok !== true) {
          peerStatus.textContent = cleanText(payload && payload.error) || STR.checkFailed(response.status);
          return;
        }
        const friends = Array.isArray(payload.friends) ? payload.friends : [];
        const hits = friends.filter((entry) => entry && entry.available);
        const unknown = friends.filter((entry) => entry && entry.state && entry.state !== 'ok').length;
        let summary = STR.stockSummary(payload.friends_checked, hits.length);
        if (unknown) summary += STR.stockUnknown(unknown);
        peerStatus.textContent = summary;
        if (!friends.length) {
          peerResults.textContent = STR.stockNoFriends;
          return;
        }
        const list = document.createElement('ul');
        list.className = 'list-unstyled mb-0 small';
        friends.forEach((entry) => {
          if (!entry || typeof entry !== 'object') return;
          const item = document.createElement('li');
          const count = Array.isArray(entry.matches) ? entry.matches.length : 0;
          const name = cleanText(entry.peer_id);
          if (entry.available) {
            item.textContent = STR.stockHit(name, count);
          } else if (!entry.state || entry.state === 'ok') {
            item.textContent = STR.stockNoHit(name);
          } else if (entry.state === 'not_synced') {
            item.textContent = STR.stockNotSynced(name);
          } else {
            item.textContent = STR.stockCatalogError(name);
          }
          list.appendChild(item);
        });
        peerResults.appendChild(list);
      } catch (_error) {
        peerStatus.textContent = STR.checkNetwork;
      } finally {
        askButton.disabled = false;
      }
    };
    askButton.addEventListener('click', () => askPeerStock());
    peerBox.appendChild(askButton);
    peerBox.appendChild(peerStatus);
    peerBox.appendChild(peerResults);
    form.appendChild(peerBox);
  };
  const renderPeerInventorySection = (ctx) => {
    const {inputs, combined, form, dataset, csrf, cleanText, safeJson} = ctx;
    if (!cleanText(dataset.peerInventoryUrl)) return;
    const invBox = document.createElement('div');
    invBox.className = 'col-12';
    const invTitle = document.createElement('div');
    invTitle.className = 'fw-semibold small mb-1';
    invTitle.textContent = STR.invTitle;
    invBox.appendChild(invTitle);
    const invNote = document.createElement('p');
    invNote.className = 'small text-secondary mb-1';
    invNote.textContent = STR.invNote;
    invBox.appendChild(invNote);
    const invStatus = document.createElement('p');
    invStatus.className = 'small mt-1 mb-0';
    invStatus.setAttribute('role', 'status');
    const invResults = document.createElement('div');
    invResults.className = 'mt-1';
    const peerInventoryUrl = cleanText(dataset.peerInventoryUrl);
    const formatIndexStand = (value) => {
      const epoch = typeof value === 'string' && value.trim() !== '' ? Number(value) : value;
      if (typeof epoch !== 'number' || !Number.isFinite(epoch) || epoch <= 0) return '';
      const date = new Date(epoch * 1000);
      if (Number.isNaN(date.getTime())) return '';
      return STR.standAsOf(date.toLocaleString());
    };
    const askButton = document.createElement('button');
    askButton.type = 'button';
    askButton.className = 'btn btn-sm btn-outline-primary';
    askButton.textContent = STR.invAsk;
    const askPeerInventory = async () => {
      const identifier = cleanText(inputs.isbn ? inputs.isbn.value : '') || cleanText(combined.isbn || '');
      if (!identifier) {
        invStatus.textContent = STR.invNeedId;
        return;
      }
      askButton.disabled = true;
      invStatus.textContent = STR.invAsking;
      invResults.replaceChildren();
      try {
        const data = new FormData();
        data.append('identifier', identifier);
        data.append('_csrf_token', csrf);
        const response = await fetch(peerInventoryUrl, {method: 'POST', body: data, cache: 'no-store'});
        const payload = await safeJson(response);
        if (!response.ok || !payload || payload.ok !== true) {
          invStatus.textContent = cleanText(payload && payload.error) || STR.checkFailed(response.status);
          return;
        }
        const friends = Array.isArray(payload.friends) ? payload.friends : [];
        const hits = friends.filter((entry) => entry && entry.available);
        const unknown = friends.filter((entry) => entry && entry.state && entry.state !== 'ok').length;
        let summary = STR.invSummary(payload.friends_checked, hits.length);
        if (unknown) summary += STR.invUnknown(unknown);
        invStatus.textContent = summary;
        if (!friends.length) {
          invResults.textContent = STR.invNoFriends;
          return;
        }
        const list = document.createElement('ul');
        list.className = 'list-unstyled mb-0 small';
        friends.forEach((entry) => {
          if (!entry || typeof entry !== 'object') return;
          const item = document.createElement('li');
          const names = (entry.matches || []).map((match) => cleanText(match && match.display_name)).filter(Boolean);
          const label = names.length ? ` („${names[0]}“)` : '';
          const name = cleanText(entry.peer_id);
          if (entry.available) {
            item.textContent = STR.invHit(name, label, formatIndexStand(entry.last_index_at));
          } else if (!entry.state || entry.state === 'ok') {
            item.textContent = STR.invNoHit(name, formatIndexStand(entry.last_index_at));
          } else if (entry.state === 'not_synced') {
            item.textContent = STR.invNotSynced(name);
          } else {
            item.textContent = STR.invDirError(name);
          }
          list.appendChild(item);
        });
        invResults.appendChild(list);
      } catch (_error) {
        invStatus.textContent = STR.checkNetwork;
      } finally {
        askButton.disabled = false;
      }
    };
    askButton.addEventListener('click', () => askPeerInventory());
    invBox.appendChild(askButton);
    invBox.appendChild(invStatus);
    invBox.appendChild(invResults);
    form.appendChild(invBox);
  };
  window.SimpleOfficeCapturePeerStock = {renderPeerStockSection, renderPeerInventorySection};
})();
