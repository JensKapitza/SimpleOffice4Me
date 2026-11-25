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
  }

  arm(destination, time) {
    if (this.state !== 'inactive' || !HAND_DESTINATIONS.includes(destination) || !Number.isFinite(time)) return false;
    this.state = 'release';
    this.destination = destination;
    this.armedTime = time;
    return true;
  }

  expire(now) {
    if (this.state === 'inactive') return false;
    if (!Number.isFinite(now) || now - this.armedTime >= ARM_DURATION) {
      this.stop();
      return true;
    }
    if (this.last !== null && now - this.last > MAX_GAP) {
      this.state = 'release';
      this.group = null;
    }
    return false;
  }

  observe(observation, time, now) {
    if (this.expire(now) || this.state === 'inactive') return null;
    if (!Number.isFinite(time) || time <= this.armedTime || time > now ||
        now - time > FRAME_DELAY || (this.last !== null && time <= this.last)) {
      this.state = 'release';
      this.group = null;
      return null;
    }
    this.last = time;
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
