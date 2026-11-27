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

  function playClips(base, version, lang, parts, language, statusElement) {
    const suffix = version ? `?v=${encodeURIComponent(version)}` : '';
    const queue = parts.map(part => ({
      url: part.key ? `${base}/${lang}/${part.key}.mp3${suffix}` : '',
      text: part.fallback, lang: language,
    }));
    if (window.KidsPiperVoice?.playSequence) return window.KidsPiperVoice.playSequence(queue, statusElement);
    return Promise.resolve(false);
  }

  const stopAudio = stopSpeaking;
  window.KidsShared = {shuffle, speak, stopSpeaking, stopAudio, uiLanguage, playClips};
})();
