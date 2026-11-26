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

test('hosted presentation/inference timings preserve confirmation without summing independent gaps', () => {
  // Published fixture timing, in milliseconds: capture cadence remains under
  // 260 and native recognition under 190. The old RAF queue added up to 14 ms
  // to the four pictured observations, rejecting one of these fresh frames.
  for (const [destination, captures] of [
    ['dashboard', [[2887,185], [3070,178], [3236,178], [3420,179]]],
    ['documents', [[2886,184], [3070,178], [3236,177], [3420,178]]],
  ]) {
    const logic = new HandShortcut();
    assert.equal(logic.arm(destination,2300),true);
    for (const time of [2387,2487,2587,2687,2787]) {
      assert.equal(logic.observe(absent,time,time+86),null);
    }
    assert.equal(logic.state,'armed');
    const result = captures.map(([time, processing]) => {
      assert.equal(logic.expire(time+2,time),false);
      return logic.observe(spark,time,time+processing+2);
    });
    assert.deepEqual(result,[null,null,null,destination]);
    assert.equal(logic.state,'inactive');
  }
});

test('presentation and execution stall still disarm, and a result older than 190 never confirms', () => {
  const f = fixture();
  f.arm('documents');
  f.group(absent);
  assert.equal(f.logic.state,'armed');
  f.one(absent,100,85);
  f.one(spark,100,190);
  assert.equal(f.logic.state,'armed');
  f.one(spark,190,191);
  assert.equal(f.logic.state,'release');
  assert.deepEqual(f.group(spark),[]);
  f.group(absent);
  assert.equal(f.logic.state,'armed');
  f.logic.expire(f.now()+261);
  assert.equal(f.logic.state,'release', 'no presentation still expires in real wall time');
  assert.deepEqual(f.group(spark),[]);
  f.group(absent);
  assert.equal(f.logic.state,'armed');
  f.one(spark,261);
  assert.equal(f.logic.state,'release', 'capture gap cannot be masked by a currently fresh result');
  assert.deepEqual(f.group(spark),[]);
});
