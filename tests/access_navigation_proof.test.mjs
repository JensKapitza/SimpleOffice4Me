import assert from 'node:assert/strict';
import test from 'node:test';
import {AccessSelection, CALIBRATION, irisFeature} from '../static/js/access_navigation_logic.mjs';
import {bindAccessNavigation, decodeFrame} from '../static/js/access_navigation_session.mjs';

const feature = ([x, y]) => [.40 + .35 * x + .05 * y, -.18 + .25 * y - .07 * x];
function setup() {
  const selection = new AccessSelection();
  let time = 0;
  const targetAt = ([x]) => x < .45 ? 'dashboard' : x > .55 ? 'calendar' : null;
  function one(gaze, hand = null, elapsed = 65, delayed = 0, resolve = targetAt) {
    time += elapsed;
    return selection.observe({gaze, hand, capturedAt: time}, time + delayed, resolve);
  }
  function sample(p, h, times = 7) {
    return Array.from({length: times}, () => one(p, h)).filter(Boolean);
  }
  function calibrate(points = CALIBRATION) {
    selection.startCalibration();
    return points.flatMap((p) => { assert(selection.capture()); return sample(feature(p), null, 8); });
  }
  const dashboard = feature([.25, .5]);
  function arm() {
    assert.equal(calibrate().at(-1), 'ready');
    assert.equal(selection.state, 'ready');
    assert.deepEqual(sample(dashboard, 'crystal'), []);
    sample(dashboard, 'star');
    assert.deepEqual(sample(dashboard, 'crystal'), ['armed']);
  }
  return {selection, one, sample, calibrate, arm, dashboard, now: () => time};
}

test('distinct held-out point cannot fit itself; flat data, closed eyes, and jitter fail closed', () => {
  const f = setup();
  assert.deepEqual(f.calibrate([...CALIBRATION.slice(0, 4), [.35, .60]]), ['next', 'next', 'next', 'next', 'recalibrate']);
  assert.equal(f.selection.state, 'calibrating');
  assert.equal(f.selection.step, 0);
  assert.equal(f.calibrate(CALIBRATION.map(() => [.5, .5])).at(-1), 'recalibrate');
  f.selection.capture();
  const results = Array.from({length: 8}, (_, i) => f.one([i % 2, 0])).filter(Boolean);
  assert.deepEqual(results, ['steady']);
});

test('only stable and released then intentional clicks; no dwell, blink, wrong sign, or double repeat', () => {
  const f = setup();
  assert.deepEqual(f.sample(f.dashboard, 'shield', 12), []);
  f.arm();
  assert.deepEqual(f.sample(f.dashboard, null, 30), []);
  assert.equal(f.selection.target, 'dashboard');
  assert.deepEqual(f.sample(null, 'shield'), []);
  assert.equal(f.selection.target, null);
  assert.deepEqual(f.sample(f.dashboard, 'shield', 14), []);
  f.sample(f.dashboard, 'star', 12);
  assert.deepEqual(f.sample(f.dashboard, 'crystal'), []);
  assert.deepEqual(f.sample(f.dashboard, 'shield'), []);
  f.sample(f.dashboard, 'star', 12);
  assert.deepEqual(f.sample(f.dashboard, 'shield', 30), ['dashboard']);
  f.sample(f.dashboard, 'star');
  assert.deepEqual(f.sample(f.dashboard, 'shield'), ['dashboard']);
});

test('old or delayed data, unlisted targets, gaze movement and tab/pause reset cannot activate', () => {
  const f = setup();
  f.arm();
  f.sample(f.dashboard, 'star', 15);
  const calendar = feature([.72, .50]);
  assert.deepEqual(f.sample(calendar, 'shield', 14), []);
  f.sample(calendar, 'star', 14);
  f.one(calendar, 'shield', 65, 180);
  assert.deepEqual(f.sample(calendar, 'shield', 14), []);
  f.sample(calendar, 'star', 14);
  f.selection.expire(f.now() + 250);
  assert.equal(f.selection.target, null);
  assert.equal(f.one(calendar, 'shield', 250), null);
  f.sample(calendar, 'star', 14);
  assert.deepEqual(f.sample(feature([.82,.50]), 'shield', 14), [], 'same target but moved gaze must release again');
  f.sample(calendar, 'star', 14);
  assert.equal(f.one(calendar, 'shield', 65, 0, () => 'arbitrary'), null);
  f.selection.stop('paused');
  assert.equal(f.selection.state, 'paused');
  assert.deepEqual(f.sample(calendar, 'shield', 14), []);
  f.selection.startCalibration();
  assert.equal(f.selection.step, 0);
});

