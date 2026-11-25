const assert = require('node:assert/strict');
const test = require('node:test');
const {execFileSync, spawnSync} = require('node:child_process');
const path = require('node:path');
const {createDetector, frameFeatures, isPeriodic} = require('../static/js/kids_syllable_signal.js');
const {createSampler} = require('../static/js/kids_syllable_mic.js');

function render(mode, rate, sounds, end = 2600) {
  let seed = 822478;
  const random = () => (seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 0x80000000 - 1;
  const max = Math.ceil(rate * end / 1000);
  const sound = new Float32Array(max);
  for (let i = 0; i < max; i += 1) {
    const t = i * 1000 / rate;
    let value = random() * .001;
    for (const [begin, length, kind, hz = 180] of sounds) {
      const elapsed = t - begin;
      if (elapsed < 0 || elapsed >= length) continue;
      if (kind === 'clap') value += random() * .8 * Math.exp(-elapsed / 9);
      if (kind === 'voice') {
        const phase = elapsed * 2 * Math.PI * hz / 1000;
        value += .22 * Math.min(1, elapsed / 12, (length - elapsed) / 12) *
          (.78 * Math.sin(phase) + .18 * Math.sin(phase * 2) + .1 * Math.sin(phase * 3));
      }
      if (kind === 'noise') value += random() * .23;
    }
    sound[i] = value;
  }
  const hits = [], states = [];
  const detector = createDetector(mode, rate);
  const frameSize = 2 ** Math.round(Math.log2(rate * .032));
  for (let now = 0; now < end - 20; now += 16) {
    const to = Math.floor(now * rate / 1000), from = Math.max(0, to - frameSize);
    const outcome = detector.process(sound.subarray(from, to), now);
    if (outcome.hit) hits.push(now);
    states.push(outcome.state);
  }
  return {hits, states};
}

test('generated broad impulses: one per clap, refractory rejects echo, sustained noise not a clap', () => {
  for (const rate of [16000, 44100, 48000]) {
    const result = render('clap', rate, [[450, 52, 'clap'], [540, 42, 'clap'], [860, 57, 'clap'],
      [1220, 520, 'noise'], [2100, 54, 'clap']]);
    assert.equal(result.hits.length, 3, `rate ${rate}: ${result.hits}`);
    assert.ok(result.hits.every(t => t < 1200 || t > 2000));
  }
  const boundaryEcho = render('clap', 48000, [[450, 52, 'clap'], [625, 61, 'clap']]);
  assert.equal(boundaryEcho.hits.length, 1, 'echo starting in refractory stays rejected if it ends later');
  const loud = render('clap', 48000, [[0, 2600, 'noise']]);
  assert.equal(loud.hits.length, 0);
  assert.ok(loud.states.includes('noisy'));
});

test('pitched generated blocks and short pauses: three; unbroken pitch counts once, unvoiced noise never', () => {
  for (const rate of [16000, 44100, 48000]) {
    const result = render('voice', rate, [[450, 225, 'voice', 210], [850, 260, 'voice', 290],
      [1290, 290, 'voice', 380], [1850, 42, 'voice'], [2040, 225, 'noise']]);
    assert.equal(result.hits.length, 3, `rate ${rate}: ${result.hits}`);
    assert.ok(result.hits.every(t => t > 675 && t < 1840));
  }
  assert.equal(render('voice', 48000, [[450, 930, 'voice']]).hits.length, 1);
  assert.equal(render('voice', 48000, [[450, 55, 'clap'], [810, 900, 'noise']]).hits.length, 0);
  assert.equal(render('voice', 48000, [[450, 120, 'voice'], [570, 202, 'noise'], [772, 120, 'voice']]).hits.length, 1);
  assert.equal(render('clap', 48000, [[450, 170, 'voice']]).hits.length, 0);
});

test('existing synthetic Ba–na–ne game clips count as three separated voiced blocks', {
  skip: spawnSync('ffmpeg', ['-version'], {stdio: 'ignore'}).status !== 0 && 'Optional existing ffmpeg needed to read MP3',
}, () => {
  // These checked-in Thorsten/Piper game clips are synthetic, not recordings
  // of children; source and attribution: static/audio/kids/games/README.md.
  const rate = 48000;
  const startSilence = new Float32Array(rate / 2);
  const gap = new Float32Array(rate / 5);
  const chunks = [startSilence];
  for (const syllable of ['ba', 'na', 'ne']) {
    const file = path.join(__dirname, '..', 'static', 'audio', 'kids', 'games', 'syllables', 'de', `p_${syllable}.mp3`);
    const pcm = execFileSync('ffmpeg', ['-nostdin', '-hide_banner', '-loglevel', 'error',
      '-i', file, '-ac', '1', '-ar', String(rate), '-f', 'f32le', 'pipe:1']);
    chunks.push(new Float32Array(pcm.buffer, pcm.byteOffset, pcm.length / 4), gap);
  }
  const sound = new Float32Array(chunks.reduce((size, chunk) => size + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) { sound.set(chunk, offset); offset += chunk.length; }
  const detector = createDetector('voice', rate);
  const hits = [];
  for (let now = 0; now * rate / 1000 <= sound.length; now += 16) {
    const end = Math.floor(now * rate / 1000);
    const result = detector.process(sound.subarray(Math.max(0, end - 2048), end), now);
    if (result.hit) hits.push(now);
  }
  assert.equal(hits.length, 3);
});

test('periodicity ignores DC and unknown frames; finite typed scratch only', () => {
  const frame = Float32Array.from({length: 2048}, (_, i) => .21 + Math.sin(i * 2 * Math.PI * 240 / 48000) * .1);
  const {rms, mean} = frameFeatures(frame);
  assert.ok(rms > .05 && rms < .09);
  assert.equal(isPeriodic(frame, 48000, mean), true);
  assert.equal(isPeriodic(new Float32Array(2048), 48000, 0), false);
});

const pending = () => {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return {resolve, promise};
};
function audioStream() {
  const calls = [];
  const track = {
    readyState: 'live',
    stop() { this.readyState = 'ended'; calls.push('stop'); },
    addEventListener(type, f) { this.onEnd = f; },
  };
  return {calls, track, getTracks: () => [track], getAudioTracks: () => [track], getVideoTracks: () => []};
}
function samplerFixture(opts = {}) {
  let scheduled, clock = 0;
  const events = [];
  const source = {connect: (node) => { assert.equal(node, analyser); events.push('connect'); },
    disconnect: () => events.push('source.off')};
  const analyser = {fftSize: 0, getFloatTimeDomainData: samples => samples.fill(.1),
    disconnect: () => events.push('analyser.off')};
  const context = {state: 'running', sampleRate: 48000, createAnalyser: () => analyser,
    createMediaStreamSource: () => source, resume: async () => {}};
  const sampler = createSampler({
    openStream: async (constraints) => {
      assert.equal(constraints.video, false);
      assert.equal(constraints.audio.autoGainControl, false);
      return audioStream();
    },
    makeContext: () => ({...context, close: async () => { events.push('context.off'); }}),
    schedule: cb => { scheduled = cb; return 0; }, cancel: () => { scheduled = null; events.push('cancel'); },
    clock: () => clock, active: () => true,
    onState: s => events.push(s), onHit: () => events.push('hit'),
    detector: () => ({process: () => ({state:'listening',hit:true})}),
    ...opts,
  });
  return {sampler, events, drive: (now) => { clock = now; scheduled?.(now); }};
}

test('explicit audio-only stream, no output, stop before output and discard old grant', async () => {
  const oldPermission = pending(), old = audioStream(), latest = audioStream();
  let asks = 0;
  const f = samplerFixture({
    openStream: (caps) => {
      assert.equal(caps.video, false);
      return ++asks === 1 ? oldPermission.promise : Promise.resolve(latest);
    },
  });
  assert.deepEqual(f.events, []);
  const p = f.sampler.start('clap');
  assert.deepEqual(f.events, ['permission']);
  f.sampler.stop('audio');
  await f.sampler.start('voice');
  oldPermission.resolve(old);
  await p;
  assert.equal(old.track.readyState, 'ended');
  assert.equal(latest.track.readyState, 'live');
  f.drive(16);
  assert.ok(f.events.includes('hit'));
  assert.equal(f.sampler.stop('audio'), true);
  assert.equal(latest.track.readyState, 'ended');
  const oldCount = f.events.filter(x => x === 'hit').length;
  f.drive(100);
  assert.equal(f.events.filter(x => x === 'hit').length, oldCount);
  assert.ok(f.events.includes('context.off') && f.events.includes('analyser.off'));
});

test('denial, inactive old panel, track disconnect and idle all release resources', async () => {
  const denial = samplerFixture({openStream: async () => { throw Object.assign(Error(), {name:'NotAllowedError'}); }});
  await denial.sampler.start('voice');
  assert.equal(denial.events.at(-1), 'denied');
  assert.ok(!denial.sampler.running());

  let active = true;
  const track = audioStream();
  const running = samplerFixture({active: () => active, openStream: async () => track});
  await running.sampler.start('clap');
  active = false;
  running.drive(16);
  assert.equal(track.track.readyState, 'ended');
  assert.equal(running.events.at(-1), 'paused');

  const disconnected = audioStream();
  const device = samplerFixture({openStream: async () => disconnected});
  await device.sampler.start('clap');
  disconnected.track.onEnd();
  assert.ok(!device.sampler.running());
  assert.equal(device.events.at(-1), 'unavailable');

  const idleTrack = audioStream();
  const idle = samplerFixture({openStream: async () => idleTrack});
  await idle.sampler.start('clap');
  idle.drive(30000);
  assert.equal(idleTrack.track.readyState, 'ended');
  assert.equal(idle.events.at(-1), 'idle');
});
