const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_shared.js'), 'utf8');

function fixture({delayedPlay = false} = {}) {
  const events = [];
  const players = [];
  const piperSpoken = [];
  class FakeAudio {
    constructor(url) {
      this.url = url; this.listeners = {}; players.push(this);
      events.push(`create:${url}`);
    }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    play() {
      events.push(`play:${this.url}`);
      if (delayedPlay) return new Promise((_, reject) => { this.rejectPlay = reject; });
      return Promise.resolve();
    }
    pause() {
      events.push(`pause:${this.url}`);
      (this.listeners.pause || []).forEach(fn => fn());
    }
    end() { events.push(`end:${this.url}`); (this.listeners.ended || []).forEach(fn => fn()); }
    fail() { events.push(`error:${this.url}`); (this.listeners.error || []).forEach(fn => fn()); }
  }
  // Server Piper voice stub: records prompts instead of browser speech.
  const KidsPiperVoice = {
    stop() { events.push('piper-stop'); },
    speak(text, language) {
      piperSpoken.push([String(text), language]);
      events.push(`piper:${text}`);
      return Promise.resolve(true);
    },
  };
  const window = {Audio: FakeAudio, KidsPiperVoice};
  vm.runInNewContext(source, {window});
  return {api: window.KidsShared, events, players, piperSpoken};
}

const tick = () => new Promise(resolve => setImmediate(resolve));

test('clips play in order with versioned same-origin urls', async () => {
  const f = fixture();
  const done = f.api.playClips('/static/audio/kids/games/matching', 'v1', 'de', [
    {key: 'praise_right', fallback: 'Richtig!'},
    {key: 'w_rot', fallback: 'Rot'},
  ], 'de-DE', null);
  await tick();
  assert.ok(f.events.includes('play:/static/audio/kids/games/matching/de/praise_right.mp3?v=v1'));
  f.players[0].end();
  await tick(); await tick();
  assert.ok(f.events.includes('play:/static/audio/kids/games/matching/de/w_rot.mp3?v=v1'));
  f.players[1].end();
  assert.equal(await done, true);
});

test('missing clip falls back to piper speech and continues the sequence', async () => {
  const f = fixture();
  const done = f.api.playClips('/base', '', 'en', [
    {key: 'gone', fallback: 'Fallback words'},
    {key: 'there', fallback: 'Next'},
  ], 'en-US', null);
  await tick();
  f.players[0].fail();
  await tick(); await tick(); await tick();
  assert.ok(f.events.includes('piper:Fallback words'));
  assert.deepEqual(f.piperSpoken, [['Fallback words', 'en-US']]);
  assert.ok(f.events.includes('play:/base/en/there.mp3'));
  f.players[1].end();
  assert.equal(await done, true);
});

test('stopAudio cancels pending clips', async () => {
  const f = fixture();
  const done = f.api.playClips('/base', '', 'de', [
    {key: 'one', fallback: 'eins'},
    {key: 'two', fallback: 'zwei'},
  ], 'de-DE', null);
  await tick();
  f.api.stopAudio();
  f.players[0].end();
  await tick(); await tick();
  assert.ok(!f.events.includes('play:/base/de/two.mp3'));
  assert.equal(await done, false);
});

test('late media errors and play rejections cannot start stale speech after a new tap', async () => {
  const f = fixture({delayedPlay: true});
  const question = f.api.playClips('/base', '', 'de', [{key: 'question', fallback: 'alte Frage'}], 'de-DE');
  await tick();
  const first = f.api.playClips('/base', '', 'de', [{key: 'num_1', fallback: 'eins'}], 'de-DE');
  // The old error can arrive after a tap but before its replacement player exists.
  f.players[0].fail();
  await tick();
  const second = f.api.playClips('/base', '', 'de', [{key: 'num_2', fallback: 'zwei'}], 'de-DE');
  await tick();
  f.players[1].rejectPlay(new Error('Late playback failure'));
  await tick();
  assert.equal(await question, false);
  assert.equal(await first, false);
  assert.deepEqual(f.piperSpoken, []);
  f.players[2].end();
  assert.equal(await second, true);
});

test('shuffle still spreads items', () => {
  const f = fixture();
  const seen = new Set();
  for (let i = 0; i < 20; i += 1) seen.add(f.api.shuffle([1, 2, 3, 4]).join(','));
  assert.ok(seen.size > 1);
});
