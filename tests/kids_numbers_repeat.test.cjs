const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_numbers.js'), 'utf8');
const {load, soundFixture} = require('./kids_audio_fixture.cjs');

class Element {
  constructor(tag = '') {
    this.tag = tag;
    this.children = [];
    this.attrs = new Map();
    this.handlers = new Map();
    this.className = '';
    this.classList = {add: name => { this.className += ` ${name}`; }};
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
  }
  setAttribute(name, value) { this.attrs.set(name, value); }
  getAttribute(name) { return this.attrs.get(name) ?? null; }
  addEventListener(name, callback) { this.handlers.set(name, callback); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren() { this.children.length = 0; }
  querySelectorAll(tag) { return this.children.filter(node => node.tag === tag); }
  click() { if (!this.disabled) this.handlers.get('click')?.(); }
  focus() {}
}

function round(level, audioAvailable = true) {
  const names = ['speech-status', 'setup', 'play', 'result', 'stage', 'title', 'prompt', 'feedback',
    'progress', 'stars', 'next', 'repeat', 'back', 'play-again', 'result-back', 'result-title', 'result-copy'];
  const ui = Object.fromEntries(names.map(name => [name, new Element()]));
  const start = new Element('button');
  start.dataset = {level};
  ui.setup.querySelector = () => start;
  const root = {dataset: {language: 'de', audioBase: '/static/audio/kids/games/numbers', audioVersion: 'test'}};
  const item = {emoji: '🐶', de: 'Hund', de_plural: 'Hunde', en: 'dog', en_plural: 'dogs', audio: 'n_hund', audio_plural: 'n_hunde'};
  const config = {items: [item], levels: {easy: {max: 3, choices: 2, tasks: 4, compare: false},
    medium: {max: 5, choices: 3, tasks: 4, compare: true}}};
  root.querySelector = key => key === '#kids-numbers-config' ? {textContent: JSON.stringify(config)} : ui[key.slice(6, -1)];
  root.querySelectorAll = () => [start];
  const document = {
    querySelector: selector => selector.includes('csrf-token') ? {content: 'fixture-csrf'} : root,
    createElement: tag => new Element(tag),
  };
  const fixture = soundFixture({document, autoEnd: audioAvailable, transport: audioAvailable ? undefined : async () => ({ok:false})});
  if (!audioAvailable) {
    const Base = fixture.window.Audio;
    fixture.window.Audio = class extends Base {
      play() { queueMicrotask(() => this.fail()); return Promise.resolve(); }
    };
  }
  let draws = 0;
  const math = Object.create(Math);
  math.random = () => ++draws % 2 ? .9 : .1;
  fixture.context.Math = math;
  load(fixture.context, 'kids_shared.js');
  fixture.window.KidsShared.shuffle = values => [...values];
  vm.runInContext(source, fixture.context);
  start.click();
  return {ui,
    get clips() { return fixture.events.filter(event => event.startsWith('play:/')).map(event => event.split('/').at(-1).split('?')[0]); },
    get fallback() { return fixture.requests.map(request => request.payload.text); },
  };
}

const tick = () => new Promise(resolve => setImmediate(resolve));
const options = game => game.ui.stage.querySelectorAll('button').filter(button => button.className.startsWith('kids-numbers-answer'));
function correctCount(game) {
  const picture = game.ui.stage.children.find(node => node.getAttribute('role') === 'img');
  const aid = game.ui.stage.children.find(node => node.className === 'kids-numbers-counting');
  return Number(picture?.getAttribute('aria-label').match(/^\d+/)?.[0] ||
    aid?.children.find(node => node.className === 'kids-numbers-picture').children.length);
}

test('Nochmal hören after a wrong answer replays the full current question; immediate feedback stays', async () => {
  const game = round('easy');
  await tick();
  const question = [...game.clips];
  assert.equal(question[0], 'how_many.mp3');
  assert.equal(question.at(-1), 'see_you.mp3');
  const correct = options(game).find(button => button.textContent === String(correctCount(game)));
  options(game).find(button => button !== correct).click();
  await tick();
  assert.match(game.ui.feedback.textContent, /Versuch es nochmal/);
  assert.equal(game.clips.at(-1), 'try_again.mp3');
  assert.equal(game.ui.next.hidden, true);
  const before = game.clips.length;
  game.ui.repeat.click();
  await tick();
  assert.deepEqual(game.clips.slice(before), question);
  assert.match(game.ui.feedback.textContent, /Versuch es nochmal/);
  correct.click();
  await tick();
  assert.match(game.clips.at(-1), /^praise_/);
  assert.equal(game.ui.next.hidden, false);
  game.ui.next.click();
  await tick();
  assert.match(game.ui.prompt.textContent, /^Tippe auf/);
  const second = game.clips.slice(-3);
  const beforeRepeat = game.clips.length;
  game.ui.repeat.click();
  await tick();
  assert.deepEqual(game.clips.slice(beforeRepeat), second);
  assert.equal(second[0], 'tap_on.mp3');
  game.ui.back.click();
  const afterLeaving = game.clips.length;
  game.ui.repeat.click();
  await tick();
  assert.equal(game.clips.length, afterLeaving, 'a hidden control cannot replay after leaving');
});

test('silent playback leaves the visible question and retry controls intact in every task type', async () => {
  const game = round('medium', false);
  await tick();
  for (let step = 1; step <= 4; step++) {
    const prompt = game.ui.prompt.textContent;
    const buttons = options(game);
    assert.ok(!game.ui.stage.children.some(node => node.className === 'kids-numbers-quantity-cue'));
    assert.ok(buttons.every(button => !button.children.some(node => node.className === 'kids-numbers-dots')));
    const quantities = buttons.map(x => Number((x.getAttribute('aria-label') || x.textContent).match(/^\d+/)[0]));
    const expected = prompt.startsWith('Wie viele') ? correctCount(game) : prompt.startsWith('Tippe auf')
      ? Number(prompt.match(/\d+/)[0]) : prompt.includes('mehr') ? Math.max(...quantities) : Math.min(...quantities);
    const wrong = buttons[quantities.findIndex(n => n !== expected)];
    assert.ok(wrong, prompt);
    wrong.click();
    await tick();
    const before = game.fallback.length;
    game.ui.repeat.click();
    await tick();
    assert.equal(game.ui.prompt.textContent, prompt);
    assert.equal(game.ui.next.hidden, true);
    assert.match(game.ui.feedback.textContent, /Versuch es nochmal/);
    assert.ok(game.fallback.length > before);
    assert.notEqual(game.fallback[before], game.ui.feedback.textContent);
    buttons[quantities.indexOf(expected)].click();
    assert.equal(game.ui.next.hidden, false);
    game.ui.next.click();
  }
  assert.equal(game.clips.length, 0);
  assert.equal(game.ui.result.hidden, false);
});
