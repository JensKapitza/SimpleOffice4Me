import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const fec = require('../static/js/acoustic_fec.js');

const input = Uint8Array.from([0x00, 0x5a, 0xff, 0x83]);

// No FEC is byte preserving and returns an independent buffer.
const plain = fec.encode(input, 'none');
assert.deepEqual(plain, input);
assert.notEqual(plain, input);
assert.deepEqual(fec.decode(plain, 0), { data: input, corrected: 0, uncorrectable: 0 });
assert.equal(fec.encodedLength(input.length, 'none'), input.length);

// Hamming SECDED corrects each single bit in each of a byte's two codewords.
const hamming = fec.encode(input, 'hamming');
assert.equal(hamming.length, fec.encodedLength(input.length, 1));
for (let position = 0; position < hamming.length; position++) {
  const damaged = hamming.slice();
  damaged[position] ^= 1 << (position % 8);
  const decoded = fec.decode(damaged, 'hamming');
  assert.deepEqual(decoded.data, input);
  assert.equal(decoded.uncorrectable, 0);
  assert.equal(decoded.corrected, 1);
}

// Two flips in one extended-Hamming codeword are detected, not silently corrected.
const doubled = hamming.slice();
doubled[0] ^= 0x03;
const doubleResult = fec.decode(doubled, 'hamming');
assert.equal(doubleResult.uncorrectable, 1);
assert.equal(doubleResult.corrected, 0);

// Repetition uses three byte lanes, so a burst confined to one lane is outvoted.
const repeated = fec.encode(input, 'repetition');
assert.equal(repeated.length, fec.encodedLength(input.length, 2));
const burst = repeated.slice();
burst.fill(0xff, 0, input.length);
const repetitionResult = fec.decode(burst, 2);
assert.deepEqual(repetitionResult.data, input);
assert.equal(repetitionResult.corrected, 3);
assert.equal(repetitionResult.uncorrectable, 0);

assert.throws(() => fec.decode(Uint8Array.of(1), 'repetition'), RangeError);
assert.throws(() => fec.encodedLength(-1, 'none'), RangeError);
console.log('acoustic FEC tests passed');
