# V2 encrypted blob runtime cutover

The V2 encrypted blob format can be activated as the authoritative **blob
backend** after the existing V1 -> V2 shadow/cutover flow is complete.

This is an incremental production step. It does **not** yet mean that the whole
SimpleOffice data root is encrypted at rest: the legacy DocumentStore
compatibility projection and the old plaintext V2 BlobStore are intentionally
retained for rollback and compatibility.

## Security model

The runtime uses the dedicated master-key profile `v2-local-storage`.

The master key is never stored in plaintext. A dedicated storage password
protects the master key through the existing Argon2id + AES-256-GCM profile.
The running service reads that password from the file referenced by:

```text
SIMPLEOFFICE_V2_STORAGE_PASSWORD_FILE
```

The password file:

- must be outside the SimpleOffice document/data root,
- must be a regular file and not a symlink,
- on POSIX must not grant group/world permissions,
- is never accepted as a password value on the command line,
- should preferably be supplied by a service/secret manager or protected mount.

The process caches the unlocked master key only in memory. The cache is
invalidated when the password-file identity/mtime/size or master-key profile
generation changes.

If the password file or key profile is unavailable, encrypted V2 storage fails
closed instead of falling back to plaintext.

## Provisioning

Create a dedicated long random storage password in a protected external file.
Do not reuse the normal SimpleOffice login password.

Provision the master-key profile and two offline recovery files:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-key-init \
  --password-file /run/credentials/simpleoffice-v2-storage-password \
  --recovery-key-output /media/offline/simpleoffice-v2-recovery.key \
  --recovery-bundle-output /media/offline/simpleoffice-v2-recovery.json \
  --apply
```

The recovery-key and bundle outputs must also be outside the SimpleOffice data
root and must not already exist.

The recovery key is the secret half of offline recovery. Store it separately
from normal backups. The recovery bundle contains only protected key material
and may be exported again later:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-key-export-bundle \
  --output /media/offline/simpleoffice-v2-recovery-copy.json \
  --apply
```

## Optional trustee recovery

The storage profile can have an additional offline trustee/emergency recovery
path. It is opt-in and does not grant normal application permissions.

Enable it with new external output files:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-trustee-init \
  --password-file /run/credentials/simpleoffice-v2-storage-password \
  --trustee-key-output /media/trustee/simpleoffice-v2-trustee.key \
  --trustee-bundle-output /media/trustee/simpleoffice-v2-trustee.json \
  --apply
```

The trustee key should be stored separately from normal storage recovery
material. It can be rotated with `storage-trustee-rotate`, disabled with
`storage-trustee-disable`, and its protected bundle can be copied with
`storage-trustee-export-bundle`. All mutations are read-only previews unless
`--apply` is supplied.

A portable trustee bundle can be checked without a SimpleOffice document root:

```bash
simpleoffice-v2-recovery trustee-recovery-check \
  --bundle /media/trustee/simpleoffice-v2-trustee.json \
  --trustee-key-file /media/trustee/simpleoffice-v2-trustee.key
```

The command validates the protected master key and profile binding but never
prints or exports the raw master key.

## Encrypted blob migration

Prerequisites:

1. normal V1 -> V2 migration, verified shadow mode and authoritative V2
   activation are already complete;
2. protection mode is currently `local-plaintext`;
3. `SIMPLEOFFICE_V2_STORAGE_PASSWORD_FILE` is configured for the process;
4. normal application writers are stopped for the applying migration.

A read-only preview does not create the encrypted store when none exists:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents storage-encrypted
```

Apply only during a maintenance window:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-encrypted --apply --acknowledge-maintenance-window
```

The migration:

- validates plaintext V2 inventory before writing,
- refuses invalid manifests, missing referenced chunks and unfinished staging
  transactions,
- migrates all versions belonging to catalogued objects,
- streams verified plaintext through a bounded spool instead of buffering whole
  documents in memory,
- preserves existing blob version IDs so the ObjectCatalog does not require a
  cross-file transaction,
- verifies ciphertext/AEAD/footer integrity,
- checks the ObjectCatalog fingerprint before and after migration,
- switches the cutover protection mode only after successful verification,
- retains plaintext data for rollback.

After activation, `storage_for(...)` injects
`EncryptedBlobCatalogStorageAdapter` into the existing
`V2AuthoritativeStorageAdapter`. Existing logical object IDs, catalog
locations, version preconditions and audit behavior therefore remain unchanged.

## Verification

With the runtime password environment configured:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents storage-cutover-status
```

The status verifies the active encrypted backend with the unlocked master key.
It continues to report:

```text
encrypted_at_rest = false
federation_storage_allowed = false
```

until the remaining plaintext compatibility projection is removed and the
federation storage path is explicitly migrated.

## Rollback boundary

The existing `storage-v1 --apply` path remains possible because the
compatibility projection and plaintext migration data are retained.

Do not manually delete either plaintext store yet. Final plaintext cleanup is a
separate V2 acceptance step after:

- all remaining consumers use StoragePort/ObjectCatalog,
- recovery from the encrypted backend is independently tested,
- rollback acceptance is complete,
- the legacy compatibility projection is no longer needed.

## Remaining production step

Activating the encrypted blob backend protects V2 blob chunks and manifests
against direct plaintext disclosure, but normal projected document files remain
plaintext. Full local encryption-at-rest must not be advertised until that
projection has been removed or replaced.

## Storage master-key rotation

After encrypted runtime activation, the dedicated storage master key can be
rotated without rewriting ciphertext payload chunks.

Preview:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-key-rotate \
  --password-file /run/credentials/simpleoffice-v2-storage-password \
  --recovery-key-output /media/offline/simpleoffice-v2-recovery-rotated.key \
  --recovery-bundle-output /media/offline/simpleoffice-v2-recovery-rotated.json
```

Apply only with normal application writers stopped:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-key-rotate \
  --password-file /run/credentials/simpleoffice-v2-storage-password \
  --recovery-key-output /media/offline/simpleoffice-v2-recovery-rotated.key \
  --recovery-bundle-output /media/offline/simpleoffice-v2-recovery-rotated.json \
  --apply --acknowledge-maintenance-window
```

If trustee recovery is enabled, add
`--trustee-key-file /media/trustee/simpleoffice-v2-trustee.key` to every
preview/apply/resume run. The rotation refuses to continue without the trustee
key and rewraps the new master key into the existing trustee recovery record.

If the process is interrupted, rerun the same command. The persistent rotation
journal identifies versions already rewrapped and resumes them safely. While
that journal exists, normal encrypted StoragePort runtime selection fails
closed.

Before the new profile is committed, a partial rotation can be reverted:

```bash
simpleoffice-v2-recovery --root /srv/simpleoffice/documents \
  storage-key-rotation-rollback \
  --password-file /run/credentials/simpleoffice-v2-storage-password \
  --apply --acknowledge-maintenance-window
```

`storage-key-rotation-status` reports whether a journal is pending without
exposing raw key material.
