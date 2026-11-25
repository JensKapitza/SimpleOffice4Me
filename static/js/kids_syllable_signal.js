/* Local time-domain features only. No words, phonemes, speaker data or recordings. */
((scope) => {
  'use strict';
  const LIMIT = 8;
  const FLOOR = .0015;

  function frameFeatures(samples) {
    let sum = 0, squared = 0, peak = 0;
    for (const sample of samples) {
      sum += sample;
      squared += sample * sample;
    }
    const mean = sum / samples.length;
    for (const sample of samples) peak = Math.max(peak, Math.abs(sample - mean));
    const rms = Math.sqrt(Math.max(0, squared / samples.length - mean * mean));
    return {rms, peak, mean};
  }

  function isPeriodic(samples, sampleRate, mean) {
    const stride = Math.max(1, Math.floor(sampleRate / 12000));
    const low = Math.floor(sampleRate / stride / 620);
    const high = Math.min(Math.ceil(sampleRate / stride / 85), Math.floor(samples.length / stride / 3));
    for (let lag = low; lag <= high; lag += 1) {
      let corr = 0, a2 = 0, b2 = 0;
      for (let i = lag * stride; i < samples.length; i += stride * 2) {
        const a = samples[i] - mean;
        const b = samples[i - lag * stride] - mean;
        corr += a * b; a2 += a * a; b2 += b * b;
      }
      if (corr > 0 && a2 > 0 && b2 > 0 && corr / Math.sqrt(a2 * b2) > .72) return true;
    }
    return false;
  }

  function createDetector(mode, sampleRate) {
    let floor = FLOOR, calibration = null, calibrationMs = 0, measured = 0, total = 0;
    let state = 'calibrating', previous = null, quietSince = null, cooldown = 0, segment = null;

    function process(samples, now) {
      if (previous !== null && now <= previous) return {state, hit: false};
      const interval = previous === null ? 0 : now - previous;
      previous = now;
      if (interval > 100) {
        quietSince = null;
        segment = null;
        if (state === 'calibrating') {
          calibration = now; calibrationMs = 0; measured = 0;
        }
      }
      if (!samples?.length) return {state, hit: false};
      const {rms, peak, mean} = frameFeatures(samples);
      if (!Number.isFinite(rms)) return {state, hit: false};
      if (state === 'calibrating') {
        calibration ??= now;
        if (interval > 0 && interval <= 100) {
          calibrationMs += interval;
          measured += rms * interval;
        }
        if (rms < .022) quietSince ??= now;
        else quietSince = null;
        if (calibrationMs >= 320 && quietSince !== null && now - quietSince >= 240) {
          floor = Math.max(FLOOR, Math.min(.012, measured / calibrationMs));
          state = 'listening';
        } else if (now - calibration >= 2200) state = 'noisy';
        return {state, hit: false};
      }
      if (state !== 'listening' || total >= LIMIT) return {state, hit: false};
      const enter = Math.max(.017, floor * 3.4);
      const exit = Math.max(.010, floor * 2.15);
      const quiet = rms < exit;
      if (quiet && !segment) floor = Math.max(FLOOR, Math.min(.012, floor * .992 + rms * .008));

      if (mode === 'clap') {
        if (!segment && rms >= enter) {
          segment = {start: now, impulse: peak > .085 && peak / rms > 2.35, lastLoud: now};
        } else if (segment && !quiet) {
          segment.lastLoud = now;
          if (now - segment.start > 130) segment.impulse = false;
        }
        if (!segment || !quiet || now - segment.lastLoud < 32) return {state, hit: false};
        const accepted = segment.impulse && segment.start >= cooldown;
        segment = null;
        if (accepted) {
          cooldown = now + 170;
          total += 1;
        }
        return {state, hit: accepted};
      }

      const voiced = rms >= enter && isPeriodic(samples, sampleRate, mean);
      if (voiced) {
        if (!segment) segment = {start: now, voicedMs: 0, lastVoiced: now, quiet: null};
        else if (now - segment.lastVoiced <= 85 && interval <= 70) segment.voicedMs += interval;
        segment.lastVoiced = now;
        segment.quiet = null;
        return {state, hit: false};
      }
      if (!segment) return {state, hit: false};
      if (!quiet) {
        segment.quiet = null;
        // Loud unvoiced noise is not a separator and must not finalize a voice segment.
        if (now - segment.lastVoiced >= 115) segment = null;
        return {state, hit: false};
      }
      segment.quiet ??= now;
      if (now - segment.quiet < 115) return {state, hit: false};
      const accepted = segment.voicedMs >= 92 && segment.lastVoiced - segment.start <= 2000;
      segment = null;
      if (accepted) total += 1;
      return {state, hit: accepted};
    }
    return {process};
  }

  const api = {createDetector, frameFeatures, isPeriodic, LIMIT};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else scope.KidsSyllableSignal = api;
})(typeof window === 'undefined' ? globalThis : window);
