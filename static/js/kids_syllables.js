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

  function slugify(word) {
    return String(word || '').toLowerCase()
      .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue').replace(/ß/g, 'ss')
      .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'clip';
  }

  // Fixed pre-recorded clip keys; words use 'w_' + slug, parts 'p_' + slug,
  // rhyme solutions 'rhyme_' + target slug + '_' + answer slug.
  const CLIPS = {
    listenWell: 'listen_well', tapEachSyllable: 'tap_each_syllable',
    praiseRight: 'praise_right', praiseGreat: 'praise_great',
    howManySyllables: 'how_many_syllables', whatRhymes: 'what_rhymes',
    lookSolution: 'look_solution', doneGreat: 'done_great', nicePlaying: 'nice_playing',
    hasOne: 'has_1', hasTwo: 'has_2', hasThree: 'has_3', hasFour: 'has_4',
    tryOneOfFive: 'try_1_of_5', tryTwoOfFive: 'try_2_of_5',
    tryThreeOfFive: 'try_3_of_5', tryFourOfFive: 'try_4_of_5',
    clapMoreOne: 'clap_more_1', clapMoreTwo: 'clap_more_2', clapMoreThree: 'clap_more_3',
  };

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
    LEVELS, QUESTIONS_PER_LEVEL, MAX_TRIES, WORDS, RHYMES, CLIPS,
    shuffle, syllableCount, slugify,
    makeClapQuestions, makeCountQuestions, makeRhymeQuestions
  };

  if (typeof document === 'undefined') return;
  const root = document.querySelector('[data-kids-syllables]');
  if (!root || !window.KidsShared) return;
  const {stopAudio, uiLanguage, playClips} = window.KidsShared;

  const english = root.dataset.language === 'en';
  const language = english ? 'en' : 'de';
  const text = (de, en) => english ? en : de;
  const speechLang = uiLanguage(english);
  const audioBase = root.dataset.audioBase || '';
  const audioVersion = root.dataset.audioVersion || '';
  const clipLang = english ? 'en' : 'de';
  const speechStatus = null;
  let currentClips = [];
  let speechRun = 0;
  function playNow(parts) {
    microphone?.pauseForAudio();
    const run = ++speechRun;
    const guarded = parts.map(({key, fallback}) => ({key, fallback: () => run === speechRun ? fallback : null}));
    return playClips(audioBase, audioVersion, clipLang, guarded, speechLang, speechStatus);
  }
  function say(parts) {
    currentClips = parts;
    return playNow(parts);
  }
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
  let cancelExample = null;
  const microphone = window.KidsSyllableMic?.mount(root.querySelector('[data-syll-mic]'), {
    text,
    beforeStart: () => { stopExample(); stopQuestionAudio(); },
  });

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
    microphone?.setTask(null);
    stopExample();
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

  function stopExample() {
    const cancel = cancelExample;
    cancelExample = null;
    if (cancel) cancel();
  }

  // Only the existing same-origin recordings are used in the demonstration.
  // A failed/missing recording still leaves a visible beat to clap along with.
  function stopQuestionAudio() {
    speechRun += 1;
    stopAudio();
  }

  function playExample(parts, paintBeat) {
    microphone?.pauseForAudio();
    stopExample();
    stopQuestionAudio();
    let timer;
    let player;
    let removePlayerListeners;
    let index = 0;
    const pauseBeat = () => {
      if (removePlayerListeners) removePlayerListeners();
      removePlayerListeners = null;
      if (player) player.pause();
    };
    const cancel = () => {
      window.clearTimeout(timer);
      pauseBeat();
      paintBeat(-1);
    };
    cancelExample = cancel;

    function advance() {
      if (cancelExample !== cancel) return;
      if (index >= parts.length) { stopExample(); return; }
      const part = parts[index];
      paintBeat(index);
      index += 1;
      const startedAt = Date.now();
      let ended = false;
      const finishBeat = () => {
        if (ended || cancelExample !== cancel) return;
        ended = true;
        window.clearTimeout(timer);
        pauseBeat();
        // Leave a small gap for a child's clap, including when audio is off.
        timer = window.setTimeout(advance, Math.max(240, 690 - (Date.now() - startedAt)));
      };
      if (typeof window.Audio !== 'function') { finishBeat(); return; }
      timer = window.setTimeout(finishBeat, 2000);
      try {
        const suffix = audioVersion ? `?v=${encodeURIComponent(audioVersion)}` : '';
        // Retain the same trusted-click element across clips for WebKit.
        if (!player) player = new window.Audio();
        player.src = `${audioBase}/${clipLang}/p_${slugify(part)}.mp3${suffix}`;
        player.addEventListener('ended', finishBeat, {once: true});
        player.addEventListener('error', finishBeat, {once: true});
        removePlayerListeners = () => {
          player.removeEventListener('ended', finishBeat);
          player.removeEventListener('error', finishBeat);
        };
        const playing = player.play();
        if (playing && typeof playing.catch === 'function') playing.catch(finishBeat);
      } catch {
        finishBeat();
      }
    }
    advance();
  }

  function showClapQuestion(question) {
    const spoken = text(
      `Höre gut zu: ${slowWord(question)}. Tippe für jede Silbe einmal auf die Hände!`,
      `Listen carefully: ${slowWord(question)}. Tap the hands once for every syllable!`
    );
    prompt.textContent = `👏 ${question.word}`;
    prompt.setAttribute('aria-label', spoken);
    stage.setAttribute('aria-label', spoken);
    const dots = document.createElement('div');
    dots.className = 'kids-syll-dots';
    dots.setAttribute('aria-hidden', 'true');
    const markers = question.parts.map(() => {
      const marker = document.createElement('span');
      marker.className = 'kids-syll-dot';
      dots.append(marker);
      return marker;
    });
    const paintDots = (exampleBeat = -1) => {
      markers.forEach((marker, index) => {
        marker.textContent = index < taps ? '●' : '○';
        marker.classList.toggle('is-example-beat', index === exampleBeat);
      });
    };
    const partClips = part => ({key: `p_${slugify(part)}`, fallback: part});
    paintDots();
    const clap = document.createElement('button');
    clap.type = 'button';
    clap.className = 'kids-syll-clap';
    clap.textContent = '👏';
    clap.setAttribute('aria-label', text(`Klatschen, ${question.parts.length} Silben`, `Clap, ${question.parts.length} syllables`));
    const activeTask = () => !clap.disabled && !play.hidden && stage.contains(clap);
    function completeClap() {
      if (!activeTask()) return;
      microphone?.setTask(null);
      stopExample();
      taps = question.parts.length;
      paintDots();
      score += 1;
      clap.disabled = true;
      feedback.textContent = text(`Richtig! ${question.word} hat ${question.parts.length} ${question.parts.length === 1 ? 'Silbe' : 'Silben'}!`, `Right! ${question.word} has ${question.parts.length} ${question.parts.length === 1 ? 'syllable' : 'syllables'}!`);
      playNow([
        {key: CLIPS.praiseRight, fallback: text('Richtig!', 'Right!')},
        {key: `w_${slugify(question.word)}`, fallback: question.word},
        {key: `has_${question.parts.length}`, fallback: ''},
      ]);
      next.hidden = false;
      focusElement(next);
    }
    clap.addEventListener('click', () => {
      if (!activeTask()) return;
      microphone?.pause();
      stopExample();
      taps += 1;
      if (taps >= question.parts.length) { completeClap(); return; }
      paintDots();
      feedback.textContent = text(`Gut! Noch ${question.parts.length - taps} mal klatschen!`, `Good! Clap ${question.parts.length - taps} more ${question.parts.length - taps === 1 ? 'time' : 'times'}!`);
      playNow([
        partClips(question.parts[taps - 1]),
        {key: `clap_more_${question.parts.length - taps}`, fallback: feedback.textContent},
      ]);
    });
    const example = document.createElement('button');
    example.type = 'button';
    example.className = 'btn btn-outline-primary btn-lg kids-syll-example';
    example.textContent = text('👂 Vormachen', '👂 Show me');
    example.setAttribute('aria-pressed', 'false');
    example.addEventListener('click', () => {
      if (!stage.contains(example) || play.hidden) return;
      if (cancelExample) { stopExample(); return; }
      playExample(question.parts, beat => {
        paintDots(beat);
        example.setAttribute('aria-pressed', String(beat >= 0));
      });
    });

    stage.append(makePicture(question.emoji, question.word), dots, clap, example);
    microphone?.setTask({target: question.parts.length, valid: activeTask, complete: completeClap});
    say([
      {key: CLIPS.listenWell, fallback: text('Höre gut zu:', 'Listen carefully:')},
      ...question.parts.map(partClips),
      {key: CLIPS.tapEachSyllable, fallback: text('Tippe für jede Silbe einmal auf die Hände!', 'Tap the hands once for every syllable!')},
    ]);
  }

  function showCountQuestion(question) {
    const spoken = text(
      `${slowWord(question)}. Wie viele Silben hörst du?`,
      `${slowWord(question)}. How many syllables can you hear?`
    );
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
    say([
      ...question.parts.map(part => ({key: `p_${slugify(part)}`, fallback: part})),
      {key: CLIPS.howManySyllables, fallback: text('Wie viele Silben hörst du?', 'How many syllables can you hear?')},
    ]);
  }

  function showRhymeQuestion(question) {
    const spoken = text(
      `Was reimt sich auf ${question.target.word}?`,
      `What rhymes with ${question.target.word}?`
    );
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
    say([
      {key: CLIPS.whatRhymes, fallback: text('Was reimt sich auf', 'What rhymes with')},
      {key: `w_${slugify(question.target.word)}`, fallback: question.target.word},
    ]);
  }

  function answer(button, correct, question) {
    if (next.hidden === false) return;
    if (correct) {
      score += 1;
      button.classList.add('is-correct');
      stage.querySelectorAll('button').forEach(choice => { choice.disabled = true; });
      if (level === 'rhyme' && question) {
        feedback.textContent = text(`${question.target.word} und ${question.answer.word} reimen sich!`, `${question.target.word} and ${question.answer.word} rhyme!`);
        playNow([{key: `rhyme_${slugify(question.target.word)}_${slugify(question.answer.word)}`, fallback: feedback.textContent}]);
      } else {
        feedback.textContent = text('Richtig! Super gemacht!', 'That is right! Well done!');
        playNow([{key: CLIPS.praiseGreat, fallback: feedback.textContent}]);
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
      playNow([{key: CLIPS.lookSolution, fallback: feedback.textContent}]);
      revealAnswer();
      return;
    }
    feedback.textContent = text(`Versuch ${mistakes} von ${MAX_TRIES}. Probier es weiter!`, `Try ${mistakes} of ${MAX_TRIES}. Keep going!`);
    playNow([{key: `try_${mistakes}_of_5`, fallback: feedback.textContent}]);
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
    stopExample();
    if (questionIndex >= QUESTIONS_PER_LEVEL - 1) { finish(); return; }
    questionIndex += 1;
    showQuestion();
    focusElement(title);
  }

  function finish() {
    microphone?.setTask(null);
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
    playNow([{key: complete ? CLIPS.doneGreat : CLIPS.nicePlaying, fallback: resultTitle.textContent}]);
  }

  function showPicker() {
    microphone?.setTask(null);
    stopExample();
    stopQuestionAudio();
    result.hidden = true;
    play.hidden = true;
    picker.hidden = false;
    focusElement(picker.querySelector('h2'));
  }

  root.querySelectorAll('[data-syll-level]').forEach(button => button.addEventListener('click', () => begin(button.dataset.syllLevel)));
  root.querySelector('[data-syll-repeat]').addEventListener('click', () => {
    stopExample();
    if (currentClips.length) playNow(currentClips);
  });
  next.addEventListener('click', nextQuestion);
  root.querySelector('[data-syll-back]').addEventListener('click', showPicker);
  root.querySelector('[data-result-back]')?.addEventListener('click', showPicker);
  root.querySelector('[data-play-again]').addEventListener('click', () => begin(level));
})();
