/* One cancelable Kids audio queue. Prompt text travels only in an authenticated
   same-origin POST, never in an audio URL or browser speech API. */
(() => {
  const FAMILIES = {'de-DE': 'kids-de', 'en-US': 'kids-en'};
  const owner = {};
  let active = null;

  function endpoint() {
    return document.querySelector('[data-tts-url]')?.dataset.ttsUrl || '/kids/lernen/sprache';
  }

  function normalizeLanguage(language) {
    return String(language || '').toLowerCase().startsWith('en') ? 'en-US' : 'de-DE';
  }

  function localURL(value) {
    const url = new URL(value, window.location.href);
    if (!['http:', 'https:'].includes(url.protocol) || url.origin !== window.location.origin ||
        url.username || url.password || url.hash) throw new Error('Not a local audio URL');
    return url;
  }

  function stop() {
    active?.finish(false);
  }

  function playMedia(source, run, statusElement) {
    return new Promise(resolve => {
      let player;
      const finish = ok => {
        if (run.release !== finish) return;
        run.release = null;
        if (player) {
          try { player.pause(); } catch {}
          try { player.removeAttribute?.('src'); player.load?.(); } catch {}
        }
        resolve(ok);
      };
      run.release = finish;
      try {
        player = new window.Audio(source);
        player.addEventListener('playing', () => {
          if (active === run && run.release === finish && statusElement) statusElement.textContent = '';
        }, {once: true});
        player.addEventListener('ended', () => finish(true), {once: true});
        player.addEventListener('error', () => finish(false), {once: true});
        player.addEventListener('pause', () => finish(false), {once: true});
        player.play()?.catch(() => finish(false));
      } catch {
        finish(false);
      }
    });
  }

  async function speakPart(part, lang, run, statusElement) {
    const value = typeof part.text === 'function' ? part.text() : part.text;
    const text = String(value ?? '').trim();
    if (!text || active !== run) return false;
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
    if (!csrf) return false;
    let url;
    try {
      const endpointUrl = localURL(endpoint());
      if (endpointUrl.search) return false;
      const response = await window.fetch(endpointUrl.href, {
        method: 'POST',
        credentials: 'same-origin',
        redirect: 'error',
        cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({text, lang, purpose: part.purpose === 'instructions' ? 'instructions' : ''}),
        signal: run.controller.signal,
      });
      if (active !== run || !response.ok || !/^audio\/(?:wav|x-wav)(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')) return false;
      const blob = await response.blob();
      if (active !== run || !blob.size) return false;
      url = window.URL.createObjectURL(blob);
      run.objectURL = url;
      return await playMedia(url, run, statusElement);
    } catch {
      return false;
    } finally {
      if (url && run.objectURL === url) {
        run.objectURL = null;
        window.URL.revokeObjectURL(url);
      }
    }
  }

  function unavailable(status, language) {
    if (!status) return;
    status.textContent = language === 'en-US'
      ? 'Read-aloud is unavailable. Read the question or tap Hear again.'
      : 'Vorlesen gerade nicht möglich. Lies die Aufgabe oder tippe auf Nochmal hören.';
  }

  function playSequence(parts, statusElement) {
    stop();
    if (document.hidden || !parts.length) return Promise.resolve(false);
    if (typeof window.Audio !== 'function' || typeof window.AbortController !== 'function') {
      unavailable(statusElement, normalizeLanguage(parts[0].lang));
      return Promise.resolve(false);
    }
    return new Promise(resolve => {
      const run = {controller: new window.AbortController(), release: null, objectURL: null};
      active = run;
      run.finish = result => {
        if (active !== run) return;
        active = null;
        run.controller.abort();
        run.release?.(false);
        if (run.objectURL) {
          window.URL.revokeObjectURL(run.objectURL);
          run.objectURL = null;
        }
        resolve(result);
      };
      if (typeof window.CustomEvent === 'function') {
        window.dispatchEvent(new window.CustomEvent('simpleoffice:speech-start', {detail: {owner}}));
      }
      void (async () => {
        let failedLang = null;
        for (const part of parts) {
          if (active !== run) return;
          const lang = normalizeLanguage(part.lang);
          const recording = part.url && localURL(part.url).href;
          let ok = recording ? await playMedia(recording, run, statusElement) : false;
          if (!ok && active === run && part.fallback) {
            ok = true;
            for (const fallback of part.fallback) {
              if (active !== run) return;
              const completed = await speakPart(fallback, normalizeLanguage(fallback.lang), run, statusElement);
              ok = ok && completed;
            }
          } else if (!ok && active === run) ok = await speakPart(part, lang, run, statusElement);
          if (!ok) failedLang ||= lang;
        }
        if (active === run && failedLang) unavailable(statusElement, failedLang);
        run.finish(!failedLang);
      })().catch(() => {
        if (active === run) unavailable(statusElement, normalizeLanguage(parts[0].lang));
        run.finish(false);
      });
    });
  }

  function speak(text, lang, statusElement, purpose) {
    return playSequence([{text, lang, purpose}], statusElement);
  }

  document.addEventListener?.('visibilitychange', () => { if (document.hidden) stop(); });
  document.querySelectorAll?.('[data-kids-stop-audio]').forEach(button => button.addEventListener('click', stop));
  window.addEventListener?.('pagehide', stop);
  window.addEventListener?.('simpleoffice:speech-stop', stop);
  window.addEventListener?.('simpleoffice:speech-start', event => {
    if (event.detail?.owner && event.detail.owner !== owner) stop();
  });
  window.KidsPiperVoice = {endpoint, speak, stop, playSequence, normalizeLanguage, families: FAMILIES};
})();
