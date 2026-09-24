# V2 master-key profiles

V2 master-key profiles persist only protected key records. A raw user/storage
master key, password or offline recovery key is never written to the profile
file.

## Hierarchy

For each profile:

- a random 256-bit master key is generated,
- the password protects that master key through the existing Argon2id +
  AES-256-GCM `CryptoService` record,
- an independent random 256-bit offline recovery key protects the same master
  key in a second record,
- an authenticated key-check payload binds the recovered master key to the
  profile hash.

Changing the password therefore rewrites only the protected master-key record.
It does not require re-encrypting blobs or their CEKs.

The profile identifier is hashed before it is used as a filename or stored in
the profile. The clear profile identifier is not required in a portable
recovery bundle.

## One-time recovery material

`MasterKeyProfileStore.create(...)` returns `RecoveryMaterial` containing:

- the raw 256-bit recovery key,
- a portable recovery bundle containing the recovery-protected master-key
  record and authenticated profile binding.

The recovery key is secret and must be stored separately/offline. It must not
be logged, committed, placed in URLs or copied into normal application
metadata.

The bundle does **not** contain the password-protected record, clear profile
identifier, raw recovery key or raw master key.

`recover_master_key_from_bundle(...)` can recover the master key from the
bundle plus recovery key without a Flask application, user database or live
SimpleOffice root. This is groundwork for the independent encrypted-store
recovery CLI.

## Independent recovery check

The existing `simpleoffice-v2-recovery` CLI provides
`master-key-recovery-check`. It accepts a recovery-bundle path and a
`--recovery-key-file` path. The recovery key itself is deliberately not
accepted as a command-line argument, so it does not enter normal process
argument listings or shell history.

The command needs no SimpleOffice document root or user database. It verifies
the protected master key and authenticated profile binding, then reports only
non-secret status metadata. It never prints or exports the recovered master
key. Invalid inputs and authentication failures use the same generic error
result.

The independent encrypted-object recovery commands consume the recovered key
internally and export only requested verified plaintext objects. They do not
materialize the raw master key as an operator-facing file.

## Recovery-key rotation

Recovery-key rotation creates a fresh independent recovery key and rewrites only
the recovery protection record. Existing encrypted payloads and their wrapped
CEKs remain unchanged. Old recovery material no longer opens the current local
profile after rotation.

Previously exported old recovery bundles still describe the older wrapping
record. They should be retired according to the operator's backup/recovery
policy when rotation is intended as revocation.

## Optional trustee/emergency recovery

A profile can optionally add a second, explicitly provisioned trustee recovery
record. The trustee key is a separate random 256-bit secret and uses its own
AES-256-GCM domain (`master-trustee`), so normal recovery-key records and
trustee records are not interchangeable.

Trustee recovery is disabled by default. Enabling it does not grant application,
document, federation, relay or metadata permissions. It only creates an
additional offline way to recover the same master key when the matching trustee
key and protected trustee bundle are deliberately supplied.

The trustee key itself is returned only once to the provisioning/rotation
caller and should be stored offline/system-separated. The profile persists only
the protected master-key record. A portable trustee bundle contains that
protected record, profile binding and authenticated key-check, but no raw
trustee or master key.

Trustee keys can be rotated or disabled without re-encrypting payloads or CEKs.
Rotation invalidates the previous local trustee record. Disable removes the
trustee record from the active profile. Enable, recovery, rotation, disable and
bundle export are audited without secret payloads.

Previously exported trustee bundles remain cryptographic snapshots: an old
bundle plus its old trustee key can still recover the master key that was
wrapped into that bundle. If old trustee material must be cryptographically
revoked rather than only removed from the live profile, rotate the trustee key
and then rotate the storage master key. The old trustee bundle then recovers
only the retired master key, while all active CEKs are wrapped by the new one.

When storage master-key rotation is performed while trustee recovery is
enabled, the current trustee key must be supplied from an external protected
file. The operation verifies it against the active master key and rewraps the
new master key for the same trustee before committing the profile. Master-key
rotation therefore cannot silently drop an enabled emergency-recovery path.

## Storage master-key rotation

The dedicated encrypted-storage profile can rotate its master key without
re-encrypting payload chunks. During a maintenance window every encrypted blob
version CEK is rewrapped from the old master key to a fresh one. Only after all
versions are on the new wrapping key is the active profile atomically replaced.

The rotation journal stores only password-protected old/new master-key records,
an encrypted pending recovery key, public version IDs and output paths. Raw
master/recovery keys are never persisted. Normal encrypted runtime access fails
closed while the journal exists.

The command can be rerun after a process interruption. Before profile commit an
operator can also roll the partial rewrap back to the old key. A new offline
recovery key and bundle are emitted outside the SimpleOffice data root as part
of a successful rotation.

## Audit

Creation, unlock, failed unlock, recovery, password change, recovery-key
rotation, trustee enable/recovery/rotation/disable, storage master-key rotation,
and recovery/trustee-bundle export generate V2 audit events. Events contain
profile hashes/generations and generic failure reasons only; passwords,
recovery keys and master keys are never audit payloads.

## Concurrent mutation and crash behavior

Profile creation, password changes and recovery-key rotation use an atomic
per-profile directory lock. A second writer fails instead of silently replacing
a key profile. If a process crashes while holding the lock, the stale lock is
left in place and the next mutation fails closed; an operator can remove that
empty lock directory only after verifying that no key-management process is
still active.

Profile JSON writes use a same-directory temporary file, fsync and atomic
replace. Reads reject symlink/non-regular profile files.

## Storage activation

The dedicated local-storage profile can be used by the encrypted V2 blob
runtime. Its password remains separate from the normal web login session and is
loaded only from the external file configured by
`SIMPLEOFFICE_V2_STORAGE_PASSWORD_FILE`.

The file must live outside the SimpleOffice data root and is intended for an OS
credential/secret mount or otherwise tightly protected service configuration.
The unlocked master key is cached only in process memory and the runtime fails
closed when the secret or profile is unavailable.

Provisioning and cutover steps are documented in
`docs/V2_ENCRYPTED_RUNTIME_CUTOVER.md`. The legacy plaintext compatibility
projection is still retained, so this activation must not yet be described as
full local encryption at rest.
