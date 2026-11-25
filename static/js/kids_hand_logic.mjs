// Fantasy shapes only. These orientation-independent geometry rules are not DGS.
export const POSES = Object.freeze({
  crystal: {icon: '💎', hand: '✌️', digits: '1100',
    de: ['Kristall', 'Zeige Zeige- und Mittelfinger. Die anderen Finger einklappen.'],
    en: ['Crystal', 'Stretch your index and middle fingers. Curl the others.']},
  star: {icon: '⭐', hand: '✋', digits: '1111',
    de: ['Stern', 'Mach die Hand weit auf und strecke alle Finger.'],
    en: ['Star', 'Open your hand and stretch your fingers.']},
  shield: {icon: '🛡️', hand: '✊', digits: '0000',
    de: ['Schild', 'Mach eine lockere Faust.'],
    en: ['Shield', 'Make a relaxed fist.']},
  spark: {icon: '✨', hand: '☝️', digits: '1000',
    de: ['Funke', 'Strecke nur den Zeigefinger.'],
    en: ['Spark', 'Stick out only your index finger.']},
});
export const DUEL_ORDER = Object.freeze(['spark', 'star', 'shield', 'shield', 'crystal', 'star']);
export const ROUND_MS = 30000;
const STABLE_MS = 200;
const RELEASE_MS = 200;
const MAX_FRAME_GAP_MS = 250;
const MIN_FRAMES = 3;

const minus = (a, b) => [a.x - b.x, a.y - b.y, a.z - b.z];
const mag = (a) => Math.hypot(...a);
const len = (a, b) => mag(minus(a, b));
function jointCos(mcp, pip, dip) {
  const a = minus(mcp, pip), b = minus(dip, pip);
  const divisor = mag(a) * mag(b);
  return divisor > 1e-8 ? a.reduce((sum, value, index) => sum + value * b[index], 0) / divisor : NaN;
}

export function classifyHand(points) {
  if (!Array.isArray(points) || points.length !== 21 ||
      !points.every((point) => point && [point.x, point.y, point.z].every(Number.isFinite))) return null;
  const wrist = points[0];
  const palm = len(wrist, points[9]);
  if (palm <= 1e-5 || len(points[5], points[17]) <= palm * 0.17) return null;
  const digits = [5, 9, 13, 17].map((base) => {
    const [mcp, pip, dip, tip] = points.slice(base, base + 4);
    const cos = jointCos(mcp, pip, dip);
    const reach = (len(tip, wrist) - len(pip, wrist)) / palm;
    const extension = (len(tip, wrist) - len(mcp, wrist)) / palm;
    if (cos <= -0.72 && reach >= 0.16 && extension >= 0.37) return '1';
    if (cos >= -0.42 && reach <= 0.11 && extension <= 0.32) return '0';
    return '?';
  }).join('');
  return Object.keys(POSES).find((name) => POSES[name].digits === digits) || null;
}

export function readResult(result) {
  if (!Array.isArray(result?.landmarks)) return {signal: 'uncertain'};
  if (!result.landmarks.length) return {signal: 'none'};
  if (result.landmarks.length !== 1) return {signal: 'multiple'};
  const points = result.landmarks[0];
  if (!Array.isArray(points) || points.length !== 21 ||
      !points.every((point) => point && [point.x, point.y].every(Number.isFinite))) return {signal: 'uncertain'};
  const handed = (result.handedness || result.handednesses)?.[0]?.[0];
  if (!['Left', 'Right'].includes(handed?.categoryName) || !Number.isFinite(handed?.score) || handed.score < 0.7) {
    return {signal: 'uncertain', points};
  }
  const gesture = classifyHand(result.worldLandmarks?.[0]);
  return {signal: gesture ? 'recognized' : 'uncertain', points, gesture, handedness: handed.categoryName};
}

export class HoldFilter {
  reset() {
    this.gesture = null;
    this.since = 0;
    this.frames = 0;
    this.previous = null;
    this.latched = null;
  }

  constructor() { this.reset(); }

  observe(observation, time) {
    const value = observation.signal === 'none' ? 'released' : observation.gesture;
    if (!Number.isFinite(time) || (this.previous !== null && time <= this.previous)) return null;
    if (!value || value !== this.gesture || (this.previous !== null && time - this.previous > MAX_FRAME_GAP_MS)) {
      this.gesture = value;
      this.since = time;
      this.frames = value ? 1 : 0;
    } else this.frames += 1;
    this.previous = time;
    if (!value || this.frames < MIN_FRAMES || time - this.since < (value === 'released' ? RELEASE_MS : STABLE_MS)) return null;
    if (value === 'released') {
      this.latched = null;
      return 'released';
    }
    if (this.latched === value) return null;
    this.latched = value;
    return value;
  }
}

export class Duel {
  constructor(order = DUEL_ORDER, duration = ROUND_MS) {
    this.order = [...order];
    this.duration = duration;
    this.results = [];
    this.started = null;
    this.index = 0;
  }

  get player() { return this.results.length + 1; }
  get finished() { return this.results.length === 2; }
  get target() { return this.order[this.index]; }

  start(now) {
    if (this.finished || this.started !== null || !Number.isFinite(now)) return false;
    this.started = now;
    this.index = 0;
    return true;
  }

  remaining(now) {
    return this.started === null ? this.duration : Math.max(0, this.duration - Math.max(0, now - this.started));
  }

  update(gesture, now) {
    if (this.started === null || !Number.isFinite(now) || now < this.started) return null;
    if (this.remaining(now) === 0) {
      this.finish(this.duration);
      return 'timeout';
    }
    if (!gesture || gesture === 'released') return null;
    if (gesture !== this.target) return 'wrong';
    this.index += 1;
    if (this.index === this.order.length) {
      this.finish(now - this.started);
      return 'complete';
    }
    return 'correct';
  }

  finish(ms) {
    this.results.push({player: this.player, hits: this.index, complete: this.index === this.order.length, ms});
    this.started = null;
    this.index = 0;
  }

  winner() {
    if (!this.finished) return null;
    const [a, b] = this.results;
    if (a.hits !== b.hits) return a.hits > b.hits ? a.player : b.player;
    if (a.complete && b.complete && a.ms !== b.ms) return a.ms < b.ms ? a.player : b.player;
    return 0;
  }
}
