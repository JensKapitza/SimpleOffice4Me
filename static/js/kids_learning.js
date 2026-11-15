(() => {
  const root = document.querySelector('[data-kids-learning]');
  if (!root) return;

  const english = root.dataset.language === 'en';
  const text = (de, en) => english ? en : de;
  const promptSpeechLanguage = english ? 'en-US' : 'de-DE';
  function splitQuotedSpeech(value, mainLanguage, quotedLanguage) {
    const segments = [];
    const pattern = /[“„"]([^“”„"]+)[””“"]/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(String(value || ''))) !== null) {
      if (match.index > cursor) segments.push({text: value.slice(cursor, match.index), language: mainLanguage});
      segments.push({text: match[1], language: quotedLanguage});
      cursor = pattern.lastIndex;
    }
    if (cursor < String(value || '').length) segments.push({text: String(value).slice(cursor), language: mainLanguage});
    return segments.filter(segment => segment.text.trim());
  }
  const {levels, subjects, bank, safetyCards, arrowCards, mazeNodes, mazeFriends, searchObjects, wordCards} = window.KidsLearningContent(text, english, promptSpeechLanguage, splitQuotedSpeech);

  const setup = root.querySelector('[data-setup]');
  const game = root.querySelector('[data-game]');
  const searchModes = root.querySelector('[data-search-modes]');
  const searchGame = root.querySelector('[data-search-game]');
  const searchScene = root.querySelector('[data-search-scene]');
  const searchPrompt = root.querySelector('[data-search-prompt]');
  const mazePanel = root.querySelector('[data-maze-panel]');
  const mazeMap = root.querySelector('[data-maze-map]');
  const mazeTask = root.querySelector('[data-maze-task]');
  const mazeListenButton = root.querySelector('[data-maze-listen]');
  const searchNext = root.querySelector('[data-search-next]');
  const letterPlay = root.querySelector('[data-letter-play]');
  const letterChoices = root.querySelector('[data-letter-choices]');
  const result = root.querySelector('[data-result]');
  const questionNode = root.querySelector('[data-question]');
  const choicesNode = root.querySelector('[data-choices]');
  const progress = root.querySelector('[data-progress]');
  const speakButton = root.querySelector('[data-speak]');
  const speechVoiceSelects = [...root.querySelectorAll('[data-speech-voice]')];
  const speechStatus = root.querySelector('[data-speech-status]');
  const selectedSpeechVoices = {de:'',en:''};
  let selectedLevel = 'preschool';
  let selectedSubject = 'math';
  let selectedSearchMode = 'objects';
  let questions = [];
  let index = 0;
  let score = 0;
  let searchTargets = [];
  let searchIndex = 0;
  let searchObjectsInScene = [];
  let activeWordCard = null;
  let searchReadyForNext = false;
  let wrongAttempts = 0;
  let mazeCurrentNode = 'start';
  let mazePreviousNode = '';
  let mazeDifficulty = 'easy';
  let mazeListen = false;
  let mazeCompleted = false;
  let mazeFriend = mazeFriends[0];
  let mazeVisited = new Set();
  let mazeWrongTurns = {};
  let mazeHappy = false;
  let mazeHappyTimer = 0;
  const maxWrongAttempts = 5;

  function setQuizFeedback(kind, message) {
    root.querySelector('[data-feedback-status-icon]').textContent = kind === 'wrong' ? '😟 ' : kind === 'right' ? '🌼😊 ' : '';
    root.querySelector('[data-feedback-status-text]').textContent = message;
    const icon = root.querySelector('[data-feedback-icon]');
    icon.textContent = kind === 'wrong' ? '😟' : kind === 'right' ? '🌼😊' : '';
    icon.hidden = !kind;
  }
  function setSearchFeedback(kind, message) {
    root.querySelector('[data-search-status-icon]').textContent = kind === 'wrong' ? '😟 ' : kind === 'right' ? '🌼😊 ' : '';
    root.querySelector('[data-search-status-text]').textContent = message;
    const icon = root.querySelector('[data-search-icon]');
    icon.textContent = kind === 'wrong' ? '😟' : kind === 'right' ? '🌼😊' : '';
    icon.hidden = !kind;
  }
  function fadeWrongChoice(button) {
    button.classList.add('is-wrong');
    const mistakes = Number(button.dataset.mistakes || 0) + 1;
    button.dataset.mistakes = String(mistakes);
    button.style.opacity = String(Math.max(.12, 1 - mistakes * .17));
  }
  function revealQuizAnswer(item) {
    choicesNode.querySelectorAll('button').forEach(option => {
      option.disabled = true;
      if (option.dataset.answer === String(item.a)) option.classList.add('is-correct');
      else option.hidden = true;
    });
    const answer = item.choiceImages?.[item.a] || item.a;
    setQuizFeedback('wrong', text(`Die Lösung ist: ${answer}`, `The answer is: ${answer}`));
    root.querySelector('[data-next]').hidden = false;
    root.querySelector('[data-next]').focus();
  }

  const shuffle = items => {
    const shuffled = [...items];
    for (let i = shuffled.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
    }
    return shuffled;
  };
  const randomInt = (min, max) => Math.floor(Math.random() * (max - min + 1)) + min;
  function randomMathChoices(answer, minimum = 0) {
    const distractors = new Set();
    for (let offset = 1; distractors.size < 3; offset += 1) {
      shuffle([answer + offset, answer - offset]).forEach(value => {
        if (value >= minimum && value !== answer && distractors.size < 3) distractors.add(value);
      });
    }
    return shuffle([String(answer), ...[...distractors].map(String)]);
  }
  function createMathQuestion(level) {
    if (level === 'preschool') {
      const count = randomInt(1, 8);
      const prompt = text('Wie viele Äpfel siehst du?','How many apples can you see?');
      return {q:prompt, visual:Array(count).fill('🍎').join(' '), a:String(count), c:randomMathChoices(count, 1), audioPrompt:prompt, speechLang:english?'en-US':'de-DE'};
    }

    let left;
    let right;
    let answer;
    let symbol;
    let spokenOperator;
    let minimum = 0;
    if (['1','2'].includes(level)) {
      const max = level === '1' ? 5 : 8;
      symbol = Math.random() < .5 ? '+' : '−';
      left = randomInt(1, max);
      right = randomInt(1, max);
      if (symbol === '−' && right > left) [left, right] = [right, left];
      answer = symbol === '+' ? left + right : left - right;
      spokenOperator = symbol === '+' ? text('plus','plus') : text('minus','minus');
      minimum = 0;
    } else if (level === '3') {
      const operation = shuffle(['+','−','×'])[0];
      symbol = operation;
      left = randomInt(2, operation === '×' ? 10 : 50);
      right = randomInt(2, operation === '×' ? 10 : 50);
      if (operation === '−' && right > left) [left, right] = [right, left];
      answer = operation === '+' ? left + right : operation === '−' ? left - right : left * right;
      spokenOperator = operation === '×' ? text('mal','times') : operation === '+' ? text('plus','plus') : text('minus','minus');
    } else {
      const operation = shuffle(['+','−','×','÷'])[0];
      symbol = operation === '÷' ? '÷' : operation;
      if (operation === '÷') {
        right = randomInt(2, 12);
        answer = randomInt(2, 12);
        left = right * answer;
      } else if (operation === '×') {
        left = randomInt(2, 12);
        right = randomInt(2, 12);
        answer = left * right;
      } else {
        left = randomInt(level === '4' ? 20 : 50, level === '4' ? 500 : 1000);
        right = randomInt(2, level === '4' ? 99 : 500);
        if (operation === '−' && right > left) [left, right] = [right, left];
        answer = operation === '+' ? left + right : left - right;
      }
      spokenOperator = operation === '÷' ? text('geteilt durch','divided by') : operation === '×' ? text('mal','times') : operation === '+' ? text('plus','plus') : text('minus','minus');
    }

    const visual = ['1','2'].includes(level)
      ? `${Array(left).fill('🍎').join(' ')} ${symbol} ${Array(right).fill('🍎').join(' ')}` : null;
    const prompt = text(`Was ist ${left} ${spokenOperator} ${right}?`, `What is ${left} ${spokenOperator} ${right}?`);
    return {q:prompt, visual, a:String(answer), c:randomMathChoices(answer, minimum), audioPrompt:prompt, speechLang:english?'en-US':'de-DE'};
  }
  function buildMathRound(level) {
    const questionsForRound = [];
    const seen = new Set();
    while (questionsForRound.length < 3) {
      const item = createMathQuestion(level);
      const signature = item.visual || item.q;
      if (seen.has(signature)) continue;
      seen.add(signature);
      questionsForRound.push(item);
    }
    return questionsForRound;
  }
  const germanWordCards = [
    ['Apfel','🍎'],['Ball','⚽'],['Maus','🐭'],['Sonne','☀️'],['Haus','🏠'],['Fisch','🐟'],['Hund','🐶'],['Blume','🌼'],['Auto','🚗'],['Banane','🍌'],['Stern','⭐'],['Schule','🏫'],['Pferd','🐴'],['Regen','🌧️'],['Katze','🐱'],['Ente','🦆']
  ];
  function createGermanQuestion(level = selectedLevel) {
    const [word, image] = shuffle(germanWordCards)[0];
    const answer = word[0].toLocaleUpperCase();
    const letters = [...new Set(germanWordCards.map(([entry]) => entry[0].toLocaleUpperCase()))].filter(letter => letter !== answer);
    const prompt = text(`Welcher Laut beginnt ${word}?`, `Which sound starts the German word ${word}?`);
    return {q:prompt, visual:['preschool','1','2'].includes(level)?image:null, a:answer, c:shuffle([answer,...shuffle(letters).slice(0,3)]), audioPrompt:prompt, promptLang:english?'en-US':'de-DE', speak:word, speechLang:'de-DE'};
  }
  function buildGermanRound(level) {
    const generated = Array.from({length:16}, () => createGermanQuestion(level));
    const existing = bank[level].german || [];
    return shuffle([...new Map([...existing,...generated].map(item=>[item.q,item])).values()]).slice(0,3);
  }
  const englishWordCards = [
    ['cat','Katze','🐱'],['dog','Hund','🐶'],['apple','Apfel','🍎'],['ball','Ball','⚽'],['sun','Sonne','☀️'],['fish','Fisch','🐟'],['house','Haus','🏠'],['flower','Blume','🌼'],['banana','Banane','🍌'],['star','Stern','⭐'],['book','Buch','📘'],['school','Schule','🏫'],['horse','Pferd','🐴'],['rain','Regen','🌧️'],['duck','Ente','🦆'],['moon','Mond','🌙']
  ];
  function createEnglishQuestion(level = selectedLevel) {
    const [word, translation] = shuffle(englishWordCards)[0];
    const options = shuffle(englishWordCards.filter(([, german]) => german !== translation)).slice(0,3);
    const selectedWords = [[word,translation,englishWordCards.find(([,german])=>german===translation)?.[2]],...options];
    const answers = selectedWords.map(([,german])=>german);
    const imageByAnswer = Object.fromEntries(selectedWords.map(([,german,emoji])=>[german,emoji]));
    const early = ['preschool','1','2'].includes(level);
    const prompt = early ? text('Höre das englische Wort und finde das Bild.','Listen to the English word and find the picture.')
      : text(`Was heißt „${word}“ auf Deutsch?`,`What does “${word}” mean in German?`);
    return {q:prompt, visual:early?'🔊':null, a:translation, c:answers, choiceImages:early?imageByAnswer:null, audioPrompt:early?word:prompt, promptLang:early?'en-US':english?'en-US':'de-DE', speak:word, speechLang:'en-US'};
  }
  function buildEnglishRound(level) {
    const generated = Array.from({length:16}, () => createEnglishQuestion(level));
    const existing = bank[level].english || [];
    // Early word-finding rounds deliberately reuse a spoken prompt, so use the
    // vocabulary item as the deduplication key instead of collapsing the round
    // to one generated question.
    return shuffle([...new Map([...existing,...generated].map(item=>[`${item.q}|${item.speak || item.a}`,item])).values()]).slice(0,3);
  }
  const worldQuestionCards = [
    ['Welches Tier sagt Miau?','Which animal says meow?','🐱',[['Katze','Cat','🐱'],['Kuh','Cow','🐄'],['Ente','Duck','🦆']]],
    ['Welches Tier lebt im Wasser?','Which animal lives in water?','🌊',[['Fisch','Fish','🐟'],['Hund','Dog','🐶'],['Vogel','Bird','🐦']]],
    ['Welches Tier kann fliegen?','Which animal can fly?','☁️',[['Vogel','Bird','🐦'],['Fisch','Fish','🐟'],['Katze','Cat','🐱']]],
    ['Was braucht eine Pflanze?','What does a plant need?','🌱',[['Wasser','Water','💧'],['Schuhe','Shoes','👟'],['Spielzeug','Toys','🧸']]],
    ['Was tragen wir auf dem Kopf?','What do we wear on our head?','🧢',[['Mütze','Hat','🧢'],['Socke','Sock','🧦'],['Tasse','Cup','☕']]],
    ['Womit hören wir?','Which body part do we hear with?','👂',[['Ohren','Ears','👂'],['Nase','Nose','👃'],['Knie','Knees','🦵']]],
    ['Welches Tier gibt Milch?','Which animal gives milk?','🥛',[['Kuh','Cow','🐄'],['Fisch','Fish','🐟'],['Frosch','Frog','🐸']]],
    ['Was wächst am Baum?','What grows on a tree?','🌳',[['Apfel','Apple','🍎'],['Auto','Car','🚗'],['Ball','Ball','⚽']]],
    ['Was wird aus Wasser bei Frost?','What does water become when it freezes?','❄️',[['Eis','Ice','🧊'],['Dampf','Steam','♨️'],['Sand','Sand','🏖️']]],
    ['Was macht die Biene?','What does a bee make?','🐝',[['Honig','Honey','🍯'],['Saft','Juice','🧃'],['Brot','Bread','🍞']]],
    ['Welche Jahreszeit kommt nach dem Winter?','Which season comes after winter?','🌷',[['Frühling','Spring','🌷'],['Herbst','Autumn','🍂'],['Sommer','Summer','☀️']]],
    ['Wie viele Beine hat ein Insekt?','How many legs does an insect have?','🐞',[['Sechs','Six','6️⃣'],['Vier','Four','4️⃣'],['Acht','Eight','8️⃣']]]
  ];
  function buildWorldRound(level) {
    const generated = worldQuestionCards.map(([de,en,visual,options]) => {
      const choices = options.map(([optionDe,optionEn,emoji])=>text(optionDe,optionEn));
      const choiceImages = Object.fromEntries(options.map(([optionDe,optionEn,emoji])=>[text(optionDe,optionEn),emoji]));
      const prompt = text(de,en);
      return {q:prompt, visual:['preschool','1','2'].includes(level)?visual:null, a:choices[0], c:choices, choiceImages, audioPrompt:prompt, promptLang:english?'en-US':'de-DE'};
    });
    const existing = ['preschool','1','2'].includes(level) ? bank[level].world : (bank[level].world || []);
    return shuffle([...new Map([...existing,...generated].map(item=>[item.q,item])).values()]).slice(0,3);
  }
  function buildSubjectRound(subject, level) {
    if (subject === 'math') return buildMathRound(level);
    if (subject === 'german') return buildGermanRound(level);
    if (subject === 'english') return buildEnglishRound(level);
    if (subject === 'world') return buildWorldRound(level);
    return [];
  }
  function buildMixedRound(level) {
    return shuffle(['math','german','english','world']).slice(0,3).map(subject => shuffle(buildSubjectRound(subject,level))[0]);
  }
  function voiceId(voice) {
    return voice.voiceURI || `${voice.name}|${voice.lang}`;
  }
  function renderSpeechVoices() {
    if (!('speechSynthesis' in window)) return;
    const voices = window.speechSynthesis.getVoices();
    speechVoiceSelects.forEach(select => {
      const language = select.dataset.speechVoice;
      const matching = voices.filter(voice => voice.lang.toLocaleLowerCase().startsWith(language));
      if (!matching.length) return;
      select.replaceChildren();
      const automatic = document.createElement('option');
      automatic.value = '';
      automatic.textContent = text('Automatisch – beste passende Stimme','Automatic – best matching voice');
      select.append(automatic);
      matching.sort((a,b)=>a.name.localeCompare(b.name)).forEach(voice=>{
        const option = document.createElement('option');
        option.value = voiceId(voice);
        option.textContent = `${voice.name} (${voice.lang}) · ${voice.localService ? text('Gerät','Device') : text('Online','Online')}`;
        select.append(option);
      });
      select.value = selectedSpeechVoices[language];
      if (select.value !== selectedSpeechVoices[language]) {
        selectedSpeechVoices[language] = '';
        select.value = '';
      }
      select.hidden = false;
    });
  }
  speechVoiceSelects.forEach(select => {
    const language = select.dataset.speechVoice;
    try { selectedSpeechVoices[language] = window.localStorage.getItem(`kids-learning.voice.${language}`) || ''; } catch {}
    select.addEventListener('change', () => {
      selectedSpeechVoices[language] = select.value;
      try { window.localStorage.setItem(`kids-learning.voice.${language}`, select.value); } catch {}
    });
  });
  if ('speechSynthesis' in window) {
    renderSpeechVoices();
    if (typeof window.speechSynthesis.addEventListener === 'function') window.speechSynthesis.addEventListener('voiceschanged', renderSpeechVoices);
    else window.speechSynthesis.onvoiceschanged = renderSpeechVoices;
  }
  function start(subject) {
    selectedSubject = subject;
    searchModes.hidden = true;
    searchGame.hidden = true;
    mazePanel.hidden = true;
    questions = subject === 'mixed' ? buildMixedRound(selectedLevel) : buildSubjectRound(subject, selectedLevel);
    index = 0;
    score = 0;
    setup.hidden = true;
    result.hidden = true;
    game.hidden = false;
    root.querySelector('[data-game-level]').textContent = levels[selectedLevel];
    root.querySelector('[data-game-subject]').textContent = subjects[subject];
    showQuestion();
  }
  function showSearchModes() {
    selectedSubject = 'search';
    setup.hidden = true;
    game.hidden = true;
    result.hidden = true;
    searchGame.hidden = true;
    mazePanel.hidden = true;
    searchModes.hidden = false;
    root.querySelector('[data-search-mode="objects"]').focus();
  }
  function startSearch(mode) {
    selectedSubject = 'search';
    selectedSearchMode = mode;
    searchIndex = 0;
    score = 0;
    searchReadyForNext = false;
    setup.hidden = true;
    searchModes.hidden = true;
    game.hidden = true;
    result.hidden = true;
    searchGame.hidden = false;
    root.querySelector('[data-search-level]').textContent = levels[selectedLevel];
    if (mode === 'objects') {
      const targets = shuffle(searchObjects[selectedLevel].map(([de, en, emoji]) => ({name:text(de, en), emoji})));
      searchTargets = targets.slice(0, 3);
      const targetEmojis = new Set(searchTargets.map(item => item.emoji));
      const distractors = Object.values(searchObjects).flat()
        .filter(([, , emoji]) => !targetEmojis.has(emoji))
        .map(([de, en, emoji]) => ({name:text(de, en), emoji}));
      searchObjectsInScene = shuffle([...searchTargets, ...shuffle(distractors).slice(0, 9)]);
    } else if (mode === 'letters') {
      searchTargets = shuffle(wordCards[selectedLevel]).slice(0, 3);
    } else if (mode === 'directions') {
      searchTargets = shuffle(arrowCards).slice(0, 3);
    } else if (mode === 'maze') {
      searchTargets = mazeFriends;
      mazeDifficulty = 'easy';
      mazeListen = false;
    } else {
      searchTargets = safetyCards;
    }
    showSearchRound();
  }
  function showSearchRound() {
    searchReadyForNext = false;
    searchNext.hidden = true;
    wrongAttempts = 0;
    setSearchFeedback('', '');
    root.querySelector('[data-search-speak]').setAttribute('aria-label', text('Aufgabe anhören', 'Listen to the question'));
    root.querySelector('[data-search-progress]').textContent = text(`Runde ${searchIndex + 1} von 3`, `Round ${searchIndex + 1} of 3`);
    mazePanel.hidden = true;
    if (selectedSearchMode === 'maze') {
      showMazeRound();
      return;
    }
    if (selectedSearchMode === 'objects' || selectedSearchMode === 'directions') {
      const target = searchTargets[searchIndex];
      const spokenPrompt = selectedSearchMode === 'directions'
        ? text(`Finde den Pfeil, der nach ${target.name} zeigt.`, `Find the arrow pointing ${target.name}.`)
        : text(`Finde ${target.name}.`, `Find ${target.name}.`);
      const early = ['preschool','1','2'].includes(selectedLevel);
      searchPrompt.textContent = early || selectedSearchMode === 'directions' ? '' : text(`Finde: ${target.name}`, `Find: ${target.name}`);
      searchPrompt.setAttribute('aria-label', spokenPrompt);
      root.querySelector('[data-search-speak]').hidden = false;
      root.querySelector('[data-search-speak]').onclick = () => speak(spokenPrompt, english ? 'en-US' : 'de-DE');
      if (early || selectedSearchMode === 'directions') speak(spokenPrompt, english ? 'en-US' : 'de-DE');
      searchPrompt.focus();
      const targetImage = root.querySelector('[data-search-target-image]');
      targetImage.textContent = target.emoji;
      targetImage.hidden = false;
      letterPlay.hidden = true;
      searchScene.hidden = false;
      searchScene.replaceChildren();
      const sceneItems = selectedSearchMode === 'directions'
        ? shuffle([target, ...shuffle(arrowCards.filter(card => card.emoji !== target.emoji)).slice(0, 11)])
        : searchObjectsInScene;
      sceneItems.forEach((item, itemIndex) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'kids-search-object';
        button.dataset.objectIndex = String(itemIndex);
        button.setAttribute('aria-label', item.name);
        const icon = document.createElement('span');
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = item.emoji;
        button.append(icon);
        button.addEventListener('click', () => findObject(button, item, target, sceneItems));
        searchScene.append(button);
      });
    } else if (selectedSearchMode === 'letters') {
      const card = searchTargets[searchIndex];
      activeWordCard = card;
      const prompt = text('Welcher Buchstabe fehlt in dem Wort?', 'Which letter is missing from the German word?');
      searchPrompt.textContent = ['preschool','1','2'].includes(selectedLevel) ? '' : prompt;
      searchPrompt.setAttribute('aria-label', prompt);
      root.querySelector('[data-search-speak]').hidden = false;
      const wordPrompt = [
        {text:prompt, language:promptSpeechLanguage},
        {text:card.word, language:'de-DE'}
      ];
      root.querySelector('[data-search-speak]').onclick = () => speak(wordPrompt);
      if (['preschool','1','2'].includes(selectedLevel)) speak(wordPrompt);
      searchPrompt.focus();
      root.querySelector('[data-search-target-image]').hidden = true;
      searchScene.hidden = true;
      letterPlay.hidden = false;
      root.querySelector('[data-letter-image]').textContent = card.image;
      root.querySelector('[data-letter-word]').textContent = card.word.toLocaleUpperCase().split('').map((letter, position) => position === card.missing ? '＿' : letter).join(' ');
      letterChoices.replaceChildren();
      const answer = card.word[card.missing].toLocaleLowerCase();
      const choices = shuffle([answer, ...shuffle('aeiourstlmnp'.split('').filter(letter => letter !== answer)).slice(0, 3)]);
      choices.forEach(letter => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'kids-learning-choice';
        button.textContent = letter.toLocaleUpperCase();
        button.addEventListener('click', () => answerLetter(letter, button));
        letterChoices.append(button);
      });
    } else {
      const card = searchTargets[searchIndex];
      const targetImage = root.querySelector('[data-search-target-image]');
      const prompt = card.prompt;
      searchPrompt.textContent = ['preschool','1','2'].includes(selectedLevel) ? '' : prompt;
      searchPrompt.setAttribute('aria-label', prompt);
      targetImage.textContent = card.image;
      targetImage.hidden = false;
      letterPlay.hidden = true;
      searchScene.hidden = false;
      searchScene.replaceChildren();
      root.querySelector('[data-search-speak]').hidden = false;
      root.querySelector('[data-search-speak]').onclick = () => speak(prompt, english ? 'en-US' : 'de-DE');
      speak(prompt, english ? 'en-US' : 'de-DE');
      card.choices.forEach(([emoji, label, unsafe]) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'kids-search-object kids-safety-choice';
        button.dataset.unsafe = String(unsafe);
        button.setAttribute('aria-label', label);
        button.textContent = emoji;
        button.addEventListener('click', () => answerSafety(button));
        searchScene.append(button);
      });
    }
  }
  function mazeDistance(from, goal) {
    const queue = [[from, 0]];
    const seen = new Set([from]);
    while (queue.length) {
      const [node, distance] = queue.shift();
      if (node === goal) return distance;
      Object.values(mazeNodes[node].exits).forEach(next => {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push([next, distance + 1]);
        }
      });
    }
    return Infinity;
  }
  function mazeRoute(from = mazeCurrentNode) {
    const queue = [[from, []]];
    const seen = new Set([from]);
    while (queue.length) {
      const [node, path] = queue.shift();
      if (node === 'goal') return path;
      Object.entries(mazeNodes[node].exits).forEach(([direction, next]) => {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push([next, [...path, direction]]);
        }
      });
    }
    return [];
  }
  function mazeDirectionName(direction) {
    return text({up:'oben',right:'rechts',down:'unten',left:'links'}[direction], direction);
  }
  function speakMazeRoute() {
    const route = mazeRoute();
    if (!route.length) return;
    const directions = route.map(mazeDirectionName);
    const instructions = english
      ? `Go ${directions.join(', then ')}.`
      : `Gehe ${directions.map((direction, position) => position ? `dann nach ${direction}` : `nach ${direction}`).join(', ')}.`;
    speak(instructions, english ? 'en-US' : 'de-DE');
  }
  function showMazeRound() {
    window.clearTimeout(mazeHappyTimer);
    mazeFriend = searchTargets[searchIndex];
    mazeCurrentNode = 'start';
    mazePreviousNode = '';
    mazeVisited = new Set(['start']);
    mazeWrongTurns = {};
    mazeCompleted = false;
    mazeHappy = false;
    mazePanel.hidden = false;
    searchScene.hidden = true;
    letterPlay.hidden = true;
    root.querySelector('[data-search-target-image]').hidden = true;
    root.querySelector('[data-search-speak]').hidden = true;
    searchPrompt.textContent = '';
    const task = text(`Führe ${mazeFriend.character} zum ${mazeFriend.de}.`, `Guide ${mazeFriend.character} to the ${mazeFriend.en}.`);
    searchPrompt.setAttribute('aria-label', task);
    mazeTask.textContent = `${mazeFriend.character}  ➜  ${mazeFriend.goal}`;
    mazeTask.setAttribute('aria-label', task);
    mazeListenButton.setAttribute('aria-pressed', String(mazeListen));
    mazeListenButton.textContent = `🔊 ${mazeListen ? text('Hörmodus an','Listen mode on') : text('Hörmodus aus','Listen mode off')}`;
    mazeListenButton.disabled = mazeDifficulty === 'hard';
    root.querySelectorAll('[data-maze-difficulty]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mazeDifficulty === mazeDifficulty)));
    mazePanel.focus();
    drawMaze();
    if (mazeListen) speakMazeRoute();
  }
  function drawMaze() {
    mazeMap.replaceChildren();
    mazeMap.setAttribute('aria-label', text(`Labyrinth: ${mazeFriend.character} soll zum ${mazeFriend.de}.`, `Maze: guide ${mazeFriend.character} to the ${mazeFriend.en}.`));
    const svg = 'http://www.w3.org/2000/svg';
    const point = node => ({x:50 + mazeNodes[node].col * 100, y:50 + mazeNodes[node].row * 100});
    const drawnEdges = new Set();
    Object.entries(mazeNodes).forEach(([node, config]) => Object.values(config.exits).forEach(next => {
      const key = [node, next].sort().join(':');
      if (drawnEdges.has(key)) return;
      drawnEdges.add(key);
      const start = point(node);
      const end = point(next);
      const road = document.createElementNS(svg, 'line');
      road.setAttribute('x1', String(start.x));
      road.setAttribute('y1', String(start.y));
      road.setAttribute('x2', String(end.x));
      road.setAttribute('y2', String(end.y));
      road.setAttribute('stroke', '#b6c0cb');
      road.setAttribute('stroke-width', '24');
      road.setAttribute('stroke-linecap', 'round');
      mazeMap.append(road);
    }));
    Object.keys(mazeNodes).forEach(node => {
      const {x,y} = point(node);
      const circle = document.createElementNS(svg, 'circle');
      circle.setAttribute('cx', String(x));
      circle.setAttribute('cy', String(y));
      circle.setAttribute('r', '20');
      circle.setAttribute('fill', node === 'goal' ? '#fff3cd' : '#fff');
      circle.setAttribute('stroke', node === 'goal' ? '#f0ad00' : '#697586');
      circle.setAttribute('stroke-width', '4');
      mazeMap.append(circle);
    });
    const addIcon = (node, icon, label, offsetX = 0) => {
      const {x,y} = point(node);
      const marker = document.createElementNS(svg, 'text');
      marker.setAttribute('x', String(x + offsetX));
      marker.setAttribute('y', String(y + 1));
      marker.setAttribute('text-anchor', 'middle');
      marker.setAttribute('dominant-baseline', 'central');
      marker.setAttribute('font-size', '32');
      marker.setAttribute('aria-label', label);
      marker.textContent = icon;
      mazeMap.append(marker);
    };
    if (mazeCurrentNode !== 'start') addIcon('start', mazeFriend.character, text('Start','Start'));
    addIcon('goal', mazeFriend.goal, text('Ziel','Goal'));
    addIcon(mazeCurrentNode, mazeHappy ? `${mazeFriend.character}😊` : mazeFriend.character, text('Figur','Character'), mazeCurrentNode === 'goal' ? -30 : 0);
    root.querySelectorAll('[data-maze-direction]').forEach(button => {
      const key = `${mazeCurrentNode}:${button.dataset.mazeDirection}`;
      const misses = mazeWrongTurns[key] || 0;
      button.style.opacity = mazeDifficulty === 'easy' && misses ? String(Math.max(.28, 1 - misses * .24)) : '';
    });
  }
  function fadeMazeChoice(direction) {
    if (mazeDifficulty !== 'easy') return;
    const junction = Object.keys(mazeNodes[mazeCurrentNode].exits).length > 1;
    if (!junction) return;
    const key = `${mazeCurrentNode}:${direction}`;
    mazeWrongTurns[key] = (mazeWrongTurns[key] || 0) + 1;
    drawMaze();
  }
  function moveMaze(direction) {
    if (mazeCompleted) return;
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    const next = mazeNodes[mazeCurrentNode].exits[direction];
    if (!next) {
      fadeMazeChoice(direction);
      if (mazeListen) speakMazeRoute();
      return;
    }
    const previous = mazeCurrentNode;
    const closerToGoal = mazeDistance(next, 'goal') < mazeDistance(previous, 'goal');
    const wrongKey = `${mazeCurrentNode}:${direction}`;
    if (!closerToGoal && next !== mazePreviousNode && (!mazeVisited.has(next) || mazeWrongTurns[wrongKey])) fadeMazeChoice(direction);
    mazePreviousNode = previous;
    mazeCurrentNode = next;
    mazeVisited.add(next);
    mazeHappy = closerToGoal;
    drawMaze();
    if (mazeCurrentNode === 'goal') {
      mazeCompleted = true;
      score += 1;
      setSearchFeedback('right', text('Ihr habt den Weg gefunden!', 'You found the way!'));
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    } else if (mazeListen) speakMazeRoute();
    window.clearTimeout(mazeHappyTimer);
    if (mazeHappy && !mazeCompleted) mazeHappyTimer = window.setTimeout(() => { mazeHappy = false; drawMaze(); }, 850);
  }
  function findObject(button, item, target, items = searchObjectsInScene) {
    if (searchReadyForNext || button.disabled) return;
    if (item.emoji === target.emoji) {
      button.classList.add('is-found');
      button.disabled = true;
      score += 1;
      setSearchFeedback('right', text('Gefunden! Gut gemacht!', 'Found it! Well done!'));
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
    } else {
      button.classList.add('is-missed');
      wrongAttempts += 1;
      button.dataset.mistakes = String(Number(button.dataset.mistakes || 0) + 1);
      button.style.opacity = String(Math.max(.12, 1 - Number(button.dataset.mistakes) * .2));
      setSearchFeedback('wrong', wrongAttempts >= maxWrongAttempts
        ? text('Schau auf das Bild: Das ist die Lösung.', 'Look at the picture: this is the answer.')
        : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Suche noch einmal.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Look again.`));
      if (wrongAttempts >= maxWrongAttempts) {
        searchScene.querySelectorAll('button').forEach((option, position) => {
          option.disabled = true;
          if (items[position].emoji !== target.emoji) option.hidden = true;
          else option.classList.add('is-found');
        });
        searchReadyForNext = true;
        searchNext.hidden = false;
        searchNext.focus();
      } else window.setTimeout(() => button.classList.remove('is-missed'), 500);
    }
  }
  function answerSafety(button) {
    if (searchReadyForNext) return;
    if (button.dataset.unsafe === 'false') {
      score += 1;
      button.classList.add('is-found');
      searchScene.querySelectorAll('button').forEach(option => { option.disabled = true; });
      setSearchFeedback('right', text('Richtig und sicher!', 'That is safe!'));
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
      return;
    }
    wrongAttempts += 1;
    fadeWrongChoice(button);
    setSearchFeedback('wrong', wrongAttempts >= maxWrongAttempts
      ? text('Schau: Das ist die sichere Wahl.', 'Look: this is the safe choice.')
      : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}.`, `Try ${wrongAttempts} of ${maxWrongAttempts}.`));
    if (wrongAttempts >= maxWrongAttempts) {
      searchScene.querySelectorAll('button').forEach(option => {
        option.disabled = true;
        if (option.dataset.unsafe === 'false') option.classList.add('is-found');
        else option.hidden = true;
      });
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
    }
  }
  function answerLetter(letter, selectedButton = null) {
    if (!activeWordCard || searchReadyForNext) return false;
    const normalizedLetter = letter.toLocaleLowerCase();
    const button = selectedButton || [...letterChoices.querySelectorAll('button')]
      .find(option => option.textContent.toLocaleLowerCase() === normalizedLetter);
    if (!button || button.disabled) return false;
    const correct = normalizedLetter === activeWordCard.word[activeWordCard.missing].toLocaleLowerCase();
    if (correct) {
      score += 1;
      const displayed = activeWordCard.word.split('');
      displayed.forEach((letter, position) => { displayed[position] = letter.toLocaleUpperCase(); });
      root.querySelector('[data-letter-word]').textContent = displayed.join(' ');
      letterChoices.querySelectorAll('button').forEach(button => {
        button.disabled = true;
        if (button.textContent.toLocaleLowerCase() === normalizedLetter) button.classList.add('is-correct');
      });
      setSearchFeedback('right', text('Richtig! Das Wort heißt: ', 'That’s right! The word is: ') + activeWordCard.word.toLocaleUpperCase());
      root.querySelector('[data-search-speak]').setAttribute('aria-label', text('Wort noch einmal anhören', 'Listen to the word again'));
      speak(activeWordCard.word, 'de-DE');
      searchReadyForNext = true;
      searchNext.hidden = false;
      searchNext.focus();
    } else {
      wrongAttempts += 1;
      fadeWrongChoice(button);
      setSearchFeedback('wrong', wrongAttempts >= maxWrongAttempts
        ? text(`Die Lösung ist ${activeWordCard.word[activeWordCard.missing].toLocaleUpperCase()}.`, `The missing letter is ${activeWordCard.word[activeWordCard.missing].toLocaleUpperCase()}.`)
        : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Probier es noch einmal.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Try again.`));
      letterChoices.querySelector('button:not(:disabled):not(.is-correct)')?.focus();
      if (wrongAttempts >= maxWrongAttempts) {
        const revealed = activeWordCard.word.toLocaleUpperCase();
        root.querySelector('[data-letter-word]').textContent = revealed.split('').join(' ');
        letterChoices.querySelectorAll('button').forEach(option => {
          option.disabled = true;
          if (option.textContent.toLocaleLowerCase() === activeWordCard.word[activeWordCard.missing].toLocaleLowerCase()) option.classList.add('is-correct');
          else option.hidden = true;
        });
        root.querySelector('[data-search-speak]').onclick = () => speak(activeWordCard.word, 'de-DE');
        setSearchFeedback('wrong', text(`Die Lösung ist ${revealed}.`, `The answer is ${revealed}.`));
        searchReadyForNext = true;
        searchNext.hidden = false;
        searchNext.focus();
      }
    }
    return true;
  }
  function finishSearch() {
    searchGame.hidden = true;
    result.hidden = false;
    root.querySelector('[data-result-title]').textContent = text('Toll gesucht! 🎉', 'Great searching! 🎉');
    root.querySelector('[data-result-copy]').textContent = text(`Du hast ${score} von 3 Aufgaben geschafft.`, `You completed ${score} of 3 tasks.`);
    root.querySelector('[data-result-icon]').textContent = score === 3 ? '🌼😊' : '😟';
    root.querySelector('[data-result-title]').focus();
  }
  function showQuestion() {
    const item = questions[index];
    wrongAttempts = 0;
    progress.textContent = text(`Frage ${index + 1} von ${questions.length}`, `Question ${index + 1} of ${questions.length}`);
    questionNode.textContent = item.visual || item.q;
    if (item.visual) questionNode.setAttribute('aria-label', item.q);
    else questionNode.removeAttribute('aria-label');
    questionNode.focus();
    setQuizFeedback('', '');
    root.querySelector('[data-next]').hidden = true;
    speakButton.hidden = !item.speak && !item.audioPrompt;
    const promptSpeech = item.audioParts?.length ? item.audioParts : item.audioPrompt || item.speak;
    speakButton.onclick = () => speak(promptSpeech, item.promptLang || item.speechLang || 'en-US');
    if (item.audioPrompt || item.audioParts?.length) speak(promptSpeech, item.promptLang || item.speechLang || 'en-US');
    choicesNode.replaceChildren();
    shuffle(item.c).forEach(choice => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kids-learning-choice';
      button.dataset.answer = String(choice);
      button.textContent = item.choiceImages?.[choice] || choice;
      if (item.choiceImages?.[choice]) button.setAttribute('aria-label', choice);
      button.addEventListener('click', () => answer(button, choice, item));
      choicesNode.append(button);
    });
  }
  function answer(button, choice, item) {
    if (choicesNode.querySelector('button.is-correct')) return;
    const correct = choice === item.a;
    if (correct) {
      score += 1;
      choicesNode.querySelectorAll('button').forEach(option => {
        option.disabled = true;
        if (option.dataset.answer === String(item.a)) option.classList.add('is-correct');
      });
      setQuizFeedback('right', text('Richtig! Super gemacht!', 'That’s right! Well done!'));
      root.querySelector('[data-next]').hidden = false;
      if (item.speak) {
        speakButton.onclick = () => speak(item.speak, item.speechLang || 'en-US');
        speak(item.speak, item.speechLang || 'en-US');
      }
    } else {
      wrongAttempts += 1;
      fadeWrongChoice(button);
      setQuizFeedback('wrong', wrongAttempts >= maxWrongAttempts
        ? text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Jetzt siehst du die Lösung.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Here is the answer.`)
        : text(`Versuch ${wrongAttempts} von ${maxWrongAttempts}. Probier es noch einmal.`, `Try ${wrongAttempts} of ${maxWrongAttempts}. Try again.`));
      if (wrongAttempts >= maxWrongAttempts) revealQuizAnswer(item);
    }
  }
  function speak(word, language = 'en-US') {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      speechStatus.textContent = text('Vorlesestimme auf diesem Gerät nicht verfügbar.','Read-aloud speech is not available on this device.');
      return;
    }
    const segments = Array.isArray(word) ? word : [{text:word, language}];
    window.speechSynthesis.cancel();
    segments.forEach(segment => {
      const spokenText = String(segment.text || '').trim();
      if (!spokenText) return;
      const utterance = new SpeechSynthesisUtterance(spokenText);
      utterance.lang = segment.language || language;
      utterance.rate = .86;
      utterance.pitch = 1.04;
      const languageKey = utterance.lang.toLocaleLowerCase().startsWith('de') ? 'de' : 'en';
      const available = window.speechSynthesis.getVoices().filter(voice => voice.lang.toLocaleLowerCase().startsWith(languageKey));
      const requested = available.find(voice => voiceId(voice) === selectedSpeechVoices[languageKey]);
      const qualityScore = voice => (voice.lang.toLocaleLowerCase() === utterance.lang.toLocaleLowerCase() ? 100 : 50)
        + (voice.localService ? 8 : 0)
        + (/natural|neural|premium|enhanced/i.test(voice.name) ? 12 : 0)
        + (voice.default ? 1 : 0);
      const chosen = requested || [...available].sort((a, b) => qualityScore(b) - qualityScore(a))[0];
      if (chosen) utterance.voice = chosen;
      utterance.onerror = event => {
        if (event.error === 'canceled' || event.error === 'interrupted') return;
        speechStatus.textContent = text('Die Stimme konnte nicht abgespielt werden. Wähle eine andere Stimme.','The voice could not be played. Choose another voice.');
      };
      utterance.onstart = () => { speechStatus.textContent = ''; };
      window.speechSynthesis.speak(utterance);
    });
  }
  function showSetup() {
    if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    window.clearTimeout(mazeHappyTimer);
    game.hidden = true;
    searchGame.hidden = true;
    searchModes.hidden = true;
    result.hidden = true;
    setup.hidden = false;
    mazePanel.hidden = true;
    root.querySelector('#kids-learning-level-heading').focus();
  }

  root.querySelectorAll('[data-level]').forEach(button => button.addEventListener('click', () => {
    selectedLevel = button.dataset.level;
    root.querySelectorAll('[data-level]').forEach(level => level.setAttribute('aria-pressed', String(level === button)));
  }));
  root.querySelectorAll('[data-subject]').forEach(button => button.addEventListener('click', () => {
    if (button.dataset.subject === 'search') showSearchModes();
    else start(button.dataset.subject);
  }));
  root.querySelectorAll('[data-search-mode]').forEach(button => button.addEventListener('click', () => startSearch(button.dataset.searchMode)));
  root.querySelectorAll('[data-maze-difficulty]').forEach(button => button.addEventListener('click', () => {
    if (mazeCompleted) return;
    mazeDifficulty = button.dataset.mazeDifficulty;
    if (mazeDifficulty === 'hard') {
      mazeListen = false;
      mazeListenButton.setAttribute('aria-pressed', 'false');
      mazeListenButton.textContent = `🔊 ${text('Hörmodus aus','Listen mode off')}`;
      mazeListenButton.disabled = true;
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    } else mazeListenButton.disabled = false;
    mazeCurrentNode = 'start';
      mazePreviousNode = '';
    mazeVisited = new Set(['start']);
    mazeWrongTurns = {};
    mazeHappy = false;
    root.querySelectorAll('[data-maze-difficulty]').forEach(option => option.setAttribute('aria-pressed', String(option === button)));
    drawMaze();
    mazePanel.focus();
  }));
  mazeListenButton.addEventListener('click', () => {
    if (mazeDifficulty === 'hard' || mazeCompleted) return;
    mazeListen = !mazeListen;
    mazeListenButton.setAttribute('aria-pressed', String(mazeListen));
    mazeListenButton.textContent = `🔊 ${mazeListen ? text('Hörmodus an','Listen mode on') : text('Hörmodus aus','Listen mode off')}`;
    if (mazeListen) speakMazeRoute();
    else if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  });
  root.querySelectorAll('[data-maze-direction]').forEach(button => button.addEventListener('click', () => moveMaze(button.dataset.mazeDirection)));
  root.addEventListener('keydown', event => {
    if (selectedSearchMode !== 'maze' || searchGame.hidden || !mazePanel.contains(document.activeElement)) return;
    const direction = {ArrowUp:'up',ArrowRight:'right',ArrowDown:'down',ArrowLeft:'left'}[event.key];
    if (!direction) return;
    event.preventDefault();
    moveMaze(direction);
  });
  root.querySelector('[data-search-modes-back]').addEventListener('click', showSetup);
  root.querySelector('[data-search-back]').addEventListener('click', showSetup);
  searchNext.addEventListener('click', () => {
    if (!searchReadyForNext) return;
    searchIndex += 1;
    if (searchIndex < 3) showSearchRound();
    else finishSearch();
  });
  searchScene.addEventListener('keydown', event => {
    const movement = {ArrowRight:1, ArrowLeft:-1, ArrowDown:4, ArrowUp:-4}[event.key];
    if (!movement) return;
    const buttons = [...searchScene.querySelectorAll('button')];
    const current = buttons.indexOf(document.activeElement);
    if (current < 0) return;
    event.preventDefault();
    buttons[Math.max(0, Math.min(buttons.length - 1, current + movement))].focus();
  });
  root.addEventListener('keydown', event => {
    const letterGameHasFocus = document.activeElement === searchPrompt || letterPlay.contains(document.activeElement);
    if (searchGame.hidden || selectedSearchMode !== 'letters' || !letterGameHasFocus
        || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing
        || !/^[a-z]$/i.test(event.key)) return;
    if (answerLetter(event.key)) event.preventDefault();
  });
  root.querySelector('[data-next]').addEventListener('click', () => {
    index += 1;
    if (index < questions.length) showQuestion();
    else {
      game.hidden = true;
      result.hidden = false;
      root.querySelector('[data-result-title]').textContent = text('Toll gespielt! 🎉', 'Great playing! 🎉');
      root.querySelector('[data-result-copy]').textContent = text(`Du hast ${score} von ${questions.length} Fragen richtig beantwortet.`, `You got ${score} out of ${questions.length} questions right.`);
      root.querySelector('[data-result-icon]').textContent = score === questions.length ? '🌼😊' : '😟';
      root.querySelector('[data-result-title]').focus();
    }
  });
  root.querySelector('[data-back]').addEventListener('click', showSetup);
  root.querySelector('[data-result-back]').addEventListener('click', showSetup);
  root.querySelector('[data-replay]').addEventListener('click', () => {
    if (selectedSubject === 'search') startSearch(selectedSearchMode);
    else start(selectedSubject);
  });
  if (new URLSearchParams(window.location.search).get('game') === 'maze') startSearch('maze');
})();
