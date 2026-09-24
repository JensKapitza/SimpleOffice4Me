# V2 encrypted blob store

The encrypted blob store is a side-by-side physical storage format for V2. It
does not replace the existing plaintext BlobStore or switch the live
StoragePort by itself.

## Security boundary

The store receives an already-unlocked 256-bit master key from its caller. It
never persists that raw key. Each blob version creates one random CEK using the
central V2 `CryptoService`; the CEK is wrapped by the master key and stored in
the version manifest.

Content is processed in bounded chunks. Every chunk:

- is encrypted independently with AES-256-GCM,
- receives a fresh random 96-bit nonce,
- authenticates purpose, chunk index and plaintext length,
- is stored under a random opaque physical ID,
- has a ciphertext SHA-256 for corruption diagnosis before decryption.

The plaintext whole-content SHA-256 and final byte count are not stored in
cleartext. They are carried in an encrypted footer authenticated with the same
chunk session. Read completion verifies the footer, chunk count, final byte
count and reconstructed plaintext SHA-256 before returning data.

The public manifest therefore leaks physical size/count information but not the
whole-content plaintext digest.

## Crash behavior

Chunks are written through a private staging transaction and atomically moved
to random physical IDs. The immutable version manifest is published only after
all chunks and the encrypted footer are ready. A crash before the manifest can
leave unreachable ciphertext chunks; those are safe orphans for a later
explicit garbage-collection path.

The current object pointer is written only after the version manifest.

The encrypted store also exposes read-only inventory, version verification,
orphan detection and age-bounded staging cleanup. Orphan cleanup is explicit and
never removes ciphertext referenced by a valid version manifest.

## Key rotation

`rewrap_version_key(...)` unwraps the version CEK with the old master key and
wraps it with the new master key. Ciphertext chunk files are unchanged.

The storage runtime now has a crash-resumable orchestration layer for the
dedicated `v2-local-storage` master key. It journals only protected key
material and version IDs, blocks normal encrypted runtime access while the
journal exists, rewraps every encrypted version, rechecks that the version set
did not change, rotates the profile and offline recovery material, and verifies
that all version CEKs are bound to the new master key before removing the
journal.

A pre-commit interruption can be resumed or explicitly rolled back to the old
master key. Rotation therefore requires a maintenance window but does not
re-encrypt payload chunks.

## StoragePort integration boundary

`EncryptedBlobCatalogStorageAdapter` proves that the existing logical
ObjectCatalog/StoragePort layer can use the encrypted physical backend without
changing logical object IDs, locations, version preconditions or audit behavior.

The adapter receives an already-unlocked master key from its caller. It does not
load keys from environment variables, web sessions or command-line arguments.
The current ObjectCatalog still stores local integrity metadata such as
whole-content SHA-256 in cleartext; that catalog is not a federation/public blob
manifest and requires a separate metadata-at-rest decision before claiming full
local metadata encryption.

## Runtime activation

The encrypted blob backend can now be activated after authoritative V2
`local-plaintext` cutover by following
`docs/V2_ENCRYPTED_RUNTIME_CUTOVER.md`. Runtime unlock uses the dedicated V2
storage master-key profile plus an external protected password file.

Activation preserves logical/catalog version IDs and keeps the legacy
DocumentStore projection and plaintext V2 BlobStore for compatibility and
rollback. Therefore this stage still reports `encrypted_at_rest=false`.

Federation must not treat the retained plaintext compatibility data as
ciphertext peer storage. Federation storage remains disabled until its own
encrypted path and authorization boundary are activated.
