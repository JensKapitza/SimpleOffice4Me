(() => {
  'use strict';
  const renderPeerStockSection = (ctx) => {
    const {inputs, combined, form, dataset, csrf, cleanText, safeJson} = ctx;
    if (!cleanText(dataset.peerStockUrl)) return;
    const peerBox = document.createElement('div');
    peerBox.className = 'col-12';
    const peerTitle = document.createElement('div');
    peerTitle.className = 'fw-semibold small mb-1';
    peerTitle.textContent = 'Geteilte Dokumente: nur auf Klick suchen, keine Auto-Abfrage';
    peerBox.appendChild(peerTitle);
    const peerNote = document.createElement('p');
    peerNote.className = 'small text-secondary mb-1';
    peerNote.textContent = 'Durchsucht lokal gespiegelte Dokumentenverzeichnisse freigegebener Freunde anhand der ISBN/des Barcodes — noch vor einem Nachkauf. Treffer sind Dokumenthinweise, kein Besitznachweis: Ein Inventarabgleich mit Freunden existiert nicht. Titelabfragen werden nicht unterstützt — Titel treffen nur bei exakt gleicher Schreibweise, daher keine verlässliche Aussage.';
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
    askButton.textContent = 'Geteilte Dokumente suchen';
    const askPeerStock = async () => {
      const identifier = cleanText(inputs.isbn ? inputs.isbn.value : '') || cleanText(combined.isbn || '');
      if (!identifier) {
        peerStatus.textContent = 'Für die Dokumentensuche zuerst eine ISBN/einen Barcode erfassen — Titelabfragen werden nicht unterstützt.';
        return;
      }
      askButton.disabled = true;
      peerStatus.textContent = 'Frage geteilte Dokumentenverzeichnisse (lokal) …';
      peerResults.replaceChildren();
      try {
        const data = new FormData();
        data.append('identifier', identifier);
        data.append('_csrf_token', csrf);
        const response = await fetch(peerStockUrl, {method: 'POST', body: data, cache: 'no-store'});
        const payload = await safeJson(response);
        if (!response.ok || !payload || payload.ok !== true) {
          peerStatus.textContent = cleanText(payload && payload.error) || `Prüfung fehlgeschlagen (${response.status}).`;
          return;
        }
        const friends = Array.isArray(payload.friends) ? payload.friends : [];
        const hits = friends.filter((entry) => entry && entry.available);
        const unknown = friends.filter((entry) => entry && entry.state && entry.state !== 'ok').length;
        let summary = `${payload.friends_checked} Freund(e) geprüft, ${hits.length} Dokumenthinweis(e).`;
        if (unknown) summary += ` ${unknown} ohne gültigen Index — keine Aussage.`;
        peerStatus.textContent = summary;
        if (!friends.length) {
          peerResults.textContent = 'Keine Freunde mit Datenfreigabe hinterlegt.';
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
            item.textContent = `✓ ${name} (${count} Dokumenthinweis(e), kein Besitznachweis)`;
          } else if (!entry.state || entry.state === 'ok') {
            item.textContent = `○ ${name} (kein Treffer, Index gültig)`;
          } else if (entry.state === 'not_synced') {
            item.textContent = `? ${name} (Katalog nie synchronisiert — keine Aussage)`;
          } else {
            item.textContent = `? ${name} (Katalogfehler — keine Aussage)`;
          }
          list.appendChild(item);
        });
        peerResults.appendChild(list);
      } catch (_error) {
        peerStatus.textContent = 'Prüfung fehlgeschlagen (Netzwerk).';
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
  window.SimpleOfficeCapturePeerStock = {renderPeerStockSection};
})();
