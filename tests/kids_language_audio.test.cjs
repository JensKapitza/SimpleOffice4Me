const {test} = require('node:test');
const assert = require('node:assert/strict');
const {load, soundFixture, tick, origin, wav} = require('./kids_audio_fixture.cjs');

function fixture(options) {
  const f = soundFixture(options);
  const status = {textContent: ''};
  load(f.context, 'kids_language_audio.js');
  return {...f, status, audio: f.window.KidsLanguageAudio({speechStatus: status})};
}
const clip = (id, other = null) => ({
  pronunciationAudioUrl: '/' + id + '.mp3', solutionAudioUrl: '/' + (other || id) + '.mp3', speak: id, a: other || id,
});

test('solution plays its distinct clips in order; duplicate URLs play once', async () => {
  const f = fixture();
  const done = f.audio.playSolution(clip('a', 'b'));
  assert.equal(f.players[0].url, '/a.mp3');
  f.players[0].end();
  await tick();
  assert.equal(f.players[1].url, '/b.mp3');
  f.players[1].end();
  assert.equal(await done, true);
  const again = f.audio.playSolution(clip('a'));
  f.players[2].end();
  assert.equal(await again, true);
  assert.equal(f.players.length, 3);
});

test('solution interrupts and settles a question immediately, with no stale pronunciation', async () => {
  const f = fixture();
  const pending = f.audio.readQuestion({questionAudioUrl:'/q.mp3', pronunciationAudioUrl:'/old.mp3', speak:'old'}, 'Prompt');
  const solution = f.audio.playSolution(clip('answer'));
  assert.equal(await pending, false);
  f.players[0].end();
  assert.deepEqual(f.players.map(audio => audio.url), ['/q.mp3', '/answer.mp3']);
  f.players[1].end();
  assert.equal(await solution, true);
});

test('only the explicit maze path sets purpose; both use CSRF POST and transient blobs', async () => {
  const f = fixture({autoEnd:true});
  assert.equal(await f.audio.speak('Gehe nach links.', 'de-DE', 'instructions'), true);
  assert.equal(await f.audio.playSolution({solutionWord: 'Apfel', speechLang: 'de-DE'}), true);
  assert.deepEqual(f.requests.map(x => x.payload), [
    {text:'Gehe nach links.', lang:'de-DE', purpose:'instructions'}, {text:'Apfel', lang:'de-DE', purpose:''},
  ]);
  for (const {url, options} of f.requests) {
    assert.equal(url, origin + '/kids/lernen/sprache');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-CSRF-Token'], 'fixture-csrf');
    assert.equal(options.credentials, 'same-origin');
    assert.equal(options.redirect, 'error');
  }
  assert.deepEqual(f.revoked, ['blob:fixture-1', 'blob:fixture-2']);
});

test('stale failed fetch cannot play or clear warnings after repeat; hide and pagehide settle', async () => {
  const returns = [];
  const f = fixture({transport: (_url, _options) => new Promise(resolve => returns.push(resolve))});
  const listen = () => f.audio.speak('Gehe nach links.', 'de-DE', 'instructions');
  const missing = listen();
  returns[0]({ok:false});
  assert.equal(await missing, false);
  assert.match(f.status.textContent, /Lies die Aufgabe/);
  const older = listen(), latest = listen();
  assert.equal(await older, false);
  assert.equal(f.requests[1].options.signal.aborted, true);
  returns[1](wav());
  returns[2](wav());
  await tick();
  assert.equal(f.players.length, 1);
  assert.match(f.status.textContent, /Vorlesen/);
  f.players[0].emit('playing');
  assert.equal(f.status.textContent, '');
  f.document.hidden = true;
  f.hidden.visibilitychange();
  assert.equal(await latest, false);
  assert.deepEqual(f.revoked, ['blob:fixture-1']);
  assert.equal(await listen(), false, 'hidden document cannot start a download');
  f.document.hidden = false;
  const leaving = listen();
  f.pageEvents.pagehide();
  assert.equal(await leaving, false);
  returns[3](wav());
  await tick();
  assert.equal(f.players.length, 1);
});

