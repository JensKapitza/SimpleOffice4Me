/* Mail client shell: keyboard navigation, command palette, mobile panes.
 * Progressive enhancement only: every action also works as plain link/form
 * without JavaScript. Write actions appear only when the server rendered
 * them, so read-only mode stays read-only by construction. */
(() => {
  'use strict';

  const rowSelector = '.mail-message-table tbody tr[data-mail-uid]';

  function isTyping(target) {
    if (!target || !target.tagName) return false;
    const tag = target.tagName.toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
    return target.isContentEditable === true;
  }

  function rows() {
    return [...document.querySelectorAll(rowSelector)];
  }

  function focusedRow() {
    return document.querySelector(rowSelector + '.mail-row-focus');
  }

  function focusRow(row) {
    const current = focusedRow();
    if (current) current.classList.remove('mail-row-focus');
    if (!row) return;
    row.classList.add('mail-row-focus');
    try {
      row.scrollIntoView({ block: 'nearest' });
    } catch (_) { /* older browsers: selection still works */ }
  }

  function moveSelection(step) {
    const list = rows();
    if (!list.length) return;
    const current = focusedRow();
    let index = current ? list.indexOf(current) : -1;
    if (index < 0) {
      const active = document.querySelector(rowSelector + '.table-active');
      index = active ? list.indexOf(active) : (step > 0 ? -1 : 0);
    }
    const next = list[Math.min(list.length - 1, Math.max(0, index + step))];
    focusRow(next);
  }

  function openFocused() {
    const row = focusedRow() || document.querySelector(rowSelector + '.table-active');
    const link = row ? row.querySelector('a.mail-subject-link') : null;
    if (link) link.click();
  }

  function submitAction(name) {
    const node = document.querySelector('[data-mail-action="' + name + '"]');
    if (!node) return false;
    if (node.tagName.toLowerCase() === 'form') {
      if (typeof node.requestSubmit === 'function') node.requestSubmit();
      else node.submit();
      return true;
    }
    node.click();
    return true;
  }

  /* ---- command palette ---- */

  function paletteRoot() {
    return document.getElementById('mail-palette');
  }

  function collectActions() {
    return [...document.querySelectorAll('[data-mail-action]')].map((node) => ({
      node,
      name: node.getAttribute('data-mail-action') || '',
      label: node.getAttribute('data-mail-action-label') || node.textContent.trim().replace(/\s+/g, ' ').slice(0, 60),
      key: node.getAttribute('data-mail-action-key') || '',
    })).filter((action) => action.name && action.label);
  }

  function renderPaletteItems(filterText) {
    const list = document.getElementById('mail-palette-list');
    if (!list) return;
    const needle = (filterText || '').trim().toLowerCase();
    list.replaceChildren();
    const actions = collectActions().filter((action) => !needle || action.label.toLowerCase().includes(needle));
    if (!actions.length) {
      const empty = document.createElement('li');
      empty.className = 'mail-palette-empty';
      empty.textContent = 'Keine passende Aktion.';
      list.appendChild(empty);
      return;
    }
    actions.forEach((action, index) => {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'mail-palette-item' + (index === 0 ? ' active' : '');
      const label = document.createElement('span');
      label.textContent = action.label;
      button.appendChild(label);
      if (action.key) {
        const hint = document.createElement('kbd');
        hint.textContent = action.key;
        button.appendChild(hint);
      }
      button.addEventListener('click', () => {
        closePalette();
        submitAction(action.name);
      });
      item.appendChild(button);
      list.appendChild(item);
    });
  }

  function openPalette() {
    const root = paletteRoot();
    if (!root) return;
    const input = document.getElementById('mail-palette-input');
    root.hidden = false;
    renderPaletteItems('');
    if (input) {
      input.value = '';
      input.focus();
    }
  }

  function closePalette() {
    const root = paletteRoot();
    if (root) root.hidden = true;
  }

  function paletteOpen() {
    const root = paletteRoot();
    return !!root && root.hidden === false;
  }

  function paletteMove(step) {
    const items = [...document.querySelectorAll('#mail-palette-list .mail-palette-item')];
    if (!items.length) return;
    const current = document.querySelector('#mail-palette-list .mail-palette-item.active');
    let index = current ? items.indexOf(current) : -1;
    index = Math.min(items.length - 1, Math.max(0, index + step));
    items.forEach((item, position) => item.classList.toggle('active', position === index));
    try {
      items[index].scrollIntoView({ block: 'nearest' });
    } catch (_) { /* selection still works */ }
  }

  function paletteConfirm() {
    const active = document.querySelector('#mail-palette-list .mail-palette-item.active')
      || document.querySelector('#mail-palette-list .mail-palette-item');
    if (active) active.click();
  }

  /* ---- mobile panes ---- */

  function panesRoot() {
    return document.getElementById('mail-panes');
  }

  function setPaneView(view) {
    const root = panesRoot();
    if (!root) return;
    if (view !== 'folders' && view !== 'list' && view !== 'message') return;
    root.setAttribute('data-mail-view', view);
    try {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (_) {
      window.scrollTo(0, 0);
    }
  }

  function initPanes() {
    const root = panesRoot();
    if (!root) return;
    if (!root.getAttribute('data-mail-view')) {
      const hasPreview = !!document.querySelector('[data-mail-pane="message"] [data-mail-has-preview]');
      root.setAttribute('data-mail-view', hasPreview ? 'message' : 'list');
    }
    document.querySelectorAll('[data-mail-view-target]').forEach((button) => {
      button.addEventListener('click', () => setPaneView(button.getAttribute('data-mail-view-target')));
    });
  }

  /* ---- dwell timing: auto-mark the open message seen ---- */

  function initAutoseen() {
    const pane = document.querySelector('[data-mail-has-preview][data-mail-autoseen="1"]');
    if (!pane) return;
    const seconds = parseInt(pane.getAttribute('data-mail-dwell-seconds') || '0', 10);
    if (!seconds || seconds <= 0) return;
    let done = false;
    window.setTimeout(() => {
      if (done) return;
      done = true;
      submitAction('seen');
    }, seconds * 1000);
  }

  /* ---- events ---- */

  function initPaletteInput() {
    const input = document.getElementById('mail-palette-input');
    if (!input) return;
    input.addEventListener('input', () => renderPaletteItems(input.value));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); paletteMove(1); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); paletteMove(-1); }
      else if (event.key === 'Enter') { event.preventDefault(); paletteConfirm(); }
    });
  }

  document.addEventListener('click', (event) => {
    const root = paletteRoot();
    if (root && root.hidden === false && event.target === root) closePalette();
  });

  document.querySelectorAll(rowSelector).forEach((row) => {
    row.addEventListener('click', () => focusRow(row));
  });

  document.addEventListener('keydown', (event) => {
    const palette = paletteOpen();
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (palette) closePalette();
      else openPalette();
      return;
    }
    if (event.key === 'Escape') {
      if (palette) closePalette();
      return;
    }
    if (palette || isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
    switch (event.key) {
      case 'j':
      case 'ArrowDown':
        event.preventDefault();
        moveSelection(1);
        break;
      case 'k':
      case 'ArrowUp':
        event.preventDefault();
        moveSelection(-1);
        break;
      case 'Enter':
        if (focusedRow()) {
          event.preventDefault();
          openFocused();
        }
        break;
      case 'e':
        if (submitAction('archive')) event.preventDefault();
        break;
      case 'u':
        if (submitAction('seen')) event.preventDefault();
        break;
      case 'r':
        if (submitAction('reply')) event.preventDefault();
        break;
      case 'n':
        if (submitAction('seen-next')) event.preventDefault();
        break;
      case 'c': {
        const panel = document.getElementById('compose-panel');
        const field = document.getElementById('compose-to');
        if (panel && field) {
          event.preventDefault();
          panel.classList.add('show');
          field.focus();
        }
        break;
      }
      case '/': {
        const search = document.querySelector('input[type="search"][name="q"]');
        if (search) {
          event.preventDefault();
          search.focus();
        }
        break;
      }
      default:
        break;
    }
  });

  initPanes();
  initPaletteInput();
  initAutoseen();
})();
