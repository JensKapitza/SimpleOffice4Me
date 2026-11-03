(() => {
  'use strict';
  const renderMarketplaceSection = (ctx) => {
    const {inputs, combined, form, dataset, cleanText, safeJson} = ctx;
    const marketBox = document.createElement('div');
    marketBox.className = 'col-12';
    const marketTitle = document.createElement('div');
    marketTitle.className = 'fw-semibold small mb-1';
    marketTitle.textContent = 'Marktpreis: nur auf Klick suchen, kein Automatismus';
    marketBox.appendChild(marketTitle);
    const marketButtons = document.createElement('div');
    marketButtons.className = 'd-flex flex-wrap gap-2 mb-1';
    const marketStatus = document.createElement('p');
    marketStatus.className = 'small mt-1 mb-0';
    marketStatus.setAttribute('role', 'status');
    const marketResults = document.createElement('div');
    marketResults.className = 'list-group mt-1';
    const marketplaceUrl = cleanText(dataset.marketplaceUrl);
    const marketplaceNav = window.SimpleOfficeInventoryNavigation || {};
    const isValidPrice = (value) => {
      const text = cleanText(value).replace(/\s/g, '');
      if (!text || /[^0-9.,]/.test(text)) return false;
      const normalized = text.includes(',')
        ? text.replace(/\./g, '').replace(',', '.')
        : text;
      if (!/^\d+(\.\d{1,2})?$/.test(normalized)) return false;
      const amount = Number(normalized);
      return Number.isFinite(amount) && amount >= 0;
    };
    const normalizeCurrency = (value) => cleanText(value).toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
    const applyMarketplacePrice = (row) => {
      if (!isValidPrice(row.market_price)) {
        marketStatus.textContent = 'Dieser Treffer enthält keinen gültigen Preis — nichts übernommen, nur „Angebot ansehen“ möglich.';
        return;
      }
      const overwriteBox = marketBox.querySelector('[data-marketplace-overwrite]');
      const overwrite = Boolean(overwriteBox && overwriteBox.checked);
      const providerName = row.provider === 'ebay' ? 'eBay' : 'Amazon';
      const source = cleanText(row.price_source) || providerName;
      const targets = [['market_price', row.market_price], ['currency', normalizeCurrency(row.currency)], ['price_source', source]];
      let kept = false;
      let applied = false;
      targets.forEach(([name, value]) => {
        const input = inputs[name];
        if (!input) return;
        const text = cleanText(value);
        if (!text) return;
        if (cleanText(input.value) && !overwrite) { kept = true; return; }
        input.value = text;
        applied = true;
      });
      if (applied && kept) marketStatus.textContent = 'Preis teils übernommen; eigene Werte behalten — „Ersetzen“ aktivieren zum Überschreiben.';
      else if (applied) marketStatus.textContent = `Preis übernommen (${providerName}). Bitte prüfen.`;
      else marketStatus.textContent = 'Eigene Preiswerte behalten — „Ersetzen“ aktivieren zum Überschreiben.';
    };
    const renderMarketplaceResults = (provider, data) => {
      marketResults.replaceChildren();
      const rows = Array.isArray(data.results) ? data.results.slice(0, 3) : [];
      if (!rows.length) {
        marketStatus.textContent = cleanText(data.warning || data.error) || 'Keine Marketplace-Treffer gefunden.';
        return;
      }
      rows.forEach((row) => {
        if (!row || typeof row !== 'object') return;
        const providerName = row.provider === 'ebay' ? 'eBay' : 'Amazon';
        const item = document.createElement('div');
        item.className = 'list-group-item py-2 small';
        const price = [cleanText(row.market_price), cleanText(row.currency)].filter(Boolean).join(' ');
        const source = cleanText(row.price_source) || providerName;
        item.textContent = `${cleanText(row.title) || 'Treffer'}${price ? ` · ${price}` : ''} · ${source} `;
        const take = document.createElement('button');
        take.type = 'button';
        take.className = 'btn btn-sm btn-outline-success ms-1';
        take.textContent = 'Preis übernehmen';
        const hasPrice = isValidPrice(row.market_price);
        take.disabled = !hasPrice;
        if (!hasPrice) take.title = 'Kein Preis in diesem Treffer — nur „Angebot ansehen“ möglich.';
        take.addEventListener('click', () => applyMarketplacePrice(row));
        item.appendChild(take);
        const target = marketplaceNav.safeMarketplaceUrl ? marketplaceNav.safeMarketplaceUrl(row.url) : '';
        if (target) {
          const view = document.createElement('a');
          view.className = 'btn btn-sm btn-outline-secondary ms-1';
          view.href = target;
          view.target = '_blank';
          view.rel = 'noopener noreferrer';
          view.textContent = 'Angebot ansehen';
          item.appendChild(view);
        }
        marketResults.appendChild(item);
      });
      marketStatus.textContent = `${rows.length} Treffer — bitte prüfen und gezielt übernehmen.`;
    };
    const searchMarketplace = async (provider) => {
      const query = cleanText(inputs.isbn ? inputs.isbn.value : '') || cleanText(combined.isbn || '') || cleanText(inputs.title ? inputs.title.value : '');
      if (!query) { marketStatus.textContent = 'Für die Marketplace-Suche zuerst ISBN oder Titel eingeben.'; return; }
      if (!marketplaceUrl) { marketStatus.textContent = 'Marketplace-Suche ist nicht verfügbar.'; return; }
      marketStatus.textContent = `Suche ${provider === 'ebay' ? 'eBay' : 'Amazon'} nach „${query}“ …`;
      marketResults.replaceChildren();
      try {
        const response = await fetch(`${marketplaceUrl}?provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(query)}`, {headers: {Accept: 'application/json'}, cache: 'no-store'});
        const data = await safeJson(response);
        if (!response.ok) { marketStatus.textContent = cleanText(data && data.error) || 'Marketplace-Suche fehlgeschlagen.'; return; }
        renderMarketplaceResults(provider, data);
      } catch (_error) {
        marketStatus.textContent = 'Marketplace-Suche ist nicht erreichbar.';
      }
    };
    [['amazon', 'Amazon suchen'], ['ebay', 'eBay suchen']].forEach(([provider, label]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-sm btn-outline-primary';
      button.textContent = label;
      button.addEventListener('click', () => searchMarketplace(provider));
      marketButtons.appendChild(button);
    });
    marketBox.appendChild(marketButtons);
    const overwriteWrap = document.createElement('div');
    overwriteWrap.className = 'form-check mt-1';
    const overwriteBoxEl = document.createElement('input');
    overwriteBoxEl.type = 'checkbox';
    overwriteBoxEl.className = 'form-check-input';
    overwriteBoxEl.setAttribute('data-marketplace-overwrite', '1');
    const overwriteLabel = document.createElement('label');
    overwriteLabel.className = 'form-check-label small';
    overwriteLabel.textContent = 'Bereits eingetragene Preiswerte ersetzen';
    overwriteWrap.appendChild(overwriteBoxEl);
    overwriteWrap.appendChild(overwriteLabel);
    marketBox.appendChild(overwriteWrap);
    marketBox.appendChild(marketStatus);
    marketBox.appendChild(marketResults);
    form.appendChild(marketBox);
  };
  window.SimpleOfficeCaptureMarketplace = {renderMarketplaceSection};
})();
