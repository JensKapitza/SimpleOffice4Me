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

  function applySearch() {
    clearMarks();
    const needle = (search.value || '').trim();
    if (!needle) {
      if (count) count.textContent = '';
      return [];
    }
    const matches = [];
    const walker = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const folded = needle.toLowerCase();
    nodes.forEach(node => {
      const text = node.textContent;
      const lower = text.toLowerCase();
      let position = 0;
      const fragment = document.createDocumentFragment();
      let found = lower.indexOf(folded, position);
      if (found < 0) return;
      while (found >= 0) {
        fragment.append(document.createTextNode(text.slice(position, found)));
        const mark = document.createElement('mark');
        mark.setAttribute('data-preview-match', '');
        mark.textContent = text.slice(found, found + needle.length);
        fragment.append(mark);
        matches.push(mark);
        position = found + needle.length;
        found = lower.indexOf(folded, position);
      }
      fragment.append(document.createTextNode(text.slice(position)));
      node.parentNode.replaceChild(fragment, node);
    });
    showPosition(matches, matches.length ? 0 : -1);
    return matches;
  }

  let timer = 0;
  search.addEventListener('input', () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(applySearch, 150);
  });
  function step(delta) {
    let marks = currentMarks();
    if (!marks.length) marks = applySearch();
    if (!marks.length) return;
    const active = marks.findIndex(mark => mark.classList.contains('bg-warning'));
    const next = (active + delta + marks.length) % marks.length;
    showPosition(marks, next);
  }
  root.querySelector('[data-text-preview-next]').addEventListener('click', () => step(1));
  root.querySelector('[data-text-preview-prev]').addEventListener('click', () => step(-1));

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
