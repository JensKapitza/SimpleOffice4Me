(() => {
  'use strict';
  const root = document.getElementById('reader-root');
  if (!root) return;
  const format = root.dataset.format;
  const version = root.dataset.documentVersion;
  const status = root.querySelector('[data-reader-status]');
  const position = root.querySelector('[data-reader-position]');
  const errorBox = root.querySelector('[data-reader-error]');
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const saved = (() => { try { return JSON.parse(root.dataset.savedLocator || '{}'); } catch (_) { return {}; } })();
  let current = format === 'pdf' ? Math.max(1, Number(saved.page || 1)) : Math.max(0, Number(saved.chapter_index || 0));
  let currentChapterId = format === 'epub' ? String(saved.chapter || '') : '';
  let fontScale = 1;
  let contrast = false;
  let saveTimer = null;
  const pageCount = Math.max(0, Number(root.dataset.pageCount || 0));

  const showError = (message) => {
    if (!errorBox) return;
    errorBox.textContent = message || 'Reader-Fehler';
    errorBox.classList.remove('d-none');
  };
  const request = async (url, options = {}) => {
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    if (options.method && options.method !== 'GET') {
      headers.set('Content-Type', 'application/json');
      headers.set('X-CSRF-Token', csrf());
    }
    const response = await fetch(url, {...options, headers, credentials: 'same-origin', cache: 'no-store'});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || ('HTTP ' + response.status));
    return body;
  };
  const locator = () => format === 'pdf'
    ? {page: current}
    : {chapter: currentChapterId || String(current), chapter_index: current, anchor: '', cfi: '', offset: 0};
  const percent = () => {
    const total = format === 'pdf' ? pageCount : Number(root.querySelectorAll('[data-reader-chapter]').length || 1);
    return total > 0 ? Math.min(100, Math.max(0, Math.round(((current + (format === 'pdf' ? 0 : 1)) / total) * 100))) : 0;
  };
  const scheduleSave = () => {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await request(root.dataset.progressApi, {method: 'POST', body: JSON.stringify({
          locator: locator(), percent: percent(), document_version: version
        })});
      } catch (error) { showError(error.message); }
    }, 350);
  };
  const updateStatus = (total) => {
    if (position) position.value = String(format === 'pdf' ? current : current + 1);
    if (status) status.textContent = (format === 'pdf' ? 'Seite ' : 'Kapitel ') +
      (format === 'pdf' ? current : current + 1) + (total ? ' / ' + total : '') + ' · ' + percent() + '%';
  };

  const epubBody = root.querySelector('[data-reader-epub-body]');
  const loadEpub = async (index) => {
    const total = root.querySelectorAll('[data-reader-chapter]').length || 1;
    current = Math.max(0, Math.min(total - 1, Number(index) || 0));
    try {
      const chapter = await request(root.dataset.epubUrl + '/' + current);
      currentChapterId = String(chapter.id || current);
      epubBody.innerHTML = chapter.html || '<p>(Leeres Kapitel)</p>';
      epubBody.style.fontSize = (1.05 * fontScale) + 'rem';
      updateStatus(total);
      scheduleSave();
      window.scrollTo({top: root.offsetTop, behavior: 'smooth'});
    } catch (error) { showError(error.message); }
  };

  const frame = root.querySelector('[data-reader-pdf-frame]');
  const nativeBox = root.querySelector('[data-reader-native-pdf]');
  const nativeImage = root.querySelector('[data-reader-pdf-image]');
  const loadPdf = async (page) => {
    current = Math.max(1, pageCount ? Math.min(pageCount, Number(page) || 1) : Number(page) || 1);
    updateStatus(pageCount);
    if (window.SimpleOfficePdf && nativeBox && nativeImage) {
      try {
        const rendered = await window.SimpleOfficePdf.render(root.dataset.pdfUrl, current - 1, Math.min(2048, Math.max(720, window.innerWidth * window.devicePixelRatio * fontScale)));
        if (rendered && rendered.dataUrl) {
          nativeImage.src = rendered.dataUrl;
          nativeBox.classList.remove('d-none');
          frame?.classList.add('d-none');
        }
      } catch (_) {
        nativeBox.classList.add('d-none');
        frame?.classList.remove('d-none');
      }
    }
    scheduleSave();
  };

  root.querySelector('[data-reader-prev]')?.addEventListener('click', () => format === 'pdf' ? loadPdf(current - 1) : loadEpub(current - 1));
  root.querySelector('[data-reader-next]')?.addEventListener('click', () => format === 'pdf' ? loadPdf(current + 1) : loadEpub(current + 1));
  position?.addEventListener('change', () => format === 'pdf' ? loadPdf(Number(position.value)) : loadEpub(Number(position.value) - 1));
  root.querySelectorAll('[data-reader-chapter]').forEach(button => button.addEventListener('click', () => loadEpub(Number(button.dataset.readerChapter))));

  root.querySelector('[data-reader-add-note]')?.addEventListener('click', async () => {
    const input = root.querySelector('[data-reader-note]');
    const text = input?.value.trim() || '';
    if (!text) return;
    try {
      const kind = root.querySelector('[data-reader-note-kind]')?.value || 'note';
      const quote = String(window.getSelection?.().toString() || '').trim().slice(0, 4000);
      await request(root.dataset.annotationApi, {method:'POST', body:JSON.stringify({locator:locator(), text, kind, quote, document_version:version})});
      window.location.reload();
    } catch (error) { showError(error.message); }
  });
  root.querySelectorAll('[data-reader-delete-note]').forEach(button => button.addEventListener('click', async () => {
    try { await request(button.dataset.readerDeleteNote, {method:'POST', body:'{}'}); button.closest('[data-annotation-id]')?.remove(); }
    catch (error) { showError(error.message); }
  }));
  root.querySelector('[data-reader-font-plus]')?.addEventListener('click', () => {
    fontScale = Math.min(1.8, fontScale + .1);
    if (epubBody) epubBody.style.fontSize = (1.05 * fontScale) + 'rem';
    if (format === 'pdf') loadPdf(current);
  });
  root.querySelector('[data-reader-font-minus]')?.addEventListener('click', () => {
    fontScale = Math.max(.7, fontScale - .1);
    if (epubBody) epubBody.style.fontSize = (1.05 * fontScale) + 'rem';
    if (format === 'pdf') loadPdf(current);
  });
  root.querySelectorAll('[data-reader-jump-note]').forEach(button => button.addEventListener('click', () => {
    try {
      const target = JSON.parse(button.dataset.readerJumpNote || '{}');
      if (format === 'pdf') return loadPdf(Number(target.page || 1));
      const byId = [...root.querySelectorAll('[data-reader-chapter]')].find(
        item => String(item.dataset.readerChapterId || '') === String(target.chapter || '')
      );
      return loadEpub(byId ? Number(byId.dataset.readerChapter) : Number(target.chapter_index || 0));
    } catch (error) { showError(error.message); }
  }));
  root.querySelector('[data-reader-theme]')?.addEventListener('click', () => { contrast = !contrast; root.classList.toggle('bg-dark', contrast); root.classList.toggle('text-light', contrast); });
  root.querySelector('[data-reader-fullscreen]')?.addEventListener('click', () => { if (!document.fullscreenElement) root.requestFullscreen?.(); else document.exitFullscreen?.(); });

  if (format === 'epub') {
    const byId = [...root.querySelectorAll('[data-reader-chapter]')].find(
      item => String(item.dataset.readerChapterId || '') === String(saved.chapter || '')
    );
    loadEpub(byId ? Number(byId.dataset.readerChapter) : current);
  } else {
    loadPdf(current);
  }
})();
