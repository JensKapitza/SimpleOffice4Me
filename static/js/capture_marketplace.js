(() => {
  'use strict';
  const EN = (document.documentElement.lang || '').toLowerCase().startsWith('en');
  const STR = {
    title: EN ? 'Market price: search only on click, no automation' : 'Marktpreis: nur auf Klick suchen, kein Automatismus',
    unknownProvider: EN ? 'Unknown provider — nothing adopted.' : 'Unbekannter Anbieter — nichts übernommen.',
    noValidPrice: EN ? 'This hit contains no valid price — nothing adopted, only “View offer” is possible.' : 'Dieser Treffer enthält keinen gültigen Preis — nichts übernommen, nur „Angebot ansehen“ möglich.',
    partialKept: EN ? 'Price partially adopted; own values kept — enable “Replace” to overwrite.' : 'Preis teils übernommen; eigene Werte behalten — „Ersetzen“ aktivieren zum Überschreiben.',
    priceAdopted: (provider) => EN ? `Price adopted (${provider}). Please review.` : `Preis übernommen (${provider}). Bitte prüfen.`,
    keepOwn: EN ? 'Own price values kept — enable “Replace” to overwrite.' : 'Eigene Preiswerte behalten — „Ersetzen“ aktivieren zum Überschreiben.',
    unknownProviderHits: EN ? 'Unknown provider — no hits adopted.' : 'Unbekannter Anbieter — keine Treffer übernommen.',
    noHits: EN ? 'No marketplace hits found.' : 'Keine Marketplace-Treffer gefunden.',
    hitFallback: EN ? 'Hit' : 'Treffer',
    adoptPrice: EN ? 'Adopt price' : 'Preis übernehmen',
    noPriceTitle: EN ? 'No price in this hit — only “View offer” is possible.' : 'Kein Preis in diesem Treffer — nur „Angebot ansehen“ möglich.',
    viewOffer: EN ? 'View offer' : 'Angebot ansehen',
    hitsReview: (n) => EN ? `${n} hits — please review and adopt selectively.` : `${n} Treffer — bitte prüfen und gezielt übernehmen.`,
    unknownProviderSearch: EN ? 'Unknown provider — no search started.' : 'Unbekannter Anbieter — keine Suche gestartet.',
    needQuery: EN ? 'For the marketplace search, first enter ISBN or title.' : 'Für die Marketplace-Suche zuerst ISBN oder Titel eingeben.',
    unavailable: EN ? 'Marketplace search is not available.' : 'Marketplace-Suche ist nicht verfügbar.',
    searching: (provider, query) => EN ? `Searching ${provider} for “${query}” …` : `Suche ${provider} nach „${query}“ …`,
    searchFailed: EN ? 'Marketplace search failed.' : 'Marketplace-Suche fehlgeschlagen.',
    unreachable: EN ? 'Marketplace search is unreachable.' : 'Marketplace-Suche ist nicht erreichbar.',
    searchAmazon: EN ? 'Search Amazon' : 'Amazon suchen',
    searchEbay: EN ? 'Search eBay' : 'eBay suchen',
    replaceOwn: EN ? 'Replace already entered price values' : 'Bereits eingetragene Preiswerte ersetzen',
  };
  const renderMarketplaceSection = (ctx) => {
    const {inputs, combined, form, dataset, cleanText, safeJson} = ctx;
    const marketBox = document.createElement('div');
    marketBox.className = 'col-12';
    const marketTitle = document.createElement('div');
    marketTitle.className = 'fw-semibold small mb-1';
    marketTitle.textContent = STR.title;
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
    const applyMarketplacePrice = (provider, row) => {
      if (provider !== 'amazon' && provider !== 'ebay') {
        marketStatus.textContent = STR.unknownProvider;
        return;
      }
      if (!isValidPrice(row.market_price)) {
        marketStatus.textContent = STR.noValidPrice;
        return;
      }
      const overwriteBox = marketBox.querySelector('[data-marketplace-overwrite]');
      const overwrite = Boolean(overwriteBox && overwriteBox.checked);
      const providerName = provider === 'ebay' ? 'eBay' : 'Amazon';
      const targets = [['market_price', row.market_price], ['currency', normalizeCurrency(row.currency)], ['price_source', providerName]];
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
      if (applied && kept) marketStatus.textContent = STR.partialKept;
      else if (applied) marketStatus.textContent = STR.priceAdopted(providerName);
      else marketStatus.textContent = STR.keepOwn;
    };
    const renderMarketplaceResults = (provider, data) => {
      if (provider !== 'amazon' && provider !== 'ebay') {
        marketStatus.textContent = STR.unknownProviderHits;
        return;
      }
      marketResults.replaceChildren();
      const rows = Array.isArray(data.results) ? data.results.slice(0, 3) : [];
      if (!rows.length) {
        marketStatus.textContent = cleanText(data.warning || data.error) || STR.noHits;
        return;
      }
      const providerName = provider === 'ebay' ? 'eBay' : 'Amazon';
      rows.forEach((row) => {
        if (!row || typeof row !== 'object') return;
        if (row.provider !== provider) return;
        const item = document.createElement('div');
        item.className = 'list-group-item py-2 small';
        const price = [cleanText(row.market_price), cleanText(row.currency)].filter(Boolean).join(' ');
        item.textContent = `${cleanText(row.title) || STR.hitFallback}${price ? ` · ${price}` : ''} · ${providerName} `;
        const take = document.createElement('button');
        take.type = 'button';
        take.className = 'btn btn-sm btn-outline-success ms-1';
        take.textContent = STR.adoptPrice;
        const hasPrice = isValidPrice(row.market_price);
        take.disabled = !hasPrice;
        if (!hasPrice) take.title = STR.noPriceTitle;
        take.addEventListener('click', () => applyMarketplacePrice(provider, row));
        item.appendChild(take);
        const target = marketplaceNav.safeMarketplaceUrl ? marketplaceNav.safeMarketplaceUrl(row.url) : '';
        if (target) {
          const view = document.createElement('a');
          view.className = 'btn btn-sm btn-outline-secondary ms-1';
          view.href = target;
          view.target = '_blank';
          view.rel = 'noopener noreferrer';
          view.textContent = STR.viewOffer;
          item.appendChild(view);
        }
        marketResults.appendChild(item);
      });
      marketStatus.textContent = STR.hitsReview(rows.length);
    };
    const searchMarketplace = async (provider) => {
      if (provider !== 'amazon' && provider !== 'ebay') {
        marketStatus.textContent = STR.unknownProviderSearch;
        return;
      }
      const query = cleanText(inputs.isbn ? inputs.isbn.value : '') || cleanText(combined.isbn || '') || cleanText(inputs.title ? inputs.title.value : '');
      if (!query) { marketStatus.textContent = STR.needQuery; return; }
      if (!marketplaceUrl) { marketStatus.textContent = STR.unavailable; return; }
      marketStatus.textContent = STR.searching(provider === 'ebay' ? 'eBay' : 'Amazon', query);
      marketResults.replaceChildren();
      try {
        const response = await fetch(`${marketplaceUrl}?provider=${encodeURIComponent(provider)}&q=${encodeURIComponent(query)}`, {headers: {Accept: 'application/json'}, cache: 'no-store'});
        const data = await safeJson(response);
        if (!response.ok) { marketStatus.textContent = cleanText(data && data.error) || STR.searchFailed; return; }
        renderMarketplaceResults(provider, data);
      } catch (_error) {
        marketStatus.textContent = STR.unreachable;
      }
    };
    [['amazon', STR.searchAmazon], ['ebay', STR.searchEbay]].forEach(([provider, label]) => {
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
    overwriteLabel.textContent = STR.replaceOwn;
    overwriteWrap.appendChild(overwriteBoxEl);
    overwriteWrap.appendChild(overwriteLabel);
    marketBox.appendChild(overwriteWrap);
    marketBox.appendChild(marketStatus);
    marketBox.appendChild(marketResults);
    form.appendChild(marketBox);
  };
  window.SimpleOfficeCaptureMarketplace = {renderMarketplaceSection};
})();
