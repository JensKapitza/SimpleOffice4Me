/* Mobile workspace behavior: active quick-access link, contextual actions,
   Web Share with clipboard fallback. The bar itself is desktop-hidden by CSS. */
(() => {
  'use strict';
  const bar = document.querySelector('[data-mobile-bar]');
  const fab = document.querySelector('[data-mobile-fab]');
  if (!bar || !fab) return;

  const path = window.location.pathname || '/';
  bar.querySelectorAll('[data-mobile-link]').forEach(link => {
    const prefix = link.dataset.mobileLink || '';
    const active = prefix !== '/' && (path === prefix || path.startsWith(prefix));
    if (active) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });

  // Contextual actions: order and visibility follow the current section.
  const menu = fab.querySelector('[data-mobile-fab-menu]');
  const toggle = fab.querySelector('[data-mobile-fab-toggle]');
  const actions = menu ? Array.from(menu.querySelectorAll('[data-mobile-action]')) : [];
  const CONTEXTS = [
    {match: ['/documents/calendar'], order: ['event', 'task', 'share']},
    {match: ['/tasks'], order: ['task', 'event', 'share']},
    {match: ['/documents/'], order: ['capture', 'task', 'event', 'share']},
    {match: ['/chat'], order: ['event', 'task', 'share']},
  ];
  const context = CONTEXTS.find(entry => entry.match.some(prefix => path.startsWith(prefix)));
  const order = context ? context.order : ['event', 'task', 'capture', 'share'];
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
