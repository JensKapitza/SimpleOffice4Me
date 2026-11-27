/* Recorded and spoken parts share the Kids audio queue. */
(() => {
  window.KidsLanguageAudio = ({speechStatus}) => {
    const run = parts => window.KidsPiperVoice?.playSequence(parts, speechStatus) ?? Promise.resolve(false);
    const speech = (value, lang, purpose) => (
      Array.isArray(value) ? value : [{text: value, language: lang}]
    ).filter(part => String(part.text || '').trim())
      .map(part => ({text: part.text, lang: part.language || lang, purpose}));
    const speak = (value, lang = 'en-US', purpose) => run(speech(value, lang, purpose));
    function readQuestion(item, prompt) {
      const lang = item.promptLang || item.speechLang || 'en-US';
      const parts = speech(prompt, lang);
      if (item.questionAudioUrl && parts.length === 1) parts[0].url = item.questionAudioUrl;
      if (item.questionAudioUrl && parts.length > 1) {
        // If a full recording fails, the same operation runs the original
        // language-tagged prompt parts, without a second overlapping queue.
        parts.splice(0, parts.length, {url: item.questionAudioUrl, lang, fallback: [...parts]});
      }
      if (item.pronunciationAudioUrl) parts.push({
        url: item.pronunciationAudioUrl, text: item.speak, lang: item.speechLang || lang,
      });
      return run(parts);
    }
    function playSolution(item) {
      const parts = [
        {url: item.pronunciationAudioUrl, text: item.speak, lang: item.speechLang || 'en-US'},
        {url: item.solutionAudioUrl, text: item.a, lang: 'de-DE'},
      ].filter((part, index, all) => part.url && all.findIndex(row => row.url === part.url) === index);
      return parts.length ? run(parts) : speak(item.solutionWord || item.a, item.speechLang || 'en-US');
    }
    return {speak, readQuestion, playSolution, stop: () => window.KidsPiperVoice?.stop()};
  };
})();
