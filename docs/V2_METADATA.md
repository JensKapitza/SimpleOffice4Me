# V2 metadata model

V2 metadata keeps object identity separate from names and from facts observed at
different times or by different peers.

## Channels

Metadata is separated into four provenance channels:

- **original**: immutable information captured at import/creation time
- **observed**: information measured or discovered later
- **imported**: metadata received from an external source
- **federated**: metadata received from another SimpleOffice peer

Values from different channels are not silently collapsed. Callers may inspect
all values for a field and apply local policy.

## Provenance

Every value records:

- source channel
- source reference
- observation/import time
- optional actor

Trust and verification are separate from provenance. A federated value can be
locally trusted or untrusted independently of where it originated.

## Trust and verification

Trust levels:

- unknown
- untrusted
- local
- trusted

Verification states:

- unverified
- verified
- conflict
- invalid

These states are local assessments and must not rewrite original provenance.

## Original metadata

Original metadata is immutable. The model rejects duplicate original field
names and rejects values marked immutable outside the original channel.

Later corrections belong in observed/imported/federated metadata rather than
rewriting the original record.

## Filename aliases

A filename is an alias, never the logical object identity.

Alias handling includes explicit namespace rules for:

- case sensitivity
- Unicode normalization
- maximum encoded name length
- collision detection

This allows adapters for Windows, macOS, Linux, WebDAV or future VFS layers to
apply the filesystem rules of the target namespace without changing the
logical object id.

Aliases reject path separators, NUL, `.` and `..`. Directory/path structure
belongs to the storage namespace, not to the alias value itself.

## Sidecar metadata

The envelope permits bounded structured sidecar metadata for format-specific
extensions. Sidecars do not replace the explicit provenance channels and must
not contain secrets merely because they are stored separately.

## Federation

Federated metadata keeps its source reference and local trust/verification
state. Receiving a field from another peer does not automatically make it
verified, trusted or authoritative.

## Migration

The V1 -> V2 migration now projects each verified legacy document into a
versioned V2 metadata envelope below `.simpleoffice-v2/metadata/`.

The adapter is additive:

- the V1 metadata JSON remains untouched and authoritative during migration;
- the V2 filename is a SHA-256 of the logical object ID, so object IDs never
  become filesystem paths;
- original filename/suffix/hash/first-seen values are immutable in the V2
  envelope;
- current path, size, digest, state and tags are stored as observed metadata;
- historical names become filename aliases with explicit provenance;
- an existing `federation_origin` attribute is mapped into the federated
  channel instead of becoming local/original truth;
- a digest of the complete legacy metadata object binds the projection back to
  the exact V1 source record without copying arbitrary legacy fields into the
  V2 sidecar.

Projection files are atomically published, private mode 0600 on POSIX, and the
metadata directory must not be a symlink. Re-running migration is idempotent.
A conflicting immutable original projection or a projection owned by a different
producer fails closed.

`verify_migration_transfer()` rebuilds the expected envelope from V1 and
requires an exact matching V2 projection for every migrated document. Blob,
catalog and metadata verification are therefore separate release gates.
