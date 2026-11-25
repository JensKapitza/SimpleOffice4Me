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
    };
  };
  const names = 'game-picker mini-play mini-result mini-stage mini-title mini-prompt mini-feedback mini-progress mini-next mini-back mini-repeat result-title result-copy result-back play-again'.split(' ');
  const parts = Object.fromEntries(names.map(name => [name, element()]));
  const game = Object.fromEntries(['memory', 'count', 'odd'].map(name => {
    const button = element();
    button.dataset.miniGame = name;
    return [name, button];
  }));
  parts['game-picker'].children = Object.values(game);
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
