(() => {
  const scope = typeof window !== 'undefined' ? window : {};
  const LEVELS = ['clap', 'count', 'rhyme'];
  const QUESTIONS_PER_LEVEL = 3;
  const MAX_TRIES = 5;

  // Preschool vocabulary: emoji picture, spoken word and syllable parts.
  // parts.join('') must equal word (case-insensitive); parts.length is the answer.
  const WORDS = {
    de: [
      {emoji: '⚽', word: 'Ball', parts: ['Ball']},
      {emoji: '🏠', word: 'Haus', parts: ['Haus']},
      {emoji: '🌳', word: 'Baum', parts: ['Baum']},
      {emoji: '🐟', word: 'Fisch', parts: ['Fisch']},
      {emoji: '🍞', word: 'Brot', parts: ['Brot']},
      {emoji: '⭐', word: 'Stern', parts: ['Stern']},
      {emoji: '🍎', word: 'Apfel', parts: ['Ap', 'fel']},
      {emoji: '🐱', word: 'Katze', parts: ['Kat', 'ze']},
      {emoji: '🐰', word: 'Hase', parts: ['Ha', 'se']},
      {emoji: '☀️', word: 'Sonne', parts: ['Son', 'ne']},
      {emoji: '🌷', word: 'Blume', parts: ['Blu', 'me']},
      {emoji: '🐦', word: 'Vogel', parts: ['Vo', 'gel']},
      {emoji: '🚗', word: 'Auto', parts: ['Au', 'to']},
      {emoji: '🦆', word: 'Ente', parts: ['En', 'te']},
      {emoji: '🍌', word: 'Banane', parts: ['Ba', 'na', 'ne']},
      {emoji: '🍅', word: 'Tomate', parts: ['To', 'ma', 'te']},
      {emoji: '🦒', word: 'Giraffe', parts: ['Gi', 'raf', 'fe']},
      {emoji: '🍓', word: 'Erdbeere', parts: ['Erd', 'bee', 're']},
      {emoji: '🦜', word: 'Papagei', parts: ['Pa', 'pa', 'gei']},
      {emoji: '🚁', word: 'Hubschrauber', parts: ['Hub', 'schrau', 'ber']},
      {emoji: '☂️', word: 'Regenschirm', parts: ['Re', 'gen', 'schirm']},
      {emoji: '🍫', word: 'Schokolade', parts: ['Scho', 'ko', 'la', 'de']},
      {emoji: '🏫', word: 'Kindergarten', parts: ['Kin', 'der', 'gar', 'ten']}
    ],
    en: [
      {emoji: '⚽', word: 'ball', parts: ['ball']},
      {emoji: '🏠', word: 'house', parts: ['house']},
      {emoji: '🌳', word: 'tree', parts: ['tree']},
      {emoji: '🐟', word: 'fish', parts: ['fish']},
      {emoji: '🍞', word: 'bread', parts: ['bread']},
      {emoji: '⭐', word: 'star', parts: ['star']},
      {emoji: '🍎', word: 'apple', parts: ['ap', 'ple']},
      {emoji: '🐱', word: 'kitten', parts: ['kit', 'ten']},
      {emoji: '🐰', word: 'rabbit', parts: ['rab', 'bit']},
      {emoji: '🌷', word: 'flower', parts: ['flow', 'er']},
      {emoji: '🐯', word: 'tiger', parts: ['ti', 'ger']},
      {emoji: '✏️', word: 'pencil', parts: ['pen', 'cil']},
      {emoji: '🐵', word: 'monkey', parts: ['mon', 'key']},
      {emoji: '🍌', word: 'banana', parts: ['ba', 'na', 'na']},
      {emoji: '🍅', word: 'tomato', parts: ['to', 'ma', 'to']},
      {emoji: '🦒', word: 'giraffe', parts: ['gi', 'raffe']},
      {emoji: '🦋', word: 'butterfly', parts: ['but', 'ter', 'fly']},
      {emoji: '📷', word: 'camera', parts: ['cam', 'e', 'ra']},
      {emoji: '🍫', word: 'chocolate', parts: ['choc', 'o', 'late']},
      {emoji: '🚁', word: 'helicopter', parts: ['he', 'li', 'cop', 'ter']},
      {emoji: '🏫', word: 'kindergarten', parts: ['kin', 'der', 'gar', 'ten']}
    ]
  };

  // Rhyme sets: target picture, rhyming answer and two distractors.
  const RHYMES = {
    de: [
      {target: {emoji: '🏠', word: 'Haus'}, rhyme: {emoji: '🐭', word: 'Maus'}, others: [{emoji: '🐟', word: 'Fisch'}, {emoji: '⚽', word: 'Ball'}]},
      {target: {emoji: '🐱', word: 'Katze'}, rhyme: {emoji: '🐾', word: 'Tatze'}, others: [{emoji: '🐶', word: 'Hund'}, {emoji: '🦆', word: 'Ente'}]},
      {target: {emoji: '🐶', word: 'Hund'}, rhyme: {emoji: '👄', word: 'Mund'}, others: [{emoji: '🐱', word: 'Katze'}, {emoji: '🐸', word: 'Frosch'}]},
      {target: {emoji: '⚽', word: 'Ball'}, rhyme: {emoji: '💥', word: 'Knall'}, others: [{emoji: '🏠', word: 'Haus'}, {emoji: '🍎', word: 'Apfel'}]},
      {target: {emoji: '🐭', word: 'Maus'}, rhyme: {emoji: '🏠', word: 'Haus'}, others: [{emoji: '🌳', word: 'Baum'}, {emoji: '⭐', word: 'Stern'}]}
    ],
    en: [
      {target: {emoji: '🏠', word: 'house'}, rhyme: {emoji: '🐭', word: 'mouse'}, others: [{emoji: '🐟', word: 'fish'}, {emoji: '⚽', word: 'ball'}]},
      {target: {emoji: '🐱', word: 'cat'}, rhyme: {emoji: '🎩', word: 'hat'}, others: [{emoji: '🐶', word: 'dog'}, {emoji: '🦆', word: 'duck'}]},
      {target: {emoji: '🐶', word: 'dog'}, rhyme: {emoji: '🐸', word: 'frog'}, others: [{emoji: '🐱', word: 'cat'}, {emoji: '🌳', word: 'tree'}]},
      {target: {emoji: '🐝', word: 'bee'}, rhyme: {emoji: '🌳', word: 'tree'}, others: [{emoji: '🐟', word: 'fish'}, {emoji: '🏠', word: 'house'}]},
      {target: {emoji: '⭐', word: 'star'}, rhyme: {emoji: '🚗', word: 'car'}, others: [{emoji: '⚽', word: 'ball'}, {emoji: '🐟', word: 'fish'}]}
    ]
  };

  function shuffle(items) {
    const values = [...items];
    for (let index = values.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [values[index], values[swap]] = [values[swap], values[index]];
    }
    return values;
  }

  function syllableCount(entry) {
    return entry.parts.length;
  }

  function vocabulary(language) {
    return WORDS[language] || WORDS.de;
  }

  function rhymeSets(language) {
    return RHYMES[language] || RHYMES.de;
  }

  // Easy rounds first: sort picked words by syllable count ascending.
  function makeClapQuestions(language, count = QUESTIONS_PER_LEVEL) {
    return shuffle(vocabulary(language)).slice(0, count)
      .map(entry => ({emoji: entry.emoji, word: entry.word, parts: [...entry.parts]}))
      .sort((a, b) => a.parts.length - b.parts.length);
  }

  function makeCountQuestions(language, count = QUESTIONS_PER_LEVEL) {
    return makeClapQuestions(language, count).map(question => ({
      ...question,
      answer: question.parts.length,
      choices: shuffle([1, 2, 3, 4])
    }));
  }

  function makeRhymeQuestions(language, count = QUESTIONS_PER_LEVEL) {
    return shuffle(rhymeSets(language)).slice(0, count).map(set => ({
      target: {...set.target},
      answer: {...set.rhyme},
      choices: shuffle([set.rhyme, ...set.others]).map(choice => ({...choice}))
    }));
  }

  // Testable logic API. The DOM game below only starts on its own page.
  scope.KidsSyllables = {
    LEVELS, QUESTIONS_PER_LEVEL, MAX_TRIES, WORDS, RHYMES,
    shuffle, syllableCount, makeClapQuestions, makeCountQuestions, makeRhymeQuestions
  };

  if (typeof document === 'undefined') return;
  const root = document.querySelector('[data-kids-syllables]');
  if (!root) return;

  const english = root.dataset.language === 'en';
  const language = english ? 'en' : 'de';
  const text = (de, en) => english ? en : de;
  const picker = root.querySelector('[data-level-picker]');
  const play = root.querySelector('[data-syll-play]');
  const result = root.querySelector('[data-syll-result]');
  const stage = root.querySelector('[data-syll-stage]');
  const title = root.querySelector('[data-syll-title]');
  const prompt = root.querySelector('[data-syll-prompt]');
  const feedback = root.querySelector('[data-syll-feedback]');
  const progress = root.querySelector('[data-syll-progress]');
  const next = root.querySelector('[data-syll-next]');

  let level = '';
  let questions = [];
  let questionIndex = 0;
  let score = 0;
  let mistakes = 0;
  let taps = 0;
  let currentSpoken = '';

  function focusElement(element) {
    if (element && typeof element.focus === 'function') element.focus();
  }

  function levelTitle() {
    if (level === 'clap') return text('Stufe 1: Klatsche mit', 'Level 1: Clap along');
    if (level === 'count') return text('Stufe 2: Silben zählen', 'Level 2: Count syllables');
    return text('Stufe 3: Reime finden', 'Level 3: Find rhymes');
  }

  function slowWord(question) {
    return question.parts.join(' … ');
  }

  function begin(selected) {
    level = selected;
    score = 0;
    questionIndex = 0;
    picker.hidden = true;
    result.hidden = true;
    play.hidden = false;
    next.hidden = true;
    feedback.textContent = '';
    title.textContent = levelTitle();
    focusElement(title);
    if (level === 'clap') questions = makeClapQuestions(language);
    else if (level === 'count') questions = makeCountQuestions(language);
    else questions = makeRhymeQuestions(language);
    showQuestion();
  }

  function showQuestion() {
    const question = questions[questionIndex];
    mistakes = 0;
    taps = 0;
    next.hidden = true;
    feedback.textContent = '';
    progress.textContent = text(`Aufgabe ${questionIndex + 1} von ${QUESTIONS_PER_LEVEL}`, `Task ${questionIndex + 1} of ${QUESTIONS_PER_LEVEL}`);
    stage.replaceChildren();
    if (level === 'clap') showClapQuestion(question);
    else if (level === 'count') showCountQuestion(question);
    else showRhymeQuestion(question);
  }

  function makePicture(emoji, label) {
    const picture = document.createElement('div');
    picture.className = 'kids-syll-picture';
    picture.setAttribute('role', 'img');
    picture.setAttribute('aria-label', label);
    picture.textContent = emoji;
    return picture;
  }

  function showClapQuestion(question) {
    const spoken = text(
      `Höre gut zu: ${slowWord(question)}. Tippe für jede Silbe einmal auf die Hände!`,
      `Listen carefully: ${slowWord(question)}. Tap the hands once for every syllable!`
    );
    currentSpoken = spoken;
    prompt.textContent = `👏 ${question.word}`;
    prompt.setAttribute('aria-label', spoken);
    stage.setAttribute('aria-label', spoken);
    const dots = document.createElement('div');
    dots.className = 'kids-syll-dots';
    dots.setAttribute('aria-hidden', 'true');
    const paintDots = () => {
      dots.textContent = '●'.repeat(taps) + '○'.repeat(question.parts.length - taps);
    };
    paintDots();
    const clap = document.createElement('button');
    clap.type = 'button';
    clap.className = 'kids-syll-clap';
    clap.textContent = '👏';
    clap.setAttribute('aria-label', text(`Klatschen, ${question.parts.length} Silben`, `Clap, ${question.parts.length} syllables`));
    clap.addEventListener('click', () => {
      if (clap.disabled) return;
      taps += 1;
      paintDots();
      if (taps >= question.parts.length) {
        score += 1;
        clap.disabled = true;
        feedback.textContent = text(`Richtig! ${question.word} hat ${question.parts.length} ${question.parts.length === 1 ? 'Silbe' : 'Silben'}!`, `Right! ${question.word} has ${question.parts.length} ${question.parts.length === 1 ? 'syllable' : 'syllables'}!`);
        speak(feedback.textContent, uiLanguage());
        next.hidden = false;
        focusElement(next);
        return;
      }
      speak(question.parts[taps - 1], uiLanguage());
      feedback.textContent = text(`Gut! Noch ${question.parts.length - taps} ${question.parts.length - taps === 1 ? 'mal klatschen' : 'mal klatschen'}!`, `Good! Clap ${question.parts.length - taps} more ${question.parts.length - taps === 1 ? 'time' : 'times'}!`);
    });
    stage.append(makePicture(question.emoji, question.word), dots, clap);
    speak(spoken, uiLanguage());
  }

  function showCountQuestion(question) {
    const spoken = text(
      `${slowWord(question)}. Wie viele Silben hörst du?`,
      `${slowWord(question)}. How many syllables can you hear?`
    );
    currentSpoken = spoken;
    prompt.textContent = `🔢 ${question.word}`;
    prompt.setAttribute('aria-label', spoken);
    stage.setAttribute('aria-label', spoken);
    stage.append(makePicture(question.emoji, question.word));
    question.choices.forEach(choice => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kids-syll-answer kids-syll-count-choice';
      button.textContent = String(choice);
      const dots = document.createElement('small');
      dots.setAttribute('aria-hidden', 'true');
      dots.textContent = '●'.repeat(choice);
      button.append(dots);
      button.setAttribute('aria-label', text(`${choice} ${choice === 1 ? 'Silbe' : 'Silben'}`, `${choice} ${choice === 1 ? 'syllable' : 'syllables'}`));
      button.addEventListener('click', () => answer(button, choice === question.answer));
      stage.append(button);
    });
    speak(spoken, uiLanguage());
  }

  function showRhymeQuestion(question) {
    const spoken = text(
      `Was reimt sich auf ${question.target.word}?`,
      `What rhymes with ${question.target.word}?`
    );
    currentSpoken = spoken;
    prompt.textContent = `🎵 ${question.target.word} → ?`;
    prompt.setAttribute('aria-label', spoken);
    stage.setAttribute('aria-label', spoken);
    stage.append(makePicture(question.target.emoji, question.target.word));
    question.choices.forEach(choice => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kids-syll-answer kids-syll-picture-choice';
      button.textContent = choice.emoji;
      button.setAttribute('aria-label', choice.word);
      button.addEventListener('click', () => answer(button, choice.emoji === question.answer.emoji, question));
      stage.append(button);
    });
    speak(spoken, uiLanguage());
  }

  function answer(button, correct, question) {
    if (next.hidden === false) return;
    if (correct) {
      score += 1;
      button.classList.add('is-correct');
      stage.querySelectorAll('button').forEach(choice => { choice.disabled = true; });
      if (level === 'rhyme' && question) {
        feedback.textContent = text(`${question.target.word} und ${question.answer.word} reimen sich!`, `${question.target.word} and ${question.answer.word} rhyme!`);
        speak(`${question.target.word}, ${question.answer.word}. ${feedback.textContent}`, uiLanguage());
      } else {
        feedback.textContent = text('Richtig! Super gemacht!', 'That is right! Well done!');
        speak(feedback.textContent, uiLanguage());
      }
      next.hidden = false;
      focusElement(next);
      return;
    }
    mistakes += 1;
    button.classList.add('is-wrong');
    button.dataset.mistakes = String(Number(button.dataset.mistakes || 0) + 1);
    button.style.opacity = String(Math.max(.18, 1 - Number(button.dataset.mistakes) * .22));
    if (mistakes >= MAX_TRIES) {
      feedback.textContent = text('Schau, hier ist die Lösung.', 'Look, here is the answer.');
      revealAnswer();
      return;
    }
    feedback.textContent = text(`Versuch ${mistakes} von ${MAX_TRIES}. Probier es weiter!`, `Try ${mistakes} of ${MAX_TRIES}. Keep going!`);
  }

  function revealAnswer() {
    const question = questions[questionIndex];
    const correctValue = level === 'count' ? String(question.answer) : question.answer.emoji;
    stage.querySelectorAll('button').forEach(button => {
      button.disabled = true;
      const value = level === 'count' ? button.textContent.replace(/●/g, '') : button.textContent;
      if (value === correctValue) button.classList.add('is-correct');
      else button.hidden = true;
    });
    next.hidden = false;
    focusElement(next);
  }

  function nextQuestion() {
    if (questionIndex >= QUESTIONS_PER_LEVEL - 1) { finish(); return; }
    questionIndex += 1;
    showQuestion();
  }

  function finish() {
    play.hidden = true;
    picker.hidden = true;
    result.hidden = false;
    const complete = score === QUESTIONS_PER_LEVEL;
    const resultTitle = root.querySelector('[data-result-title]');
    const resultCopy = root.querySelector('[data-result-copy]');
    resultTitle.textContent = text(
      complete ? 'Das hast du toll gemacht! 🌼' : 'Gut gespielt! 🌟',
      complete ? 'You did great! 🌼' : 'Nice playing! 🌟'
    );
    resultCopy.textContent = text(
      `Du hast ${score} von ${QUESTIONS_PER_LEVEL} Aufgaben richtig gelöst.`,
      `You got ${score} out of ${QUESTIONS_PER_LEVEL} right.`
    );
    focusElement(resultTitle);
    speak(resultTitle.textContent, uiLanguage());
  }

  function showPicker() {
    if ('speechSynthesis' in scope) scope.speechSynthesis.cancel();
    result.hidden = true;
    play.hidden = true;
    picker.hidden = false;
    focusElement(picker.querySelector('h2'));
  }

  function uiLanguage() { return english ? 'en-US' : 'de-DE'; }

  function speak(value, lang) {
    if (!('speechSynthesis' in scope) || !('SpeechSynthesisUtterance' in scope)) return;
    scope.speechSynthesis.cancel();
    const utterance = new scope.SpeechSynthesisUtterance(value);
    utterance.lang = lang;
    utterance.rate = .82;
    const prefix = lang.slice(0, 2).toLowerCase();
    const voices = scope.speechSynthesis.getVoices().filter(voice => voice.lang.toLowerCase().startsWith(prefix));
    let requested = '';
    try { requested = scope.localStorage.getItem(`kids-learning.voice.${prefix}`) || ''; } catch (error) { requested = ''; }
    const voiceId = voice => voice.voiceURI || `${voice.name}|${voice.lang}`;
    const chosen = voices.find(voice => voiceId(voice) === requested) || [...voices].sort((a, b) => voiceRank(b, lang) - voiceRank(a, lang))[0];
    if (chosen) utterance.voice = chosen;
    scope.speechSynthesis.speak(utterance);
  }

  function voiceRank(voice, lang) {
    // On-device voices first: same-language local voices must beat
    // same-language cloud voices so prompts stay offline and private.
    return (voice.lang.toLowerCase() === lang.toLowerCase() ? 100 : 50)
      + (voice.localService ? 24 : 0)
      + (/natural|neural|premium|enhanced/i.test(voice.name) ? 6 : 0);
  }

  root.querySelectorAll('[data-syll-level]').forEach(button => button.addEventListener('click', () => begin(button.dataset.syllLevel)));
  root.querySelector('[data-syll-repeat]').addEventListener('click', () => {
    speak(currentSpoken || levelTitle(), uiLanguage());
  });
  next.addEventListener('click', nextQuestion);
  root.querySelector('[data-syll-back]').addEventListener('click', showPicker);
  root.querySelector('[data-result-back]').addEventListener('click', showPicker);
  root.querySelector('[data-play-again]').addEventListener('click', () => begin(level));
})();
