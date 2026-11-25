const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_matching.js'), 'utf8');
const cow = {emoji: '🐮', de: 'Kuh', en: 'cow', sound_de: 'Muh', sound_en: 'moo', prompt_audio: 'ask_kuh', word_audio: 'w_kuh'};
const duck = {emoji: '🦆', de: 'Ente', en: 'duck', sound_de: 'Quak', sound_en: 'quack', prompt_audio: 'ask_ente', word_audio: 'w_ente'};
const config = {
  categories: {
    sounds: [cow, duck],
    shapes: [{emoji: '⭐', de: 'Stern', en: 'star'}, {emoji: '🔺', de: 'Dreieck', en: 'triangle'}],
  },
  levels: {easy: {choices: 2, rounds: 4}},
};

function fixture(language) {
  const element = () => {
    const attributes = new Map();
    const listeners = new Map();
    return {
      hidden: false, disabled: false, dataset: {}, style: {}, textContent: '', children: [],
      classList: {add() {}},
      focus() {},
      setAttribute: (name, value) => attributes.set(name, value),
      getAttribute: name => attributes.get(name),
      addEventListener: (name, listener) => listeners.set(name, listener),
      click() { if (!this.disabled) listeners.get('click')(); },
      append(child) { this.children.push(child); },
      replaceChildren() { this.children = []; },
      querySelectorAll() { return this.children; },
    };
  };
  const names = 'setup play result stage target title prompt feedback progress next repeat picture-hint speech-status back result-back play-again'.split(' ');
  const hooks = Object.fromEntries(names.map(name => [`[data-${name}]`, element()]));
  const ui = name => hooks[`[data-${name}]`];
  ui('play').hidden = true;
  ui('picture-hint').hidden = true;
  hooks['#kids-matching-config'] = {textContent: JSON.stringify(config)};
  const modeButtons = ['sounds', 'shapes', 'mixed'].map(category => {
    const button = element();
    button.dataset.category = category;
    return button;
  });
  const root = {
    dataset: {language},
    querySelector: selector => hooks[selector],
    querySelectorAll: selector => selector === '[data-category]' ? modeButtons : [],
  };
  const audioCalls = [];
  vm.runInNewContext(source, {
    document: {querySelector: () => root, createElement: element},
    Math: {random: () => 0, floor: Math.floor},
    window: {KidsShared: {
      shuffle: items => [...items],
      stopAudio() {},
      uiLanguage: english => english ? 'en-US' : 'de-DE',
      playClips(base, version, lang, parts) {
        audioCalls.push(...parts.map(part => part.key));
        return Promise.resolve(false); // no playable recording or voice
      },
    }},
  });
  const select = name => modeButtons.find(button => button.dataset.category === name).click();
  return {ui, select, audioCalls};
}

test('the picture cue does not submit a response or change audio and resets for the next sound', () => {
  const {ui, select, audioCalls} = fixture('de');
  const hint = ui('picture-hint');
  const target = ui('target');
  select('sounds');
  assert.equal(target.textContent, '🔊❓');
  assert.equal(hint.hidden, false);
  assert.deepEqual(audioCalls, ['ask_kuh']);

  hint.click();
  assert.equal(hint.getAttribute('aria-pressed'), 'true');
  assert.equal(target.textContent, '🐮');
  assert.equal(target.getAttribute('aria-label'), 'Kuh');
  assert.match(ui('prompt').textContent, /Tippbild: Kuh/);
  assert.equal(ui('next').hidden, true);
  assert.ok(ui('stage').children.every(button => !button.disabled));
  assert.deepEqual(audioCalls, ['ask_kuh']);
  ui('repeat').click();
  assert.deepEqual(audioCalls, ['ask_kuh', 'ask_kuh']);

  hint.click();
  assert.equal(hint.getAttribute('aria-pressed'), 'false');
  assert.equal(target.textContent, '🔊❓');
  hint.click();
  ui('stage').children.find(button => button.textContent === target.textContent).click();
  assert.equal(ui('next').hidden, false);
  ui('next').click();
  assert.equal(target.textContent, '🔊❓');
  assert.equal(hint.getAttribute('aria-pressed'), 'false');
  assert.equal(audioCalls.at(-1), 'ask_ente');
});

test('only sound tasks offer the clue, even when the mixed mode switches task type', () => {
  const {ui, select} = fixture('en');
  const hint = ui('picture-hint');
  select('mixed');
  for (const item of [cow, duck]) {
    assert.equal(hint.hidden, false);
    hint.click();
    assert.match(ui('prompt').textContent, new RegExp(`Picture clue: ${item.en}`));
    ui('stage').children.find(button => button.textContent === item.emoji).click();
    ui('next').click();
  }
  assert.equal(hint.hidden, true);
  assert.equal(hint.getAttribute('aria-pressed'), 'false');
  assert.equal(ui('target').textContent, '⭐');
  hint.click(); // Even a programmatic activation must not affect a non-sound task.
  assert.equal(ui('prompt').textContent, 'Tap the matching picture.');
});
