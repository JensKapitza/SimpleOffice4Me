const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_numbers.js'), 'utf8');

class Element {
  constructor(tag = '') {
    this.tag = tag;
    this.children = [];
    this.attributes = new Map();
    this.handlers = new Map();
    this.dataset = {};
    this.className = '';
    this.classList = {add: value => { this.className += ` ${value}`; }};
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
  }
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key) ?? null; }
  addEventListener(key, handler) { this.handlers.set(key, handler); }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children.length = 0; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  querySelectorAll(tag) {
    return this.children.flatMap(child => [
      ...(child.tag === tag ? [child] : []), ...child.querySelectorAll(tag),
    ]);
  }
  click() { if (!this.disabled) this.handlers.get('click')?.(); }
  focus() { this.focused = true; }
}

function page(language = 'de') {
  const names = ['speech-status', 'setup', 'play', 'result', 'stage', 'title', 'prompt',
    'feedback', 'progress', 'stars', 'next', 'repeat', 'back', 'result-back', 'play-again'];
  const root = new Element();
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  const first = new Element('button');
  first.dataset.level = 'easy';
  root.dataset.language = language;
  root.dataset.audioBase = '/static/audio/kids/games/numbers';
  const item = {emoji: '🐶', de: 'Hund', de_plural: 'Hunde', en: 'dog', en_plural: 'dogs', audio: 'n_hund', audio_plural: 'n_hunde'};
  const config = {levels: {easy: {max: 3, choices: 2, tasks: 4, compare: false}}, items: [item]};
  root.querySelector = key => key === '#kids-numbers-config' ? {textContent: JSON.stringify(config)} : elements[key.slice(6, -1)];
  root.querySelectorAll = key => key === '[data-level]' ? [first] : [];
  elements.setup.querySelector = () => first;
  const audio = [];
  const window = {KidsShared: {
    shuffle: values => [...values], stopAudio() {}, uiLanguage: en => en ? 'en-US' : 'de-DE',
    playClips: (base, _version, clipLang, parts) => audio.push(Array.from(parts, part => `${base}/${clipLang}/${part.key}.mp3`)),
  }};
  let randomCalls = 0;
  const math = Object.create(Math);
  math.random = () => { randomCalls += 1; return (randomCalls <= 4 || randomCalls % 2 === 1) ? 0.95 : 0; };
  vm.runInNewContext(source, {
    window, Math: math, document: {querySelector: () => root, createElement: tag => new Element(tag)},
  });
  first.click();
  const tiles = () => elements.stage.querySelectorAll('button').filter(x => x.className === 'kids-numbers-count-tile');
  const answers = () => elements.stage.querySelectorAll('button').filter(x => x.className.startsWith('kids-numbers-answer'));
  const clipNames = () => audio.map(parts => parts.map(url => url.split('/').at(-1)));
  return {elements, first, tiles, answers, audio, clipNames};
}

test('each object counts at most once, follows the child’s tap order and keeps repeat on the question', () => {
  const p = page();
  const tiles = p.tiles();
  assert.equal(tiles.length, 3);
  assert.ok(tiles.every(tile => tile.children[1].hidden));
  assert.equal(tiles[0].getAttribute('aria-label'), 'Hund zählen');
  assert.deepEqual(p.clipNames().at(-1), ['how_many.mp3', 'n_hunde.mp3', 'see_you.mp3']);

  tiles[2].click();
  tiles[2].click();
  tiles[0].click();
  assert.equal(tiles[2].children[1].textContent, '1');
  assert.equal(tiles[0].children[1].textContent, '2');
  assert.equal(tiles[2].getAttribute('aria-disabled'), 'true');
  assert.equal(tiles[2].getAttribute('aria-label'), 'Hund gezählt: 1');
  assert.equal(tiles[1].children[1].hidden, true);
  assert.deepEqual(p.clipNames().slice(1), [['num_1.mp3'], ['num_2.mp3']]);
  assert.ok(p.audio.at(-1)[0].startsWith('/static/audio/kids/games/numbers/de/'));
  assert.equal(p.elements.next.hidden, true, 'tapping is a hint, not an answer');

  p.elements.repeat.click();
  assert.deepEqual(p.clipNames().at(-1), ['how_many.mp3', 'n_hunde.mp3', 'see_you.mp3']);
});

