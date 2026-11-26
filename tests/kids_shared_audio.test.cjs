const {test} = require('node:test');
const assert = require('node:assert/strict');
const {load, soundFixture, tick} = require('./kids_audio_fixture.cjs');

function fixture(options) {
  const f = soundFixture(options);
  load(f.context, 'kids_shared.js');
  return {...f, api:f.window.KidsShared};
}
const clip = key => ({key, fallback: 'Text ' + key});

test('recorded clips play in order, awaiting a local POST fallback before loading the next clip', async () => {
  const f = fixture();
  const done = f.api.playClips('/base', 'v1', 'de', [clip('one'), clip('two')], 'de-DE');
  assert.equal(f.players[0].url, '/base/de/one.mp3?v=v1');
  f.players[0].fail();
  await tick();
  assert.deepEqual(f.players.map(x => x.url), ['/base/de/one.mp3?v=v1','blob:fixture-1']);
  assert.deepEqual(f.requests[0].payload, {text:'Text one',lang:'de-DE',purpose:''});
  f.players[1].end();
  await tick();
  assert.equal(f.players[2].url, '/base/de/two.mp3?v=v1');
  f.players[2].end();
  assert.equal(await done, true);
});

test('stop resolves immediately with no tail; late error and rejected play cannot synthesize', async () => {
  const f = fixture({delayedPlay:true});
  const p = f.api.playClips('/base', '', 'de', [clip('one'), clip('two')], 'de-DE');
  f.api.stopAudio();
  assert.equal(await p, false);
  f.players[0].fail();
  f.players[0].rejectPlay(new Error('Late failure'));
  await tick();
  assert.equal(f.players.length, 1);
  assert.equal(f.requests.length, 0);
});

test('reject foreign recorded URLs or audio returned as HTML; retain visible failure state', async () => {
  const f = fixture({transport: async () => ({ok:true,headers:{get:() => 'text/html'}})});
  const status = {textContent:''};
  assert.equal(await f.api.playClips('https://elsewhere.invalid', '', 'de', [clip('x')], 'de-DE', status), false);
  assert.equal(f.requests.length, 0);
  assert.equal(f.players.length, 0);
  assert.equal(await f.api.speak('Lokaler Text','de-DE',status), false);
  assert.equal(f.players.length, 0);
  assert.match(status.textContent, /Lies die Aufgabe/);
});

test('shuffle still spreads items', () => {
  const f = fixture();
  const seen = new Set(Array.from({length: 20}, () => f.api.shuffle([1, 2, 3, 4]).join(',')));
  assert.ok(seen.size > 1);
});
