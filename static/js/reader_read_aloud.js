(() => {
  'use strict';

  const wavMime = 'audio/wav';
  const maxWavBytes = 8 * 1024 * 1024;
  const maxPagesPerRun = 5;

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
    const continuous = root.querySelector('[data-speech-continuous]');
    const jump = root.querySelector('[data-speech-jump]');
    const pause = root.querySelector('[data-speech-pause]');
    const stop = root.querySelector('[data-speech-stop]');
    const status = root.querySelector('[data-speech-status]');
    const rotation = root.querySelector('[data-speech-rotation]');
    if (!start || !pause || !stop || !status) return;

    const position = reader?.querySelector('[data-reader-position]');
    const epub = reader?.dataset.format === 'epub';
    const previousSegment = root.querySelector('[data-speech-prev-segment]');
    const nextSegment = root.querySelector('[data-speech-next-segment]');
    const segmentStatus = root.querySelector('[data-speech-part]');
    let segment = 1;
    const totalSegments = () => Number(reader?.dataset.speechChapterSegments) || 0;
    const version = reader?.dataset.documentVersion;
    const rawCount = Number(reader?.dataset.pageCount);
    const lastPage = Number.isSafeInteger(rawCount) && rawCount <= 10000 && rawCount > 1 ? rawCount : 0;
    const url = sameOriginUrl(root.dataset.speechUrl);
    const ocrUrl = sameOriginUrl(root.dataset.speechOcrUrl);
    const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
    const pageNumber = () => {
      if (epub) return Number(reader?.dataset.speechChapter) || 0;
      const value = Number(position?.value);
      return Number.isSafeInteger(value) && value > 0 ? value : 1;
    };
    const label = (key, fallback) => root.dataset[key] || fallback;
    const forPage = (key, fallback, page) => label(key, fallback).replace('{page}', String(page));
    const available = Boolean(version && url && csrf() && typeof Audio === 'function' &&
      typeof AbortController === 'function' && typeof URL.createObjectURL === 'function' &&
      typeof URL.revokeObjectURL === 'function');
    let active = null;
    let state = 'idle';
    let displayedPage = pageNumber();
    let jumpPage = null;

    function speechLocator(request) {
      const locator = {page: request.page, document_version: version};
      const clockwise = request.ocr && rotation ? Number(rotation.value) : 0;
      if (clockwise === 90 || clockwise === 270) locator.ocr_clockwise = clockwise;
      if (epub) locator.segment = request.segment;
      return locator;
    }

    function render(nextState, message = '') {
      state = nextState;
      const total = totalSegments();
      const canStart = available && (!epub || (pageNumber() > 0 && total > 0)) &&
        ['idle', 'error', 'unavailable'].includes(state);
      if (previousSegment) previousSegment.disabled = !available || segment <= 1;
      if (nextSegment) nextSegment.disabled = !available || segment >= total;
      if (segmentStatus) segmentStatus.textContent = total
        ? label('speechSegmentLabel', 'Part {current} of {total}')
            .replace('{current}', String(segment)).replace('{total}', String(total))
        : pageNumber() ? label('speechNoText', 'No readable text in this chapter.') : '';
      start.disabled = !canStart;
      if (ocr) ocr.disabled = !canStart || !ocrUrl;
      if (continuous) continuous.disabled = !canStart || !lastPage || pageNumber() >= lastPage;
      if (jump) {
        jump.hidden = jumpPage === null;
        jump.disabled = jump.hidden;
        if (!jump.hidden) jump.textContent = forPage('speechJumpLabel', 'Go to page {page}', jumpPage);
      }
      pause.disabled = !['playing', 'paused'].includes(state);
      stop.disabled = !active;
      if (rotation) rotation.disabled = !canStart || !ocrUrl;
      pause.textContent = state === 'paused'
        ? label('speechResumeLabel', 'Resume') : label('speechPauseLabel', 'Pause');
      status.textContent = message;
    }

    function release(nextState = 'idle', message = '', target = null) {
      const previous = active;
      active = null;
      jumpPage = target;
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
      const pageMatches = request.continuing
        ? request.anchorPage === pageNumber()
        : request.page === pageNumber();
      if (!pageMatches || (epub && request.segment !== segment)) {
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
        if (isCurrent(request)) render('playing', request.continuing
          ? forPage('speechReadingPage', 'Reading page {page}.', request.page)
          : label('speechPlaying', 'Reading aloud.'));
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
          const documentLimit = code === 'document_too_large' &&
            (epub ? root.dataset.speechEpubTooLarge : root.dataset.speechPdfTooLarge);
          release('error', documentLimit || label('speechTooLarge', 'This page exceeds the read-aloud limit.'));
        } else if (statusCode === 422) {
          if (request.continuing && code === 'no_text') {
            release('error', forPage('speechContinuousBlank',
              'Stopped at page {page}: no text layer. Use OCR manually if needed.', request.page),
            request.page === request.anchorPage ? null : request.page);
          } else if (request.ocr && (code === 'ocr_no_text' || code === 'no_text')) {
            release('error', label('speechOcrNoText', 'OCR found no readable text on this page.'));
          } else if (code === 'no_text') {
            release('error', label('speechNoText', 'No readable text on this page.'));
          } else if (code === 'pdf_text_unavailable') {
            release('error', label('speechPdfTextUnavailable', 'Text could not be read from this PDF page.'));
          } else {
            fail(request);
          }
        } else if (code === 'read_aloud_busy' || code === 'read_aloud_timeout') {
          const guidance = code === 'read_aloud_busy'
            ? label('speechBusy', 'Local read-aloud is busy. Wait a moment, then try again.')
            : label('speechTimeout', 'Preparing local audio took too long. Wait a moment, then try again.');
          release('error', guidance, request.continuing && request.page !== request.anchorPage ? request.page : null);
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

    function ended(request) {
      if (!isCurrent(request)) return;
      if (!request.continuing) return release();
      if (state !== 'playing') return;
      if (request.page === request.endPage) {
        if (request.page === lastPage) {
          // Reaching audio EOF is not a display navigation or a saved
          // reading position. Offer the same deliberate page jump as at a stop.
          return release('idle', label('speechContinuousEnd', 'End of PDF.'),
            request.page === request.anchorPage ? null : request.page);
        }
        return release('idle', label('speechContinuousLimit', 'Page limit reached. Start again to continue.'),
          request.page + 1);
      }
      release();
      return fetchPage({endpoint: request.endpoint, anchorPage: request.anchorPage,
        endPage: request.endPage, page: request.page + 1, ocr: false, continuing: true});
    }

    function startPage(endpoint, button) {
      if (!available || !endpoint || button.disabled) return;
      const page = pageNumber();
      const continuing = button === continuous;
      if (continuing && page !== displayedPage) return;
      return fetchPage({
        endpoint, page, anchorPage: page, ocr: button === ocr, continuing,
        endPage: continuing ? Math.min(lastPage, page + maxPagesPerRun - 1) : page,
      });
    }

    async function fetchPage(run) {
      const token = csrf();
      if (!token) {
        release('unavailable', label('speechUnavailable', 'Read aloud is unavailable.'));
        return;
      }

      jumpPage = null;
      const request = {
        ...run,
        page: run.page ?? pageNumber(),
        segment,
        controller: new AbortController(),
        objectUrl: null,
        audio: null,
      };
      active = request;
      render('loading', request.ocr
        ? label('speechOcrLoading', 'Recognizing this page locally…')
        : request.continuing
          ? forPage('speechPreparingPage', 'Preparing page {page}…', request.page)
          : label('speechLoading', 'Preparing audio…'));
      try {
        const response = await fetch(request.endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', Accept: wavMime, 'X-CSRF-Token': token},
          credentials: 'same-origin',
          cache: 'no-store',
          redirect: 'error',
          signal: request.controller.signal,
          body: JSON.stringify(speechLocator(request)),
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
        request.audio.addEventListener('ended', () => ended(request));
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
    continuous?.addEventListener('click', () => startPage(url, continuous));
    if (ocrUrl && ocr) ocr.addEventListener('click', () => startPage(ocrUrl, ocr));
    pause.addEventListener('click', () => {
      if (!active || !active.audio || pause.disabled || !isCurrent(active)) return;
      if (state === 'paused') return play(active);
      active.audio.pause();
      render('paused', label('speechPaused', 'Paused.'));
    });
    stop.addEventListener('click', () => release());
    jump?.addEventListener('click', () => {
      if (jumpPage === null || !position) return;
      const target = jumpPage;
      release();
      position.value = String(target);
      position.dispatchEvent(new Event('change', {bubbles: true}));
    });
    const selectSegment = next => {
      if (next < 1 || next > totalSegments() || next === segment) return;
      segment = next;
      release();
    };
    previousSegment?.addEventListener('click', () => selectSegment(segment - 1));
    nextSegment?.addEventListener('click', () => selectSegment(segment + 1));
    if (epub) reader.addEventListener('reader:chapter-displayed', () => {
      const nextPage = pageNumber();
      if (displayedPage !== nextPage || segment > totalSegments()) segment = 1;
      displayedPage = nextPage;
      release();
    });
    reader.addEventListener('reader:page-changed', event => {
      const nextPage = event.detail?.page ?? pageNumber();
      if (displayedPage === nextPage) return;
      displayedPage = nextPage;
      if (rotation) rotation.value = '0';
      release();
    });
    window.addEventListener('pagehide', () => { if (active) release(); });
  }

  document.querySelectorAll('[data-reader-read-aloud]').forEach(initialize);
})();
