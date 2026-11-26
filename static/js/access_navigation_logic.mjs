// Camera-free logic. Only paired synthetic samples are used in the private proof.
export const TARGETS = Object.freeze(['dashboard', 'documents', 'calendar']);
export const CALIBRATION = Object.freeze([
  [0.20, 0.25], [0.80, 0.25], [0.20, 0.75], [0.80, 0.75], [0.61, 0.44],
].map(Object.freeze));
const FRAME_GAP = 170;
const FRESH = 130;
const CAL_MIN = 7;
const HOLD_FRAMES = 4;
const TARGET_MS = 300;
const RELEASE_MS = 260;
const CONFIRM_MS = 300;
const isPoint = (p) => Array.isArray(p) && p.length === 2 && p.every(Number.isFinite);
const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

// Candidate adapter for the official 478-point convention. It estimates iris
// placement relative to each eye, not a screen point. Closed/occluded eyes or
// disagreement must never count as a release or activation.
export function irisFeature(faces, aspect) {
  if (!Array.isArray(faces) || faces.length !== 1 || !Number.isFinite(aspect) ||
      aspect < 0.5 || aspect > 4) return null;
  const p = faces[0];
  if (!Array.isArray(p) || p.length !== 478) return null;
  function eye([first, last, upper, lower, iris]) {
    const coords = [first, last, upper, lower, iris].map((id) => {
      const v = p[id];
      return [v?.x, v?.y];
    });
    if (!coords.every((point) => isPoint(point) && point.every((n) => n >= 0 && n <= 1))) return null;
    const [a, b, u, l, c] = coords.map(([x, y]) => [x * aspect, y]);
    const dx = b[0] - a[0], dy = b[1] - a[1], w = Math.hypot(dx, dy);
    if (w < 0.035 || dx < w * 0.85) return null;
    const across = ([x, y]) => ((x - a[0]) * dx + (y - a[1]) * dy) / w / w;
    const down = ([x, y]) => (-(x - a[0]) * dy + (y - a[1]) * dx) / w / w;
    const opening = down(l) - down(u);
    const x = across(c), y = down(c) - (down(u) + down(l)) / 2;
    return opening >= .12 && opening <= .7 && x >= .13 && x <= .87 &&
      Math.abs(y) <= opening * .65 ? [x, y] : null;
  }
  const a = eye([33, 133, 159, 145, 468]);
  const b = eye([362, 263, 386, 374, 473]);
  if (!a || !b || Math.abs(a[0] - b[0]) > .16 || Math.abs(a[1] - b[1]) > .12) return null;
  return a.map((value, i) => (value + b[i]) / 2);
}

function calibrationMap([tl, tr, bl, br, check]) {
  const avg = (a, b) => a.map((n, i) => (n + b[i]) / 2);
  const delta = (a, b) => a.map((n, i) => n - b[i]);
  const [u, v] = [delta(avg(tr, br), avg(tl, bl)), delta(avg(bl, br), avg(tl, tr))];
  const centre = avg(avg(tl, tr), avg(bl, br));
  const determinant = u[0] * v[1] - u[1] * v[0];
  if (Math.hypot(...u) < .05 || Math.hypot(...v) < .03 ||
      Math.abs(determinant) < .45 * Math.hypot(...u) * Math.hypot(...v)) return null;
  function project(feature) {
    const [x, y] = delta(feature, centre);
    return [.5 + .6 * (x * v[1] - y * v[0]) / determinant,
      .5 + .5 * (u[0] * y - u[1] * x) / determinant];
  }
  // Point 5 is never used to fit the map. No map if any of the five disagree.
  return [tl, tr, bl, br, check].every((p, i) => distance(project(p), CALIBRATION[i]) <= (i === 4 ? .075 : .045))
    ? project : null;
}

function count(last, value, now) {
  return last?.value === value
    ? {value, frames: last.frames + 1, since: last.since}
    : {value, frames: 1, since: now};
}
const held = (sample, now, duration) => sample && sample.frames >= HOLD_FRAMES && now - sample.since >= duration;

export class AccessSelection {
  #project;
  #points;
  #window;
  #previous;
  #candidate;
  #release;
  #confirm;
  #ready;

  constructor() { this.stop(); }

