/* Mobile workspace behavior: active quick-access link, contextual actions,
   Web Share with clipboard fallback. The bar itself is desktop-hidden by CSS. */
(() => {
  'use strict';
  const bar = document.querySelector('[data-mobile-bar]');
  const fab = document.querySelector('[data-mobile-fab]');
  if (!bar || !fab) return;

  const normalize = value => (value || '').replace(/\/+$/, '');
  const path = normalize(window.location.pathname);
  const matchLength = value => {
    const prefix = normalize(value);
    return prefix && (path === prefix || path.startsWith(prefix + '/')) ? prefix.length : 0;
  };
  const links = Array.from(bar.querySelectorAll('[data-mobile-link]'));
  let active = null;
  let bestLength = 0;
  // Search and Calendar sit under Files. Mark only the most specific rendered link.
  for (const link of links) {
    const length = matchLength(link.dataset.mobileLink);
    if (length > bestLength) {
      active = link;
      bestLength = length;
    }
  }
  for (const link of links) {
    if (link === active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  }

  // Contextual actions: order and visibility follow the current section.
  const menu = fab.querySelector('[data-mobile-fab-menu]');
  const toggle = fab.querySelector('[data-mobile-fab-toggle]');
  const actions = menu ? Array.from(menu.querySelectorAll('[data-mobile-action]')) : [];
  const CONTEXTS = [
    {match: ['/documents/calendar'], order: ['event', 'resubmit', 'copy', 'share']},
    {match: ['/tasks'], order: ['resubmit', 'task', 'copy', 'share']},
    {match: ['/documents/'], order: ['capture', 'copy', 'resubmit', 'share']},
    {match: ['/chat'], order: ['copy', 'event', 'task', 'share']},
  ];
  const context = CONTEXTS.find(entry => entry.match.some(prefix => matchLength(prefix) > 0));
  const order = context ? context.order : ['event', 'resubmit', 'task', 'copy', 'share'];
  const byAction = {};
  actions.forEach(action => { byAction[action.dataset.mobileAction] = action; });
  order.forEach(name => {
    if (byAction[name]) {
      byAction[name].hidden = false;
      menu.append(byAction[name]);
    }
  });
  actions.forEach(action => {
    if (!order.includes(action.dataset.mobileAction)) action.hidden = true;
  });

  function setMenu(open) {
    if (!menu) return;
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
  }
  toggle.addEventListener('click', () => setMenu(menu.hidden));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu && !menu.hidden) {
      setMenu(false);
      toggle.focus();
    }
  });
  document.addEventListener('click', event => {
    if (menu && !menu.hidden && !fab.contains(event.target)) setMenu(false);
  });

  const feedback = menu ? menu.querySelector('[data-mobile-feedback]') : null;
  function announce(message) {
    if (feedback) feedback.textContent = message;
  }

  const copyButton = menu ? menu.querySelector('[data-mobile-action="copy"]') : null;
  if (copyButton) {
    copyButton.addEventListener('click', async () => {
      const selection = window.getSelection ? String(window.getSelection() || '').trim() : '';
      const value = selection || `${document.title} — ${window.location.href}`;
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        try {
          await navigator.clipboard.writeText(value);
          announce('✓');
        } catch {
          announce('!');
        }
      } else {
        announce('!');
      }
    });
  }

  const shareButton = menu ? menu.querySelector('[data-mobile-action="share"]') : null;
  if (shareButton) {
    shareButton.addEventListener('click', async () => {
      const data = {title: document.title, url: window.location.href};
      if (navigator.share && typeof navigator.share === 'function') {
        try {
          await navigator.share(data);
          return;
        } catch {
          return;
        }
      }
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        try { await navigator.clipboard.writeText(data.url); } catch {}
      }
    });
  }
})();
