import assert from 'node:assert/strict';
import test from 'node:test';
import {
  appleSignal, appleStep, compareSelection, gateSignal, gateStep, logic, matchingIds,
  nextSetOperation, SetChallenge, SET_MAX_ATTEMPTS,
} from '../static/js/kids_logic_rules.mjs';

test('all four input combinations, inclusive OR, and NOT ignores the second input', () => {
  for (const [a,b,series,parallel,onlyA] of [
    [false,false,false,false,false], [false,true,false,true,false],
    [true,false,false,true,true], [true,true,true,true,false],
  ]) {
    assert.equal(logic('single',a,b),a);
    assert.equal(logic('and',a,b),series);
    assert.equal(logic('or',a,b),parallel);
    assert.equal(logic('not',a,b),!a);
    assert.equal(logic('difference',a,b),onlyA);
  }
  assert.throws(() => logic('xor',true,true),RangeError);
});

test('gate cannot run both directions or cross either limit; reverse starts normally', () => {
  for (const p of [0,1,2]) assert.deepEqual(gateStep(p,true,true),{position:p,action:'conflict'});
  assert.equal(gateSignal(0,false,false),'idle');
  assert.deepEqual(gateStep(0,false,true),{position:0,action:'bottom'});
  assert.deepEqual(gateStep(0,true,false),{position:1,action:'up'});
  assert.deepEqual(gateStep(1,true,false),{position:2,action:'up'});
  assert.deepEqual(gateStep(2,true,false),{position:2,action:'top'});
  assert.deepEqual(gateStep(2,false,true),{position:1,action:'down'});
  assert.deepEqual(gateStep(1,false,true),{position:0,action:'down'});
  assert.deepEqual(gateStep(0,false,true),{position:0,action:'bottom'});
});

test('conveyor, processing and glass conditions gate each step; completed jar is stable', () => {
  const allEnabled = {belt:true,processing:true,filling:true,glass:true};
  assert.deepEqual(appleStep('left',{...allEnabled,belt:false}),{stage:'left',action:'needs_belt'});
  assert.deepEqual(appleStep('left',allEnabled),{stage:'press',action:'move'});
  assert.deepEqual(appleStep('press',{...allEnabled,processing:false}),{stage:'press',action:'needs_processing'});
  assert.deepEqual(appleStep('press',allEnabled),{stage:'sauce',action:'process'});
  for (const [filling,glass,expected] of [
    [false,false,'needs_both'], [false,true,'needs_filling'], [true,false,'needs_glass'],
  ]) assert.deepEqual(appleStep('sauce',{...allEnabled,filling,glass}),{stage:'sauce',action:expected});
  assert.deepEqual(appleStep('sauce',allEnabled),{stage:'jar',action:'fill'});
  assert.deepEqual(appleStep('jar',allEnabled),{stage:'jar',action:'done'});
  assert.equal(appleSignal('left',{belt:false,processing:true,filling:true,glass:true}),'needs_belt');
});

test('sets use whole displayed universe; overlaps appear exactly once and different mistakes are distinct', () => {
  const rows = [
    {id:'rc',color:'red',shape:'circle'}, {id:'rs',color:'red',shape:'square'},
    {id:'bc',color:'blue',shape:'circle'}, {id:'ys',color:'yellow',shape:'square'},
  ];
  assert.deepEqual(matchingIds(rows,'and'),['rc']);
  assert.deepEqual(matchingIds(rows,'or'),['rc','rs','bc']);
  assert.deepEqual(matchingIds(rows,'not'),['bc','ys']);
  assert.deepEqual(matchingIds(rows,'difference'),['rs']);
  assert.deepEqual(compareSelection(rows,'or',['rc','rc','ys','elsewhere']),{missing:['rs','bc'],outside:['ys']});
  assert.deepEqual(compareSelection(rows,'difference',['rs']),{missing:[],outside:[]});
});

test('set challenges keep answers hidden for five failed checks, then reveal and continue cleanly', () => {
  const tiles = [
    {id:'rc',color:'red',shape:'circle'}, {id:'rs',color:'red',shape:'square'},
    {id:'bc',color:'blue',shape:'circle'}, {id:'ys',color:'yellow',shape:'square'},
  ];
  const task = new SetChallenge(tiles, 'and');
  assert.equal(task.canReveal, false);
  assert.equal(task.reveal(), false);
  assert.equal(task.toggle('unknown'), false);
  assert.deepEqual(task.check(), {missing:['rc'],outside:[],result:'wrong',attempts:1});
  for (let attempt = 2; attempt < SET_MAX_ATTEMPTS; attempt += 1) assert.equal(task.check().result, 'wrong');
  const last = task.check();
  assert.equal(last.result, 'limit');
  assert.equal(last.missing[0], 'rc');
  assert.equal(task.canReveal, true);
  assert.equal(task.toggle('rc'), false, 'answers cannot be edited after the fifth try');
  assert.equal(task.check().result, 'locked');
  assert.equal(task.reveal(), true);
  assert.equal(task.reveal(), false);
  task.next();
  assert.equal(task.operation, 'or');
  assert.equal(task.attempts, 0);
  assert.equal(task.selected.size, 0);
  assert.equal(task.solved, false);
  for (const id of ['rc', 'rs', 'bc']) assert.equal(task.toggle(id), true);
  assert.equal(task.check().result, 'correct');
  assert.equal(task.solved, true);
  assert.equal(task.canReveal, false);
  assert.deepEqual(['and','or','not','difference'].map(nextSetOperation),
    ['or','not','difference','and']);
  assert.throws(() => task.setOperation('xor'), RangeError);
  assert.throws(() => new SetChallenge([tiles[0], tiles[0]]), TypeError);
  assert.throws(() => new SetChallenge([null]), TypeError);
});
