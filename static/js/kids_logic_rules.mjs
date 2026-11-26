// Shared signal rules for circuits, machine interlocks and shape challenges.
export function logic(operation, a, b) {
  switch (operation) {
    case 'single': return a;
    case 'and': return a && b;
    case 'or': return a || b;
    case 'not': return !a;
    case 'difference': return logic('and', a, logic('not', b));
    default: throw new RangeError('Unknown operation');
  }
}

export function gateSignal(position, up, down) {
  if (logic('and', up, down)) return 'conflict';
  if (up) return position === 2 ? 'top' : 'up';
  if (down) return position === 0 ? 'bottom' : 'down';
  return 'idle';
}
export function gateStep(position, up, down) {
  const action = gateSignal(position, up, down);
  return {position: position + (action === 'up' ? 1 : action === 'down' ? -1 : 0), action};
}

export const STATIONS = Object.freeze(['left', 'press', 'sauce', 'jar']);
export function appleSignal(stage, switches) {
  if (stage === 'left') return switches.belt ? 'move' : 'needs_belt';
  if (stage === 'press') return switches.processing ? 'process' : 'needs_processing';
  if (stage === 'sauce') {
    if (logic('and', switches.filling, switches.glass)) return 'fill';
    if (logic('or', switches.filling, switches.glass)) return switches.glass ? 'needs_filling' : 'needs_glass';
    return 'needs_both';
  }
  return 'done';
}
export function appleStep(stage, switches) {
  const action = appleSignal(stage, switches);
  const index = STATIONS.indexOf(stage);
  return {action, stage: ['move', 'process', 'fill'].includes(action) ? STATIONS[index + 1] : stage};
}

export function matchingIds(objects, operation) {
  return objects.filter(({color, shape}) => logic(operation, color === 'red', shape === 'circle'))
    .map(({id}) => id);
}

export function compareSelection(objects, operation, tokens) {
  const result = new Set(matchingIds(objects, operation));
  const known = new Set(objects.map(({id}) => id));
  const chosen = new Set(tokens.filter((id) => known.has(id)));
  return {
    missing: [...result].filter((id) => !chosen.has(id)),
    outside: [...chosen].filter((id) => !result.has(id)),
  };
}
