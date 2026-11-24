/* Shared Kids helpers: random tasks and spoken audio feedback.
   Offline-first: speech renders on this server (local Piper TTS endpoint),
   never in the browser and never over an external network service. There is
   intentionally no SpeechSynthesis fallback (consistent voice, kids privacy). */
(() => {
  function shuffle(items) {
    const values = [...items];
    for (let index = values.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [values[index], values[swap]] = [values[swap], values[index]];
    }
    return values;
  }

  function speak(value, language, statusElement) {
    // Lazy clip fallbacks can decline stale requests after activity changes.
    if (typeof value === 'function') {
      value = value();
      if (value === null) return Promise.resolve(false);
    }
    if (statusElement) statusElement.textContent = '';
    if (window.KidsPiperVoice) return window.KidsPiperVoice.speak(value, language, statusElement);
    if (statusElement) {
      statusElement.textContent = String(language || '').toLowerCase().startsWith('en')
        ? 'Read-aloud voice is not available.'
        : 'Vorlesestimme ist nicht verfügbar.';
    }
    return Promise.resolve(false);
  }

  function stopSpeaking() {
    if (window.KidsPiperVoice) window.KidsPiperVoice.stop();
  }

  function uiLanguage(english) { return english ? 'en-US' : 'de-DE'; }

  // Pre-recorded clips first, speech synthesis per clip as fallback.
  // parts: [{key, fallback}] played in order; a missing clip never breaks
  // the sequence. stopAudio() cancels everything including pending clips.
  let audioSequence = 0;
  let activePlayer = null;

  function stopAudio() {
    audioSequence += 1;
    stopSpeaking();
    if (activePlayer) {
      const player = activePlayer;
      activePlayer = null;
      try { player.pause(); } catch (error) { /* already gone */ }
    }
  }

  function playClip(url, fallback, language, statusElement) {
    if (!url || typeof window.Audio !== 'function') {
      speak(fallback, language, statusElement);
      return Promise.resolve(false);
    }
    stopSpeaking();
    if (activePlayer) {
      try { activePlayer.pause(); } catch (error) { /* already gone */ }
    }
    const player = new window.Audio(url);
    activePlayer = player;
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true;
        if (activePlayer === player) activePlayer = null;
        resolve(value);
      };
      player.addEventListener('ended', () => finish(true), {once: true});
      player.addEventListener('error', () => {
        if (settled) return;
        settled = true;
        if (activePlayer === player) activePlayer = null;
        speak(fallback, language, statusElement);
        resolve(false);
      }, {once: true});
      try {
        const started = player.play();
        if (started && typeof started.catch === 'function') {
          started.catch(() => {
            if (settled) return;
            settled = true;
            if (activePlayer === player) activePlayer = null;
            speak(fallback, language, statusElement);
            resolve(false);
          });
        }
      } catch (error) {
        finish(false);
      }
    });
  }

  function playClips(base, version, lang, parts, language, statusElement) {
    const current = ++audioSequence;
    stopSpeaking();
    const prefix = `${base}/${lang}/`;
    const suffix = version ? `?v=${encodeURIComponent(version)}` : '';
    let chain = Promise.resolve(true);
    parts.forEach(part => {
      chain = chain.then(async previous => {
        if (current !== audioSequence) return false;
        await playClip(`${prefix}${part.key}.mp3${suffix}`, part.fallback, language, statusElement);
        return previous;
      });
    });
    return chain.then(() => current === audioSequence);
  }

  window.KidsShared = {shuffle, speak, stopSpeaking, stopAudio, uiLanguage, playClips};
})();
