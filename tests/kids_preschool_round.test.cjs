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
    this.attrs = new Map();
    this.handlers = new Map();
    this.dataset = {};
    this.className = '';
    this.classList = {add: value => { this.className += ` ${value}`; }};
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
  }
  setAttribute(key, value) { this.attrs.set(key, value); }
  getAttribute(key) { return this.attrs.get(key); }
  addEventListener(key, handler) { this.handlers.set(key, handler); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren() { this.children.length = 0; }
  querySelectorAll(tag) { return this.children.filter(node => node.tag === tag); }
  click() { if (!this.disabled) this.handlers.get('click')?.(); }
  focus() {}
}

function run(preset) {
  const names = ['speech-status', 'setup', 'play', 'result', 'stage', 'title', 'prompt',
    'feedback', 'progress', 'stars', 'next', 'repeat', 'back', 'play-again', 'result-title', 'result-copy'];
  if (!preset) names.push('result-back');
  const root = new Element();
  const ui = Object.fromEntries(names.map(name => [name, new Element()]));
  const start = new Element('button');
  start.dataset.level = 'easy';
  root.dataset = {language: 'en', ...(preset ? {preschoolRound: 'true'} : {})};
  root.querySelector = selector => ui[selector.slice(6, -1)] ?? null;
  root.querySelectorAll = () => [start];
  const math = Object.create(Math);
  let randomCalls = 0;
  math.random = () => ++randomCalls % 2 ? .9 : .1;
  const window = {KidsShared: {
    shuffle: items => [...items], stopAudio() {}, playClips() {}, uiLanguage: () => 'en-US',
  }};
  vm.runInNewContext(source, {
    window, Math: math, document: {querySelector: () => root, createElement: tag => new Element(tag)},
  });
  return {start, ui};
}

function playThrough({start, ui}, total) {
  start.click();
  for (let step = 1; step <= total; step++) {
    assert.equal(ui.progress.textContent, `Task ${step} of ${total}`);
    assert.equal(ui.next.hidden, true);
    const picture = ui.stage.children.find(node => node.getAttribute('role') === 'img');
    const tapAid = ui.stage.children.find(node => node.className === 'kids-numbers-counting');
    const figures = tapAid?.children.find(node => node.className === 'kids-numbers-picture');
    const amount = figures?.children.length ??
      Number((picture?.getAttribute('aria-label') || ui.prompt.textContent).match(/\d+/)[0]);
    const answers = ui.stage.querySelectorAll('button');
    const right = answers.find(button => (button.getAttribute('aria-label') || button.textContent).startsWith(String(amount)));
    right.click();
    assert.equal(ui.next.hidden, false);
    ui.next.click();
    assert.equal(ui.result.hidden, step < total);
  }
  assert.match(ui['result-copy'].textContent, new RegExp(`All ${total} tasks`));
}

test('preschool shortcut completes in four tasks and Count again keeps its preset', () => {
  const game = run(true);
  playThrough(game, 4);
  game.ui['play-again'].click();
  assert.equal(game.ui.progress.textContent, 'Task 1 of 4');
  assert.equal(game.ui.result.hidden, true);
});

test('direct Easy game remains eight tasks and preserves its usual result', () => {
  playThrough(run(false), 8);
});