test('children can answer by sight, and stale tapping never changes later tasks', () => {
  const p = page('en');
  const oldTiles = p.tiles();
  const correct = p.answers().find(button => button.textContent === '3');
  correct.click();
  assert.equal(p.elements.next.hidden, false);
  assert.ok(p.answers().filter(button => button !== correct).every(button => button.disabled));
  assert.ok(oldTiles.every(tile => tile.disabled));
  assert.match(p.elements.feedback.textContent, /^🎉/);

  p.elements.next.click();
  assert.equal(p.elements.title.focused, true);
  assert.equal(p.tiles().length, 0, 'Find-the-group keeps its original interaction');
  const audioCalls = p.audio.length;
  oldTiles[0].disabled = false; // Queued/stale click must still be ignored after reset.
  oldTiles[0].click();
  assert.equal(p.audio.length, audioCalls);

  const correctGroup = p.answers().find(button => button.getAttribute('aria-label').startsWith('1 '));
  correctGroup.click();
  assert.equal(p.elements.next.hidden, false);
  p.elements.next.click();
  const newTile = p.tiles()[0];
  assert.equal(newTile.children[1].hidden, true);
  newTile.click();
  assert.equal(newTile.children[1].textContent, '1');
  assert.ok(p.audio.at(-1)[0].endsWith('/en/num_1.mp3'));
});

test('a wrong answer leaves the counting aid usable without altering the check marks', () => {
  const p = page();
  const tiles = p.tiles();
  tiles[0].click();
  const wrong = p.answers().find(button => button.textContent !== '3');
  wrong.click();
  assert.equal(wrong.disabled, true);
  assert.equal(p.elements.next.hidden, true);
  tiles[1].click();
  assert.equal(tiles[0].children[1].textContent, '1');
  assert.equal(tiles[1].children[1].textContent, '2');
  assert.equal(p.elements.next.hidden, true);
  assert.match(p.elements.feedback.textContent, /Versuch es nochmal/);
});

test('easy rounds match pictures to dots and show dots for the requested group without changing answers', () => {
  for (const language of ['de', 'en']) {
    const p = page(language);
    const dots = element => element.children.find(child => child.className === 'kids-numbers-dots');
    const assertDots = (group, expected) => {
      assert.equal(group.getAttribute('aria-hidden'), 'true');
      assert.equal(group.children.length, expected);
      assert.ok(group.children.every(dot => dot.className === 'kids-numbers-dot'));
    };
    assert.equal(p.tiles().length, 3);
    assert.ok(p.answers().length > 1);
    p.answers().forEach(button => assertDots(dots(button), Number(button.textContent)));
    assert.equal(p.elements.next.hidden, true);
    p.answers().find(button => dots(button).children.length === p.tiles().length).click();
    p.elements.next.click();

    const cue = p.elements.stage.children.find(child => child.className === 'kids-numbers-quantity-cue');
    assert.equal(cue.getAttribute('aria-hidden'), 'true');
    const wanted = Number(p.elements.prompt.textContent.match(/\d+/)[0]);
    assertDots(dots(cue), wanted);
    assert.ok(p.answers().every(button => !dots(button)));
    assert.equal(p.elements.next.hidden, true);
    const match = p.answers().find(button => button.textContent.split(' ').length === wanted);
    p.answers().find(button => button !== match).click();
    assert.ok(p.elements.next.hidden);
    assert.ok(p.elements.stage.contains(cue), 'retry retains the same visible clue');
    match.click();
    assert.equal(p.elements.next.hidden, false);
    p.elements.next.click();
    assert.ok(!p.elements.stage.contains(cue));
  }
});