test('official iris index mapping geometry does not infer from one eye, a blink or a second face', () => {
  const face = Array.from({length: 478}, () => null);
  for (const [a, b, u, l, c, offset] of [[33, 133, 159, 145, 468, 0], [362, 263, 386, 374, 473, .30]]) {
    [[a,.16,.40], [b,.26,.40], [u,.21,.382], [l,.21,.419], [c,.21,.40]].forEach(([i,x,y]) => {
      face[i] = {x:x+offset,y,z:0};
    });
  }
  const p = irisFeature([face], 4 / 3);
  assert(Math.abs(p[0] - .50) < .01);
  assert.equal(irisFeature([], 4 / 3), null);
  assert.equal(irisFeature([face, face], 4 / 3), null);
  assert.equal(irisFeature([face.slice(0, 468)], 4 / 3), null);
  assert.equal(decodeFrame({faceLandmarks:[face]}, {landmarks:[]}, 4/3, 1).hand, null);
  const hand = Array.from({length: 21}, (_, i) => ({x:i / 25,y:i / 25,z:0}));
  assert.deepEqual(decodeFrame({faceLandmarks:[face]}, {landmarks:[hand], worldLandmarks:[hand],
    handedness: [[{categoryName:'Left',score:.65}]]}, 4/3, 1), {gaze:p, hand:null, capturedAt:1});
  assert.equal(decodeFrame({faceLandmarks:[face]}, {landmarks:[hand,hand]}, 4/3, 1).hand, null);
  face[159].y = .40;
  face[145].y = .40;
  assert.equal(irisFeature([face], 4/3), null);
});

