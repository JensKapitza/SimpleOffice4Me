(function (root) {
  'use strict';

  const MODES = Object.freeze({ none: 0, hamming: 1, repetition: 2, repeat3: 2 });

  function modeId(mode) {
    if (typeof mode === 'number' && mode >= 0 && mode <= 2 && Number.isInteger(mode)) return mode;
    if (Object.prototype.hasOwnProperty.call(MODES, mode)) return MODES[mode];
    throw new RangeError('Unknown FEC mode');
  }

  function byteView(input) {
    if (input instanceof Uint8Array) return input;
    if (input instanceof ArrayBuffer) return new Uint8Array(input);
    if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
    if (Array.isArray(input)) return Uint8Array.from(input);
    throw new TypeError('FEC input must be bytes');
  }

  function encodedLength(dataByteLength, mode) {
    if (!Number.isSafeInteger(dataByteLength) || dataByteLength < 0) throw new RangeError('Invalid data length');
    switch (modeId(mode)) {
      case 0: return dataByteLength;
      case 1: return dataByteLength * 2;
      case 2: return dataByteLength * 3;
      default: throw new RangeError('Unknown FEC mode');
    }
  }

  // Extended Hamming (8,4): positions 1,2,4 are parity, 3,5,6,7 data, 8 overall parity.
  function encodeNibble(value) {
    const bits = new Uint8Array(9);
    bits[3] = (value >> 0) & 1;
    bits[5] = (value >> 1) & 1;
    bits[6] = (value >> 2) & 1;
    bits[7] = (value >> 3) & 1;
    bits[1] = bits[3] ^ bits[5] ^ bits[7];
    bits[2] = bits[3] ^ bits[6] ^ bits[7];
    bits[4] = bits[5] ^ bits[6] ^ bits[7];
    bits[8] = bits[1] ^ bits[2] ^ bits[3] ^ bits[4] ^ bits[5] ^ bits[6] ^ bits[7];
    let code = 0;
    for (let i = 1; i <= 8; i++) code |= bits[i] << (i - 1);
    return code;
  }

  function decodeCodeword(code) {
    let s1 = 0, s2 = 0, s4 = 0, parity = 0;
    for (let i = 1; i <= 8; i++) {
      const bit = (code >> (i - 1)) & 1;
      parity ^= bit;
      if (i <= 7) {
        if (i & 1) s1 ^= bit;
        if (i & 2) s2 ^= bit;
        if (i & 4) s4 ^= bit;
      }
    }
    const syndrome = s1 | (s2 << 1) | (s4 << 2);
    let corrected = 0, uncorrectable = 0;
    if (parity && syndrome) {
      code ^= 1 << (syndrome - 1);
      corrected = 1;
    } else if (parity) {
      code ^= 1 << 7;
      corrected = 1;
    } else if (syndrome) {
      uncorrectable = 1;
    }
    const data = (((code >> 2) & 1) | (((code >> 4) & 1) << 1) |
      (((code >> 5) & 1) << 2) | (((code >> 6) & 1) << 3));
    return { data, corrected, uncorrectable };
  }

  function encode(input, mode) {
    const data = byteView(input), id = modeId(mode);
    if (id === 0) return data.slice();
    if (id === 2) {
      const out = new Uint8Array(data.length * 3);
      for (let i = 0; i < data.length; i++) {
        out[i] = data[i];
        out[i + data.length] = data[i];
        out[i + data.length * 2] = data[i];
      }
      return out;
    }
    const out = new Uint8Array(data.length * 2);
    for (let i = 0; i < data.length; i++) {
      out[i * 2] = encodeNibble(data[i] & 0x0f);
      out[i * 2 + 1] = encodeNibble(data[i] >> 4);
    }
    return out;
  }

  function decode(input, mode) {
    const data = byteView(input), id = modeId(mode);
    if (id === 0) return { data: data.slice(), corrected: 0, uncorrectable: 0 };
    if (data.length % (id === 1 ? 2 : 3)) throw new RangeError('Encoded FEC length is invalid');
    const out = new Uint8Array(data.length / (id === 1 ? 2 : 3));
    let corrected = 0, uncorrectable = 0;
    if (id === 2) {
      for (let i = 0; i < out.length; i++) {
        const a = data[i], b = data[i + out.length], c = data[i + out.length * 2];
        out[i] = (a & b) | (a & c) | (b & c);
        if (a !== b || b !== c) corrected++;
      }
    } else {
      for (let i = 0; i < out.length; i++) {
        const low = decodeCodeword(data[i * 2]);
        const high = decodeCodeword(data[i * 2 + 1]);
        out[i] = low.data | (high.data << 4);
        corrected += low.corrected + high.corrected;
        uncorrectable += low.uncorrectable + high.uncorrectable;
      }
    }
    return { data: out, corrected, uncorrectable };
  }

  const api = Object.freeze({ encode, decode, encodedLength, modes: Object.freeze({ none: 0, hamming: 1, repetition: 2 }) });
  root.AcousticFEC = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
