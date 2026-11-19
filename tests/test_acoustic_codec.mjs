import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
require('../static/js/acoustic_fec.js');
const Codec = require('../static/js/acoustic_codec.js');

test('round trips UTF-8 over ultrasound PCM with uneven streaming chunks', () => {
  const options = { sampleRate: 48000, band: 'ultrasound' };
  const message = 'Hello, acoustic modem — café 🌍';
  const pcm = Codec.encode(message, options);
  const decoder = new Codec.Decoder(options);
  let result = null;
  // Deliberately split in non-symbol-aligned pieces.
  for (let at = 0; at < pcm.length; at += 997) {
    const found = decoder.push(pcm.subarray(at, Math.min(pcm.length, at + 997)));
    if (found !== null) result = found;
  }
  assert.equal(result, message);
});

test('uses audible FSK when sample rate is below ultrasound range', () => {
  const options = { sampleRate: 22050, band: 'audible' };
  const pcm = Codec.encode('fallback', options);
  const decoder = new Codec.Decoder(options);
  assert.equal(decoder.push(pcm), 'fallback');
});

test('rejects oversized UTF-8 payloads and invalid CRC frames', () => {
  assert.throws(() => Codec.encode('x'.repeat(Codec.MAX_PAYLOAD_BYTES + 1)), /128/);
  const options = { sampleRate: 48000, band: 'ultrasound', fec: 'none' };
  const pcm = Codec.encode('integrity check', options);
  // Replace a complete payload symbol with the opposite FSK tone.
  const altered = pcm.slice();
  const symbolSamples = Math.round(options.sampleRate * Codec.DEFAULT_SYMBOL_MS / 1000);
  const symbolIndex = 40 + 32; // preamble/sync, then 32-bit frame header
  const start = symbolIndex * symbolSamples;
  const step = 2 * Math.PI * 19500 / options.sampleRate;
  for (let i = 0; i < symbolSamples; i++) altered[start + i] = 0.55 * Math.sin(step * i);
  const decoder = new Codec.Decoder(options);
  assert.equal(decoder.push(altered), null);
});

test('Hamming FEC corrects a corrupted coded bit before CRC validation', () => {
  const options = { sampleRate: 48000, band: 'ultrasound', fec: 'hamming' };
  const message = 'error correction';
  const pcm = Codec.encode(message, options);
  const altered = pcm.slice();
  const symbolSamples = Math.round(options.sampleRate * Codec.DEFAULT_SYMBOL_MS / 1000);
  // First coded bit, after preamble, sync, 32-bit header, and one byte.
  const symbolIndex = 40 + 32 + 8;
  const start = symbolIndex * symbolSamples;
  const step = 2 * Math.PI * 19500 / options.sampleRate;
  for (let i = 0; i < symbolSamples; i++) altered[start + i] = 0.55 * Math.sin(step * i);
  assert.equal(new Codec.Decoder(options).push(altered), message);
});

test('resampled maximum-length messages survive both sample-rate directions', () => {
  for (const [from, to, band, fec] of [[44100, 48000, 'ultrasound', 'repeat3'], [48000, 44100, 'audible', 'hamming']]) {
    const message = 'ä'.repeat(64);
    const pcm = Codec.encode(message, { sampleRate: from, band, fec });
    const lead = 5371;
    const signal = new Float32Array(lead + Math.ceil(pcm.length * to / from) + 4096);
    for (let i = 0; i < pcm.length * to / from; i++) {
      const at = i * from / to, left = Math.floor(at), fraction = at - left;
      signal[lead + i] = pcm[left] * (1 - fraction) + (pcm[left + 1] || 0) * fraction + 0.008 * Math.sin(i);
    }
    const decoder = new Codec.Decoder({ sampleRate: to, band, fec });
    let decoded = null;
    for (let i = 0; i < signal.length && decoded === null; i += 4096) {
      decoded = decoder.push(signal.subarray(i, i + 4096));
    }
    assert.equal(decoded, message);
  }
});

test('rejects a valid transmission with mismatched receiver correction', () => {
  const pcm = Codec.encode('test', { fec: 'repeat3' });
  assert.equal(new Codec.Decoder({ fec: 'hamming' }).push(pcm), null);
});

test('transfer budget matches the encoded duration for every correction mode', () => {
  for (const fec of Codec.FEC_MODES) {
    const text = 'Aß🌍';
    const size = Codec.measure(text, fec);
    assert.equal(size.bytes, 7);
    assert.equal(size.durationMs, Codec.encode(text, { fec, sampleRate: 48000 }).length / 48);
  }
  assert.equal(Codec.measure('ä'.repeat(65), 'none').fits, false);
});
