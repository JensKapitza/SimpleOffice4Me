(() => {
  'use strict';
  const root = document.getElementById('reader-root');
  if (!root) return;
  const format = root.dataset.format;
  const version = root.dataset.documentVersion;
  const status = root.querySelector('[data-reader-status]');
  const position = root.querySelector('[data-reader-position]');
  const errorBox = root.querySelector('[data-reader-error]');
  const staleProgress = root.querySelector('[data-reader-stale-progress]');
  const saveNewProgress = root.querySelector('[data-reader-save-new-progress]');
  const saveNewStatus = root.querySelector('[data-reader-save-new-status]');
  let progressPaused = Boolean(staleProgress);
  let positionReady = false;
  let acceptingProgress = false;
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const saved = (() => { try { return JSON.parse(root.dataset.savedLocator || '{}'); } catch (_) { return {}; } })();
  let current = format === 'pdf' ? Math.max(1, Number(saved.page || 1)) : Math.max(0, Number(saved.chapter_index || 0));
  let currentChapterId = format === 'epub' ? String(saved.chapter || '') : '';
  let currentOffset = format === 'epub' ? Math.max(0, Number(saved.offset || 0)) : 0;
  let restoredOffset = false;
  let requestedEpub = current;
  let resumeEpubIndex = current;
  let epubRequest = 0;
  let displayedEpubRequest = 0;
  let errorSource;
  let scrollTimer = null;
  let fontScale = 1;
  let contrast = false;
  let saveTimer = null;
  const pageCount = Math.max(0, Number(root.dataset.pageCount || 0));
  // The publisher TOC may omit chapters. Older cached markup can only provide
  // a lower bound in the form of the last linked spine position.
  const epubChapterCount = () => {
    const count = Number(root.dataset.chapterCount);
    if (Number.isSafeInteger(count) && count > 0) return count;
    return [...root.querySelectorAll('[data-reader-chapter]')].reduce((highest, item) => {
      const index = Number(item.dataset.readerChapter);
      return Number.isSafeInteger(index) && index >= 0 ? Math.max(highest, index + 1) : highest;
    }, 1);
  };
  const chapterCount = format === 'epub' ? epubChapterCount() : 0;

  const showError = (message, source) => {
    if (!errorBox) return;
    errorSource = source;
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
    : {chapter: currentChapterId || String(current), chapter_index: current, anchor: '', cfi: '', offset: currentOffset};
  const percent = () => {
    const total = format === 'pdf' ? pageCount : chapterCount;
    return total > 0 ? Math.min(100, Math.max(0, Math.round(((current + (format === 'pdf' ? 0 : 1)) / total) * 100))) : 0;
  };
  const progressPayload = () => JSON.stringify({
    locator: locator(), percent: percent(), document_version: version
  });
  const writeProgress = body => request(root.dataset.progressApi, {method: 'POST', body});
  const scheduleSave = () => {
    positionReady = true;
    if (progressPaused) {
      if (saveNewProgress && !acceptingProgress) saveNewProgress.disabled = false;
      return;
    }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try { await writeProgress(progressPayload()); }
      catch (error) { showError(error.message); }
    }, 350);
  };
  saveNewProgress?.addEventListener('click', async () => {
    if (!progressPaused || !positionReady || acceptingProgress) return;
    acceptingProgress = true;
    saveNewProgress.disabled = true;
    saveNewStatus.textContent = 'Speichert …';
    const body = progressPayload();
    try {
      const state = await writeProgress(body);
      if (state?.document_version !== version || state.stale !== false) {
        throw new Error('Speichern konnte nicht bestätigt werden. Bitte lade den Reader neu.');
      }
      progressPaused = false;
      const returnFocus = document.activeElement === saveNewProgress;
      staleProgress.hidden = true;
      if (returnFocus) position?.focus();
      if (progressPayload() !== body) scheduleSave();
    } catch (error) {
      saveNewStatus.textContent = error.message;
    } finally {
      acceptingProgress = false;
      saveNewProgress.disabled = false;
    }
  });
  const updateStatus = (total) => {
    if (position) position.value = String(format === 'pdf' ? current : current + 1);
    if (status) status.textContent = (format === 'pdf' ? 'Seite ' : 'Kapitel ') +
      (format === 'pdf' ? current : current + 1) + (total ? ' / ' + total : '') + ' · ' + percent() + '%';
  };

  const epubBody = root.querySelector('[data-reader-epub-body]');
  const addNote = root.querySelector('[data-reader-add-note]');
  if (format === 'epub' && addNote) addNote.disabled = true;
  const loadEpub = async (index) => {
    requestedEpub = Math.max(0, Math.min(chapterCount - 1, Math.trunc(Number(index) || 0)));
    const target = requestedEpub;
    const requestId = ++epubRequest;
    try {
      const chapter = await request(root.dataset.epubUrl + '/' + target);
      if (requestId !== epubRequest) return;
      clearTimeout(scrollTimer);
      displayedEpubRequest = requestId;
      current = target;
      currentChapterId = String(chapter.id || target);
      epubBody.innerHTML = chapter.html || '<p>(Leeres Kapitel)</p>';
      epubBody.style.fontSize = (1.05 * fontScale) + 'rem';
      if (addNote) addNote.disabled = false;
      if (errorSource === 'epub') {
        errorBox?.classList.add('d-none');
        errorSource = undefined;
      }
      updateStatus(chapterCount);
      if (!restoredOffset && resumeEpubIndex === current && currentOffset > 0) {
        restoredOffset = true;
        const offset = currentOffset;
        requestAnimationFrame(() => {
          if (displayedEpubRequest === requestId) {
            window.scrollTo({top: epubBody.offsetTop + offset, behavior: 'auto'});
          }
        });
      } else {
        currentOffset = 0;
        window.scrollTo({top: root.offsetTop, behavior: 'smooth'});
      }
      scheduleSave();
    } catch (error) {
      if (requestId !== epubRequest) return;
      requestedEpub = displayedEpubRequest ? current : target;
      if (position) position.value = String(requestedEpub + 1);
      showError(error.message, 'epub');
    }
  };

  const frame = root.querySelector('[data-reader-pdf-frame]');
  const nativeBox = root.querySelector('[data-reader-native-pdf]');
  const nativeImage = root.querySelector('[data-reader-pdf-image]');
  const showPdfFramePage = () => {
    if (!frame) return;
    const url = new URL(root.dataset.pdfUrl, window.location.href);
    url.hash = 'page=' + current;
    frame.src = url.toString();
    frame.classList.remove('d-none');
  };
  let pdfRequest = 0;
  const loadPdf = async (page) => {
    const requestId = ++pdfRequest;
    const previous = current;
    current = Math.max(1, pageCount ? Math.min(pageCount, Number(page) || 1) : Number(page) || 1);
    updateStatus(pageCount);
    if (current !== previous) {
      root.dispatchEvent(new CustomEvent('reader:page-changed', {bubbles: true, detail: {page: current}}));
    }
    if (window.SimpleOfficePdf && nativeBox && nativeImage) {
      try {
        const rendered = await window.SimpleOfficePdf.render(root.dataset.pdfUrl, current - 1, Math.min(2048, Math.max(720, window.innerWidth * window.devicePixelRatio * fontScale)));
        if (requestId !== pdfRequest) return;
        if (rendered && rendered.dataUrl) {
          nativeImage.src = rendered.dataUrl;
          nativeBox.classList.remove('d-none');
          frame?.classList.add('d-none');
        }
      } catch (_) {
        if (requestId !== pdfRequest) return;
        nativeBox.classList.add('d-none');
        showPdfFramePage();
      }
    } else {
      showPdfFramePage();
    }
    scheduleSave();
  };

  root.querySelector('[data-reader-prev]')?.addEventListener('click', () => format === 'pdf' ? loadPdf(current - 1) : loadEpub(requestedEpub - 1));
  root.querySelector('[data-reader-next]')?.addEventListener('click', () => format === 'pdf' ? loadPdf(current + 1) : loadEpub(requestedEpub + 1));
  position?.addEventListener('change', () => format === 'pdf' ? loadPdf(Number(position.value)) : loadEpub(Number(position.value) - 1));
  root.querySelectorAll('[data-reader-chapter]').forEach(button => button.addEventListener('click', () => loadEpub(Number(button.dataset.readerChapter))));

  addNote?.addEventListener('click', async () => {
    if (format === 'epub' && !displayedEpubRequest) return;
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
  window.addEventListener('scroll', () => {
    if (format !== 'epub' || !epubBody || !displayedEpubRequest) return;
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      currentOffset = Math.max(0, Math.round(window.scrollY - epubBody.offsetTop));
      scheduleSave();
    }, 250);
  }, {passive: true});

  if (format === 'epub') {
    const byId = [...root.querySelectorAll('[data-reader-chapter]')].find(
      item => String(item.dataset.readerChapterId || '') === String(saved.chapter || '')
    );
    resumeEpubIndex = byId ? Number(byId.dataset.readerChapter) : current;
    loadEpub(resumeEpubIndex);
  } else {
    loadPdf(current);
  }
})();
