const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_syllables.js'), 'utf8');
const shared = fs.readFileSync(path.join(__dirname, '../static/js/kids_shared.js'), 'utf8');

class Element {
  constructor() {
    this.children = [];
    this.attributes = new Map();
    this.classes = new Set();
    this.handlers = new Map();
    this.dataset = {};
    this.hidden = false;
    this.disabled = false;
    this.textContent = '';
    this.classList = {toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name)};
  }
  setAttribute(key, value) { this.attributes.set(key, value); }
  getAttribute(key) { return this.attributes.get(key); }
  addEventListener(key, handler) { this.handlers.set(key, handler); }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children.length = 0; }
  contains(node) { return node === this || this.children.some(child => child.contains(node)); }
  click() { if (!this.disabled) this.handlers.get('click')?.(); }
  focus() { this.focused = true; }
}

function page({withAudio = true, language = 'de', realPlayer = false} = {}) {
  const names = ['level-picker', 'syll-play', 'syll-result', 'syll-stage', 'syll-title', 'syll-prompt',
    'syll-feedback', 'syll-progress', 'syll-next', 'syll-repeat', 'syll-back', 'result-back', 'play-again',
    'result-title', 'result-copy'];
  const elements = Object.fromEntries(names.map(name => [name, new Element()]));
  const root = new Element();
  root.dataset = {language, audioBase: '/static/audio/kids/games/syllables', audioVersion: 'fixture'};
  root.querySelector = key => elements[key.slice(6, -1)];
  const levels = ['clap', 'count', 'rhyme'].map(syllLevel => Object.assign(new Element(), {dataset: {syllLevel}}));
  root.querySelectorAll = () => levels;
  elements['level-picker'].querySelector = () => new Element();
  const players = [];
  const ordinaryAudio = [];
  const events = [];
  const fallbacks = [];
  class FakeAudio {
    constructor(url) {
      this.url = url;
      this.handlers = new Map();
      players.push(this);
    }
    set src(url) { this.url = url; }
    addEventListener(type, callback) { this.handlers.set(type, callback); }
    removeEventListener(type, callback) { if (this.handlers.get(type) === callback) this.handlers.delete(type); }
    play() {
      events.push('play');
      if (realPlayer) return new Promise((_, reject) => { this.failLater = reject; });
      return Promise.resolve();
    }
    pause() { this.paused = true; }
    emit(type) { this.handlers.get(type)?.(); }
  }
  const timers = new Map();
  let clock = 0;
  let timerId = 0;
  const window = {
    Audio: withAudio ? FakeAudio : undefined,
    KidsPiperVoice: {stop() {}, speak: value => { fallbacks.push(value); return Promise.resolve(true); }},
    KidsShared: {
      stopAudio: () => { events.push('stop'); },
      uiLanguage: en => en ? 'en-US' : 'de-DE',
      playClips: (_base, _version, _lang, parts) => ordinaryAudio.push(Array.from(parts, part => part.key)),
    },
    setTimeout: (fn, delay) => { timers.set(++timerId, {fn, time: clock + delay}); return timerId; },
    clearTimeout: id => timers.delete(id),
  };
  const math = Object.create(Math);
  math.random = () => .95;
  const context = {
    window, Math: math, Date: {now: () => clock},
    document: {querySelector: () => root, createElement: () => new Element()},
  };
  if (realPlayer) vm.runInNewContext(shared, context);
  vm.runInNewContext(source, context);
  const words = window.KidsSyllables.WORDS[language];
  const selected = language === 'de' ? ['Apfel', 'Banane', 'Schokolade'] : ['apple', 'banana', 'helicopter'];
  words.splice(0, words.length, ...selected.map(word => words.find(item => item.word === word)));
  levels[0].click();
  const stage = elements['syll-stage'];
  const example = () => stage.children.find(child => child.className?.includes('kids-syll-example'));
  const clap = () => stage.children.find(child => child.className === 'kids-syll-clap');
  const markers = () => stage.children.find(child => child.className === 'kids-syll-dots').children;
  return {
    elements, levels, players, events, timers, fallbacks, ordinaryAudio, example, clap, markers,
    beat: () => markers().findIndex(marker => marker.classes.has('is-example-beat')),
    advance() {
      const [id, timer] = [...timers].sort((a, b) => a[1].time - b[1].time)[0];
      timers.delete(id);
      clock = timer.time;
      timer.fn();
    },
  };
}

