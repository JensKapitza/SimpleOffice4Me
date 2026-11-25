const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_language_audio.js'), 'utf8');
const piperSource = fs.readFileSync(path.join(__dirname, '../static/js/kids_piper_voice.js'), 'utf8');

function fixture() {
  const events = [];
  const players = [];
  const piperSpoken = [];
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
  vm.runInNewContext(source, {window, Audio: FakeAudio});
  const audio = window.KidsLanguageAudio({
    text: (de) => de, speechStatus: {textContent: ''},
  });
  return {audio, events, players, piperSpoken};
}

const tick = () => new Promise(resolve => setImmediate(resolve));

test('solution plays its clips in order, exactly once', async () => {
  const f = fixture();
  const done = f.audio.playSolution({pronunciationAudioUrl: 'a.mp3', solutionAudioUrl: 'b.mp3', speak: 'A', a: 'B'});
  await tick();
  assert.deepEqual(f.events, ['piper-stop', 'piper-stop', 'create:a.mp3', 'play:a.mp3']);
  f.players[0].end();
  await tick(); await tick();
  assert.ok(f.events.includes('play:b.mp3'));
  assert.equal(f.events.filter(e => e.startsWith('play:')).length, 2);
  f.players[1].end();
  assert.equal(await done, true);
});

test('a second solution abandons the first chain (no overlap)', async () => {
  const f = fixture();
  const first = f.audio.playSolution({pronunciationAudioUrl: 'a.mp3', solutionAudioUrl: 'b.mp3', speak: 'A', a: 'B'});
  await tick();
  const second = f.audio.playSolution({pronunciationAudioUrl: 'c.mp3', solutionAudioUrl: 'd.mp3', speak: 'C', a: 'D'});
  await tick(); await tick();
  f.players[0].end(); // stale 'ended' from the abandoned chain
  await tick(); await tick();
  assert.ok(!f.events.includes('play:b.mp3'), `abandoned chain continued: ${f.events}`);
  assert.ok(f.events.includes('play:c.mp3'));
  assert.equal(await first, false);
  f.players[1].end();
  await tick(); await tick();
  assert.ok(f.events.includes('play:d.mp3'));
  f.players[2].end();
  assert.equal(await second, true);
});

test('solution interrupts a running question without overlap', async () => {
  const f = fixture();
  f.audio.readQuestion({questionAudioUrl: 'q.mp3', pronunciationAudioUrl: 'w.mp3', speak: 'W'}, 'prompt');
  await tick();
  const done = f.audio.playSolution({pronunciationAudioUrl: 's.mp3', solutionAudioUrl: 's.mp3', speak: 'S', a: 'S'});
  await tick(); await tick();
  f.players[0].end(); // stale question clip
  await tick(); await tick();
  assert.ok(!f.events.includes('play:w.mp3'), `question chain continued: ${f.events}`);
  assert.ok(f.events.includes('play:s.mp3'));
  assert.equal(f.events.filter(e => e === 'play:s.mp3').length, 1);
  f.players[1].end();
  assert.equal(await done, true);
});

test('clip error falls back to piper speech exactly once', async () => {
  const f = fixture();
  const done = f.audio.playSolution({pronunciationAudioUrl: 'x.mp3', solutionAudioUrl: 'x.mp3', speak: 'Wort', a: 'Wort'});
  await tick();
  f.players[0].fail();
  await tick(); await tick(); await tick();
  assert.deepEqual(f.piperSpoken, [['Wort', 'en-US']]);
  assert.equal(await done, true);
});

test('identical solution urls play only once', async () => {
  const f = fixture();
  const done = f.audio.playSolution({pronunciationAudioUrl: 'same.mp3', solutionAudioUrl: 'same.mp3', speak: 'W', a: 'W'});
  await tick();
  f.players[0].end();
  assert.equal(await done, true);
  assert.equal(f.players.length, 1);
});

test('spoken question without clip uses piper directly', async () => {
  const f = fixture();
  const done = await f.audio.speak('Gehe nach oben.', 'de-DE');
  assert.equal(done, true);
  assert.deepEqual(f.piperSpoken, [['Gehe nach oben.', 'de-DE']]);
});

test('only the explicit maze instruction path marks the real local Piper URL', async () => {
  const urls = [];
  class FakeAudio {
    constructor(url) { urls.push(new URL(url, 'http://localhost')); }
    addEventListener(type, callback) { if (type === 'ended') this.finish = callback; }
    play() { this.finish(); return Promise.resolve(); }
  }
  const window = {Audio: FakeAudio};
  const context = {window, Audio: FakeAudio, URLSearchParams, document: {querySelector: () => null}};
  vm.runInNewContext(piperSource, context);
  vm.runInNewContext(source, context);
  const audio = window.KidsLanguageAudio({text: de => de, speechStatus: null});
  assert.equal(await audio.speak('Gehe nach links.', 'de-DE', 'instructions'), true);
  assert.equal(await audio.playSolution({solutionWord: 'Apfel', speechLang: 'de-DE'}), true);
  assert.equal(urls[0].origin, 'http://localhost');
  assert.equal(urls[0].pathname, '/kids/lernen/sprache');
  assert.equal(urls[0].searchParams.get('purpose'), 'instructions');
  assert.equal(urls[0].searchParams.get('text'), 'Gehe nach links.');
  assert.equal(urls[1].searchParams.get('purpose'), null);
  assert.equal(urls[1].searchParams.get('text'), 'Apfel');
});
