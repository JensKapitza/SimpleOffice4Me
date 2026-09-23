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

## Recovery-key rotation

Recovery-key rotation creates a fresh independent recovery key and rewrites only
the recovery protection record. Existing encrypted payloads and their wrapped
CEKs remain unchanged. Old recovery material no longer opens the current local
profile after rotation.

Previously exported old recovery bundles still describe the older wrapping
record. They should be retired according to the operator's backup/recovery
policy when rotation is intended as revocation.

## Audit

Creation, unlock, failed unlock, recovery, password change, recovery-key
rotation and recovery-bundle export generate V2 audit events. Events contain
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

This component does not automatically unlock encrypted storage at process
startup and does not bind storage encryption to the normal web login session.
That integration remains explicit so unattended servers, interactive users,
recovery operation and future trustee policies can use different unlock
policies without weakening the key hierarchy.
