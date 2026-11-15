(() => {
  window.KidsLanguageAudio = ({text, speechStatus, selectedVoices}) => {
    let activeAudio = null;
    let sequence = 0;
    function speak(value, language = 'en-US') {
      if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
        speechStatus.textContent = text('Vorlesestimme auf diesem Gerät nicht verfügbar.','Read-aloud speech is not available on this device.');
        return Promise.resolve(false);
      }
      const queue = (Array.isArray(value) ? value : [{text:value, language}]).filter(segment => String(segment.text || '').trim());
      window.speechSynthesis.cancel();
      return new Promise(resolve => {
        let index = 0;
        const next = () => {
          if (index >= queue.length) { resolve(true); return; }
          const segment = queue[index++];
          const utterance = new SpeechSynthesisUtterance(String(segment.text).trim());
          utterance.lang = segment.language || language;
          utterance.rate = .86;
          utterance.pitch = 1.04;
          const languageKey = utterance.lang.toLowerCase().startsWith('de') ? 'de' : 'en';
          const available = window.speechSynthesis.getVoices().filter(voice => voice.lang.toLowerCase().startsWith(languageKey));
          const requested = available.find(voice => (voice.voiceURI || `${voice.name}|${voice.lang}`) === selectedVoices[languageKey]);
          const score = voice => (voice.lang.toLowerCase() === utterance.lang.toLowerCase() ? 100 : 50) + (voice.localService ? 8 : 0) + (/natural|neural|premium|enhanced/i.test(voice.name) ? 12 : 0) + (voice.default ? 1 : 0);
          const chosen = requested || [...available].sort((a,b) => score(b) - score(a))[0];
          if (chosen) utterance.voice = chosen;
          utterance.onerror = event => {
            if (!['canceled','interrupted'].includes(event.error)) speechStatus.textContent = text('Die Stimme konnte nicht abgespielt werden. Wähle eine andere Stimme.','The voice could not be played. Choose another voice.');
            resolve(false);
          };
          utterance.onstart = () => { speechStatus.textContent = ''; };
          utterance.onend = next;
          window.speechSynthesis.speak(utterance);
        };
        next();
      });
    }
    function playRecorded(url, fallbackText, language, current = sequence) {
      if (!url || typeof window.Audio !== 'function') return speak(fallbackText, language);
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      if (activeAudio) activeAudio.pause();
      const player = new Audio(url);
      activeAudio = player;
      return new Promise(resolve => {
        let settled = false;
        const finish = value => { if (!settled) { settled = true; resolve(value); } };
        const fallback = () => {
          if (settled) return;
          settled = true;
          if (activeAudio === player) activeAudio = null;
          if (current !== sequence) { resolve(false); return; }
          speak(fallbackText, language).then(resolve);
        };
        player.addEventListener('ended', () => { if (activeAudio === player) activeAudio = null; finish(true); }, {once:true});
        player.addEventListener('error', fallback, {once:true});
        player.addEventListener('pause', () => { if (current !== sequence) finish(false); }, {once:true});
        player.play().catch(fallback);
      });
    }
    function readQuestion(item, prompt) {
      const current = ++sequence;
      if (activeAudio) { activeAudio.pause(); activeAudio = null; }
      const language = item.promptLang || item.speechLang || 'en-US';
      const audio = item.questionAudioUrl ? playRecorded(item.questionAudioUrl, prompt, language, current) : speak(prompt, language);
      Promise.resolve(audio).then(() => {
        if (current === sequence && item.pronunciationAudioUrl) playRecorded(item.pronunciationAudioUrl, item.speak, item.speechLang || language, current);
      });
    }
    function playSolution(item) {
      const current = ++sequence;
      const clips = [[item.pronunciationAudioUrl,item.speak,item.speechLang || 'en-US'],[item.solutionAudioUrl,item.a,'de-DE']]
        .filter(([url], index, all) => url && all.findIndex(candidate => candidate[0] === url) === index);
      if (!clips.length) return speak(item.solutionWord || item.a, item.speechLang || 'en-US');
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      if (activeAudio) { activeAudio.pause(); activeAudio = null; }
      return clips.reduce((chain, [url, fallback, lang]) => chain.then(() => current === sequence ? playRecorded(url, fallback, lang, current) : false), Promise.resolve());
    }
    function stop() {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      sequence += 1;
      if (activeAudio) { activeAudio.pause(); activeAudio = null; }
    }
    return {speak, readQuestion, playSolution, stop};
  };
})();
