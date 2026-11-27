import {test} from 'node:test';
import assert from 'node:assert/strict';
import {Duel, HoldFilter, POSES, PracticeSession, readResult, classifyHand} from '../static/js/kids_hand_logic.mjs';
import {cameraController} from '../static/js/kids_hand_camera.mjs';

const p = (x, y, z = 0) => ({x, y, z});
function geometry(digits) {
  const points = [p(0, 0), p(-.3,.2), p(-.5,.4), p(-.6,.65), p(-.65,.7)];
  [-.36, -.12, .14, .37].forEach((x, i) => {
    points.push(p(x, .95), p(x, 1.35));
    points.push(...(digits[i] === '1' ? [p(x, 1.64), p(x, 1.9)] : [p(x, 1.25, .35), p(x, .98, .33)]));
  });
  return points;
}

test('four geometries classify distinct left/right/rotated/scaled poses, and ambiguous folds do not score', () => {
  for (const [name, {digits}] of Object.entries(POSES)) {
    const raw = geometry(digits);
    for (const [flip, angle, scale] of [[1,0,1], [-1,0,1], [1,.9,2], [-1,-1.8,.3]]) {
      const world = raw.map(({x,y,z}) => p(
        scale * (flip*x*Math.cos(angle) - y*Math.sin(angle)) + 3,
        scale * (flip*x*Math.sin(angle) + y*Math.cos(angle)) - 4, scale*z + .5,
      ));
      assert.equal(classifyHand(world), name);
      assert.equal(readResult({landmarks: [raw], worldLandmarks: [world],
        handedness: [[{categoryName: flip < 0 ? 'Left' : 'Right', score: .95}]],
      }).gesture, name);
    }
  }
  const raw = geometry('1100');
  assert.equal(readResult({landmarks: [raw, raw]}).signal, 'multiple');
  assert.equal(readResult({landmarks: []}).signal, 'none');
  assert.equal(readResult({landmarks: [raw], worldLandmarks: [raw],
    handedness: [[{categoryName:'Left', score:.49}]]}).signal, 'uncertain');
  assert.equal(classifyHand(geometry('1010')), null);
  assert.equal(classifyHand(Array(21).fill(p(0,0))), null);
});

const seen = (gesture) => ({signal: 'recognized', gesture});
const absent = {signal: 'none'};
test('stable frames, repeated signs and deliberate release: wrong/uncertain/multi-hand do not add hits', () => {
  const f = new HoldFilter();
  for (const t of [0,67,135]) assert.equal(f.observe(seen('star'), t), null);
  assert.equal(f.observe(seen('star'), 201), 'star');
  for (const t of [271,340,440]) assert.equal(f.observe(seen('star'), t), null);
  for (const t of [510,580,700]) assert.equal(f.observe({signal:'uncertain'}, t), null);
  for (const t of [760,820,1010]) assert.equal(f.observe(seen('star'), t), null);
  assert.equal(f.observe(absent, 1080), null);
  assert.equal(f.observe({signal: 'multiple'}, 1150), null);
  for (const t of [1210,1280,1340]) assert.equal(f.observe(absent, t), null);
  assert.equal(f.observe(absent, 1420), 'released');
  for (const t of [1500,1570,1640]) assert.equal(f.observe(seen('star'), t), null);
  assert.equal(f.observe(seen('star'), 1710), 'star');

  const g = new HoldFilter();
  g.observe(seen('spark'), 0);
  assert.equal(g.observe(seen('spark'), 800), null, 'camera stall must not appear as a held shape');
  assert.equal(g.observe(seen('spark'), 810), null);
  assert.equal(g.observe(seen('spark'), 800), null, 'backward frame ignored');
  assert.equal(g.observe(seen('spark'), 1020), 'spark');
});

test('a stable matching practice sign advances the target; wrong signs do not, manual skip still works', () => {
  const practice = new PracticeSession(['crystal', 'star', 'shield']);
  assert.equal(practice.target, 'crystal');
  assert.equal(practice.update('spark'), 'wrong');
  assert.equal(practice.target, 'crystal');
  assert.equal(practice.update(null), null);
  assert.equal(practice.target, 'crystal');

  const hold = new HoldFilter();
  let matched = null;
  for (const time of [0, 67, 135, 201]) {
    const gesture = hold.observe(seen('crystal'), time);
    if (gesture) matched = practice.update(gesture);
  }
  assert.equal(matched, 'correct');
  assert.equal(practice.target, 'star', 'correct camera recognition must update the visible target');
  assert.equal(hold.observe(seen('crystal'), 271), null, 'one held pose cannot score twice');

  practice.next();
  assert.equal(practice.target, 'shield');
  assert.equal(practice.update('shield'), 'correct');
  assert.equal(practice.target, 'crystal', 'practice cycles after the last sign');
  assert.throws(() => new PracticeSession([]), TypeError);
  assert.throws(() => new PracticeSession(['unknown']), TypeError);
});

