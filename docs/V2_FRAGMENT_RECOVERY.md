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
Presence alone is insufficient. Physical fragment IDs inside one recovery set
must also be unique so that one stored fragment cannot be counted twice.

## Encoding and reconstruction

The V2 core now provides the complete codec-neutral pipeline:

- `encode_recovery_set(...)` validates the selected codec contract, creates
  `n` opaque physical identities, records per-fragment integrity and emits a
  portable versioned recovery set.
- `assess_fragments(...)` distinguishes valid, corrupt and missing fragments.
- `recover_payload(...)` passes only valid fragments to the codec and verifies
  both reconstructed size and the whole-content SHA-256 before returning data.
- `recovery_set_to_dict(...)` and `recovery_set_from_dict(...)` provide the
  stable portable descriptor representation.

Unknown schemas, codec names or codec versions fail closed.

## Production codec adapter

The core still defines the `ErasureCodec` protocol and does not implement
Reed-Solomon/Galois-field mathematics itself.

A production adapter is available for **zfec** using persisted codec contract
`zfec/1`. The package remains optional:

```text
pip install -e ".[erasure]"
```

This keeps native-code requirements out of the unconditional base install and
out of Android/portable builds that do not need erasure coding. The persisted
`zfec/1` value is the SimpleOffice adapter contract; it is deliberately not a
copy of the Python package patch version.

The dependency decision was revisited on 2026-09-23. zfec 1.6.x provides the
required direct k-of-n share model and published wheels for several current
CPython/platform combinations, while source installs still require a native
toolchain. Therefore it is suitable as an opt-in production adapter, not as a
mandatory core dependency.

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

## Privacy boundary

Per-fragment SHA-256 values and the whole-content digest are integrity metadata
inside the authorized recovery descriptor. They are **not** public
federation/deduplication identifiers.

Privacy-preserving deduplication remains a separate protocol boundary. Neither
these hashes nor fragment IDs may be exposed as a general existence oracle.

## Compatibility

The recovery-set schema is `simpleoffice-v2-fragments/v1`. Unknown schema or
codec versions fail closed. A recovery tool must only reconstruct a set when it
has a registered codec implementation matching the persisted codec/version.
