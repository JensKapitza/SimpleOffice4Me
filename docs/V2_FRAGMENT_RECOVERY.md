# V2 fragment and erasure-recovery contract

Phase 6 separates **integrity**, **redundancy** and **confidentiality**.

## Model

A recovery set contains:

- stable recovery identity
- logical object and immutable version identity
- codec name/version
- explicit `k` and `n`
- original size and codec padding
- whole-content integrity digest
- exactly `n` fragment descriptors
- per-fragment opaque physical id, size and SHA-256 integrity digest

Fragment state is one of:

- `present_valid`
- `present_corrupt`
- `missing`

A set is reconstructable only when at least `k` fragments are **valid**.
Presence alone is insufficient.

## Codec boundary

The core defines an `ErasureCodec` protocol but deliberately does not
implement Reed-Solomon/Galois-field mathematics itself.

The dependency review on 2026-09-19 found:

- `zfec` has the desired direct k-of-n shard model, but its published package
  metadata currently limits Python to below 3.13 and it includes native C build
  requirements. That does not fit an unconditional core dependency for the
  project's open-ended Python >=3.10 and Android/portable build targets.
- `reedsolo` is mature Reed-Solomon error correction, but its high-level API is
  not a direct drop-in replacement for independently stored k-of-n shards.

Therefore the first stable format records codec name/version explicitly and
keeps the codec pluggable. A production codec adapter can be added when its
Python/platform/build matrix is acceptable and must pass cross-version recovery
fixtures before it becomes a persisted default.

## Security ordering

For distributed confidential storage the intended order is:

1. prepare/version plaintext in memory,
2. encrypt/authenticate using the V2 crypto service,
3. erasure-code the ciphertext,
4. authenticate/hash every physical fragment,
5. distribute only explicitly authorized fragments.

Recovery reverses this order after verifying each fragment.

Storage peers therefore do not need plaintext or CEKs to hold recovery
fragments.

## Compatibility

The recovery-set schema is `simpleoffice-v2-fragments/v1`. Unknown schema or
codec versions fail closed. The independent recovery CLI will only reconstruct a
set when it has a registered codec implementation matching the persisted
codec/version.