test('same turns, equal deadlines, wrong order, tie and fair tiebreak', () => {
  const duel = new Duel(['shield', 'shield', 'star'], 1500);
  assert.equal(duel.start(0), true);
  assert.equal(duel.start(1), false);
  assert.equal(duel.update('star', 100), 'wrong');
  assert.equal(duel.index, 0);
  assert.equal(duel.update('shield', 250), 'correct');
  assert.equal(duel.update('released', 450), null);
  assert.equal(duel.update('shield', 700), 'correct');
  assert.equal(duel.update('star', 850), 'complete');
  assert.equal(duel.winner(), null);
  assert.equal(duel.player, 2);
  assert.equal(duel.start(10000), true);
  assert.equal(duel.target, 'shield');
  assert.equal(duel.remaining(10000), 1500);
  assert.equal(duel.update('shield', 10300), 'correct');
  assert.equal(duel.update('shield', 10600), 'correct');
  assert.equal(duel.update('star', 10800), 'complete');
  assert.equal(duel.winner(), 2);
  assert.deepEqual(duel.results, [{player:1,hits:3,complete:true,ms:850}, {player:2,hits:3,complete:true,ms:800}]);

  const timeout = new Duel(['star', 'shield'], 1000);
  for (const start of [0, 9500]) {
    timeout.start(start);
    assert.equal(timeout.update('star', start + 300), 'correct');
    assert.equal(timeout.update('shield', start + 1000), 'timeout', 'deadline before scoring');
  }
  assert.equal(timeout.winner(), 0);
  assert.deepEqual(timeout.results.map(({hits, ms}) => [hits, ms]), [[1, 1000], [1, 1000]]);
});

const defer = () => { let resolve, reject; const pending = new Promise((r,j) => {resolve=r;reject=j;}); return {pending, resolve, reject}; };
const stream = () => {
  let stopped = 0, ended = null;
  const track = {stop: () => {stopped++;}, readyState: 'live', addEventListener: (_event, callback) => {ended = callback;}};
  return {getTracks: () => [track], getVideoTracks: () => [track], stops: () => stopped, end: () => ended?.()};
};
function cameraFixture(overrides) {
  const events = [], active = {value: true};
  let current = null;
  const detector = {close: () => events.push('model-closed')};
  const camera = cameraController({
    openCamera: async () => stream(), openModel: async () => detector, attach: () => events.push('attached'),
    detach: () => events.push('detached'), active: () => active.value, update: () => events.push('frame'),
    schedule: (callback) => {current = callback; return 0;}, cancel: () => {current = null;},
    onState: (state) => events.push(state), ...overrides,
  });
  return {camera, events, active, frame: (t) => current?.(t), detector};
}

test('stop before model load and after grant request clears every late result; no stale restart', async () => {
  const model = defer(), payload = stream();
  const first = cameraFixture({openModel: () => model.pending,
    openCamera: () => assert.fail('camera must not open after stop'),
  });
  const pendingModel = first.camera.start();
  first.camera.stop();
  model.resolve(first.detector);
  await pendingModel;
  assert.deepEqual(first.events, ['loading','detached','stopped','model-closed']);

  const grant = defer();
  let count = 0;
  const next = stream();
  const second = cameraFixture({
    openCamera: (constraints) => {
      assert.equal(constraints.audio, false);
      return ++count === 1 ? grant.pending : Promise.resolve(next);
    },
  });
  const firstStart = second.camera.start();
  await Promise.resolve();
  await Promise.resolve();
  assert.ok(second.events.includes('permission'));
  second.camera.stop();
  await second.camera.start();
  assert.ok(second.camera.live());
  grant.resolve(payload);
  await firstStart;
  assert.equal(payload.stops(),1);
  assert.equal(next.stops(),0);
  second.camera.stop();
  second.frame(500);
  assert.equal(next.stops(),1);
  assert.equal(second.events.filter((x) => x === 'attached').length,1);
  assert.ok(!second.events.includes('frame'));
});

test('permission denial, disconnect, and inference failure all close preview and track', async () => {
  const f = cameraFixture({openCamera: async () => {throw Object.assign(Error(), {name:'NotAllowedError'});}});
  await f.camera.start();
  assert.equal(f.camera.live(), false);
  assert.equal(f.events.at(-1), 'denied');
  const slow = defer(), late = stream();
  const g = cameraFixture({openCamera: () => slow.pending});
  const pending = g.camera.start();
  await Promise.resolve();
  await Promise.resolve();
  g.active.value = false;
  g.camera.stop();
  slow.resolve(late);
  await pending;
  assert.equal(late.stops(), 1);
  assert.ok(!g.events.includes('attached'));
  const disconnected = stream();
  const h = cameraFixture({openCamera: async () => disconnected});
  await h.camera.start();
  disconnected.end();
  assert.equal(h.events.at(-1),'camera_error');
  const bad = stream();
  const j = cameraFixture({openCamera: async () => bad, update: () => {throw Error('test');}});
  await j.camera.start();
  j.frame(100);
  assert.equal(bad.stops(),1);
  assert.equal(j.events.at(-1),'inference_error');
});
