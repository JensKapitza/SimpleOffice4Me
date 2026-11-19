/* Maze renderer for the Kids learning game.
   Extracted from kids_learning.js so the game file stays within the size gate.
   Pure rendering: reads the given context, never touches game state. */
(() => {
  function drawMaze(ctx) {
    const {
      mapEl: mazeMap, nodes: mazeNodes, current: mazeCurrentNode, happy: mazeHappy,
      friend: mazeFriend, wrongTurns: mazeWrongTurns, difficulty: mazeDifficulty,
      text, root,
    } = ctx;
    mazeMap.replaceChildren();
    mazeMap.setAttribute('aria-label', text(`Labyrinth: ${mazeFriend.character} soll zum ${mazeFriend.de}.`, `Maze: guide ${mazeFriend.character} to the ${mazeFriend.en}.`));
    const svg = 'http://www.w3.org/2000/svg';
    const point = node => ({x:50 + mazeNodes[node].col * 100, y:50 + mazeNodes[node].row * 100});
    const drawnEdges = new Set();
    Object.entries(mazeNodes).forEach(([node, config]) => Object.values(config.exits).forEach(next => {
      const key = [node, next].sort().join(':');
      if (drawnEdges.has(key)) return;
      drawnEdges.add(key);
      const start = point(node);
      const end = point(next);
      const road = document.createElementNS(svg, 'line');
      road.setAttribute('x1', String(start.x));
      road.setAttribute('y1', String(start.y));
      road.setAttribute('x2', String(end.x));
      road.setAttribute('y2', String(end.y));
      road.setAttribute('stroke', '#b6c0cb');
      road.setAttribute('stroke-width', '24');
      road.setAttribute('stroke-linecap', 'round');
      mazeMap.append(road);
    }));
    Object.keys(mazeNodes).forEach(node => {
      const {x,y} = point(node);
      const circle = document.createElementNS(svg, 'circle');
      circle.setAttribute('cx', String(x));
      circle.setAttribute('cy', String(y));
      circle.setAttribute('r', '20');
      circle.setAttribute('fill', node === 'goal' ? '#fff3cd' : '#fff');
      circle.setAttribute('stroke', node === 'goal' ? '#f0ad00' : '#697586');
      circle.setAttribute('stroke-width', '4');
      mazeMap.append(circle);
      if (mazeNodes[node].letter) {
        const letter = document.createElementNS(svg, 'text');
        letter.setAttribute('x', String(x));
        letter.setAttribute('y', String(y - 29));
        letter.setAttribute('text-anchor', 'middle');
        letter.setAttribute('dominant-baseline', 'central');
        letter.setAttribute('font-size', '27');
        letter.setAttribute('font-weight', '800');
        letter.setAttribute('fill', '#26364a');
        letter.textContent = mazeNodes[node].letter;
        mazeMap.append(letter);
      }
    });
    const addIcon = (node, icon, label, offsetX = 0) => {
      const {x,y} = point(node);
      const marker = document.createElementNS(svg, 'text');
      marker.setAttribute('x', String(x + offsetX));
      marker.setAttribute('y', String(y + 1));
      marker.setAttribute('text-anchor', 'middle');
      marker.setAttribute('dominant-baseline', 'central');
      marker.setAttribute('font-size', '32');
      marker.setAttribute('aria-label', label);
      marker.textContent = icon;
      mazeMap.append(marker);
    };
    if (mazeCurrentNode !== 'start') addIcon('start', mazeFriend.character, text('Start','Start'));
    addIcon('goal', mazeFriend.goal, text('Ziel','Goal'));
    addIcon(mazeCurrentNode, mazeHappy ? `${mazeFriend.character}😊` : mazeFriend.character, text('Figur','Character'), mazeCurrentNode === 'goal' ? -30 : 0);
    root.querySelectorAll('[data-maze-direction]').forEach(button => {
      const key = `${mazeCurrentNode}:${button.dataset.mazeDirection}`;
      const misses = mazeWrongTurns[key] || 0;
      button.style.opacity = mazeDifficulty === 'easy' && misses ? String(Math.max(.28, 1 - misses * .24)) : '';
    });
  }
  window.KidsMazeDraw = {drawMaze};
})();
