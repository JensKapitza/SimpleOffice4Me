(() => {
  'use strict';

  const wavMime = 'audio/wav';
  const maxWavBytes = 8 * 1024 * 1024;

  function sameOriginUrl(value) {
    if (!value) return null;
    try {
      const url = new URL(value, window.location.href);
      if (url.origin === window.location.origin && /^(https?):$/.test(url.protocol) &&
          !url.username && !url.password && !url.hash) return url.href;
    } catch (_) {
      // Malformed configuration is handled as unavailable below.
    }
    return null;
  }

  async function readWav(response) {
    if (response.headers.get('Content-Type') !== wavMime) throw new Error('Unexpected audio type');
    const length = response.headers.get('Content-Length');
    if (length && /^\d+$/.test(length) && Number(length) > maxWavBytes) throw new Error('WAV too large');
    const blob = await response.blob();
    if (blob.size < 44 || blob.size > maxWavBytes) throw new Error('Invalid WAV size');
    const header = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
    const signature = String.fromCharCode(...header);
    if (!signature.startsWith('RIFF') || signature.slice(8) !== 'WAVE') throw new Error('Invalid WAV');
    return blob;
  }

  function initialize(root) {
    const reader = root.closest('[data-document-reader], #reader-root');
    const start = root.querySelector('[data-speech-start]');
    const ocr = root.querySelector('[data-speech-ocr]');
    const pause = root.querySelector('[data-speech-pause]');
    const stop = root.querySelector('[data-speech-stop]');
    const status = root.querySelector('[data-speech-status]');
    if (!start || !pause || !stop || !status) return;

    const position = reader?.querySelector('[data-reader-position]');
    const version = reader?.dataset.documentVersion;
    const url = sameOriginUrl(root.dataset.speechUrl);
    const ocrUrl = sameOriginUrl(root.dataset.speechOcrUrl);
    const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
    const pageNumber = () => {
      const value = Number(position?.value);
      return Number.isSafeInteger(value) && value > 0 ? value : 1;
    };
    const label = (key, fallback) => root.dataset[key] || fallback;
    const available = Boolean(version && url && csrf() && typeof Audio === 'function' &&
      typeof AbortController === 'function' && typeof URL.createObjectURL === 'function' &&
      typeof URL.revokeObjectURL === 'function');
    let active = null;
    let state = 'idle';
    let displayedPage = pageNumber();

    function render(nextState, message = '') {
      state = nextState;
      const canStart = available && ['idle', 'error', 'unavailable'].includes(state);
      start.disabled = !canStart;
      if (ocr) ocr.disabled = !canStart || !ocrUrl;
      pause.disabled = !['playing', 'paused'].includes(state);
      stop.disabled = !active;
      pause.textContent = state === 'paused'
        ? label('speechResumeLabel', 'Resume') : label('speechPauseLabel', 'Pause');
      status.textContent = message;
    }

    function release(nextState = 'idle', message = '') {
      const previous = active;
      active = null;
      if (previous) {
        previous.controller.abort();
        if (previous.audio) {
          previous.audio.pause();
          previous.audio.removeAttribute('src');
          previous.audio.load();
        }
        if (previous.objectUrl) URL.revokeObjectURL(previous.objectUrl);
      }
      render(nextState, message);
    }

    function isCurrent(request) {
      if (active !== request) return false;
      if (request.page !== pageNumber()) {
        release();
        return false;
      }
      return true;
    }

    function fail(request) {
      if (isCurrent(request)) release('error', label('speechError', 'Could not play this page.'));
    }

    async function play(request) {
      if (!isCurrent(request)) return;
      render('loading', label('speechLoading', 'Preparing audio…'));
      try {
        await request.audio.play();
        if (isCurrent(request)) render('playing', label('speechPlaying', 'Reading aloud.'));
      } catch (error) {
        if (!isCurrent(request)) return;
        if (error?.name === 'NotAllowedError') {
          render('paused', label('speechPlayRequired', 'Audio ready. Select Resume to play.'));
        } else {
          fail(request);
        }
      }
    }

    async function showHttpError(request, response) {
      const statusCode = response.status;
      if (statusCode === 413 || statusCode === 422 || statusCode === 503) {
        // Only recognized application codes may select localized messages.
        const problem = await response.json().catch(() => null);
        if (!isCurrent(request)) return;
        const code = problem?.error;
        if (statusCode === 413) {
          const documentLimit = code === 'document_too_large' && root.dataset.speechPdfTooLarge;
          release('error', documentLimit || label('speechTooLarge', 'This page exceeds the read-aloud limit.'));
        } else if (statusCode === 422) {
          if (request.ocr && (code === 'ocr_no_text' || code === 'no_text')) {
            release('error', label('speechOcrNoText', 'OCR found no readable text on this page.'));
          } else if (code === 'no_text') {
            release('error', label('speechNoText', 'No readable text on this page.'));
          } else if (code === 'pdf_text_unavailable') {
            release('error', label('speechPdfTextUnavailable', 'Text could not be read from this PDF page.'));
          } else {
            fail(request);
          }
        } else if (code === 'pdf_source_timeout') {
          release('error', label('speechPdfSourceTimeout', 'Preparing the PDF took too long.'));
        } else if (code === 'pdf_stage_unavailable') {
          release('error', label('speechPdfStageUnavailable', 'The server could not prepare the PDF.'));
        } else if (request.ocr && code === 'ocr_unavailable') {
          release('unavailable', label('speechOcrUnavailable', 'Local OCR is unavailable.'));
        } else if (request.ocr && code === 'ocr_timeout') {
          release('error', label('speechOcrTimeout', 'Local OCR took too long.'));
        } else {
          release('unavailable', label('speechUnavailable', 'Read aloud is unavailable.'));
        }
      } else if (statusCode === 409) {
        release('error', label('speechStale', 'This document changed. Reload the reader.'));
      } else {
        fail(request);
      }
    }

    async function startPage(endpoint, button) {
      if (!available || !endpoint || button.disabled) return;
      const token = csrf();
      if (!token) {
        render('unavailable', label('speechUnavailable', 'Read aloud is unavailable.'));
        return;
      }

      const request = {page: pageNumber(), ocr: button === ocr, controller: new AbortController()};
      active = request;
      render('loading', request.ocr
        ? label('speechOcrLoading', 'Recognizing this page locally…')
        : label('speechLoading', 'Preparing audio…'));
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', Accept: wavMime, 'X-CSRF-Token': token},
          credentials: 'same-origin',
          cache: 'no-store',
          redirect: 'error',
          signal: request.controller.signal,
          body: JSON.stringify({page: request.page, document_version: version}),
        });
        if (!isCurrent(request)) return;
        if (!response.ok) {
          await showHttpError(request, response);
          return;
        }
        const blob = await readWav(response);
        if (!isCurrent(request)) return;
        request.objectUrl = URL.createObjectURL(blob);
        request.audio = new Audio();
        request.audio.addEventListener('ended', () => { if (isCurrent(request)) release(); });
        request.audio.addEventListener('error', () => fail(request));
        request.audio.src = request.objectUrl;
        await play(request);
      } catch (_) {
        fail(request);
      }
    }

    render(available ? 'idle' : 'unavailable', available ? '' :
      label('speechUnavailable', 'Read aloud is unavailable.'));
    if (!available) return;
    start.addEventListener('click', () => startPage(url, start));
    if (ocrUrl && ocr) ocr.addEventListener('click', () => startPage(ocrUrl, ocr));
    pause.addEventListener('click', () => {
      if (!active || !active.audio || pause.disabled || !isCurrent(active)) return;
      if (state === 'paused') return play(active);
      active.audio.pause();
      render('paused', label('speechPaused', 'Paused.'));
    });
    stop.addEventListener('click', () => release());
    reader.addEventListener('reader:page-changed', event => {
      const nextPage = event.detail?.page ?? pageNumber();
      if (displayedPage === nextPage) return;
      displayedPage = nextPage;
      release();
    });
    window.addEventListener('pagehide', () => { if (active) release(); });
  }

  document.querySelectorAll('[data-reader-read-aloud]').forEach(initialize);
})();
