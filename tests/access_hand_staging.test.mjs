import assert from 'node:assert/strict';
import test from 'node:test';
import {ARM_DURATION, HandShortcut, playHandPreview} from '../static/js/access_hand_staging_logic.mjs';

const absent = {signal:'none'};
const spark = {signal:'recognized', gesture:'spark'};
const uncertain = {signal:'uncertain'};
const multiple = {signal:'multiple'};
function fixture() {
  const logic = new HandShortcut();
  let time = 1000;
  const arm = (target) => logic.arm(target, time);
  const one = (status, advance = 100, late = 0) => {
    time += advance;
    return logic.observe(status,time,time+late);
  };
  const group = (status, n = 6) => Array.from({length:n}, () => one(status)).filter(Boolean);
  return {logic, arm, one, group, now:()=>time};
}

test('explicit fixed target, deliberate release followed by sustained recognized index, exactly one action', () => {
  for (const target of ['dashboard','documents']) {
    const f = fixture();
    assert.equal(f.arm('calendar'),false);
    assert.deepEqual(f.group(spark), []);
    assert.equal(f.arm(target),true);
    assert.equal(f.arm('dashboard'),false);
    assert.deepEqual(f.group(spark), [], 'already-held pose cannot trigger');
    assert.equal(f.logic.state,'release');
    f.group(absent);
    assert.equal(f.logic.state,'armed');
    assert.deepEqual(f.group(spark,12), [target]);
    assert.equal(f.logic.state,'inactive');
    f.group(absent);
    assert.deepEqual(f.group(spark), []);
  }
});

test('slips, stale/duplicate frames, another gesture and many hands require new release', () => {
  for (const bad of [uncertain,multiple,{signal:'recognized',gesture:'star'}]) {
    const f = fixture();
    f.arm('dashboard');
    f.group(absent);
    f.group(spark,2);
    f.one(bad);
    assert.deepEqual(f.group(spark), []);
    f.group(absent);
    f.one(spark,100,400);
    assert.deepEqual(f.group(spark), []);
    f.group(absent);
    f.one(spark);
    f.one(spark,0);
    assert.deepEqual(f.group(spark), []);
    f.group(absent);
    f.one(spark,600);
    assert.deepEqual(f.group(spark), []);
  }
});

test('bounded arming and explicit stop reject later observations', () => {
  const f = fixture();
  f.arm('dashboard');
  f.group(absent);
  assert.equal(f.logic.expire(f.now()+ARM_DURATION),true);
  assert.deepEqual(f.group(spark), []);
  assert.equal(f.arm('documents'),true);
  f.group(absent);
  f.logic.stop();
  assert.deepEqual(f.group(spark), []);
});

test('preview sets actual muted playback before attaching stream and preserves rejected play', async () => {
  const stream = {};
  const order = [];
  const video = {
    set muted(value) { order.push(['mute', value]); },
    set playsInline(value) { order.push(['inline', value]); },
    set srcObject(value) { order.push(['stream', value]); },
    play() { order.push(['play']); return Promise.reject(new DOMException('test-only', 'NotAllowedError')); },
  };
  await assert.rejects(playHandPreview(video, stream), {name:'NotAllowedError'});
  assert.deepEqual(order, [['mute',true], ['inline',true], ['stream',stream], ['play']]);
});
