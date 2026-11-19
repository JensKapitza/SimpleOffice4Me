/* Offline PCM acoustic modem. No browser APIs or network access required. */
(function (root) {
  'use strict';

  const MAX_PAYLOAD_BYTES = 128;
  const PREAMBLE_BITS = 24;
  const SYNC = [1, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1];
  const DEFAULT_SYMBOL_MS = 12;
  const FRAME_VERSION = 1;
  const FEC_IDS = Object.freeze({ none: 0, hamming: 1, repetition: 2, repeat3: 2 });

  function bytesOf(text) {
    if (typeof text !== 'string') throw new TypeError('payload must be a string');
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(text);
    return Uint8Array.from(unescape(encodeURIComponent(text)), c => c.charCodeAt(0));
  }
  function stringOf(bytes) {
    if (typeof TextDecoder !== 'undefined') return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    let s = ''; for (const b of bytes) s += String.fromCharCode(b);
    return decodeURIComponent(escape(s));
  }
  function crc16(bytes) {
    let crc = 0xffff;
    for (const byte of bytes) {
      crc ^= byte << 8;
      for (let i = 0; i < 8; i++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
    return crc;
  }
  function bitsFromBytes(bytes) {
    const bits = [];
    for (const b of bytes) for (let i = 7; i >= 0; i--) bits.push((b >> i) & 1);
    return bits;
  }
  function bytesFromBits(bits) {
    const out = new Uint8Array(bits.length >> 3);
    for (let i = 0; i < out.length; i++) {
      let b = 0; for (let j = 0; j < 8; j++) b = (b << 1) | bits[i * 8 + j];
      out[i] = b;
    }
    return out;
  }
  function params(options) {
    const o = options || {};
    const sampleRate = Number(o.sampleRate || 48000);
    const band = o.band || (sampleRate >= 40000 ? 'ultrasound' : 'audible');
    if (!Number.isFinite(sampleRate) || sampleRate < 8000) throw new RangeError('sampleRate must be at least 8000 Hz');
    if (band !== 'ultrasound' && band !== 'audible') throw new RangeError('band must be ultrasound or audible');
    if (band === 'ultrasound' && sampleRate < 40000) throw new RangeError('ultrasound band requires sampleRate >= 40000 Hz');
    const f0 = o.frequencies ? o.frequencies[0] : band === 'ultrasound' ? 18000 : 1200;
    const f1 = o.frequencies ? o.frequencies[1] : band === 'ultrasound' ? 19500 : 2200;
    const symbolMs = Number(o.symbolMs || DEFAULT_SYMBOL_MS);
    const symbolDuration = sampleRate * symbolMs / 1000;
    const symbolSamples = Math.round(symbolDuration);
    if (symbolSamples < 64 || !(f0 > 0 && f1 > f0 && f1 < sampleRate / 2)) throw new RangeError('invalid modem parameters');
    return { sampleRate, band, f0, f1, symbolSamples, symbolDuration, amplitude: o.amplitude == null ? 0.55 : Number(o.amplitude) };
  }
  function fecApi() {
    if (!root.AcousticFEC) throw new Error('AcousticFEC must be loaded before acoustic_codec.js');
    return root.AcousticFEC;
  }
  function fecId(mode) {
    mode = mode == null ? 'hamming' : mode;
    if (!Object.prototype.hasOwnProperty.call(FEC_IDS, mode)) throw new RangeError('fec must be hamming, repeat3, or none');
    return FEC_IDS[mode];
  }
  function fecName(id) { return id === 0 ? 'none' : id === 1 ? 'hamming' : id === 2 ? 'repetition' : null; }
  function frameBits(payload, options) {
    if (payload.length > MAX_PAYLOAD_BYTES) throw new RangeError('payload exceeds 128 UTF-8 bytes');
    const id = fecId(options.fec);
    const header = new Uint8Array([FRAME_VERSION, id, payload.length >> 8, payload.length & 255]);
    const integrityInput = new Uint8Array(header.length + payload.length);
    integrityInput.set(header); integrityInput.set(payload, header.length);
    const crc = crc16(integrityInput);
    const protectedData = new Uint8Array(payload.length + 2);
    protectedData.set(payload); protectedData[payload.length] = crc >> 8; protectedData[payload.length + 1] = crc & 255;
    const coded = fecApi().encode(protectedData, fecName(id));
    const bits = new Array(PREAMBLE_BITS).fill(0).map((_, i) => i & 1 ? 0 : 1);
    bits.push(...SYNC, ...bitsFromBytes(header), ...bitsFromBytes(coded));
    return bits;
  }
  function measure(text, fec) {
    const payload = bytesOf(text);
    const fits = payload.length <= MAX_PAYLOAD_BYTES;
    const durationMs = fits ? frameBits(payload, { fec }).length * DEFAULT_SYMBOL_MS : null;
    return { bytes: payload.length, fits, durationMs };
  }

  function encode(text, options) {
    const p = params(options), bits = frameBits(bytesOf(text), options || {});
    const result = new Float32Array(Math.round(bits.length * p.symbolDuration));
    let phase = 0;
    for (let i = 0; i < bits.length; i++) {
      const frequency = bits[i] ? p.f1 : p.f0;
      const step = 2 * Math.PI * frequency / p.sampleRate;
      const offset = Math.round(i * p.symbolDuration);
      const end = Math.round((i + 1) * p.symbolDuration);
      for (let j = 0; j < end - offset; j++) {
        result[offset + j] = p.amplitude * Math.sin(phase);
        phase += step;
        if (phase > 2 * Math.PI) phase -= 2 * Math.PI;
      }
    }
    return result;
  }
  function tonePower(samples, offset, length, frequency, sampleRate) {
    const w = 2 * Math.PI * frequency / sampleRate, c = 2 * Math.cos(w);
    let q0, q1 = 0, q2 = 0;
    for (let i = 0; i < length; i++) {
      q0 = c * q1 - q2 + samples[offset + i]; q2 = q1; q1 = q0;
    }
    return q1 * q1 + q2 * q2 - c * q1 * q2;
  }
  function decision(samples, offset, p) {
    const a = tonePower(samples, offset, p.symbolSamples, p.f0, p.sampleRate);
    const b = tonePower(samples, offset, p.symbolSamples, p.f1, p.sampleRate);
    return b > a ? 1 : 0;
  }
  function symbolDecision(samples, base, index, p) {
    const start = base + Math.round(index * p.symbolDuration);
    const end = base + Math.round((index + 1) * p.symbolDuration);
    return decision(samples, start, Object.assign({}, p, { symbolSamples: end - start }));
  }

  class Decoder {
    constructor(options) {
      this.options = options || {};
      fecId(this.options.fec);
      this.p = params(options);
      this.reset();
    }
    reset() { this.buffer = new Float32Array(0); this.used = 0; this.scanAt = 0; }
    push(chunk) {
      if (!chunk || typeof chunk.length !== 'number') throw new TypeError('chunk must be an array of PCM samples');
      const needed = this.used + chunk.length;
      if (needed > this.buffer.length) {
        const grown = new Float32Array(Math.max(needed, this.buffer.length * 2));
        grown.set(this.buffer.subarray(0, this.used));
        this.buffer = grown;
      }
      this.buffer.set(chunk, this.used);
      this.used = needed;
      const p = this.p, unit = p.symbolSamples, leadCount = PREAMBLE_BITS + SYNC.length;
      const scanStep = Math.max(1, Math.floor(unit / 8));
      while (this.scanAt + Math.round(leadCount * p.symbolDuration) <= this.used) {
        const start = this.scanAt;
        let valid = true;
        for (let i = 0; i < PREAMBLE_BITS; i++) {
          if (symbolDecision(this.buffer, start, i, p) !== (i & 1 ? 0 : 1)) { valid = false; break; }
        }
        if (valid) for (let i = 0; i < SYNC.length; i++) {
          if (symbolDecision(this.buffer, start, PREAMBLE_BITS + i, p) !== SYNC[i]) { valid = false; break; }
        }
        if (valid) {
          if (start + Math.round((leadCount + 32) * p.symbolDuration) > this.used) break;
          const headerBits = [];
          for (let i = 0; i < 32; i++) headerBits.push(symbolDecision(this.buffer, start, leadCount + i, p));
          const header = bytesFromBits(headerBits), length = (header[2] << 8) | header[3];
          const mode = fecName(header[1]);
          if (header[0] === FRAME_VERSION && mode && header[1] === fecId(this.options.fec) && length <= MAX_PAYLOAD_BYTES) {
            const fec = fecApi(), codedBytes = fec.encodedLength(length + 2, mode);
            const totalBits = 32 + codedBytes * 8, totalEnd = start + Math.round((leadCount + totalBits) * p.symbolDuration);
            if (totalEnd > this.used) break;
            const allBits = [];
            for (let i = 0; i < codedBytes * 8; i++) allBits.push(symbolDecision(this.buffer, start, leadCount + 32 + i, p));
            const decoded = fec.decode(bytesFromBits(allBits), mode), frame = decoded.data;
            const got = (frame[frame.length - 2] << 8) | frame[frame.length - 1];
            const check = new Uint8Array(header.length + length);
            check.set(header); check.set(frame.subarray(0, length), header.length);
            if (decoded.uncorrectable === 0 && crc16(check) === got) {
              const message = stringOf(frame.subarray(0, length));
              this.reset();
              return message;
            }
          }
        }
        this.scanAt += scanStep;
      }
      // Bound memory after noise while retaining enough tail for a partial preamble/frame.
      if (this.scanAt > unit * (PREAMBLE_BITS + SYNC.length)) {
        const drop = this.scanAt - unit * (PREAMBLE_BITS + SYNC.length);
        this.buffer.copyWithin(0, drop, this.used);
        this.used -= drop;
        this.scanAt -= drop;
      }
      return null;
    }
  }

  root.AcousticCodec = Object.freeze({ encode, measure, Decoder, crc16, MAX_PAYLOAD_BYTES, MAX_BYTES: MAX_PAYLOAD_BYTES, DEFAULT_SYMBOL_MS, FEC_MODES: Object.freeze(['hamming', 'repeat3', 'none']) });
  if (typeof module !== 'undefined' && module.exports) module.exports = root.AcousticCodec;
})(typeof globalThis !== 'undefined' ? globalThis : this);