function harness(camera, model) {
  const element = (name) => {
    const listeners = new Map();
    return {
      style:{}, textContent:name, classList:{toggle() {}}, getAttribute: () => null, isConnected:true,
      getBoundingClientRect: () => ({left:100,right:400,top:200,bottom:600}), contains(el) { return el === this; },
      addEventListener: (type, fn) => listeners.set(type, fn),
      press: (type, isTrusted) => listeners.get(type)?.({type, isTrusted}),
    };
  };
  const controls = Object.fromEntries(['start','capture','pause','stop','status','cue','marker','video','dashboard',
    'calendar','documents'].map((key) => [key, element(key)]));
  const base = 'https://app.test';
  ['dashboard','calendar','documents'].forEach((id) => { controls[id].href = base + '/existing-' + id; });
  const root = {dataset:{language:'de'}, querySelector(selector) {
    const [,target] = selector.match(/target="(\w+)/) || [];
    return controls[target || selector.slice(13,-1)];
  }};
  let elapsed = 0, raf, timer = null, videoFrame, totalFrames = 0;
  const assignments = [];
  const win = Object.assign(new EventTarget(), {
    isSecureContext:true, innerWidth:1000, innerHeight:800,
    location:{origin:base,href:base+'/bedienhilfe',assign: (href) => assignments.push(href)},
    setTimeout(fn) { timer = fn; return 1; }, clearTimeout() { timer = null; },
    requestAnimationFrame(fn) { raf = fn; return 1; }, cancelAnimationFrame() { raf = null; },
  });
  win.top = win;
  const doc = Object.assign(new EventTarget(), {hidden:false, hasFocus:()=>true, elementFromPoint:()=>controls.dashboard});
  Object.assign(controls.video, {readyState:2, play:async()=>{}, pause(){},
    requestVideoFrameCallback(fn) { videoFrame = fn; return 2; },
    cancelVideoFrameCallback() { videoFrame = null; },
  });
  const bind = () => bindAccessNavigation(root, {window:win,document:doc,openCamera:camera,openModel:model,now:()=>elapsed});
  const event = (name, isTrusted=true) => controls[name].press('click', isTrusted);
  function run(n = 8, newFrame = true) {
    for (let i = 0; i < n; i++) {
      elapsed += 65;
      if (newFrame && videoFrame) {
        const deliver = videoFrame;
        videoFrame = null;
        deliver(elapsed, {presentedFrames:++totalFrames});
      }
      const tick = raf;
      raf = null;
      tick?.(elapsed);
    }
  }
  return {controls, doc, win, bind, event, run, assignments, tickCue: () => timer?.()};
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
function trackFixture() {
  const track = Object.assign(new EventTarget(), {readyState:'live', stopped:0, stop() { this.stopped++; }});
  return {track, stream:{getVideoTracks:()=>[track],getTracks:()=>[track]}};
}

test('shipped page-controller binding: single stream, prompt, calibration, focus cue, one safe navigation', async () => {
  const {track, stream} = trackFixture();
  let gaze, hand, requests = 0, closed = 0;
  const f = harness(async (constraints) => { requests++; assert.equal(constraints.audio,false); return stream; },
    async () => ({sample: (video, t) => ({gaze, hand, capturedAt:t}), close: () => { closed++; }}));
  assert(f.bind());
  assert.equal(requests, 0);
  f.event('start', false);
  await flush();
  assert.equal(requests, 0, 'a synthesized button event is not consent');
  f.event('start');
  f.event('start');
  await flush();
  assert.equal(requests, 1);
  assert.match(f.controls.status.textContent, /Kalibrierung/);
  for (const p of CALIBRATION) { gaze = feature(p); f.event('capture'); f.run(); }
  assert.match(f.controls.status.textContent, /Fokus-Siegel/);
  assert.equal(f.controls.cue.hidden, true);
  hand='star'; f.run();
  hand='crystal'; f.run();
  assert.match(f.controls.status.textContent, /Blicksteuerung aktiv/);
  assert.equal(f.controls.cue.textContent, 'Blick-Ninja: Hokuspokus Fokus!');
  f.tickCue();
  assert.equal(f.controls.cue.hidden, true);
  gaze = feature([.25,.5]); hand = 'star'; f.run(16);
  assert.match(f.controls.status.textContent, /Aktuelles Ziel: dashboard/);
  f.run(4, false);
  assert.doesNotMatch(f.controls.status.textContent, /Aktuelles Ziel/);
  f.run(16);
  hand='shield'; f.run(20);
  assert.deepEqual(f.assignments, ['https://app.test/existing-dashboard']);
  assert.equal(track.stopped, 1);
  assert.equal(closed, 1);
  assert.equal(f.controls.video.srcObject, null);
});

test('tab change invalidates late permission; denied, Escape and Pause never navigate', async () => {
  const {track, stream} = trackFixture();
  let resolve, closed = 0;
  const f = harness(() => new Promise((answer) => {resolve=answer;}),
    async () => ({close() {closed++;}}));
  f.bind(); f.event('start'); await flush();
  f.doc.hidden = true;
  f.doc.dispatchEvent(new Event('visibilitychange'));
  resolve(stream); await flush();
  assert.equal(track.stopped, 1);
  assert.equal(closed, 1);
  assert.deepEqual(f.assignments, []);
  const denied = harness(async () => { throw Object.assign(new Error(), {name:'NotAllowedError'}); },
    async () => ({close() {}}));
  denied.bind(); denied.event('start'); await flush();
  assert.match(denied.controls.status.textContent, /abgelehnt/);
  const live = harness(async () => stream, async () => ({close(){}}));
  live.bind(); live.event('start'); await flush();
  live.event('pause');
  assert.match(live.controls.status.textContent, /Pausiert/);
  live.event('start'); await flush();
  const escape = Object.assign(new Event('keydown'), {key:'Escape'});
  live.doc.dispatchEvent(escape);
  assert.match(live.controls.status.textContent, /Blicksteuerung aus/);
  assert.deepEqual(live.assignments, []);
});

test('resize invalidates current calibration and stream; restart starts clean', async () => {
  const fixtures = [trackFixture(), trackFixture()];
  let starts = 0, gaze;
  const f = harness(async () => fixtures[starts++].stream,
    async () => ({sample: () => ({gaze,hand:null}), close(){}}));
  f.bind(); f.event('start'); await flush();
  for (const p of CALIBRATION) { gaze=feature(p); f.event('capture'); f.run(); }
  assert.match(f.controls.status.textContent, /Kontrolle bestanden/);
  f.win.dispatchEvent(new Event('resize'));
  assert.equal(fixtures[0].track.stopped, 1);
  assert.match(f.controls.status.textContent, /Blicksteuerung aus/);
  assert.equal(f.controls.marker.hidden, true);
  f.event('start'); await flush();
  assert.match(f.controls.status.textContent, /Kalibrierung/);
  assert.equal(f.controls.marker.style.left, '20%');
  assert.equal(f.controls.cue.hidden, true);
  f.win.dispatchEvent(new Event('pagehide'));
  assert.equal(fixtures[1].track.stopped, 1);
  assert.deepEqual(f.assignments, []);
});
