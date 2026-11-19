(() => {
  const root = document.querySelector('[data-kids-numbers]');
  if (!root) return;

  const english = root.dataset.language === 'en';
  const text = (de, en) => english ? en : de;
  const setup = root.querySelector('[data-setup]');
  const play = root.querySelector('[data-play]');
  const result = root.querySelector('[data-result]');
  const stage = root.querySelector('[data-stage]');
  const title = root.querySelector('[data-title]');
  const prompt = root.querySelector('[data-prompt]');
  const feedback = root.querySelector('[data-feedback]');
  const progress = root.querySelector('[data-progress]');
  const stars = root.querySelector('[data-stars]');
  const next = root.querySelector('[data-next]');
  const speechStatus = root.querySelector('[data-speech-status]');
  const fallbackConfig = {
    levels: {
      easy: {max: 3, choices: 2, tasks: 8, compare: false},
      medium: {max: 5, choices: 3, tasks: 8, compare: true},
      hard: {max: 10, choices: 3, tasks: 8, compare: true}
    },
    items: [
      {emoji: '🐶', de: 'Hund', de_plural: 'Hunde', en: 'dog', en_plural: 'dogs'},
      {emoji: '🐱', de: 'Katze', de_plural: 'Katzen', en: 'cat', en_plural: 'cats'},
      {emoji: '🐰', de: 'Hase', de_plural: 'Hasen', en: 'rabbit', en_plural: 'rabbits'},
      {emoji: '🐸', de: 'Frosch', de_plural: 'Frösche', en: 'frog', en_plural: 'frogs'},
      {emoji: '🐞', de: 'Käfer', de_plural: 'Käfer', en: 'ladybug', en_plural: 'ladybugs'},
      {emoji: '⭐', de: 'Stern', de_plural: 'Sterne', en: 'star', en_plural: 'stars'}
    ]
  };
  let config = fallbackConfig;
  try {
    const raw = root.querySelector('#kids-numbers-config');
    if (raw) config = { ...fallbackConfig, ...JSON.parse(raw.textContent) };
  } catch { config = fallbackConfig; }

  const praise = [
    text('Super!', 'Great!'),
    text('Genau richtig!', 'Exactly right!'),
    text('Toll gemacht!', 'Well done!'),
    text('Prima, weiter so!', 'Awesome, keep going!')
  ];
  let level = 'easy';
  let tasks = [];
  let taskIndex = 0;
  let firstTry = 0;
  let mistakes = 0;
  let locked = false;
  let currentSpoken = '';

  function shuffle(items) {
    const values = [...items];
    for (let index = values.length - 1; index > 0; index -= 1) {
      const swap = Math.floor(Math.random() * (index + 1));
      [values[index], values[swap]] = [values[swap], values[index]];
    }
    return values;
  }

  function uiLanguage() {
    return english ? 'en-US' : 'de-DE';
  }

  function voiceRank(voice, language) {
    // On-device voices first: same-language local voices must beat
    // same-language cloud voices so prompts stay offline and private.
    return (voice.lang.toLowerCase() === language.toLowerCase() ? 100 : 50)
      + (voice.localService ? 24 : 0)
      + (/natural|neural|premium|enhanced/i.test(voice.name) ? 6 : 0);
  }

  function speak(value) {
    if (!('speechSynthesis' in window) || !('SpeechSynthesisUtterance' in window)) {
      if (speechStatus) speechStatus.textContent = text('Vorlesen geht auf diesem Gerät nicht. Schau dir die Bilder genau an.', 'Read-aloud is not available on this device. Look closely at the pictures.');
      return;
    }
    if (speechStatus) speechStatus.textContent = '';
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = uiLanguage();
    utterance.rate = 0.9;
    utterance.pitch = 1.05;
    const prefix = utterance.lang.slice(0, 2).toLowerCase();
    const voices = window.speechSynthesis.getVoices().filter(voice => voice.lang.toLowerCase().startsWith(prefix));
    let requested = '';
    try { requested = window.localStorage.getItem(`kids-learning.voice.${prefix}`) || ''; } catch (error) { requested = ''; }
    const voiceId = voice => voice.voiceURI || `${voice.name}|${voice.lang}`;
    const chosen = voices.find(voice => voiceId(voice) === requested)
      || [...voices].sort((a, b) => voiceRank(b, utterance.lang) - voiceRank(a, utterance.lang))[0];
    if (chosen) utterance.voice = chosen;
    window.speechSynthesis.speak(utterance);
  }

  function noun(item, amount) {
    if (amount === 1) return english ? item.en : item.de;
    return english ? item.en_plural : item.de_plural;
  }

  function picture(amount, item) {
    return Array(amount).fill(item.emoji).join(' ');
  }

  function distinctNumbers(count, max, include) {
    const values = new Set([include]);
    let guard = 0;
    while (values.size < Math.min(count, max) && guard < 200) {
      values.add(1 + Math.floor(Math.random() * max));
      guard += 1;
    }
    return shuffle([...values]);
  }

  function makeTasks() {
    const options = config.levels[level] || fallbackConfig.levels.easy;
    const kinds = options.compare ? ['count', 'show', 'more', 'less'] : ['count', 'show'];
    const list = [];
    for (let index = 0; index < options.tasks; index += 1) {
      const kind = kinds[index % kinds.length];
      const item = config.items[Math.floor(Math.random() * config.items.length)];
      // Rotate kinds in shuffled blocks so every game mixes all task types.
      list.push({ kind, item, max: options.max, choices: options.choices });
    }
    // Shuffle within blocks of all kinds to vary the order every round.
    const blocks = [];
    for (let start = 0; start < list.length; start += kinds.length) {
      blocks.push(...shuffle(list.slice(start, start + kinds.length)));
    }
    return blocks;
  }

  function begin(selected) {
    level = selected;
    tasks = makeTasks();
    taskIndex = 0;
    firstTry = 0;
    setup.hidden = true;
    result.hidden = true;
    play.hidden = false;
    title.textContent = text('Zählspaß', 'Counting fun');
    showTask();
    title.focus();
  }

  function showTask() {
    const task = tasks[taskIndex];
    const options = config.levels[level];
    mistakes = 0;
    locked = false;
    next.hidden = true;
    feedback.textContent = '';
    feedback.className = 'kids-numbers-feedback';
    progress.textContent = text(`Aufgabe ${taskIndex + 1} von ${tasks.length}`, `Task ${taskIndex + 1} of ${tasks.length}`);
    renderStars();
    stage.replaceChildren();
    if (task.kind === 'count') showCount(task, options);
    else if (task.kind === 'show') showFind(task, options);
    else showCompare(task, options);
  }

  function renderStars() {
    stars.textContent = '⭐'.repeat(firstTry) + '☆'.repeat(Math.max(0, taskIndex - firstTry));
  }

  function showCount(task, options) {
    const amount = 1 + Math.floor(Math.random() * options.max);
    task.amount = amount;
    const spoken = text(`Wie viele ${noun(task.item, amount)} siehst du?`, `How many ${noun(task.item, amount)} can you see?`);
    ask(spoken);
    const figures = document.createElement('div');
    figures.className = 'kids-numbers-picture';
    figures.setAttribute('role', 'img');
    figures.setAttribute('aria-label', `${amount} ${noun(task.item, amount)}`);
    figures.textContent = picture(amount, task.item);
    stage.append(figures);
    distinctNumbers(options.choices, options.max, amount).forEach(value => {
      stage.append(makeAnswerButton(String(value), value === amount));
    });
  }

  function showFind(task, options) {
    const amount = 1 + Math.floor(Math.random() * options.max);
    task.amount = amount;
    const spoken = text(`Tippe auf ${amount} ${noun(task.item, amount)}.`, `Tap ${amount} ${noun(task.item, amount)}.`);
    ask(spoken);
    distinctNumbers(options.choices, options.max, amount).forEach(value => {
      const button = makeAnswerButton(picture(value, task.item), value === amount);
      button.classList.add('kids-numbers-group');
      button.setAttribute('aria-label', `${value} ${noun(task.item, value)}`);
      stage.append(button);
    });
  }

  function showCompare(task, options) {
    const first = 1 + Math.floor(Math.random() * options.max);
    let second = 1 + Math.floor(Math.random() * options.max);
    let guard = 0;
    while (second === first && guard < 50) {
      second = 1 + Math.floor(Math.random() * options.max);
      guard += 1;
    }
    const wantMore = task.kind === 'more';
    task.amount = wantMore ? Math.max(first, second) : Math.min(first, second);
    const spoken = wantMore
      ? text(`Wo sind mehr ${noun(task.item, 2)}? Tipp drauf.`, `Where are more ${noun(task.item, 2)}? Tap it.`)
      : text(`Wo sind weniger ${noun(task.item, 2)}? Tipp drauf.`, `Where are fewer ${noun(task.item, 2)}? Tap it.`);
    ask(spoken);
    shuffle([first, second]).forEach(value => {
      const button = makeAnswerButton(picture(value, task.item), value === task.amount);
      button.classList.add('kids-numbers-group');
      button.setAttribute('aria-label', `${value} ${noun(task.item, value)}`);
      stage.append(button);
    });
  }

  function ask(spoken) {
    currentSpoken = spoken;
    prompt.textContent = spoken;
    speak(spoken);
  }

  function makeAnswerButton(label, correct) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'kids-numbers-answer';
    button.textContent = label;
    button.addEventListener('click', () => answer(button, correct));
    return button;
  }

  function answer(button, correct) {
    if (locked) return;
    if (correct) {
      locked = true;
      button.classList.add('is-correct');
      if (mistakes === 0) firstTry += 1;
      renderStars();
      const cheer = praise[Math.floor(Math.random() * praise.length)];
      feedback.textContent = `🎉 ${cheer}`;
      feedback.classList.add('is-good');
      speak(cheer);
      stage.querySelectorAll('button').forEach(candidate => {
        if (candidate !== button) candidate.disabled = true;
      });
      next.hidden = false;
      next.focus();
    } else {
      mistakes += 1;
      button.classList.add('is-wrong');
      button.disabled = true;
      const retry = text('Versuch es nochmal. Du schaffst das! 💪', 'Try again. You can do it! 💪');
      feedback.textContent = retry;
      feedback.classList.add('is-try');
      speak(retry);
    }
  }

  function finish() {
    play.hidden = true;
    result.hidden = false;
    const total = tasks.length;
    const heading = root.querySelector('[data-result-title]');
    const copy = root.querySelector('[data-result-copy]');
    if (firstTry === total) {
      heading.textContent = text('Spitzenmäßig! 🎉', 'Amazing! 🎉');
      copy.textContent = text(`Alle ${total} Aufgaben beim ersten Versuch! Du bist ein Zählprofi.`, `All ${total} tasks on the first try! You are a counting star.`);
    } else if (firstTry * 2 >= total) {
      heading.textContent = text('Gut gemacht! ⭐', 'Well done! ⭐');
      copy.textContent = text(`Du hast ${firstTry} von ${total} Sternen gesammelt.`, `You collected ${firstTry} of ${total} stars.`);
    } else {
      heading.textContent = text('Schön mitgemacht! 🌼', 'Nice playing! 🌼');
      copy.textContent = text(`Du hast ${firstTry} von ${total} Sternen gesammelt. Nochmal?`, `You collected ${firstTry} of ${total} stars. Again?`);
    }
    speak(heading.textContent);
    heading.focus();
  }

  function toSetup() {
    window.speechSynthesis?.cancel();
    play.hidden = true;
    result.hidden = true;
    setup.hidden = false;
    feedback.textContent = '';
    const first = setup.querySelector('[data-level]');
    if (first) first.focus();
  }

  root.querySelectorAll('[data-level]').forEach(button => {
    button.addEventListener('click', () => begin(button.dataset.level));
  });
  root.querySelector('[data-repeat]').addEventListener('click', () => speak(currentSpoken));
  root.querySelector('[data-back]').addEventListener('click', toSetup);
  root.querySelector('[data-result-back]').addEventListener('click', toSetup);
  root.querySelector('[data-play-again]').addEventListener('click', () => begin(level));
  next.addEventListener('click', () => {
    taskIndex += 1;
    if (taskIndex >= tasks.length) finish();
    else showTask();
  });
})();
