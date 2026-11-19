const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_shared.js'), 'utf8');

function fixture() {
  const events = [];
  const players = [];
  class FakeAudio {
    constructor(url) {
      this.url = url; this.listeners = {}; players.push(this);
      events.push(`create:${url}`);
    }
    addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
    play() { events.push(`play:${this.url}`); return Promise.resolve(); }
    pause() {
      events.push(`pause:${this.url}`);
      (this.listeners.pause || []).forEach(fn => fn());
    }
    end() { events.push(`end:${this.url}`); (this.listeners.ended || []).forEach(fn => fn()); }
    fail() { events.push(`error:${this.url}`); (this.listeners.error || []).forEach(fn => fn()); }
  }
  const speechSynthesis = {
    cancel() { events.push('tts-cancel'); },
    getVoices() { return []; },
    speak(u) { events.push(`tts:${u.text}`); setImmediate(() => u.onend && u.onend()); },
  };
  function SpeechSynthesisUtterance(t) { this.text = t; }
  const window = {Audio: FakeAudio, speechSynthesis, SpeechSynthesisUtterance, localStorage: {getItem: () => ''}};
  vm.runInNewContext(source, {window, SpeechSynthesisUtterance});
  return {api: window.KidsShared, events, players};
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

test('missing clip falls back to speech and continues the sequence', async () => {
  const f = fixture();
  const done = f.api.playClips('/base', '', 'en', [
    {key: 'gone', fallback: 'Fallback words'},
    {key: 'there', fallback: 'Next'},
  ], 'en-US', null);
  await tick();
  f.players[0].fail();
  await tick(); await tick(); await tick();
  assert.ok(f.events.includes('tts:Fallback words'));
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

test('shuffle still spreads items', () => {
  const f = fixture();
  const seen = new Set();
  for (let i = 0; i < 20; i += 1) seen.add(f.api.shuffle([1, 2, 3, 4]).join(','));
  assert.ok(seen.size > 1);
});
