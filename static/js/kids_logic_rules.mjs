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

export const SET_OPERATIONS = Object.freeze(['and', 'or', 'not', 'difference']);
export const SET_MAX_ATTEMPTS = 5;

export function nextSetOperation(operation) {
  const index = SET_OPERATIONS.indexOf(operation);
  if (index < 0) throw new RangeError('Unknown set operation');
  return SET_OPERATIONS[(index + 1) % SET_OPERATIONS.length];
}

export class SetChallenge {
  constructor(objects, operation = SET_OPERATIONS[0], maxAttempts = SET_MAX_ATTEMPTS) {
    if (!Array.isArray(objects) || !objects.length || !objects.every((item) => item &&
      typeof item.id === 'string' && item.id && typeof item.color === 'string' && typeof item.shape === 'string') ||
      new Set(objects.map((item) => item?.id)).size !== objects.length) {
      throw new TypeError('A set challenge needs identified tiles');
    }
    if (!SET_OPERATIONS.includes(operation)) throw new RangeError('Unknown set operation');
    if (!Number.isInteger(maxAttempts) || maxAttempts < 1) throw new RangeError('Invalid attempt limit');
    this.objects = objects.map(({id, color, shape}) => ({id, color, shape}));
    this.operation = operation;
    this.maxAttempts = maxAttempts;
    this.selected = new Set();
    this.attempts = 0;
    this.solved = false;
  }

  get canReveal() { return this.attempts >= this.maxAttempts && !this.solved; }

  toggle(id) {
    if (this.solved || this.attempts >= this.maxAttempts || !this.objects.some((item) => item.id === id)) return false;
    if (this.selected.has(id)) this.selected.delete(id);
    else this.selected.add(id);
    return true;
  }

  check() {
    if (this.solved || this.attempts >= this.maxAttempts) return {result: 'locked', attempts: this.attempts};
    const checked = compareSelection(this.objects, this.operation, [...this.selected]);
    if (!checked.missing.length && !checked.outside.length) {
      this.solved = true;
      return {...checked, result: 'correct', attempts: this.attempts};
    }
    this.attempts += 1;
    return {...checked, result: this.canReveal ? 'limit' : 'wrong', attempts: this.attempts};
  }

  reveal() {
    if (!this.canReveal) return false;
    this.solved = true;
    return true;
  }

  setOperation(operation) {
    if (!SET_OPERATIONS.includes(operation)) throw new RangeError('Unknown set operation');
    this.operation = operation;
    this.reset();
  }

  next() {
    this.setOperation(nextSetOperation(this.operation));
  }

  reset() {
    this.selected.clear();
    this.attempts = 0;
    this.solved = false;
  }
}
