/* Text preview helpers: search with match navigation plus copy actions.
   Progressive enhancement over the server-rendered preview text; works with
   plain file text and recognized (extracted/OCR) text alike. */
(() => {
  'use strict';
  const root = document.querySelector('[data-text-preview]');
  if (!root) return;
  const body = root.querySelector('[data-text-preview-body]');
  const search = root.querySelector('[data-text-preview-search]');
  const count = root.querySelector('[data-text-preview-count]');
  const feedback = root.querySelector('[data-text-preview-feedback]');
  const original = body ? body.textContent : '';
  const query = () => (search.value || '').trim();
  let renderedQuery = '';

  function clearMarks() {
    if (!body) return;
    body.textContent = original;
  }

  function currentMarks() {
    return body ? Array.from(body.querySelectorAll('mark[data-preview-match]')) : [];
  }

  function showPosition(marks, index) {
    marks.forEach((mark, position) => {
      mark.classList.toggle('bg-warning', position === index);
    });
    if (count) {
      count.textContent = marks.length ? `${index + 1} / ${marks.length}` : '0 / 0';
    }
    if (index >= 0 && marks[index] && typeof marks[index].scrollIntoView === 'function') {
      marks[index].scrollIntoView({block: 'nearest'});
    }
  }

  function applySearch(fromEnd = false) {
    const needle = query();
    renderedQuery = needle;
    if (!needle) {
      clearMarks();
      if (count) count.textContent = '';
      return;
    }
    // No single Unicode character consumes more than two UTF-16 units.
    if (needle.length > original.length * 2) {
      clearMarks();
      showPosition([], -1);
      return;
    }
    // Literal, case-insensitive matches on the original text preserve its offsets
    // even when a lowercase copy would change length (for example, after İ).
    const pattern = new RegExp(needle.replace(/[.*+?^{}$()|[\]\\]/g, '\\$&'), 'giu');
    const matches = [];
    const fragment = document.createDocumentFragment();
    let position = 0;
    for (const hit of original.matchAll(pattern)) {
      fragment.append(document.createTextNode(original.slice(position, hit.index)));
      const mark = document.createElement('mark');
      mark.setAttribute('data-preview-match', '');
      mark.textContent = hit[0];
      fragment.append(mark);
      matches.push(mark);
      position = hit.index + hit[0].length;
    }
    if (matches.length) {
      fragment.append(document.createTextNode(original.slice(position)));
      body.replaceChildren(fragment);
    } else {
      clearMarks();
    }
    showPosition(matches, matches.length ? (fromEnd ? matches.length - 1 : 0) : -1);
  }

  let timer = 0;
  search.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => applySearch(), 150);
  });
  function step(delta) {
    window.clearTimeout(timer);
    if (query() !== renderedQuery) {
      applySearch(delta < 0);
      return;
    }
    const marks = currentMarks();
    if (!marks.length) return;
    const active = marks.findIndex(mark => mark.classList.contains('bg-warning'));
    const next = (active + delta + marks.length) % marks.length;
    showPosition(marks, next);
  }
  root.querySelector('[data-text-preview-next]').addEventListener('click', () => step(1));
  root.querySelector('[data-text-preview-prev]').addEventListener('click', () => step(-1));
  search.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || event.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    step(event.shiftKey ? -1 : 1);
  });

  function announce(message) {
    if (feedback) feedback.textContent = message;
  }
  function copyText(value, isSelection) {
    const emptyMessage = root.dataset.msgEmpty || '';
    if (!value) {
      announce(isSelection ? emptyMessage : '');
      return;
    }
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(value).then(
        () => announce(root.dataset.msgCopied || ''),
        () => announce(emptyMessage),
      );
    } else {
      announce(emptyMessage);
    }
  }
  root.querySelector('[data-text-preview-copy]').addEventListener('click', () => {
    copyText(body.textContent, false);
  });
  root.querySelector('[data-text-preview-copy-selection]').addEventListener('click', () => {
    const selection = window.getSelection ? String(window.getSelection() || '') : '';
    copyText(selection, true);
  });
})();
