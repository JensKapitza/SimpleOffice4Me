const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../static/js/kids_learning_content.js'), 'utf8');
const drawSource = fs.readFileSync(path.join(__dirname, '../static/js/kids_maze_draw.js'), 'utf8');

function content(english = false) {
  const window = {};
  vm.runInNewContext(source, {window});
  const text = (de, en) => (english ? en : de);
  return window.KidsLearningContent(text, english, english ? 'en-US' : 'de-DE', v => [{text: v, language: 'de-DE'}]);
}

const line = () => ({
  start: {exits: {right: 'a'}},
  a: {exits: {left: 'start', right: 'b'}, letterIndex: 0},
  b: {exits: {left: 'a', right: 'goal'}, letterIndex: 1},
  goal: {exits: {left: 'b'}},
});

test('distances, routes and letter lookup on a fixed graph', () => {
  const {mazeTyping} = content();
  const nodes = line();
  assert.equal(mazeTyping.mazeDistanceTo(nodes, 'start', 'goal'), 3);
  assert.equal(mazeTyping.mazeDistanceTo(nodes, 'b', 'b'), 0);
  assert.deepEqual([...mazeTyping.mazeRouteTo(nodes, 'start', 'b')], ['right', 'right']);
  assert.deepEqual([...mazeTyping.mazeRouteTo(nodes, 'b', 'b')], []);
  assert.equal(mazeTyping.mazeLetterNode(nodes, 1), 'b');
  assert.equal(mazeTyping.mazeLetterNode(nodes, 9), '');
});

test('expected letter follows collection order, then the goal phase', () => {
  const {mazeTyping} = content();
  assert.equal(mazeTyping.mazeNextExpected('HUND', []), 0);
  assert.equal(mazeTyping.mazeNextExpected('HUND', new Set([0, 1])), 2);
  assert.equal(mazeTyping.mazeNextExpected('HUND', [0, 1, 2, 3]), -1);
});

test('correct steps walk toward the target, wrong steps never approach it', () => {
  const {mazeTyping} = content();
  const nodes = line();
  assert.equal(mazeTyping.mazeCorrectStep(nodes, 'start', 'b'), 'right');
  assert.equal(mazeTyping.mazeCorrectStep(nodes, 'b', 'b'), '');
  assert.equal(mazeTyping.mazeWrongStep(nodes, 'a', 'b', new Set(['start'])), 'left');
  assert.equal(mazeTyping.mazeWrongStep(nodes, 'missing', 'b', new Set()), '');
});

test('no wrong direction at the start means stay put', () => {
  const {mazeTyping} = content();
  const nodes = line();
  // From 'start' the only exit leads toward the letter: a wrong letter stays.
  assert.equal(mazeTyping.mazeWrongStep(nodes, 'start', 'a', new Set(['start'])), '');
});

test('generated mazes place every letter exactly once inside the grid', () => {
  for (const english of [false, true]) {
    const {mazeFriends, generateMaze, mazeTyping} = content(english);
    assert.equal(mazeFriends.length, 8);
    for (const friend of mazeFriends) {
      for (let round = 0; round < 5; round += 1) {
        const {nodes, word} = generateMaze(friend);
        assert.ok(word.length >= 3, `${friend.character} word ${word}`);
        const placed = Object.values(nodes).filter(n => n.letterIndex >= 0).map(n => n.letterIndex).sort();
        assert.deepEqual(placed, [...word].map((_, i) => i));
        for (const [id, node] of Object.entries(nodes)) {
          assert.ok(node.col >= 0 && node.col < 4 && node.row >= 0 && node.row < 4, `${id} in grid`);
          assert.ok(50 + node.col * 100 <= 350 && 50 + node.row * 100 <= 350, `${id} inside 400 viewBox`);
        }
        assert.ok(mazeTyping.mazeRouteTo(nodes, 'start', 'goal').length > 0, 'goal reachable');
      }
    }
  }
});

test('extracted renderer draws edges, nodes, letters and icons', () => {
  const created = [];
  const document = {
    createElementNS: (ns, tag) => {
      const el = {tag, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, textContent: ''};
      created.push(el);
      return el;
    },
  };
  const appended = [];
  const mapEl = {
    replaceChildren() {}, setAttribute() {}, append(el) { appended.push(el); },
  };
  const window = {};
  vm.runInNewContext(drawSource, {window, document});
  const {generateMaze, mazeFriends} = content(false);
  const {nodes, word} = generateMaze(mazeFriends[0]);
  window.KidsMazeDraw.drawMaze({
    mapEl, nodes, current: 'start', happy: false, friend: mazeFriends[0],
    wrongTurns: {}, difficulty: 'easy', text: (de) => de,
    root: {querySelectorAll: () => []},
  });
  const lines = created.filter(el => el.tag === 'line').length;
  const circles = created.filter(el => el.tag === 'circle').length;
  const texts = created.filter(el => el.tag === 'text');
  assert.ok(lines >= word.length, `edges drawn: ${lines}`);
  assert.equal(circles, Object.keys(nodes).length);
  assert.equal(texts.filter(el => el.textContent && el.textContent.length === 1).length, word.length);
  assert.ok(texts.some(el => String(el.textContent).includes(mazeFriends[0].character)));
});

test('typing every expected letter always completes the maze', () => {
  const {mazeFriends, generateMaze, mazeTyping} = content(false);
  for (const friend of mazeFriends) {
    for (let round = 0; round < 3; round += 1) {
      const {nodes, word} = generateMaze(friend);
      let current = 'start';
      const collected = new Set();
      const visited = new Set(['start']);
      for (let turn = 0; turn < 60 && current !== 'goal'; turn += 1) {
        const expected = mazeTyping.mazeNextExpected(word, collected);
        const target = expected < 0 ? 'goal' : mazeTyping.mazeLetterNode(nodes, expected);
        assert.ok(target, 'target exists');
        // Sprinkle wrong letters: they must stay legal (a real exit or stay).
        if (turn % 4 === 3 && expected >= 0) {
          const wrong = mazeTyping.mazeWrongStep(nodes, current, target, visited);
          if (wrong) {
            assert.ok(nodes[current].exits[wrong], 'wrong step uses a real exit');
            current = nodes[current].exits[wrong];
          }
        } else {
          const step = mazeTyping.mazeCorrectStep(nodes, current, target);
          assert.ok(step, 'correct step exists');
          current = nodes[current].exits[step];
        }
        visited.add(current);
        if (nodes[current].letterIndex >= 0) collected.add(nodes[current].letterIndex);
      }
      assert.equal(current, 'goal', `${friend.character} reaches the goal by typing`);
      assert.equal(collected.size, word.length, 'all letters collected');
    }
  }
});
