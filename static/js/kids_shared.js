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
    return (voice.lang.toLowerCase() === language.toLowerCase() ? 100 : 50)
      + (voice.localService ? 8 : 0)
      + (/natural|neural|premium|enhanced/i.test(voice.name) ? 12 : 0);
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

  window.KidsShared = {shuffle, speak, stopSpeaking, uiLanguage};
})();
