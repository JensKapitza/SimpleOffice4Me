// This single-use hand-only mode never reads gaze or emits a generic click.
export const HAND_DESTINATIONS = Object.freeze(['dashboard', 'documents']);
export const FRAME_DELAY = 190;
export const ARM_DURATION = 12000;
const MAX_GAP = 260;
const RELEASE_MS = 330;
const HOLD_MS = 380;
const ENOUGH_FRAMES = 4;
const complete = (t, group, duration) => group?.n >= ENOUGH_FRAMES && t - group.first >= duration;

export class HandShortcut {
  constructor() { this.stop(); }

  stop() {
    this.state = 'inactive';
    this.destination = null;
    this.group = null;
    this.last = null;
    this.lastCompleted = null;
  }

  arm(destination, time) {
    if (this.state !== 'inactive' || !HAND_DESTINATIONS.includes(destination) || !Number.isFinite(time)) return false;
    this.state = 'release';
    this.destination = destination;
    this.armedTime = time;
    return true;
  }

  expire(now, presented = null) {
    if (this.state === 'inactive') return false;
    if (!Number.isFinite(now) || now - this.armedTime >= ARM_DURATION) {
      this.stop();
      return true;
    }
    // Preserve the wall-time stall check when no current presentation
    // exists. A valid in-flight frame is separate from the prior frame gap.
    const latest = this.last === null ? null : Math.max(this.last,
      Number.isFinite(presented) && presented <= now ? presented : this.last);
    if ((latest !== null && now - latest > MAX_GAP) ||
        (this.lastCompleted !== null && now - this.lastCompleted > MAX_GAP)) {
      this.state = 'release';
      this.group = null;
    }
    return false;
  }

  observe(observation, time, now) {
    const fresh = Number.isFinite(time) && time > this.armedTime && time <= now &&
      now - time <= FRAME_DELAY && (this.last === null || time > this.last);
    if (this.expire(now, fresh ? time : null) || this.state === 'inactive') return null;
    if (!fresh) {
      this.state = 'release';
      this.group = null;
      return null;
    }
    if (this.last !== null && time - this.last > MAX_GAP) {
      this.state = 'release';
      this.group = null;
    }
    this.last = time;
    this.lastCompleted = now;
    const noHand = observation?.signal === 'none';
    const sign = observation?.signal === 'recognized' && observation.gesture === 'spark';
    if (!(noHand || sign)) {
      this.group = null;
      this.state = 'release';
      return null;
    }
    const kind = noHand ? 'absent' : 'spark';
    this.group = this.group?.kind === kind
      ? {...this.group, n: this.group.n + 1}
      : {kind, first: time, n: 1};
    if (this.state === 'release' && noHand && complete(time, this.group, RELEASE_MS)) this.state = 'armed';
    if (this.state === 'armed' && sign && complete(time, this.group, HOLD_MS)) {
      const destination = this.destination;
      this.stop();
      return destination;
    }
    return null;
  }
}


// Muting as an explicit property precedes delayed playback (the model and
// permission both resolve after the initiating click). No autoplay grant is
// needed, and the shared controller still tears down a rejected attempt.
export async function playHandPreview(video, stream) {
  video.muted = true;
  video.playsInline = true;
  video.srcObject = stream;
  await video.play();
}
