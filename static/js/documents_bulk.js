/* Bulk document actions: serialize local writes and keep uncertain results visible. */
(() => {
  'use strict';
  const bar = document.querySelector('[data-bulk-bar]');
  if (!bar) return;
  const CHUNK = 25;
  const boxes = () => Array.from(document.querySelectorAll('[data-bulk-select]'));
  const find = name => bar.querySelector(`[data-bulk-${name}]`);
  const count = find('count');
  const status = find('status');
  const progress = find('progress');
  const progressBar = find('progress-bar');
  const reload = find('reload');
  const tags = find('tags');
  const folder = find('folder');
  const tagApply = find('tags-apply');
  const moveApply = find('move-apply');
  const words = bar.dataset.bulkLanguage === 'en'
    ? {running: 'Processing…', updated: 'updated', failed: 'failed', unknown: 'unconfirmed',
      unsent: 'not sent', tagsRequired: 'Enter at least one tag.', folderRequired: 'Enter a target folder.',
      review: 'Reload the list to check the result before trying again.'}
    : {running: 'Wird verarbeitet…', updated: 'aktualisiert', failed: 'fehlgeschlagen', unknown: 'unbestätigt',
      unsent: 'nicht gesendet', tagsRequired: 'Gib mindestens ein Tag ein.', folderRequired: 'Gib einen Zielordner ein.',
      review: 'Lade die Liste neu und prüfe das Ergebnis, bevor du es erneut versuchst.'};
  let locked = false;

  function selected() {
    return [...new Set(boxes().filter(box => box.checked).map(box => box.value))];
  }
  function refresh() {
    const total = selected().length;
    bar.hidden = total === 0 && !locked;
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

  function outcomes(chunk, data) {
    if (!Array.isArray(data?.updated) || !Array.isArray(data?.failed)) throw new Error('Invalid bulk reply');
    const requested = new Set(chunk);
    const updated = new Set(data.updated);
    const failed = new Set(data.failed.map(item => item?.id));
    if (updated.size !== data.updated.length || failed.size !== data.failed.length ||
        [...updated, ...failed].some(id => !requested.has(id)) ||
        [...updated].some(id => failed.has(id))) {
      throw new Error('Invalid bulk reply');
    }
    return {updated: updated.size, failed: failed.size, unknown: chunk.length - updated.size - failed.size};
  }

  async function run(url, extra) {
    if (locked) return;
    const ids = selected();
    if (!ids.length) return;
    // All buttons share this guard; even a second queued event cannot start another batch.
    locked = true;
    [...boxes(), tags, folder, tagApply, moveApply].forEach(control => { control.disabled = true; });
    if (status) status.textContent = words.running;
    let sent = 0, updated = 0, failed = 0, unknown = 0;
    setProgress(0, ids.length);
    while (sent < ids.length && !unknown) {
      const chunk = ids.slice(sent, sent + CHUNK);
      sent += chunk.length;
      try {
        const response = await fetch(url, {
          method: 'POST',
          redirect: 'error',
          headers: {'Content-Type': 'application/json', Accept: 'application/json'},
          body: JSON.stringify({ids: chunk, ...extra}),
        });
        if (!response.ok) throw new Error('Bulk request failed');
        const result = outcomes(chunk, await response.json());
        updated += result.updated;
        failed += result.failed;
        unknown += result.unknown;
      } catch {
        // The server may already have committed part or all of a lost reply.
        unknown += chunk.length;
      }
      setProgress(sent, ids.length);
    }
    const unsent = ids.length - sent;
    if (status) {
      const summary = [`${updated}/${ids.length} ${words.updated}`];
      if (failed) summary.push(`${failed} ${words.failed}`);
      if (unknown) summary.push(`${unknown} ${words.unknown}`);
      if (unsent) summary.push(`${unsent} ${words.unsent}`);
      status.textContent = summary.join(' · ') + (failed || unknown ? '. ' + words.review : '.');
    }
    if (failed || unknown) {
      if (reload) reload.hidden = false;
    } else {
      window.setTimeout(() => window.location.reload(), 800);
    }
  }

  reload?.addEventListener('click', () => window.location.reload());
  function apply(url, field, key, isValid, message) {
    if (locked) return;
    const value = field.value.trim();
    if (!isValid(value)) {
      if (status) status.textContent = message;
      field.focus();
      return;
    }
    run(url, {[key]: value});
  }

  tagApply.addEventListener('click', () =>
    apply(bar.dataset.bulkTagsUrl, tags, 'tags', value => value.split(',').some(tag => tag.trim()), words.tagsRequired));
  moveApply.addEventListener('click', () =>
    apply(bar.dataset.bulkMoveUrl, folder, 'folder', Boolean, words.folderRequired));
})();