test('the optional demonstration marks and plays one local recording per syllable without answering', () => {
  const p = page();
  assert.equal(p.beat(), -1);
  assert.equal(p.markers().length, 2);
  p.example().click();
  assert.equal(p.beat(), 0);
  assert.equal(p.example().getAttribute('aria-pressed'), 'true');
  assert.equal(p.players[0].url, '/static/audio/kids/games/syllables/de/p_ap.mp3?v=fixture');
  p.players[0].emit('ended');
  p.advance();
  assert.equal(p.beat(), 1);
  assert.equal(p.players.length, 1, 'Safari grants playback per media element');
  assert.equal(p.players[0].url, '/static/audio/kids/games/syllables/de/p_fel.mp3?v=fixture');
  p.players[0].emit('ended');
  p.advance();
  assert.equal(p.beat(), -1);
  assert.equal(p.example().getAttribute('aria-pressed'), 'false');
  assert.deepEqual(p.markers().map(marker => marker.textContent), ['○', '○']);
  assert.equal(p.elements['syll-next'].hidden, true);
  assert.equal(p.ordinaryAudio.length, 1, 'the demonstration never sends a synthesis fallback');
  p.clap().click();
  p.clap().click();
  assert.equal(p.elements['syll-next'].hidden, false);
  assert.equal(p.ordinaryAudio.at(-1)[0], 'praise_right');
});

test('manual tap, toggle, new question and leaving stop an example without losing a counted tap', () => {
  const p = page();
  p.clap().click();
  p.example().click();
  assert.equal(p.beat(), 0);
  assert.deepEqual(p.markers().map(marker => marker.textContent), ['●', '○']);
  p.example().click();
  p.players[0].emit('error');
  assert.equal(p.timers.size, 0);
  assert.equal(p.players[0].paused, true);
  assert.equal(p.markers()[0].textContent, '●');

  p.example().click();
  p.clap().click();
  assert.equal(p.elements['syll-next'].hidden, false);
  assert.equal(p.beat(), -1);
  assert.equal(p.players[1].paused, true);
  p.example().click();
  p.elements['syll-next'].click();
  p.players[2].emit('ended');
  assert.equal(p.timers.size, 0);
  assert.equal(p.markers().length, 3);
  assert.equal(p.elements['syll-title'].focused, true);
  assert.ok(p.markers().every(marker => marker.textContent === '○'));

  p.example().click();
  p.elements['syll-back'].click();
  assert.equal(p.timers.size, 0);
  assert.equal(p.players[3].paused, true);
  assert.equal(p.elements['level-picker'].hidden, false);
  p.levels[1].click();
  assert.equal(p.example(), undefined);
});

test('audio unavailable or slow keeps the visual example usable, and Hear again cancels it', () => {
  const silent = page({withAudio: false, language: 'en'});
  silent.example().click();
  assert.equal(silent.beat(), 0);
  silent.advance();
  assert.equal(silent.beat(), 1);
  silent.advance();
  assert.equal(silent.beat(), -1);
  assert.equal(silent.players.length, 0);
  assert.equal(silent.elements['syll-next'].hidden, true);

  const slow = page();
  slow.example().click();
  slow.advance(); // Limit a stalled media player to two seconds.
  assert.equal(slow.players[0].paused, true);
  slow.advance();
  assert.equal(slow.beat(), 1);
  slow.elements['syll-repeat'].click();
  slow.players[0].emit('error');
  assert.equal(slow.timers.size, 0);
  assert.equal(slow.beat(), -1);
  assert.deepEqual(slow.ordinaryAudio.at(-1), ['listen_well', 'p_ap', 'p_fel', 'tap_each_syllable']);
});

test('starting the example prevents a late failed original clip from synthesizing speech', async () => {
  const p = page({realPlayer: true});
  await new Promise(resolve => setImmediate(resolve));
  const original = p.players[0];
  assert.match(original.url, /listen_well\.mp3/);
  p.example().click();
  assert.match(p.players[1].url, /p_ap\.mp3/);
  original.failLater(new Error('Old clip failed after pause'));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(p.fallbacks, []);
  assert.equal(p.beat(), 0);
});

test('an ordinary prompt still uses its configured local fallback when it remains current', async () => {
  const p = page({realPlayer: true});
  await new Promise(resolve => setImmediate(resolve));
  p.players[0].failLater(new Error('Recording unavailable'));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(p.fallbacks, ['Höre gut zu:']);
});