test('missing full recording falls back to the complete language-tagged prompt before pronunciation', async () => {
  const f = fixture();
  const done = f.audio.readQuestion({questionAudioUrl:'/prompt.mp3', pronunciationAudioUrl:'/word.mp3', speak:'apple'},
    [{text:'Höre das Wort',language:'de-DE'}, {text:'apple',language:'en-US'}, {text:'und finde das Bild',language:'de-DE'}]);
  f.players[0].fail();
  for (let i = 1; i <= 3; ++i) { await tick(); f.players[i].end(); }
  await tick();
  assert.deepEqual(f.requests.map(r => [r.payload.text,r.payload.lang]), [
    ['Höre das Wort','de-DE'], ['apple','en-US'], ['und finde das Bild','de-DE'],
  ]);
  assert.equal(f.players[4].url, '/word.mp3');
  f.players[4].end();
  assert.equal(await done, true);
});

test('explicit stop and competing page speech cancel the real queue without late playback', async () => {
  let stopClick;
  const f = fixture({document:{
    querySelector: selector => selector.includes('csrf-token') ? {content:'fixture'} : null,
    querySelectorAll: () => [{addEventListener: (_type, listener) => { stopClick = listener; }}],
  }});
  const first = f.audio.speak('Erste Frage', 'de-DE');
  await tick();
  stopClick();
  assert.equal(await first, false);
  assert.ok(f.events.includes('pause:blob:fixture-1'));
  const latest = f.audio.speak('Zweite Frage', 'de-DE');
  await tick();
  f.window.dispatchEvent(new f.window.CustomEvent('simpleoffice:speech-start', {detail:{owner:{}}}));
  assert.equal(await latest, false);
  f.players[0].fail();
  assert.equal(f.status.textContent, '');
  assert.deepEqual(f.revoked, ['blob:fixture-1','blob:fixture-2']);
});

test('Mixed entry renders a whole question and preserves repeat when vocabulary and sound are unavailable', async () => {
  const nodes = new Map();
  const node = () => ({
    hidden:false, textContent:'', children:[], dataset:{}, focus(){}, addEventListener(){},
    append(child){ this.children.push(child); }, replaceChildren(){ this.children=[]; },
    setAttribute(){}, removeAttribute(){},
  });
  const root = {
    dataset:{language:'de'}, querySelector(name){ if (!nodes.has(name)) nodes.set(name,node()); return nodes.get(name); },
    querySelectorAll(){ return []; }, addEventListener(){},
  };
  const f = soundFixture({
    document:{
      querySelector: selector => selector.includes('csrf-token') ? {content:'fixture'} : root,
      getElementById: () => null, createElement: node,
    },
    transport: async () => ({ok:false}),
  });
  f.context.URLSearchParams = URLSearchParams;
  f.window.location = new URL(origin + '/kids/lernen/?game=quiz&subject=mixed');
  load(f.context, 'kids_language_audio.js');
  load(f.context, 'kids_learning_content.js');
  load(f.context, 'kids_learning.js');
  await tick();
  assert.equal(nodes.get('[data-setup-notice]').hidden, true);
  assert.equal(nodes.get('[data-game]').hidden, false);
  const visible = nodes.get('[data-question]').textContent;
  assert.match(visible, /\?/);
  assert.ok(nodes.get('[data-choices]').children.length >= 2);
  assert.ok(visible.includes(f.requests[0].payload.text), 'the visible task includes the entire spoken question');
  assert.match(nodes.get('[data-speech-status]').textContent, /Lies die Aufgabe/);
  const repeat = nodes.get('[data-speak]');
  assert.equal(repeat.hidden, false);
  repeat.onclick();
  await tick();
  assert.equal(f.requests[1].payload.text, f.requests[0].payload.text);
  assert.equal(nodes.get('[data-question]').textContent, visible);
});
