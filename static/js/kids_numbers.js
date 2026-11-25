(() => {
  const root = document.querySelector('[data-kids-numbers]');
  if (!root || !window.KidsShared) return;
  const {shuffle, stopAudio, uiLanguage, playClips} = window.KidsShared;

  const english = root.dataset.language === 'en';
  const text = (de, en) => english ? en : de;
  const language = uiLanguage(english);
  const audioBase = root.dataset.audioBase || '';
  const audioVersion = root.dataset.audioVersion || '';
  const clipLang = english ? 'en' : 'de';
  let currentClips = [];
  const speechStatus = root.querySelector('[data-speech-status]');
  function say(parts) {
    currentClips = parts;
    return playClips(audioBase, audioVersion, clipLang, parts, language, speechStatus);
  }
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
  const fallbackConfig = {
    levels: {
      easy: {max: 3, choices: 2, tasks: 8, compare: false},
      medium: {max: 5, choices: 3, tasks: 8, compare: true},
      hard: {max: 10, choices: 3, tasks: 8, compare: true}
    },
    items: [
      {emoji: '🐶', de: 'Hund', de_plural: 'Hunde', en: 'dog', en_plural: 'dogs', audio: 'n_hund', audio_plural: 'n_hunde'},
      {emoji: '🐱', de: 'Katze', de_plural: 'Katzen', en: 'cat', en_plural: 'cats', audio: 'n_katze', audio_plural: 'n_katzen'},
      {emoji: '🐰', de: 'Hase', de_plural: 'Hasen', en: 'rabbit', en_plural: 'rabbits', audio: 'n_hase', audio_plural: 'n_hasen'},
      {emoji: '🐸', de: 'Frosch', de_plural: 'Frösche', en: 'frog', en_plural: 'frogs', audio: 'n_frosch', audio_plural: 'n_froesche'},
      {emoji: '🐞', de: 'Käfer', de_plural: 'Käfer', en: 'ladybug', en_plural: 'ladybugs', audio: 'n_kaefer', audio_plural: 'n_kaefer_pl'},
      {emoji: '⭐', de: 'Stern', de_plural: 'Sterne', en: 'star', en_plural: 'stars', audio: 'n_stern', audio_plural: 'n_sterne'}
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
  let replayQuestion = null;

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

  function nounAudio(item, amount) {
    return amount === 1 ? item.audio : item.audio_plural;
  }

  function showCount(task, options) {
    const amount = 1 + Math.floor(Math.random() * options.max);
    task.amount = amount;
    const spoken = text(`Wie viele ${noun(task.item, amount)} siehst du?`, `How many ${noun(task.item, amount)} can you see?`);
    ask([
      {key: 'how_many', fallback: text('Wie viele', 'How many')},
      {key: nounAudio(task.item, amount), fallback: noun(task.item, amount)},
      {key: 'see_you', fallback: text('siehst du?', 'can you see?')},
    ], spoken);
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
    ask([
      {key: 'tap_on', fallback: text('Tippe auf', 'Tap')},
      {key: `num_${amount}`, fallback: String(amount)},
      {key: nounAudio(task.item, amount), fallback: noun(task.item, amount)},
    ], spoken);
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
    ask([
      {key: wantMore ? 'where_more' : 'where_less', fallback: wantMore ? text('Wo sind mehr', 'Where are more') : text('Wo sind weniger', 'Where are fewer')},
      {key: task.item.audio_plural, fallback: noun(task.item, 2)},
      {key: 'tap_it', fallback: text('Tipp drauf.', 'Tap it.')},
    ], spoken);
    shuffle([first, second]).forEach(value => {
      const button = makeAnswerButton(picture(value, task.item), value === task.amount);
      button.classList.add('kids-numbers-group');
      button.setAttribute('aria-label', `${value} ${noun(task.item, value)}`);
      stage.append(button);
    });
  }

  function ask(parts, spoken) {
    prompt.textContent = spoken;
    say(parts);
    // Pin this question before feedback or the optional counting aid speaks.
    const questionClips = currentClips;
    replayQuestion = () => say(questionClips);
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
      const cheerIndex = Math.floor(Math.random() * praise.length);
      const cheer = praise[cheerIndex];
      feedback.textContent = `🎉 ${cheer}`;
      feedback.classList.add('is-good');
      say([{key: `praise_${cheerIndex + 1}`, fallback: cheer}]);
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
      say([{key: 'try_again', fallback: retry}]);
    }
  }

  function finish() {
    play.hidden = true;
    result.hidden = false;
    const total = tasks.length;
    const heading = root.querySelector('[data-result-title]');
    const copy = root.querySelector('[data-result-copy]');
    let resultKey = 'nice_playing';
    if (firstTry === total) {
      heading.textContent = text('Spitzenmäßig! 🎉', 'Amazing! 🎉');
      copy.textContent = text(`Alle ${total} Aufgaben beim ersten Versuch! Du bist ein Zählprofi.`, `All ${total} tasks on the first try! You are a counting star.`);
      resultKey = 'amazing';
    } else if (firstTry * 2 >= total) {
      heading.textContent = text('Gut gemacht! ⭐', 'Well done! ⭐');
      copy.textContent = text(`Du hast ${firstTry} von ${total} Sternen gesammelt.`, `You collected ${firstTry} of ${total} stars.`);
      resultKey = 'well_done';
    } else {
      heading.textContent = text('Schön mitgemacht! 🌼', 'Nice playing! 🌼');
      copy.textContent = text(`Du hast ${firstTry} von ${total} Sternen gesammelt. Nochmal?`, `You collected ${firstTry} of ${total} stars. Again?`);
    }
    say([{key: resultKey, fallback: heading.textContent}]);
    heading.focus();
  }

  function toSetup() {
    stopAudio();
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
  root.querySelector('[data-repeat]').addEventListener('click', () => {
    if (!play.hidden) replayQuestion?.();
  });
  root.querySelector('[data-back]').addEventListener('click', toSetup);
  root.querySelector('[data-result-back]').addEventListener('click', toSetup);
  root.querySelector('[data-play-again]').addEventListener('click', () => begin(level));
  next.addEventListener('click', () => {
    taskIndex += 1;
    if (taskIndex >= tasks.length) finish();
    else showTask();
  });
})();
