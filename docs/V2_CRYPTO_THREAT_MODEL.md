# V2 cryptography threat model

This document defines the minimum threat model for V2 crypto work in #314/#306.
It intentionally contains no operational keys, credentials or private deployment
details.

## Assets

- plaintext document/blob content
- content-encryption keys (CEKs)
- user master keys
- offline recovery keys
- future trustee/emergency key material
- encrypted manifests and authorization metadata

## Adversaries considered

1. A party obtains copied encrypted blob/chunk files but no usable key material.
2. A storage/relay peer is allowed to retain or forward ciphertext but has no
   read permission.
3. A local disk or backup is copied after application shutdown.
4. An attacker modifies, truncates, reorders or substitutes ciphertext,
   wrapped keys, manifests or nonces.
5. An attacker knows some plaintext/ciphertext pairs.
6. A user password is guessed offline from a copied protected master-key record.
7. An old master key is rotated and should no longer protect newly wrapped CEKs.
8. Logs, errors or audit events are inspected by an operator without vault read
   permission.

## Explicit non-goals for this phase

- protection from a fully compromised process while plaintext keys are unlocked
- hardware-backed key storage
- remote attestation
- post-quantum cryptography
- full OPRF/PSI-based cross-peer deduplication beyond the session-scoped equality protocol
- trustee quorum policy
- browser extension protocol

Those require separate designs rather than weakening this base layer.

## Key hierarchy

- **CEK:** random 256-bit key per encrypted payload/version.
- **User master key:** random 256-bit key used only to wrap/unwrap CEKs.
- **Password-derived key:** Argon2id output used only to protect a user master
  key. The login password is never itself a master key.
- **Offline recovery key:** independent random 256-bit key that may protect the
  same master key in a separate record.

Password change therefore replaces only the protected-master-key record.
Master-key rotation rewraps CEKs without re-encrypting payload bytes.

## Algorithms and policy

- Payload AEAD: AES-256-GCM from `cryptography`.
- Large/storage payloads may use independently authenticated chunks with one
  random CEK per payload/version. Each chunk uses a fresh 96-bit nonce and binds
  purpose, chunk index and plaintext size as associated data.
- Key wrapping: AES-256-GCM with an independent random nonce and domain-specific
  associated data.
- Password KDF: Argon2id using the project dependency, with parameters encoded
  by policy and validated when unlocking.
- Randomness: operating-system CSPRNG through `os.urandom`.
- No deterministic ciphertext and no content hash as an encryption key.

This is composition of established primitives; no custom cipher, MAC or KDF is
introduced.

## Domain separation

Associated data uses separate fixed domains for:

- payload encryption
- chunk-level payload encryption
- CEK wrapping
- password protection of the master key
- recovery-key protection of the master key

The caller additionally supplies a bounded purpose string for payload and CEK
wrapping. A wrapped key for one purpose cannot be silently reused for another.

## Nonce rules

Every AES-GCM operation receives a fresh 96-bit random nonce generated at the
operation boundary. Callers cannot provide or reuse nonces through the public
service API.

For chunked payloads the caller cannot provide a nonce either. The chunk index
is authenticated but is not used as a nonce. Re-encrypting the same chunk
therefore produces unrelated ciphertext.

## Integrity and substitution resistance

AEAD authentication failure is fatal. Ciphertext, nonce, purpose or wrapped-key
substitution must fail closed. The blob manifest integrity layer remains
separate: storage hashes detect corruption before/without decryption while AEAD
authenticates protected content cryptographically.

Chunked encryption additionally authenticates the expected chunk index and
plaintext length. Reordering, truncation or substitution must fail before
plaintext is accepted. The storage layer still needs a final object-level size
and digest check so omission of a complete authenticated chunk cannot be hidden
by otherwise valid neighboring chunks.

## Logging and audit

The crypto API returns ciphertext records only. Plaintext keys, passwords and
recovery keys must not be serialized into normal logs, audit events, URLs,
search indexes or exception messages.

Audit should record events such as rotation/recovery by object/key identifier
and actor, not key bytes.

## Rotation and revocation

- Password change: derive a new password-protection key and protect the same
  user master key again.
- Master-key rotation: authenticate/unwrap each CEK using the old master key and
  rewrap it under the new master key; payload ciphertext remains unchanged.
- Recovery-key rotation: create a new recovery protection record for the master
  key, then retire the old record/key according to recovery policy.

Revocation semantics across devices/federation are a later authorization layer.

## Session-scoped federation deduplication

V2 cross-peer block reuse must not publish stable block hashes as equality
identifiers. The transitional V2 protocol derives short-lived HMAC equality
tokens from the existing peer secret and a signed, blob-bound session nonce.

Properties:

- the same local block produces the same token only inside one short session,
- a new session produces unrelated tokens for the same block,
- sessions are bound to one blob hash and expire after a short lifetime,
- the V2 manifest exposes scoped tokens but no SHA-512 block hashes,
- block download requires the session plus the exact scoped proof for the
  requested block index,
- there is no V2 arbitrary block-availability endpoint.

The old SOFP v1 raw SHA-512 block interface remains compatibility-only for old
peers. Current V2 download code does not use it. If a peer does not support the
scoped V2 path, the client falls back to the ordinary per-blob chunk transfer
instead of the raw cross-file hash-dedup path.

This is a bounded transitional equality protocol, not an OPRF or PSI. A peer
that already has both the shared federation secret and a scoped manifest can
still test candidate content within that authorized session. Stronger
cryptographic private-set-intersection remains separate future work.

## Required tests

- same plaintext encrypted twice produces different ciphertext
- wrong master/password/recovery key fails
- modified ciphertext fails authentication
- chunk index/size substitution fails authentication
- repeated chunk plaintext produces different ciphertext
- purpose substitution fails
- master-key rotation changes only wrapped-key material
- password/recovery protection round-trips
- no test fixture contains production secrets
