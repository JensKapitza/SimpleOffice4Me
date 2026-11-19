/* Bulk document actions: multi-select in the folder tree with chunked
   tag/move requests and a progress bar. Selection never leaves the page. */
(() => {
  'use strict';
  const bar = document.querySelector('[data-bulk-bar]');
  if (!bar) return;
  const CHUNK = 25;
  const boxes = () => Array.from(document.querySelectorAll('[data-bulk-select]'));
  const count = bar.querySelector('[data-bulk-count]');
  const status = bar.querySelector('[data-bulk-status]');
  const progress = bar.querySelector('[data-bulk-progress]');
  const progressBar = bar.querySelector('[data-bulk-progress-bar]');

  function selected() {
    return boxes().filter(box => box.checked).map(box => box.value);
  }
  function refresh() {
    const total = selected().length;
    bar.hidden = total === 0;
    if (count) count.textContent = String(total);
  }
  boxes().forEach(box => {
    box.addEventListener('click', event => event.stopPropagation());
    box.addEventListener('change', refresh);
  });
  refresh();

  function setProgress(done, total) {
    if (!progress || !progressBar) return;
    progress.hidden = total === 0;
    const percent = total ? Math.round((done / total) * 100) : 0;
    progress.setAttribute('aria-valuenow', String(percent));
    progressBar.style.width = `${percent}%`;
  }

  async function run(url, extra) {
    const ids = selected();
    if (!ids.length) return;
    const chunks = [];
    for (let at = 0; at < ids.length; at += CHUNK) chunks.push(ids.slice(at, at + CHUNK));
    let done = 0;
    const failed = [];
    setProgress(0, ids.length);
    if (status) status.textContent = '';
    for (const chunk of chunks) {
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', Accept: 'application/json'},
          body: JSON.stringify({ids: chunk, ...extra}),
        });
        if (!response.ok) throw new Error(`bulk ${response.status}`);
        const data = await response.json();
        (data.failed || []).forEach(item => failed.push(item));
        done += chunk.length;
      } catch {
        chunk.forEach(id => failed.push({id, error: 'request failed'}));
        done += chunk.length;
      }
      setProgress(done, ids.length);
    }
    if (status) {
      status.textContent = failed.length
        ? `${ids.length - failed.length}/${ids.length} ✓, ${failed.length} ✗`
        : `${ids.length}/${ids.length} ✓`;
    }
    window.setTimeout(() => window.location.reload(), failed.length ? 2500 : 800);
  }

  bar.querySelector('[data-bulk-tags-apply]').addEventListener('click', () => {
    const tags = bar.querySelector('[data-bulk-tags]').value;
    run(bar.dataset.bulkTagsUrl, {tags});
  });
  bar.querySelector('[data-bulk-move-apply]').addEventListener('click', () => {
    const folder = bar.querySelector('[data-bulk-folder]').value;
    run(bar.dataset.bulkMoveUrl, {folder});
  });
})();
