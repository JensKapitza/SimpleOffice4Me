(() => {
  const root = document.querySelector('[data-kids-mini-games]');
  if (!root) return;

  const english = root.dataset.language === 'en';
  const text = (de, en) => english ? en : de;
  const picker = root.querySelector('[data-game-picker]');
  const play = root.querySelector('[data-mini-play]');
  const result = root.querySelector('[data-mini-result]');
  const stage = root.querySelector('[data-mini-stage]');
  const title = root.querySelector('[data-mini-title]');
  const prompt = root.querySelector('[data-mini-prompt]');
  const feedback = root.querySelector('[data-mini-feedback]');
  const progress = root.querySelector('[data-mini-progress]');
  const next = root.querySelector('[data-mini-next]');
  const pairs = [
    ['🐶','Hund','dog'], ['🐱','Katze','cat'], ['🐰','Hase','rabbit'], ['🐸','Frosch','frog'],
    ['🐻','Bär','bear'], ['🦊','Fuchs','fox'], ['🐼','Panda','panda'], ['🐵','Affe','monkey']
  ];
  const countItems = [
    ['🐶','Hunde','dogs'], ['🐟','Fische','fish'], ['🐞','Käfer','ladybugs'], ['🐸','Frösche','frogs'],
    ['🦋','Schmetterlinge','butterflies'], ['🐤','Küken','chicks'], ['🐰','Hasen','rabbits']
  ];
  const oddSets = [
    {same:['🍎','🍐','🍌'], odd:'🚗', word:'Auto', en:'car'},
    {same:['🐶','🐱','🐰'], odd:'🚲', word:'Fahrrad', en:'bicycle'},
    {same:['🧦','👟','👢'], odd:'🍰', word:'Kuchen', en:'cake'},
    {same:['🌷','🌼','🌹'], odd:'🐟', word:'Fisch', en:'fish'},
    {same:['🚒','🚜','🚕'], odd:'🍓', word:'Erdbeere', en:'strawberry'},
    {same:['🥄','🍴','🥢'], odd:'🐸', word:'Frosch', en:'frog'}
  ];
  let game = '';
  let questions = [];
  let questionIndex = 0;
  let score = 0;
  let mistakes = 0;
  let matchedPairs = 0;
  let firstCard = null;
  let lockBoard = false;
  let memoryAttempts = 0;
  let currentSpoken = '';

  function shuffle(items) {
    const values = [...items];
    for (let index = values.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [values[index], values[swap]] = [values[swap], values[index]];
    }
    return values;
  }

  function begin(selected) {
    game = selected;
    score = 0;
    questionIndex = 0;
    mistakes = 0;
    matchedPairs = 0;
    firstCard = null;
    lockBoard = false;
    memoryAttempts = 0;
    picker.hidden = true;
    result.hidden = true;
    play.hidden = false;
    next.hidden = true;
    feedback.textContent = '';
    title.textContent = gameTitle();
    title.focus();
    if (game === 'memory') startMemory();
    else {
      questions = game === 'count' ? makeCountQuestions() : makeOddQuestions();
      showQuestion();
    }
  }

  function gameTitle() {
    if (game === 'memory') return text('Tierpaare','Animal pairs');
    if (game === 'count') return text('Freunde zählen','Count the friends');
    return text('Finde den Ausreißer','Find the different one');
  }

  function startMemory() {
    const selected = shuffle(pairs).slice(0, 3);
    const cards = shuffle(selected.flatMap(([emoji, de, en], index) => [
      {pair:index, emoji, word:english ? en : de}, {pair:index, emoji, word:english ? en : de}
    ]));
    progress.textContent = text('Finde die drei Bildpaare.','Find the three picture pairs.');
    prompt.textContent = text('Tippe zwei Karten an.','Tap two cards.');
    currentSpoken = text('Finde die drei Bildpaare.','Find the three picture pairs.');
    speak(currentSpoken, uiLanguage());
    stage.classList.add('is-memory');
    stage.replaceChildren(...cards.map((card, index) => makeMemoryCard(card, index)));
  }

  function makeMemoryCard(card, index) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'kids-mini-memory-card';
    button.textContent = '❔';
    button.dataset.pair = String(card.pair);
    button.dataset.word = card.word;
    button.setAttribute('aria-label', text(`Verdeckte Karte ${index + 1}`, `Hidden card ${index + 1}`));
    button.addEventListener('click', () => flipMemoryCard(button, card));
    return button;
  }

  function flipMemoryCard(button, card) {
    if (lockBoard || button.disabled || button.classList.contains('is-flipped')) return;
    button.classList.add('is-flipped');
    button.textContent = card.emoji;
    button.setAttribute('aria-label', card.word);
    if (!firstCard) { firstCard = {button, card}; return; }
    memoryAttempts += 1;
    if (firstCard.card.pair === card.pair) {
      matchedPairs += 1;
      score += 1;
      button.disabled = true;
      firstCard.button.disabled = true;
      button.classList.add('is-matched');
      firstCard.button.classList.add('is-matched');
      feedback.textContent = text(`Richtig! ${card.word}!`, `A match! ${card.word}!`);
      speak(card.word, uiLanguage());
      firstCard = null;
      if (matchedPairs === 3) finish();
      return;
    }
    lockBoard = true;
    const previous = firstCard.button;
    firstCard = null;
    feedback.textContent = text('Fast! Schau noch einmal.','Almost! Try another pair.');
    window.setTimeout(() => {
      [button, previous].forEach(cardButton => {
        cardButton.classList.remove('is-flipped');
        cardButton.textContent = '❔';
        cardButton.setAttribute('aria-label', text('Verdeckte Karte','Hidden card'));
      });
      lockBoard = false;
    }, 750);
  }

  function makeCountQuestions() {
    const max = 5;
    return shuffle(countItems).slice(0, 3).map(item => {
      const amount = 1 + Math.floor(Math.random() * max);
      const alternatives = new Set([amount]);
      while (alternatives.size < 3) alternatives.add(1 + Math.floor(Math.random() * max));
      return {item, amount, choices:shuffle([...alternatives])};
    });
  }

  function makeOddQuestions() {
    return shuffle(oddSets).slice(0, 3).map(set => ({...set, choices:shuffle([...set.same, set.odd])}));
  }

  function showQuestion() {
    const question = questions[questionIndex];
    mistakes = 0;
    next.hidden = true;
    feedback.textContent = '';
    stage.classList.remove('is-memory');
    progress.textContent = text(`Runde ${questionIndex + 1} von 3`,`Round ${questionIndex + 1} of 3`);
    stage.replaceChildren();
    if (game === 'count') showCountQuestion(question);
    else showOddQuestion(question);
  }

  function showCountQuestion(question) {
    const [emoji, de, en] = question.item;
    const noun = english ? en : de;
    const spoken = text(`Wie viele ${de} siehst du?`,`How many ${en} can you see?`);
    currentSpoken = spoken;
    prompt.textContent = '';
    prompt.setAttribute('aria-label', spoken);
    stage.setAttribute('aria-label', spoken);
    const picture = document.createElement('div');
    picture.className = 'kids-mini-count-picture';
    picture.setAttribute('role', 'img');
    picture.setAttribute('aria-label', `${question.amount} ${noun}`);
    picture.textContent = Array(question.amount).fill(emoji).join(' ');
    stage.append(picture);
    question.choices.forEach(answer => stage.append(makeAnswerButton(String(answer), answer === question.amount)));
    speak(spoken, uiLanguage());
  }

  function showOddQuestion(question) {
    const spoken = text('Drei Bilder passen zusammen. Tippe auf das andere Bild.','Three pictures belong together. Tap the different one.');
    currentSpoken = spoken;
    prompt.textContent = '';
    prompt.setAttribute('aria-label', spoken);
    stage.setAttribute('aria-label', spoken);
    question.choices.forEach(emoji => {
      const button = makeAnswerButton(emoji, emoji === question.odd);
      button.classList.add('kids-mini-picture-choice');
      button.setAttribute('aria-label', emoji === question.odd ? (english ? question.en : question.word) : text('Bild aus der Gruppe','Picture in the group'));
      stage.append(button);
    });
    speak(spoken, uiLanguage());
  }

  function makeAnswerButton(label, correct) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'kids-mini-answer';
    button.textContent = label;
    button.addEventListener('click', () => answer(button, correct));
    return button;
  }

  function answer(button, correct) {
    if (next.hidden === false) return;
    if (correct) {
      score += 1;
      button.classList.add('is-correct');
      stage.querySelectorAll('button').forEach(choice => { choice.disabled = true; });
      feedback.textContent = text('Richtig! Super gemacht!','That is right! Well done!');
      if (game === 'odd') speak(english ? questions[questionIndex].en : questions[questionIndex].word, uiLanguage());
      next.hidden = false;
      next.focus();
      return;
    }
    mistakes += 1;
    button.classList.add('is-wrong');
    button.dataset.mistakes = String(Number(button.dataset.mistakes || 0) + 1);
    button.style.opacity = String(Math.max(.18, 1 - Number(button.dataset.mistakes) * .22));
    feedback.textContent = mistakes >= 5
      ? text('Schau, hier ist die Lösung.','Look, here is the answer.')
      : text(`Versuch ${mistakes} von 5. Probier es weiter!`,`Try ${mistakes} of 5. Keep going!`);
    if (mistakes >= 5) revealAnswer();
  }

  function revealAnswer() {
    const question = questions[questionIndex];
    const correctValue = game === 'count' ? String(question.amount) : question.odd;
    stage.querySelectorAll('button').forEach(button => {
      button.disabled = true;
      if (button.textContent === correctValue) button.classList.add('is-correct');
      else button.hidden = true;
    });
    next.hidden = false;
    next.focus();
  }

  function nextQuestion() {
    if (questionIndex >= 2) { finish(); return; }
    questionIndex += 1;
    showQuestion();
  }

  function finish() {
    play.hidden = true;
    picker.hidden = true;
    result.hidden = false;
    const complete = game === 'memory' ? matchedPairs === 3 : score === 3;
    root.querySelector('[data-result-title]').textContent = text(complete ? 'Das hast du toll gemacht! 🌼' : 'Gut gespielt! 🌟', complete ? 'You did great! 🌼' : 'Nice playing! 🌟');
    root.querySelector('[data-result-copy]').textContent = game === 'memory'
      ? text(`Du hast ${matchedPairs} Paare in ${memoryAttempts} Zügen gefunden.`,`You found ${matchedPairs} pairs in ${memoryAttempts} turns.`)
      : text(`Du hast ${score} von 3 Aufgaben richtig gelöst.`,`You got ${score} out of 3 right.`);
    root.querySelector('[data-result-title]').focus();
    speak(root.querySelector('[data-result-title]').textContent, uiLanguage());
  }

  function showPicker() {
    window.speechSynthesis?.cancel();
    result.hidden = true;
    play.hidden = true;
    picker.hidden = false;
    root.querySelector('[data-game-picker] h2').focus();
  }

  function uiLanguage() { return english ? 'en-US' : 'de-DE'; }

  function speak(value, language) {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = language;
    utterance.rate = .86;
    const prefix = language.slice(0, 2).toLowerCase();
    const voices = window.speechSynthesis.getVoices().filter(voice => voice.lang.toLowerCase().startsWith(prefix));
    let requested = '';
    try { requested = localStorage.getItem(`kids-learning.voice.${prefix}`) || ''; } catch {}
    const voiceId = voice => voice.voiceURI || `${voice.name}|${voice.lang}`;
    const chosen = voices.find(voice => voiceId(voice) === requested) || [...voices].sort((a, b) => voiceRank(b, language) - voiceRank(a, language))[0];
    if (chosen) utterance.voice = chosen;
    window.speechSynthesis.speak(utterance);
  }

  function voiceRank(voice, language) {
    // On-device voices first: same-language local voices must beat
    // same-language cloud voices so prompts stay offline and private.
    return (voice.lang.toLowerCase() === language.toLowerCase() ? 100 : 50)
      + (voice.localService ? 24 : 0)
      + (/natural|neural|premium|enhanced/i.test(voice.name) ? 6 : 0);
  }

  root.querySelectorAll('[data-mini-game]').forEach(button => button.addEventListener('click', () => begin(button.dataset.miniGame)));
  root.querySelector('[data-mini-repeat]').addEventListener('click', () => {
    speak(currentSpoken || text('Finde die drei Bildpaare.','Find the three picture pairs.'), uiLanguage());
  });
  next.addEventListener('click', nextQuestion);
  root.querySelector('[data-mini-back]').addEventListener('click', showPicker);
  root.querySelector('[data-result-back]').addEventListener('click', showPicker);
  root.querySelector('[data-play-again]').addEventListener('click', () => begin(game));
})();
