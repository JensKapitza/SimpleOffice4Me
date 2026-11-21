/* Kids language-game audio: pre-recorded clips first, local Piper voice fallback.
   Speech always stays on this server: recorded MP3 clips play when present,
   every other prompt renders through the Piper endpoint (KidsPiperVoice).
   There is intentionally no browser SpeechSynthesis fallback (offline-first,
   consistent maze/quiz voice, kids privacy). */
(() => {
  window.KidsLanguageAudio = ({text, speechStatus}) => {
    let activeAudio = null;
    let sequence = 0;
    let speakRun = 0;
    const piper = () => window.KidsPiperVoice;
    function speak(value, language = 'en-US') {
      const queue = (Array.isArray(value) ? value : [{text: value, language}]).filter(segment => String(segment.text || '').trim());
      if (!queue.length) return Promise.resolve(false);
      // Own generation: rapid repeats cancel the older segment chain without
      // disturbing the question/solution generation tracked in `sequence`.
      const run = ++speakRun;
      piper().stop();
      if (activeAudio) { activeAudio.pause(); activeAudio = null; }
      return queue.reduce((chain, segment) => chain.then(ok => {
        if (!ok || run !== speakRun) return false;
        return piper().speak(segment.text, segment.language || language, speechStatus);
      }), Promise.resolve(true));
    }
    function playRecorded(url, fallbackText, language, current = sequence) {
      if (!url || typeof window.Audio !== 'function') return speak(fallbackText, language);
      piper().stop();
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
        player.addEventListener('ended', () => { if (activeAudio === player) activeAudio = null; finish(true); }, {once: true});
        player.addEventListener('error', fallback, {once: true});
        player.addEventListener('pause', () => { if (current !== sequence) finish(false); }, {once: true});
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
      const clips = [[item.pronunciationAudioUrl, item.speak, item.speechLang || 'en-US'], [item.solutionAudioUrl, item.a, 'de-DE']]
        .filter(([url], index, all) => url && all.findIndex(candidate => candidate[0] === url) === index);
      if (!clips.length) return speak(item.solutionWord || item.a, item.speechLang || 'en-US');
      piper().stop();
      if (activeAudio) { activeAudio.pause(); activeAudio = null; }
      return clips.reduce((chain, [url, fallback, lang]) => chain.then(() => current === sequence ? playRecorded(url, fallback, lang, current) : false), Promise.resolve());
    }
    function stop() {
      piper().stop();
      sequence += 1;
      speakRun += 1;
      if (activeAudio) { activeAudio.pause(); activeAudio = null; }
    }
    return {speak, readQuestion, playSolution, stop};
  };
})();
