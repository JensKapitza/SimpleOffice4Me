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

  let projection;

  function searchProjection(text) {
    // Older browsers keep the previous literal behavior rather than guessing
    // Unicode boundaries and highlighting the wrong characters.
    if (typeof Intl !== 'object' || typeof Intl.Segmenter !== 'function') return {text, canonical: false};
    if (/^[\x00-\x7F]*$/.test(text)) return {text, canonical: true};

    const normalized = text.normalize('NFC');
    const boundaries = new Int32Array(normalized.length + 1).fill(-1);
    const parts = [];
    let offset = 0;
    for (const {segment, index} of new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(text)) {
      boundaries[offset] = index;
      const part = segment.normalize('NFC');
      parts.push(part);
      offset += part.length;
    }
    if (parts.join('') !== normalized) return {text, canonical: false};
    boundaries[offset] = text.length;
    return {text: normalized, boundaries, canonical: true};
  }

  function* findRanges(text, boundaries, pattern) {
    let hit;
    while ((hit = pattern.exec(text)) !== null) {
      const from = hit.index;
      const to = from + hit[0].length;
      const start = boundaries ? boundaries[from] : from;
      const end = boundaries ? boundaries[to] : to;
      if (start >= 0 && end >= 0) {
        yield [start, end];
      } else {
        // Skip a partial grapheme without consuming any later valid start
        // that the rejected match might overlap.
        let next = from + 1;
        while (next < boundaries.length && boundaries[next] < 0) next++;
        pattern.lastIndex = next;
      }
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
    // Reject enormous pastes before Unicode work, but compare lengths after
    // NFC because some single characters decompose to more than two units.
    if (needle.length > 96_000) {
      clearMarks();
      showPosition([], -1);
      return;
    }
    projection ??= searchProjection(original);
    const normalizedNeedle = projection.canonical ? needle.normalize('NFC') : needle;
    if (normalizedNeedle.length > projection.text.length) {
      clearMarks();
      showPosition([], -1);
      return;
    }
    const pattern = new RegExp(normalizedNeedle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'giu');
    const matches = [];
    const fragment = document.createDocumentFragment();
    let position = 0;
    for (const [start, end] of findRanges(projection.text, projection.boundaries, pattern)) {
      fragment.append(document.createTextNode(original.slice(position, start)));
      const mark = document.createElement('mark');
      mark.setAttribute('data-preview-match', '');
      mark.textContent = original.slice(start, end);
      fragment.append(mark);
      matches.push(mark);
      position = end;
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
