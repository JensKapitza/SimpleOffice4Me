/* Kids Piper read-aloud client (offline-first, kids privacy).
   All spoken prompts in the Kids area play through the local Piper TTS
   endpoint (/kids/lernen/sprache) instead of browser SpeechSynthesis voices.
   Rationale: browser voices differ per device (the maze game was
   unintelligible on some devices) and cloud voices would send children's
   speech to a network service. There is intentionally no SpeechSynthesis
   fallback: when Piper is unavailable the caller shows a status message that
   names the missing server component. Clips are addressed by (text, language)
   and cached by the server on disk plus the browser HTTP cache. */
(() => {
  const FAMILIES = {'de-DE': 'kids-de', 'en-US': 'kids-en'};

  function endpoint() {
    const tagged = document.querySelector('[data-tts-url]');
    const url = (tagged && tagged.dataset.ttsUrl) || '/kids/lernen/sprache';
    return url;
  }

  function normalizeLanguage(language) {
    return String(language || '').toLowerCase().startsWith('en') ? 'en-US' : 'de-DE';
  }

  function urlFor(value, language, purpose) {
    const lang = normalizeLanguage(language);
    const params = new URLSearchParams({text: String(value || ''), lang});
    if (purpose === 'instructions') params.set('purpose', purpose);
    return `${endpoint()}?${params.toString()}`;
  }

  let sequence = 0;
  let activePlayer = null;

  function stop() {
    sequence += 1;
    if (activePlayer) {
      const player = activePlayer;
      activePlayer = null;
      try { player.pause(); } catch (error) { /* already gone */ }
    }
  }

  // Speak one text through the Piper endpoint. Resolves true when playback
  // finished, false when cancelled, superseded or unavailable (statusElement
  // then names the missing server voice).
  function speak(value, language, statusElement, purpose) {
    const lang = normalizeLanguage(language);
    const message = String(value || '').trim();
    if (!message || typeof window.Audio !== 'function') {
      if (statusElement) {
        statusElement.textContent = lang === 'de-DE'
          ? 'Vorlesestimme ist nicht eingerichtet.'
          : 'Read-aloud voice is not set up.';
      }
      return Promise.resolve(false);
    }
    // Cancel previous playback first, then claim the new sequence number.
    stop();
    const run = ++sequence;
    const player = new window.Audio(urlFor(message, lang, purpose));
    activePlayer = player;
    return new Promise(resolve => {
      let settled = false;
      const finish = ok => {
        if (settled || run !== sequence) return;
        settled = true;
        if (activePlayer === player) activePlayer = null;
        resolve(ok);
      };
      const unavailable = () => {
        if (settled || run !== sequence) return;
        settled = true;
        if (activePlayer === player) activePlayer = null;
        if (statusElement) {
          statusElement.textContent = lang === 'de-DE'
            ? 'Vorlesen gerade nicht möglich. Bitte versuche es erneut. Wenn es nicht klappt, prüfe die Piper-Stimme mit der App-Verwaltung.'
            : 'Read-aloud is unavailable. Try again. If it still fails, ask the app administrator to check the Piper voice.';
        }
        resolve(false);
      };
      const cancelled = () => { if (run !== sequence) finish(false); };
      player.addEventListener('playing', () => {
        // Retry only clears the previous warning once this announcement starts playing.
        if (!settled && run === sequence && statusElement) statusElement.textContent = '';
      }, {once: true});
      player.addEventListener('ended', () => finish(true), {once: true});
      player.addEventListener('error', unavailable, {once: true});
      player.addEventListener('pause', cancelled, {once: true});
      try {
        const started = player.play();
        if (started && typeof started.catch === 'function') started.catch(unavailable);
      } catch (error) {
        unavailable();
      }
    });
  }

  window.KidsPiperVoice = {endpoint, urlFor, speak, stop, normalizeLanguage, families: FAMILIES};
})();
