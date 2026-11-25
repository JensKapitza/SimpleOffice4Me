const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_mini_games.js'), 'utf8');

function fixture(language = 'de') {
  let focused;
  let now = 0;
  let timerId = 0;
  const timers = new Map();
  const advance = ms => {
    now += ms;
    for (const [id, timer] of timers) {
      if (timer.at <= now) { timers.delete(id); timer.callback(); }
    }
  };
  const element = () => {
    const attributes = new Map();
    const classes = new Set();
    const events = new Map();
    return {
      children: [], dataset: {}, hidden: false, disabled: false, style: {},
      textContent: '',
      classList: {
        add: name => classes.add(name), remove: name => classes.delete(name),
        contains: name => classes.has(name),
      },
      setAttribute: (name, value) => attributes.set(name, value),
      getAttribute: name => attributes.get(name),
      removeAttribute: name => attributes.delete(name),
      addEventListener: (name, callback) => events.set(name, callback),
      click() { if (!this.disabled) events.get('click')?.(); },
      focus() { focused = this; },
      replaceChildren(...items) { this.children = items; },
      append(...items) { this.children.push(...items); },
      querySelectorAll() { return this.children; },
      contains(node) { return this === node || this.children.some(child => child.contains(node)); },
    };
  };
  const names = 'game-picker mini-play mini-result mini-stage mini-title mini-prompt mini-feedback mini-progress mini-memory-progress mini-next mini-back mini-repeat result-title result-copy result-back play-again'.split(' ');
  const parts = Object.fromEntries(names.map(name => [name, element()]));
  const game = Object.fromEntries(['memory', 'count', 'odd'].map(name => {
    const button = element();
    button.dataset.miniGame = name;
    return [name, button];
  }));
  parts['game-picker'].children = Object.values(game);
  parts['mini-memory-progress'].hidden = true;
  const root = {
    dataset: {language},
    querySelector(selector) { return parts[selector.match(/^\[data-(.*?)\]$/)?.[1]] ?? element(); },
    querySelectorAll() { return Object.values(game); },
  };
  vm.runInNewContext(source, {
    document: {querySelector: () => root, createElement: element},
    window: {
      setTimeout(callback, delay) {
        timers.set(++timerId, {at: now + delay, callback});
        return timerId;
      },
      clearTimeout(id) { timers.delete(id); },
    },
  });
  return {parts, game, advance, focused: () => focused, cards: () => parts['mini-stage'].children};
}

function mismatch(cards) {
  const [first] = cards;
  const second = cards.find(card => first.dataset.pair !== card.dataset.pair);
  const untouched = cards.find(card => card !== first && card !== second);
  first.click();
  second.click();
  return [first, second, untouched];
}

test('return and restart isolate the two mismatch timers and preserve position labels', () => {
  const {parts, game, advance, focused, cards} = fixture();
  game.memory.click();
  mismatch(cards());
  advance(250);
  parts['mini-back'].focus();
  parts['mini-back'].click();
  assert.equal(parts['game-picker'].hidden, false);
  assert.equal(focused(), game.memory);
  game.memory.click();

  const [first, second, untouched] = mismatch(cards());
  advance(500); // The previous round's 750 ms is up, the new round's is not.
  untouched.click();
  assert.notEqual(first.textContent, '❔');
  assert.notEqual(second.textContent, '❔');
  assert.equal(untouched.textContent, '❔');
  advance(250);
  assert.ok(cards().every(card => card.textContent === '❔'));
  for (const [i, card] of cards().entries()) {
    assert.equal(card.getAttribute('aria-label'), `Verdeckte Karte ${i + 1}`);
  }
  untouched.click();
  assert.notEqual(untouched.textContent, '❔');
});

test('return from results focuses the launcher and switching clears old instructions', () => {
  const {parts, game, focused, cards} = fixture('en');
  game.odd.click();
  assert.match(parts['mini-stage'].getAttribute('aria-label'), /different one/);
  parts['mini-back'].click();
  assert.equal(focused(), game.odd);
  game.memory.click();
  assert.equal(parts['mini-stage'].getAttribute('aria-label'), undefined);
  assert.match(parts['mini-prompt'].textContent, /Tap two cards/);
  const memory = cards();
  for (const card of memory) {
    if (card.disabled) continue;
    card.click();
    memory.find(other => other !== card && other.dataset.pair === card.dataset.pair).click();
  }
  assert.equal(parts['mini-result'].hidden, false);
  assert.match(parts['result-copy'].textContent, /3 pairs in 3 turns/);
  parts['result-back'].click();
  assert.equal(focused(), game.memory);
});

test('memory progress only marks real pairs and resets on replay', () => {
  for (const language of ['de', 'en']) {
    const {parts, game, advance, cards} = fixture(language);
    const progress = parts['mini-memory-progress'];
    const labels = () => progress.children.map(slot => slot.textContent);
    const empty = ['○ ○', '○ ○', '○ ○'];
    assert.equal(progress.hidden, true);
    game.memory.click();
    assert.equal(progress.hidden, false);
    assert.deepEqual(labels(), empty);

    const [first] = cards();
    first.click();
    first.click();
    assert.deepEqual(labels(), empty, 'one card cannot count as a pair');
    cards().find(card => card.dataset.pair !== first.dataset.pair).click();
    advance(750);
    assert.deepEqual(labels(), empty, 'two different cards do not count');

    const matched = [];
    for (let index = 0; index < empty.length; index++) {
      const a = cards().find(card => !card.disabled);
      const b = cards().find(card => card !== a && card.dataset.pair === a.dataset.pair);
      a.click();
      b.click();
      matched.push(`${a.textContent} ${b.textContent}`);
      assert.deepEqual(labels(), [...matched, ...empty.slice(matched.length)]);
      assert.match(parts['mini-feedback'].textContent, new RegExp(`${matched.length} ${language === 'de' ? 'von' : 'of'} 3`));
    }
    assert.equal(parts['mini-result'].hidden, false);
    parts['play-again'].click();
    assert.equal(parts['mini-result'].hidden, true);
    assert.equal(parts['mini-play'].hidden, false);
    assert.deepEqual(labels(), empty);
    assert.ok(cards().every(card => !card.disabled && card.textContent === '❔'));

    const oldBoard = cards();
    parts['mini-back'].click();
    game.memory.click();
    oldBoard[0].click();
    oldBoard.find(card => card !== oldBoard[0] && card.dataset.pair === oldBoard[0].dataset.pair).click();
    assert.deepEqual(labels(), empty, 'buttons from an earlier board cannot fill the new row');
    parts['mini-back'].click();
    game.odd.click();
    assert.equal(progress.hidden, true);
    assert.equal(parts['mini-play'].hidden, false);
  }
});
