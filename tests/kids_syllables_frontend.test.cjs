const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const scriptSource = fs.readFileSync(path.join(__dirname, '../static/js/kids_syllables.js'), 'utf8');
const sharedSource = fs.readFileSync(path.join(__dirname, '../static/js/kids_shared.js'), 'utf8');

function matches(node, selector) {
  return selector.split(',').map(part => part.trim()).some(part => {
    const attribute = part.match(/^\[([a-z-]+)(?:="([^"]*)")?\]$/);
    if (attribute) {
      if (!(attribute[1] in node.attributes)) return false;
      return attribute[2] === undefined || node.attributes[attribute[1]] === attribute[2];
    }
    return node.tag === part;
  });
}

function makeNode(tag) {
  const node = {
    tag, children: [], attributes: {}, dataset: {}, handlers: {},
    hidden: false, disabled: false, textContent: '', style: {},
    classList: {
      classes: new Set(),
      add(...names) { names.forEach(name => this.classes.add(name)); },
      remove(...names) { names.forEach(name => this.classes.delete(name)); },
      contains(name) { return this.classes.has(name); },
    },
    setAttribute(key, value) {
      this.attributes[key] = String(value);
      if (key.startsWith('data-')) {
        const camel = key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        this.dataset[camel] = String(value);
      }
    },
    getAttribute(key) { return this.attributes[key]; },
    addEventListener(type, listener) { (this.handlers[type] = this.handlers[type] || []).push(listener); },
    click() { (this.handlers.click || []).forEach(listener => listener()); },
    append(...items) { this.children.push(...items); return items[0]; },
    appendChild(item) { this.children.push(item); return item; },
    replaceChildren(...items) { this.children = items; },
    focus() { this.focused = true; },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    querySelectorAll(selector) {
      const found = [];
      const walk = current => {
        current.children.forEach(child => {
          if (matches(child, selector)) found.push(child);
          walk(child);
        });
      };
      walk(this);
      return found;
    },
  };
  return node;
}

// Mirror of templates/kids/syllables.html hooks.
function buildPage(language = 'de') {
  const root = makeNode('main');
  root.setAttribute('data-kids-syllables', '');
  root.dataset.language = language;
  const picker = makeNode('section');
  picker.setAttribute('data-level-picker', '');
  picker.append(makeNode('h2'));
  const levelButtons = {};
  ['clap', 'count', 'rhyme'].forEach(level => {
    const button = makeNode('button');
    button.setAttribute('data-syll-level', level);
    picker.append(button);
    levelButtons[level] = button;
  });
  const play = makeNode('section');
  play.setAttribute('data-syll-play', '');
  play.hidden = true;
  const progress = makeNode('p');
  progress.setAttribute('data-syll-progress', '');
  const title = makeNode('h2');
  title.setAttribute('data-syll-title', '');
  const repeat = makeNode('button');
  repeat.setAttribute('data-syll-repeat', '');
  const prompt = makeNode('p');
  prompt.setAttribute('data-syll-prompt', '');
  const stage = makeNode('div');
  stage.setAttribute('data-syll-stage', '');
  const feedback = makeNode('p');
  feedback.setAttribute('data-syll-feedback', '');
  const next = makeNode('button');
  next.setAttribute('data-syll-next', '');
  next.hidden = true;
  const back = makeNode('button');
  back.setAttribute('data-syll-back', '');
  play.append(progress, title, repeat, prompt, stage, feedback, next, back);
  const result = makeNode('section');
  result.setAttribute('data-syll-result', '');
  result.hidden = true;
  const resultTitle = makeNode('h2');
  resultTitle.setAttribute('data-result-title', '');
  const resultCopy = makeNode('p');
  resultCopy.setAttribute('data-result-copy', '');
  const again = makeNode('button');
  again.setAttribute('data-play-again', '');
  const resultBack = makeNode('button');
  resultBack.setAttribute('data-result-back', '');
  result.append(resultTitle, resultCopy, again, resultBack);
  root.append(picker, play, result);
  const document = {
    root,
    createElement(tag) { return makeNode(tag); },
    querySelector(selector) {
      if (selector === '[data-kids-syllables]') return this.root;
      return this.root ? this.root.querySelector(selector) : null;
    },
    querySelectorAll(selector) { return this.root ? this.root.querySelectorAll(selector) : []; },
  };
  return {document, root, picker, play, result, progress, title, repeat, prompt, stage, feedback, next, back, resultTitle, resultCopy, again, resultBack, levelButtons};
}

function runScript(page) {
  const sandbox = {window: {}};
  if (page) sandbox.document = page.document;
  vm.runInNewContext(sharedSource, sandbox);
  vm.runInNewContext(scriptSource, sandbox);
  return sandbox.window.KidsSyllables;
}

test('syllable vocabulary is consistent in both languages', () => {
  const api = runScript(null);
  for (const language of ['de', 'en']) {
    const counts = new Set();
    for (const entry of api.WORDS[language]) {
      assert.ok(entry.emoji, `${language}: emoji required`);
      assert.ok(entry.word, `${language}: word required`);
      assert.ok(Array.isArray(entry.parts) && entry.parts.length >= 1 && entry.parts.length <= 4, `${entry.word}: 1-4 syllables`);
      assert.equal(entry.parts.join('').toLowerCase(), entry.word.toLowerCase(), `${entry.word}: parts must spell the word`);
      assert.equal(api.syllableCount(entry), entry.parts.length);
      counts.add(entry.parts.length);
    }
    assert.deepEqual([...counts].sort(), [1, 2, 3, 4], `${language}: pool must cover 1-4 syllables`);
  }
});

test('rhyme sets offer one rhyme and two distinct distractors', () => {
  const api = runScript(null);
  for (const language of ['de', 'en']) {
    assert.ok(api.RHYMES[language].length >= 3, 'at least one round per language');
    for (const set of api.RHYMES[language]) {
      assert.ok(set.target.emoji && set.target.word);
      assert.ok(set.rhyme.emoji && set.rhyme.word);
      assert.equal(set.others.length, 2);
      const emojis = [set.target.emoji, set.rhyme.emoji, ...set.others.map(item => item.emoji)];
      assert.equal(new Set(emojis).size, 4, `${set.target.word}: pictures must be distinct`);
      assert.notEqual(set.rhyme.word, set.target.word);
    }
  }
});

test('question builders return valid rounds and fall back to German', () => {
  const api = runScript(null);
  const clap = api.makeClapQuestions('en');
  assert.equal(clap.length, api.QUESTIONS_PER_LEVEL);
  // Spread vm-realm arrays into outer-realm arrays before deep comparison.
  const counts = [...clap.map(question => question.parts.length)];
  assert.deepEqual([...counts].sort((a, b) => a - b), counts, 'clap round starts easy');
  const count = api.makeCountQuestions('de');
  assert.equal(count.length, api.QUESTIONS_PER_LEVEL);
  for (const question of count) {
    assert.equal(question.answer, question.parts.length);
    assert.deepEqual([...question.choices].sort(), [1, 2, 3, 4]);
  }
  const rhymes = api.makeRhymeQuestions('de');
  assert.equal(rhymes.length, api.QUESTIONS_PER_LEVEL);
  for (const question of rhymes) {
    const emojis = question.choices.map(choice => choice.emoji);
    assert.equal(new Set(emojis).size, 3);
    assert.ok(emojis.includes(question.answer.emoji));
    assert.ok(!emojis.includes(question.target.emoji), 'target picture is shown, not a choice');
  }
  const fallback = api.makeClapQuestions('fr');
  assert.equal(fallback.length, api.QUESTIONS_PER_LEVEL);
  assert.ok(fallback.every(question => api.WORDS.de.some(entry => entry.word === question.word)));
});

test('clap level counts taps and finishes a full round', () => {
  const page = buildPage('de');
  runScript(page);
  page.levelButtons.clap.click();
  assert.equal(page.play.hidden, false);
  assert.equal(page.title.textContent, 'Stufe 1: Klatsche mit');
  for (let round = 1; round <= 3; round += 1) {
    assert.equal(page.progress.textContent, `Aufgabe ${round} von 3`);
    const dots = page.stage.children.find(child => child.className === 'kids-syll-dots');
    const clap = page.stage.children.find(child => child.tag === 'button');
    const total = dots.textContent.length;
    assert.ok(total >= 1 && total <= 4);
    for (let tap = 1; tap < total; tap += 1) {
      clap.click();
      assert.ok(dots.textContent.startsWith('●'.repeat(tap)));
      assert.match(page.feedback.textContent, /Noch \d/);
    }
    clap.click();
    assert.match(page.feedback.textContent, /Richtig!/);
    assert.equal(page.next.hidden, false);
    page.next.click();
  }
  assert.equal(page.result.hidden, false);
  assert.match(page.resultTitle.textContent, /toll gemacht/);
  assert.match(page.resultCopy.textContent, /3 von 3/);
});

test('count level accepts a retry and returns to the picker', () => {
  const page = buildPage('en');
  const api = runScript(page);
  page.levelButtons.count.click();
  assert.equal(page.title.textContent, 'Level 2: Count syllables');
  const word = page.prompt.textContent.replace(/^🔢 /, '');
  const entry = api.WORDS.en.find(item => item.word === word);
  assert.ok(entry, `prompt word ${word} must come from the vocabulary`);
  const buttons = page.stage.querySelectorAll('button');
  assert.equal(buttons.length, 4);
  const wrong = buttons.find(button => button.textContent !== String(entry.parts.length));
  wrong.click();
  assert.match(page.feedback.textContent, /Try 1 of 5/);
  assert.ok(wrong.classList.contains('is-wrong'));
  buttons.find(button => button.textContent === String(entry.parts.length)).click();
  assert.match(page.feedback.textContent, /right/);
  assert.equal(page.next.hidden, false);
  page.back.click();
  assert.equal(page.picker.hidden, false);
  assert.equal(page.play.hidden, true);
});

test('rhyme level praises pairs and reveals answers after five tries', () => {
  const page = buildPage('de');
  const api = runScript(page);
  page.levelButtons.rhyme.click();
  const targetWord = page.prompt.textContent.replace(/^🎵 /, '').replace(/ → \?$/, '');
  const set = api.RHYMES.de.find(item => item.target.word === targetWord);
  assert.ok(set, `rhyme target ${targetWord} must come from the rhyme sets`);
  const buttons = page.stage.querySelectorAll('button');
  buttons.find(button => button.getAttribute('aria-label') === set.rhyme.word).click();
  assert.match(page.feedback.textContent, /reimen sich/);
  page.next.click();
  const second = page.stage.querySelectorAll('button');
  const wrongTarget = page.prompt.textContent.replace(/^🎵 /, '').replace(/ → \?$/, '');
  const wrongSet = api.RHYMES.de.find(item => item.target.word === wrongTarget);
  const wrongButton = second.find(button => button.getAttribute('aria-label') !== wrongSet.rhyme.word);
  assert.ok(wrongButton, 'distractor buttons must exist');
  for (let attempt = 0; attempt < 5; attempt += 1) wrongButton.click();
  assert.match(page.feedback.textContent, /hier ist die Lösung/);
  assert.equal(page.next.hidden, false);
  const revealed = second.find(button => button.getAttribute('aria-label') === wrongSet.rhyme.word);
  assert.ok(revealed.classList.contains('is-correct'));
  assert.equal(wrongButton.hidden, true);
});

test('script without its page exposes only the logic api', () => {
  const sandbox = {window: {}, document: {querySelector: () => null, createElement: () => assert.fail('no dom needed')}};
  vm.runInNewContext(scriptSource, sandbox);
  assert.deepEqual([...sandbox.window.KidsSyllables.LEVELS], ['clap', 'count', 'rhyme']);
});
