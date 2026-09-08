(() => {
  'use strict';

  const sameOriginHttpUrl = value => {
    try {
      const url = new URL(String(value || ''), window.location.href);
      if (!['http:', 'https:'].includes(url.protocol) || url.origin !== window.location.origin) return null;
      return url.href;
    } catch (_) {
      return null;
    }
  };

  const element = (tag, options = {}) => {
    const node = document.createElement(tag);
    if (options.className) node.className = options.className;
    if (options.text !== undefined) node.textContent = String(options.text ?? '');
    return node;
  };

  const alertNode = (message, className = 'alert alert-warning py-2') =>
    element('div', {className, text: message});

  const renderFindRows = (target, rows) => {
    target.replaceChildren();
    for (const row of Array.isArray(rows) ? rows : []) {
      const href = sameOriginHttpUrl(row?.detail_url);
      if (!href) continue;
      const link = element('a', {className: 'list-group-item list-group-item-action'});
      link.href = href;
      link.append(element('strong', {text: `#${row?.display_id ?? ''} ${row?.name ?? ''}`}));
      link.append(element('div', {text: `Standort: ${row?.location_path || 'nicht zugeordnet'}${row?.location_code ? ` · ${row.location_code}` : ''}`}));
      target.append(link);
    }
  };

  const renderAssignedBook = (status, history, data) => {
    status.replaceChildren(alertNode(`#${data?.display_id ?? ''} ${data?.name ?? ''} → ${data?.location_path ?? ''}`, 'alert alert-success py-2 mb-1'));
    const href = sameOriginHttpUrl(data?.detail_url);
    if (!href) return;
    const link = element('a', {className: 'list-group-item list-group-item-action py-2'});
    link.href = href;
    link.append(document.createTextNode(`✓ #${data?.display_id ?? ''} ${data?.name ?? ''} `));
    link.append(element('span', {className: 'float-end small', text: data?.location?.code ?? ''}));
    history.prepend(link);
  };

  const renderCaptureLink = (status, data) => {
    const box = element('div', {className: 'alert alert-warning py-2'});
    box.append(document.createTextNode('Noch nicht im Inventar. '));
    const href = sameOriginHttpUrl(data?.capture_url);
    if (href) {
      const link = element('a', {className: 'alert-link', text: 'Jetzt Buch erfassen und danach hier weitermachen'});
      link.href = href;
      box.append(link, document.createTextNode('.'));
    } else {
      box.append(document.createTextNode('Erfassungslink wurde aus Sicherheitsgründen verworfen.'));
    }
    status.replaceChildren(box);
  };

  const renderPrinters = (box, printers, onSelect) => {
    box.replaceChildren();
    for (const row of Array.isArray(printers) ? printers : []) {
      const uri = String(row?.uri ?? '');
      const button = element('button', {
        className: 'btn btn-sm btn-outline-secondary me-1 mb-1 printer-choice',
        text: `${row?.name ?? ''} · ${row?.kind ?? ''}`,
      });
      button.type = 'button';
      button.addEventListener('click', () => onSelect(uri));
      box.append(button);
    }
  };

  window.SimpleOfficeLibrarySafeDom = Object.freeze({
    alertNode,
    renderAssignedBook,
    renderCaptureLink,
    renderFindRows,
    renderPrinters,
    sameOriginHttpUrl,
  });
})();
