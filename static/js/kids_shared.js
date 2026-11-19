/* Shared Kids helpers: random tasks and spoken audio feedback.
   Offline-first: speech uses on-device voices first, no network calls. */
(() => {
  function shuffle(items) {
    const values = [...items];
    for (let index = values.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [values[index], values[swap]] = [values[swap], values[index]];
    }
    return values;
  }

  function voiceRank(voice, language) {
    // On-device voices first: same-language local voices must beat
    // same-language cloud voices so prompts stay offline and private.
    return (voice.lang.toLowerCase() === language.toLowerCase() ? 100 : 50)
      + (voice.localService ? 24 : 0)
      + (/natural|neural|premium|enhanced/i.test(voice.name) ? 6 : 0);
  }

  function speak(value, language, statusElement) {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      if (statusElement) statusElement.textContent = '';
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = language;
    utterance.rate = .86;
    utterance.pitch = 1.04;
    const prefix = language.slice(0, 2).toLowerCase();
    const voices = window.speechSynthesis.getVoices().filter(voice => voice.lang.toLowerCase().startsWith(prefix));
    let requested = '';
    try { requested = window.localStorage.getItem(`kids-learning.voice.${prefix}`) || ''; } catch (error) { requested = ''; }
    const voiceId = voice => voice.voiceURI || `${voice.name}|${voice.lang}`;
    const chosen = voices.find(voice => voiceId(voice) === requested)
      || [...voices].sort((a, b) => voiceRank(b, language) - voiceRank(a, language))[0];
    if (chosen) utterance.voice = chosen;
    utterance.onstart = () => { if (statusElement) statusElement.textContent = ''; };
    utterance.onerror = event => {
      if (statusElement && !['canceled', 'interrupted'].includes(event.error)) {
        statusElement.textContent = prefix === 'de'
          ? 'Die Stimme konnte nicht abgespielt werden.'
          : 'The voice could not be played.';
      }
    };
    window.speechSynthesis.speak(utterance);
  }

  function stopSpeaking() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
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
