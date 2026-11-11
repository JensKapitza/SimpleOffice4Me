(() => {
  'use strict';
  const section = document.getElementById('screen-workspace-section');
  const frame = document.getElementById('screen-workspace-frame');
  if (!section || !frame) return;
  let active = false, epoch = 0, nextId = 0;
  let identifiers = new WeakMap();
  let observer = null;
  const blockedPath = /(?:^|\/)(?:admin|auth|login|logout|register|security|settings|users|tokens|credentials|password|oauth|licensing)(?:\/|$)/i;
  const sensitive = /password|passwd|secret|token|credential|csrf|api[-_]?key|otp|one[-_]?time/i;
  const textTypes = new Set(['text', 'search', 'url', 'tel', 'email', 'number', 'date', 'time', 'datetime-local', 'month', 'week']);
  const changed = () => window.dispatchEvent(new CustomEvent('screen-workspace-change'));
  const workspaceDocument = () => {
    try {
      const doc = frame.contentDocument;
      if (!active || !doc || frame.contentWindow.location.origin !== window.location.origin || blockedPath.test(frame.contentWindow.location.pathname)) return null;
      return doc;
    } catch (_) { return null; }
  };
  const editable = (field, doc) => {
    if (!field.isConnected || field.ownerDocument !== doc || field.disabled || field.readOnly || field.matches(':disabled') || !field.getClientRects().length) return false;
    const style = doc.defaultView.getComputedStyle(field);
    if (style.visibility !== 'visible' || style.display === 'none' || style.opacity === '0' || field.closest('[hidden], [inert], [aria-hidden="true"]')) return false;
    const tag = field.tagName.toLowerCase();
    if (!(tag === 'textarea' || (tag === 'select' && !field.multiple) || (tag === 'input' && (textTypes.has(field.type) || field.type === 'checkbox')))) return false;
    if (sensitive.test([field.name, field.id, field.autocomplete, field.getAttribute('aria-label')].join(' '))) return false;
    if (/cc-|transaction-|new-password|current-password/i.test(field.autocomplete || '')) return false;
    if (field.form) {
      const action = new URL(field.form.action || doc.URL, doc.URL);
      if (action.origin !== window.location.origin || blockedPath.test(action.pathname)) return false;
    }
    return true;
  };
  const entries = () => {
    const doc = workspaceDocument();
    if (!doc) return [];
    return [...doc.querySelectorAll('input,textarea,select')].filter(field => editable(field, doc)).slice(0, 100).map(field => {
      if (!identifiers.has(field)) identifiers.set(field, `f${++nextId}`);
      return {field, id: identifiers.get(field)};
    });
  };
  const fields = () => ({epoch: String(epoch), fields: entries().map(({field, id}) => {
    const label = [...(field.labels || [])].map(item => item.textContent.trim()).join(' ') || field.getAttribute('aria-label') || field.placeholder || field.name || 'Eingabefeld';
    const type = field.type === 'checkbox' ? 'checkbox' : field.tagName.toLowerCase() === 'select' ? 'select' : 'text';
    const result = {id, label: label.slice(0, 160), type};
    if (type === 'select') result.options = [...field.options].filter(option => !option.disabled && !option.parentElement?.disabled).slice(0, 100).map(option => ({value: option.value, label: option.textContent.slice(0, 160)}));
    return result;
  })});
  const apply = command => {
    if (!command || String(command.epoch) !== String(epoch)) throw new Error('Die Anwendungsseite wurde gewechselt. Felder erneut laden.');
    const found = entries().find(item => item.id === (command.field_id || command.id));
    if (!found) throw new Error('Dieses Feld ist nicht zur Eingabe freigegeben.');
    const field = found.field, realm = field.ownerDocument.defaultView;
    const checkbox = field.type === 'checkbox';
    if (checkbox ? typeof command.value !== 'boolean' : typeof command.value !== 'string' || command.value.length > 4000) throw new Error('Ungültige Feldeingabe.');
    if (!checkbox && field.maxLength >= 0 && command.value.length > field.maxLength) throw new Error('Der Text ist für dieses Feld zu lang.');
    if (field.tagName.toLowerCase() === 'select' && ![...field.options].some(option => option.value === command.value && !option.disabled && !option.parentElement?.disabled)) throw new Error('Diese Auswahl ist nicht verfügbar.');
    const prototype = field.tagName.toLowerCase() === 'textarea' ? realm.HTMLTextAreaElement.prototype : field.tagName.toLowerCase() === 'select' ? realm.HTMLSelectElement.prototype : realm.HTMLInputElement.prototype;
    const key = checkbox ? 'checked' : 'value';
    const setter = Object.getOwnPropertyDescriptor(prototype, key)?.set;
    if (!setter) throw new Error('Dieses Feld unterstützt keine Eingabefreigabe.');
    const previous = field[key];
    setter.call(field, command.value);
    if (field[key] !== command.value || (!checkbox && field.validity && !field.validity.valid)) {
      setter.call(field, previous);
      throw new Error('Die Eingabe passt nicht zu den Feldvorgaben.');
    }
    // Do not dispatch input/change: existing application listeners may auto-submit.
    // The owner explicitly saves the edited native form values.
    return true;
  };
  const navigate = (event, inFrame) => {
    if (!active || event.defaultPrevented || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.('a[href]');
    if (!link || link.hasAttribute('download') || link.getAttribute?.('href')?.startsWith('#') || link.hasAttribute('data-bs-toggle')) return;
    const url = new URL(link.href, window.location.href);
    if (!['http:', 'https:'].includes(url.protocol)) return;
    if (url.origin !== window.location.origin) {
      if (inFrame) { event.preventDefault(); window.open(url.href, '_blank', 'noopener,noreferrer'); }
      return;
    }
    event.preventDefault();
    if (url.pathname === '/screen' || url.pathname.startsWith('/screen/')) { section.scrollIntoView?.({block: 'start'}); return; }
    frame.src = url.href;
    epoch += 1; changed();
  };
  document.addEventListener('click', event => navigate(event, false));
  frame.addEventListener('load', () => {
    observer?.disconnect(); observer = null;
    epoch += 1; identifiers = new WeakMap(); nextId = 0;
    try {
      const doc = frame.contentDocument;
      doc?.addEventListener('click', event => navigate(event, true));
      doc?.addEventListener('submit', event => {
        const form = event.target;
        const target = new URL(form.action || doc.URL, doc.URL);
        if (target.origin !== window.location.origin) { event.preventDefault(); return; }
        form.target = '_self';
        epoch += 1; changed();
      }, true);
      doc?.addEventListener('change', changed);
      if (doc?.documentElement && window.MutationObserver) {
        observer = new window.MutationObserver(changed);
        observer.observe(doc.documentElement, {childList: true, subtree: true, attributes: true,
          attributeFilter: ['disabled', 'readonly', 'hidden', 'inert', 'type', 'class', 'style']});
      }
    } catch (_) { /* External destinations cannot expose editable application fields. */ }
    changed();
  });
  window.SimpleOfficeScreenWorkspace = {
    start() { active = true; section.hidden = false; if (!frame.getAttribute('src')) frame.src = frame.dataset.startUrl; changed(); },
    stop() { active = false; changed(); }, fields, apply
  };
})();
