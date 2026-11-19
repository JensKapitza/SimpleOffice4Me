/* Matching game for ages 2-3: hear the task, tap the matching picture.
   Categories: colors, shapes, animals, animal sounds. No reading needed. */
(() => {
  const root = document.querySelector('[data-kids-matching]');
  if (!root || !window.KidsShared) return;
  const {shuffle, stopAudio, uiLanguage, playClips} = window.KidsShared;

  const english = root.dataset.language === 'en';
  const text = (de, en) => english ? en : de;
  const language = uiLanguage(english);
  const audioBase = root.dataset.audioBase || '';
  const audioVersion = root.dataset.audioVersion || '';
  const clipLang = english ? 'en' : 'de';
  let currentClips = [];
  function say(parts) {
    currentClips = parts;
    return playClips(audioBase, audioVersion, clipLang, parts, language, speechStatus);
  }
  const setup = root.querySelector('[data-setup]');
  const play = root.querySelector('[data-play]');
  const result = root.querySelector('[data-result]');
  const stage = root.querySelector('[data-stage]');
  const target = root.querySelector('[data-target]');
  const title = root.querySelector('[data-title]');
  const prompt = root.querySelector('[data-prompt]');
  const feedback = root.querySelector('[data-feedback]');
  const progress = root.querySelector('[data-progress]');
  const next = root.querySelector('[data-next]');
  const repeat = root.querySelector('[data-repeat]');
  const speechStatus = root.querySelector('[data-speech-status]');
  const levelButtons = [...root.querySelectorAll('[data-level]')];

  let config = null;
  try {
    config = JSON.parse(root.querySelector('#kids-matching-config').textContent);
  } catch (error) { config = null; }
  if (!config || !config.categories || !config.levels) {
    setup.insertAdjacentHTML('beforeend', `<p class="alert alert-warning mb-0" role="alert">${text('Das Spiel konnte nicht geladen werden. Bitte lade die Seite neu.', 'The game could not be loaded. Please reload the page.')}</p>`);
    return;
  }

  const categories = config.categories;
  const levels = config.levels;
  let level = 'easy';
  let category = '';
  let tasks = [];
  let taskIndex = 0;
  let score = 0;
  let mistakes = 0;

  const word = item => english ? item.en : item.de;
  const soundWord = item => english ? item.sound_en : item.sound_de;

  function categoryTitle(key) {
    const titles = {
      colors: text('Farben', 'Colors'),
      shapes: text('Formen', 'Shapes'),
      animals: text('Tiere', 'Animals'),
      sounds: text('Tiergeräusche', 'Animal sounds'),
      mixed: text('Überrasch mich', 'Surprise me'),
    };
    return titles[key] || titles.mixed;
  }

  function poolFor(key) {
    if (key !== 'mixed') return (categories[key] || []).map(item => ({...item, kind: key}));
    return Object.entries(categories).flatMap(([kind, items]) => (items || []).map(item => ({...item, kind})));
  }

  function makeTasks(key) {
    const settings = levels[level] || levels.easy;
    const picked = [];
    while (picked.length < settings.rounds) {
      const cycle = shuffle(poolFor(key));
      if (!cycle.length) break;
      while (cycle.length && picked.length < settings.rounds) {
        picked.push(cycle.splice(Math.floor(Math.random() * cycle.length), 1)[0]);
      }
    }
    return picked.map(targetItem => {
      const distractors = shuffle(poolFor(targetItem.kind).filter(item => item.emoji !== targetItem.emoji || word(item) !== word(targetItem))).slice(0, settings.choices - 1);
      return {target: targetItem, choices: shuffle([targetItem, ...distractors]), kind: targetItem.kind};
    });
  }

  function taskSpoken(task) {
    if (task.kind === 'sounds') {
      return text(`Welches Tier macht „${soundWord(task.target)}"?`, `Which animal says “${soundWord(task.target)}”?`);
    }
    return text(`Finde: ${word(task.target)}!`, `Find: ${word(task.target)}!`);
  }

  function begin(selected) {
    category = selected;
    tasks = makeTasks(selected);
    if (!tasks.length) return;
    taskIndex = 0;
    score = 0;
    setup.hidden = true;
    result.hidden = true;
    play.hidden = false;
    title.textContent = categoryTitle(selected);
    showTask();
    title.focus();
  }

  function showTask() {
    const task = tasks[taskIndex];
    mistakes = 0;
    next.hidden = true;
    feedback.textContent = '';
    progress.textContent = text(`Runde ${taskIndex + 1} von ${tasks.length}`, `Round ${taskIndex + 1} of ${tasks.length}`);
    stage.replaceChildren();
    if (task.kind === 'sounds') {
      target.textContent = '🔊❓';
      target.setAttribute('aria-label', taskSpoken(task));
      prompt.textContent = text(`Hör gut zu: ${soundWord(task.target)}`, `Listen well: ${soundWord(task.target)}`);
    } else {
      target.textContent = task.target.emoji;
      target.setAttribute('aria-label', word(task.target));
      prompt.textContent = text('Tippe auf das gleiche Bild.', 'Tap the matching picture.');
    }
    task.choices.forEach(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kids-matching-answer';
      button.textContent = item.emoji;
      button.setAttribute('aria-label', word(item));
      button.addEventListener('click', () => answer(button, item, task));
      stage.append(button);
    });
    say([{key: task.target.prompt_audio, fallback: taskSpoken(task)}]);
  }

  function answer(button, item, task) {
    if (next.hidden === false || button.disabled) return;
    if (item.emoji === task.target.emoji && word(item) === word(task.target)) {
      score += 1;
      button.classList.add('is-correct');
      stage.querySelectorAll('button').forEach(choice => { choice.disabled = true; });
      const praise = text(`Richtig! ${word(task.target)}!`, `Correct! ${word(task.target)}!`);
      feedback.textContent = `🌟 ${praise}`;
      say([
        {key: 'praise_right', fallback: text('Richtig!', 'Correct!')},
        {key: task.target.word_audio, fallback: word(task.target)},
      ]);
      next.hidden = false;
      next.focus();
      return;
    }
    mistakes += 1;
    button.classList.add('is-wrong');
    button.dataset.mistakes = String(Number(button.dataset.mistakes || 0) + 1);
    button.style.opacity = String(Math.max(.25, 1 - Number(button.dataset.mistakes) * .25));
    if (mistakes >= 3) {
      revealAnswer(task);
      return;
    }
    const comfort = text('Fast! Versuch es noch einmal.', 'Almost! Try again.');
    feedback.textContent = comfort;
    say([{key: 'try_again', fallback: comfort}]);
  }

  function revealAnswer(task) {
    stage.querySelectorAll('button').forEach(button => {
      button.disabled = true;
      if (button.getAttribute('aria-label') === word(task.target)) button.classList.add('is-correct');
      else button.hidden = true;
    });
    const solution = text(`Schau, hier ist: ${word(task.target)}.`, `Look, here is: ${word(task.target)}.`);
    feedback.textContent = solution;
    say([
      {key: 'look_here', fallback: text('Schau, hier ist:', 'Look, here is:')},
      {key: task.target.word_audio, fallback: word(task.target)},
    ]);
    next.hidden = false;
    next.focus();
  }

  function nextTask() {
    if (taskIndex >= tasks.length - 1) { finish(); return; }
    taskIndex += 1;
    showTask();
  }

  function finish() {
    play.hidden = true;
    setup.hidden = true;
    result.hidden = false;
    const complete = score === tasks.length;
    const heading = text(complete ? 'Das hast du toll gemacht! 🌼' : 'Gut gespielt! 🌟', complete ? 'You did great! 🌼' : 'Nice playing! 🌟');
    root.querySelector('[data-result-title]').textContent = heading;
    root.querySelector('[data-result-copy]').textContent = text(
      `Du hast ${score} von ${tasks.length} Aufgaben geschafft.`,
      `You finished ${score} out of ${tasks.length} tasks.`,
    );
    root.querySelector('[data-result-title]').focus();
    say([{key: complete ? 'done_great' : 'nice_playing', fallback: heading}]);
  }

  function showSetup() {
    stopAudio();
    result.hidden = true;
    play.hidden = true;
    setup.hidden = false;
    const heading = root.querySelector('[data-setup] h2');
    if (heading) {
      heading.setAttribute('tabindex', '-1');
      heading.focus();
    }
  }

  levelButtons.forEach(button => button.addEventListener('click', () => {
    level = button.dataset.level;
    levelButtons.forEach(candidate => {
      const selected = candidate === button;
      candidate.classList.toggle('is-selected', selected);
      candidate.setAttribute('aria-pressed', String(selected));
    });
  }));
  root.querySelectorAll('[data-category]').forEach(button => button.addEventListener('click', () => begin(button.dataset.category)));
  repeat.addEventListener('click', () => { if (currentClips.length) say(currentClips); });
  next.addEventListener('click', nextTask);
  root.querySelector('[data-back]').addEventListener('click', showSetup);
  root.querySelector('[data-result-back]').addEventListener('click', showSetup);
  root.querySelector('[data-play-again]').addEventListener('click', () => begin(category));
})();
