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
wraps it with the new master key. Ciphertext chunk files are unchanged. Full
account-wide rotation still needs an orchestration step that rewraps every
reachable version before changing the active master key.

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

## Not yet activated

This format is groundwork for the remaining encrypted-at-rest cutover. The live
V2 StoragePort remains in the explicitly acknowledged `local-plaintext` mode
until key provisioning/unlock, migration, recovery and cutover semantics are
wired and verified together.

Federation must not treat the plaintext BlobStore as encrypted peer storage in
the meantime.
