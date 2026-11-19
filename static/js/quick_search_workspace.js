/* Live quick-search workspace: debounced search, keyboard navigation, preview
   pane, recents and favorites. Progressive enhancement: without JavaScript
   the form submits to the server-rendered result list. All labels are set
   through textContent; markup strings are never assigned. */
(() => {
  'use strict';
  const root = document.querySelector('[data-quick-workspace]');
  if (!root) return;
  const input = document.getElementById('quick-search-query');
  const list = root.querySelector('[data-quick-list]');
  const countBadge = root.querySelector('[data-quick-count]');
  const filtersBar = root.querySelector('[data-quick-filters]');
  const emptyFilter = root.querySelector('[data-quick-empty-filter]');
  const emptyState = root.querySelector('[data-quick-empty]');
  const feedback = document.getElementById('quick-search-copy-feedback');
  const preview = root.querySelector('[data-quick-preview]');
  if (!input || !list) return;
  const api = root.dataset.quickApi;
  const previewApi = root.dataset.quickPreview;
  const german = (root.dataset.quickLang || 'de') !== 'en';
  const text = (de, en) => (german ? de : en);
  const KIND_LABELS = {
    all: ['Alle', 'All'], document: ['Dokument', 'Document'], appointment: ['Termin', 'Appointment'],
    task: ['Aufgabe', 'Task'], chat: ['Chat', 'Chat'], contact: ['Kontakt', 'Contact'],
    project: ['Projekt', 'Project'], object: ['Objekt', 'Object'], invoice: ['Rechnung', 'Invoice'],
    link: ['Link', 'Link'],
  };
  const kindLabel = kind => (KIND_LABELS[kind] || [kind, kind])[german ? 0 : 1];

  function store(key) {
    try {
      const value = JSON.parse(window.localStorage.getItem(key) || '[]');
      return Array.isArray(value) ? value : [];
    } catch { return []; }
  }
  function saveStore(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch {}
  }
  const RECENTS_KEY = 'quicksearch.recent.v1';
  const FAVORITES_KEY = 'quicksearch.fav.v1';

  function el(tag, attrs, label) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attrs || {})) {
      if (name === 'text') node.textContent = value;
      else if (value !== null && value !== undefined) node.setAttribute(name, value);
    }
    if (label !== undefined) node.textContent = label;
    return node;
  }

  function favorites() { return store(FAVORITES_KEY); }
  function isFavorite(url) { return favorites().some(item => item.url === url); }
  function toggleFavorite(match) {
    const items = favorites().filter(item => item.url !== match.url);
    if (!isFavorite(match.url)) items.unshift({kind: match.kind, label: match.label, url: match.url});
    saveStore(FAVORITES_KEY, items.slice(0, 20));
    renderCollections();
    list.querySelectorAll('[data-quick-fav]').forEach(button => {
      const active = isFavorite(button.dataset.quickFav);
      button.textContent = active ? '★' : '☆';
      button.setAttribute('aria-pressed', String(active));
    });
  }
  function recordRecent(match) {
    const items = store(RECENTS_KEY).filter(item => item.url !== match.url);
    items.unshift({kind: match.kind, label: match.label, url: match.url, at: Date.now()});
    saveStore(RECENTS_KEY, items.slice(0, 8));
  }

  function copyText(value, okMessage) {
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== 'function') return;
    navigator.clipboard.writeText(value || '').then(() => {
      if (feedback) feedback.textContent = okMessage;
    }).catch(() => {
      if (feedback) feedback.textContent = text('Kopieren fehlgeschlagen.', 'Copy failed.');
    });
  }
  function absoluteUrl(url) {
    try { return new URL(url, window.location.origin).toString(); }
    catch { return url; }
  }

  function card(match) {
    const article = el('article', {
      class: 'card shadow-sm', 'data-quick-match': match.kind,
      'data-quick-ref': match.ref || '', role: 'option', 'aria-selected': 'false', tabindex: '-1',
    });
    const body = el('div', {class: 'card-body p-2 p-sm-3 d-flex flex-wrap align-items-center justify-content-between gap-2'});
    const main = el('div', {class: 'min-w-0 flex-grow-1', style: 'min-width: 0'});
    main.append(el('span', {class: 'badge text-bg-light border mb-1'}, kindLabel(match.kind)));
    const link = el('a', {class: 'd-block fw-semibold text-break', 'data-quick-open': '', href: match.url}, match.label);
    link.addEventListener('click', () => recordRecent(match));
    main.append(link);
    if (match.detail) main.append(el('small', {class: 'd-block text-secondary text-break'}, match.detail));
    const actions = el('div', {class: 'd-flex gap-2 flex-shrink-0 quick-card-actions'});
    const fav = el('button', {
      type: 'button', class: 'btn btn-sm btn-outline-secondary', 'data-quick-fav': match.url,
      'aria-pressed': String(isFavorite(match.url)),
      'aria-label': text('Favorit umschalten', 'Toggle favorite'),
    }, isFavorite(match.url) ? '★' : '☆');
    fav.addEventListener('click', event => { event.stopPropagation(); toggleFavorite(match); });
    const copyName = el('button', {
      type: 'button', class: 'btn btn-sm btn-outline-secondary',
      'aria-label': `${text('Bezeichnung kopieren: ', 'Copy name: ')}${match.label}`,
    }, text('Kopieren', 'Copy'));
    copyName.addEventListener('click', event => {
      event.stopPropagation();
      copyText(match.label, text('Bezeichnung kopiert.', 'Name copied.'));
    });
    const copyLink = el('button', {
      type: 'button', class: 'btn btn-sm btn-outline-secondary',
      'aria-label': `${text('Link kopieren: ', 'Copy link: ')}${match.label}`,
    }, text('Link', 'Link'));
    copyLink.addEventListener('click', event => {
      event.stopPropagation();
      copyText(absoluteUrl(match.url), text('Link kopiert.', 'Link copied.'));
    });
    const see = el('button', {
      type: 'button', class: 'btn btn-sm btn-outline-secondary', 'data-quick-preview-open': '',
      'aria-label': `${text('Vorschau: ', 'Preview: ')}${match.label}`,
    }, text('Vorschau', 'Preview'));
    see.addEventListener('click', event => { event.stopPropagation(); openPreview(match); });
    const open = el('a', {class: 'btn btn-sm btn-outline-primary', 'data-quick-open': '', href: match.url}, text('Öffnen', 'Open'));
    open.addEventListener('click', () => recordRecent(match));
    actions.append(fav, copyName, copyLink, see, open);
    body.append(main, actions);
    article.append(body);
    article.addEventListener('click', event => {
      if (event.target.closest('a,button')) return;
      openPreview(match);
    });
    return article;
  }

  function currentFilter() {
    const active = root.querySelector('[data-quick-filter][aria-pressed="true"]');
    return active ? active.dataset.quickFilter : 'all';
  }
  function applyFilter(kind) {
    let visible = 0;
    list.querySelectorAll('[data-quick-match]').forEach(row => {
      row.hidden = kind !== 'all' && row.dataset.quickMatch !== kind;
      if (!row.hidden) visible += 1;
    });
    if (emptyFilter) emptyFilter.hidden = visible > 0;
    return visible;
  }
  function updateCounts() {
    const rows = Array.from(list.querySelectorAll('[data-quick-match]'));
    root.querySelectorAll('[data-quick-filter]').forEach(button => {
      const kind = button.dataset.quickFilter;
      const count = kind === 'all' ? rows.length : rows.filter(row => row.dataset.quickMatch === kind).length;
      const badge = button.querySelector('[data-quick-filter-count]');
      if (badge) badge.textContent = String(count);
      button.hidden = count === 0;
    });
    if (countBadge) countBadge.textContent = String(rows.length);
    if (filtersBar && rows.length) filtersBar.hidden = false;
  }

  function visibleCards() {
    return Array.from(list.querySelectorAll('[data-quick-match]:not([hidden])'));
  }
  function setActive(card) {
    list.querySelectorAll('[data-quick-match].quick-card-active').forEach(node => {
      node.classList.remove('quick-card-active');
      node.setAttribute('aria-selected', 'false');
    });
    if (card) {
      card.classList.add('quick-card-active');
      card.setAttribute('aria-selected', 'true');
      card.scrollIntoView({block: 'nearest'});
    }
  }

  async function openPreview(match) {
    if (!preview) return;
    preview.hidden = false;
    preview.querySelector('[data-quick-preview-title]').textContent = match.label;
    preview.querySelector('[data-quick-preview-kind]').textContent = kindLabel(match.kind);
    const rows = preview.querySelector('[data-quick-preview-rows]');
    const actions = preview.querySelector('[data-quick-preview-actions]');
    rows.replaceChildren();
    actions.replaceChildren();
    rows.append(el('p', {class: 'text-secondary'}, text('Wird geladen …', 'Loading …')));
    try {
      const params = new URLSearchParams({kind: match.kind, id: match.ref || ''});
      const response = await fetch(`${previewApi}?${params.toString()}`, {headers: {Accept: 'application/json'}});
      if (!response.ok) throw new Error(`preview ${response.status}`);
      const data = await response.json();
      rows.replaceChildren();
      (data.rows || []).forEach(([label, value]) => {
        if (!value) return;
        rows.append(el('dt', {class: 'col-sm-4'}, label));
        rows.append(el('dd', {class: 'col-sm-8 text-break'}, String(value)));
      });
      const open = el('a', {class: 'btn btn-primary', href: data.url || match.url}, text('Öffnen', 'Open'));
      open.addEventListener('click', () => recordRecent(match));
      const copyLink = el('button', {type: 'button', class: 'btn btn-outline-secondary'}, text('Link kopieren', 'Copy link'));
      copyLink.addEventListener('click', () => copyText(absoluteUrl(data.url || match.url), text('Link kopiert.', 'Link copied.')));
      actions.append(open, copyLink);
    } catch {
      rows.replaceChildren();
      rows.append(el('p', {class: 'alert alert-warning mb-0'}, text('Vorschau nicht verfügbar.', 'Preview unavailable.')));
    }
  }
  function closePreview() { if (preview) preview.hidden = true; }

  function renderCollection(container, items, emptyText) {
    const section = container.closest('section');
    container.replaceChildren();
    if (!items.length) {
      if (section) section.hidden = true;
      return;
    }
    if (section) section.hidden = false;
    items.forEach(item => {
      const link = el('a', {class: 'list-group-item list-group-item-action d-flex justify-content-between align-items-center gap-2', href: item.url});
      link.append(el('span', {class: 'text-break'}, item.label));
      link.append(el('span', {class: 'badge text-bg-light border'}, kindLabel(item.kind)));
      link.addEventListener('click', () => recordRecent(item));
      container.append(link);
    });
    if (emptyText && !items.length && feedback) feedback.textContent = emptyText;
  }
  function renderCollections() {
    const recents = root.querySelector('[data-quick-recents-list]');
    const favoritesEl = root.querySelector('[data-quick-favorites-list]');
    const show = input.value.trim().length < 2;
    if (recents) renderCollection(recents, show ? store(RECENTS_KEY) : []);
    if (favoritesEl) renderCollection(favoritesEl, show ? favorites() : []);
  }

  let requestId = 0;
  let controller = null;
  async function liveSearch(query) {
    const run = ++requestId;
    if (controller) controller.abort();
    controller = new AbortController();
    try {
      const params = new URLSearchParams({q: query});
      const response = await fetch(`${api}?${params.toString()}`, {
        headers: {Accept: 'application/json'}, signal: controller.signal,
      });
      if (!response.ok) throw new Error(`search ${response.status}`);
      const data = await response.json();
      if (run !== requestId) return;
      list.replaceChildren();
      (data.matches || []).forEach(match => list.append(card(match)));
      list.hidden = false;
      if (emptyState) emptyState.hidden = (data.matches || []).length > 0;
      updateCounts();
      const visible = applyFilter(currentFilter());
      setActive(null);
      closePreview();
      input.setAttribute('aria-expanded', String(visible > 0));
      if (feedback) feedback.textContent = text(
        `${visible} Treffer.`, `${visible} result${visible === 1 ? '' : 's'}.`);
    } catch (error) {
      if (error && error.name === 'AbortError') return;
      if (run !== requestId) return;
      if (feedback) feedback.textContent = text('Suche fehlgeschlagen.', 'Search failed.');
    }
  }

  let timer = 0;
  let serverSnapshot = null;
  function snapshotServerRows() {
    if (serverSnapshot) return serverSnapshot;
    serverSnapshot = Array.from(list.querySelectorAll('[data-quick-match]')).map(row => {
      const link = row.querySelector('[data-quick-open]');
      const detail = row.querySelector('small');
      return {
        kind: row.dataset.quickMatch, ref: row.dataset.quickRef || '',
        label: link ? link.textContent : '', detail: detail ? detail.textContent : '',
        url: link ? link.getAttribute('href') : '#',
      };
    });
    return serverSnapshot;
  }
  function restoreServerRows() {
    list.replaceChildren();
    snapshotServerRows().forEach(match => list.append(card(match)));
    if (emptyState) emptyState.hidden = snapshotServerRows().length > 0;
    updateCounts();
    applyFilter(currentFilter());
    setActive(null);
    input.setAttribute('aria-expanded', 'false');
  }

  input.addEventListener('input', () => {
    window.clearTimeout(timer);
    closePreview();
    renderCollections();
    const query = input.value.trim();
    if (query.length < 2) {
      if (serverSnapshot && serverSnapshot.length) restoreServerRows();
      else {
        list.replaceChildren();
        list.hidden = true;
        updateCounts();
      }
      if (feedback) feedback.textContent = '';
      return;
    }
    snapshotServerRows();
    list.hidden = false;
    timer = window.setTimeout(() => { liveSearch(query); }, 250);
  });

  root.querySelectorAll('[data-quick-filter]').forEach(button => button.addEventListener('click', () => {
    applyFilter(button.dataset.quickFilter);
    setActive(null);
  }));
  root.querySelector('[data-quick-preview-close]').addEventListener('click', closePreview);
  list.addEventListener('click', event => {
    const open = event.target.closest('[data-quick-open]');
    if (open) {
      const article = open.closest('[data-quick-match]');
      if (article) {
        const link = article.querySelector('a[data-quick-open]');
        recordRecent({kind: article.dataset.quickMatch, label: link ? link.textContent : '', url: open.getAttribute('href')});
      }
    }
  });

  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      if (preview && !preview.hidden) { closePreview(); return; }
      input.value = '';
      input.dispatchEvent(new Event('input'));
      return;
    }
    if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Enter') return;
    const cards = visibleCards();
    if (!cards.length) return;
    event.preventDefault();
    const active = list.querySelector('[data-quick-match].quick-card-active');
    const position = active ? cards.indexOf(active) : -1;
    if (event.key === 'Enter') {
      const target = active || cards[0];
      const link = target.querySelector('a[data-quick-open]');
      if (link) link.click();
      return;
    }
    const next = event.key === 'ArrowDown'
      ? cards[Math.min(cards.length - 1, position + 1)]
      : cards[Math.max(0, position < 0 ? cards.length - 1 : position - 1)];
    setActive(next || null);
  });

  renderCollections();
})();