  #resetSignals() {
    this.#candidate = this.#release = this.#confirm = null;
    this.#ready = false;
    this.target = null;
  }

  stop(state = 'inactive') {
    this.#resetSignals();
    this.#project = null;
    this.#points = [];
    this.#window = null;
    this.#previous = null;
    this.state = state === 'paused' ? 'paused' : 'inactive';
  }

  startCalibration() {
    this.stop();
    this.state = 'calibrating';
  }

  get step() { return this.#points.length; }

  capture() {
    if (this.state !== 'calibrating' || this.#window) return false;
    this.#window = [];
    return true;
  }

  observe({gaze, hand, capturedAt}, now, targetAt) {
    if (!['calibrating', 'ready', 'armed'].includes(this.state)) return null;
    const goodTime = Number.isFinite(capturedAt) && Number.isFinite(now) && now >= capturedAt &&
      now - capturedAt <= FRESH && (this.#previous === null || capturedAt > this.#previous);
    const gap = this.#previous !== null && capturedAt - this.#previous > FRAME_GAP;
    if (goodTime) this.#previous = capturedAt;
    if (!goodTime || gap || !isPoint(gaze)) {
      this.#resetSignals();
      if (this.#window) this.#window = [];
      return null;
    }
    if (this.state === 'calibrating') {
      if (this.#window === null) return null;
      this.#window.push({gaze: [...gaze], time: capturedAt});
      const width = capturedAt - this.#window[0].time;
      if (width > 1100) { this.#window = []; return 'steady'; }
      if (this.#window.length < CAL_MIN || width < 450) return null;
      const mean = [0, 1].map((i) => this.#window.reduce((sum, frame) => sum + frame.gaze[i], 0) / this.#window.length);
      if (this.#window.some((frame) => distance(frame.gaze, mean) > .018)) {
        this.#window = [];
        return 'steady';
      }
      this.#window = null;
      this.#points.push(mean);
      if (this.#points.length < CALIBRATION.length) return 'next';
      this.#project = calibrationMap(this.#points);
      this.#points = [];
      if (!this.#project) return 'recalibrate';
      this.state = 'ready';
      return 'ready';
    }

    if (this.state === 'ready') {
      if (hand === 'star') {
        this.#confirm = null;
        if (!held(this.#release, capturedAt, RELEASE_MS)) this.#release = count(this.#release, hand, capturedAt);
        this.#ready = held(this.#release, capturedAt, RELEASE_MS);
      } else if (hand === 'crystal' && this.#ready) {
        this.#confirm = count(this.#confirm, hand, capturedAt);
        if (held(this.#confirm, capturedAt, CONFIRM_MS)) {
          this.#resetSignals();
          this.state = 'armed';
          return 'armed';
        }
      } else this.#resetSignals();
      return null;
    }

    const cursor = this.#project(gaze);
    const id = cursor.every((x) => x >= 0 && x <= 1) ? targetAt(cursor) : null;
    if (!TARGETS.includes(id)) { this.#resetSignals(); return null; }
    if (id !== this.#candidate?.value || distance(cursor, this.#candidate.origin) > .055) this.#resetSignals();
    const origin = this.#candidate?.origin ?? cursor;
    this.#candidate = {...count(this.#candidate, id, capturedAt), origin};
    if (!held(this.#candidate, capturedAt, TARGET_MS)) return null;
    this.target = id;
    if (hand === 'star') {
      this.#confirm = null;
      if (!held(this.#release, capturedAt, RELEASE_MS)) this.#release = count(this.#release, hand, capturedAt);
      this.#ready = held(this.#release, capturedAt, RELEASE_MS);
    } else if (hand === 'shield' && this.#ready) {
      this.#confirm = count(this.#confirm, hand, capturedAt);
      if (held(this.#confirm, capturedAt, CONFIRM_MS)) {
        this.#release = this.#confirm = null;
        this.#ready = false;
        return id;
      }
    } else {
      this.#release = this.#confirm = null;
      this.#ready = false;
    }
    return null;
  }

  expire(now) {
    if (this.#previous === null || !Number.isFinite(now) || now - this.#previous > FRESH) {
      this.#resetSignals();
      if (this.#window) this.#window = [];
    }
  }
}
