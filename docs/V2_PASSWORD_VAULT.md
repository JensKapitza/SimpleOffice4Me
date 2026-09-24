# V2 password-vault key hierarchy

The password vault uses the V2 secret storage security class. Secret payloads
do not inherit normal document deduplication, indexing, federation or ordinary
storage-read grants. Vault unlock remains separate from the normal web login
session.

## Password protection

Each vault owns a random 256-bit vault key. Entry ciphertext remains encrypted
with that vault key and existing per-entry AES-GCM associated-data binding.

New vault profiles protect the vault key with the central V2 CryptoService:

- Argon2id derives the password-protection key,
- AES-256-GCM protects the vault key,
- KDF policy is encoded in the protected-key record,
- changing the master password rewrites only the protected vault-key record.

Historical password-vault profiles used the earlier scrypt wrapper. They remain
readable. After a successful legacy unlock, SimpleOffice verifies the vault-key
check and transparently migrates the profile to the V2 Argon2id/AES-GCM
protection record. Legacy scrypt parameters are accepted only at the known
historical policy values so tampered KDF parameters cannot request arbitrary
work factors.

New master passwords require at least 12 characters. Existing legacy passwords
with the historical 10-character minimum remain unlockable so migration does
not lock users out.

## Offline recovery

Offline recovery is optional and must be explicitly enabled while the vault is
already unlocked.

Enabling recovery creates:

- a fresh random 256-bit recovery key,
- a recovery-protected vault-key record using the central V2 recovery-key
  wrapping primitive,
- a portable recovery bundle containing only the protected vault-key record,
  the vault user binding and the authenticated vault-key check.

The raw recovery key is returned only to the caller and is never stored in the
vault database or audit stream.

The recover_vault_key_from_bundle API validates the recovery wrapping and
vault-key check without requiring a live SimpleOffice database. The live
PasswordVault.unlock_with_recovery path additionally validates the key against
the current local profile before granting vault access.

Recovery can be rotated or disabled without re-encrypting entries because the
vault key itself remains unchanged. Previously exported old recovery bundles
are snapshots: an old bundle plus its old recovery key can still recover the
vault key represented by that bundle. Full cryptographic revocation of
compromised historical recovery material therefore requires a future vault-key
rotation that re-encrypts/re-wraps the vault entry layer.

## Backups

Password-vault backup format version 2 includes the V2 password-protection
record and, when configured, the protected recovery record. It never includes a
raw vault key, master password or raw recovery key.

Version-1 backups remain importable. A restored legacy profile is migrated to
the V2 password-protection record after its first successful password unlock.

## Audit

The following V2 recovery events are audit-visible without secret payloads:

- recovery enabled,
- recovery succeeded/failed,
- recovery bundle exported,
- recovery key rotated,
- recovery disabled,
- legacy password protection migrated.

Audit records contain identifiers, operation names and generic failure reasons,
not passwords, vault keys, entry plaintext or recovery keys.

## Stable search and mail-reference service

The V2 VaultService is the non-Flask service boundary intended for future UI and
API consumers.

Credential search is deliberately evaluated in memory after a successful vault
unlock. No plaintext credential search index is written to disk. Searchable
fields are limited to non-secret identity/service metadata such as title,
username, e-mail address, URL/domain, tags and folder. Passwords, TOTP secrets
and notes are excluded from the search projection.

Combined filters support username, e-mail address, parent/service domain, tags,
folder and favorites. Identity-usage summaries expose only service metadata and
never return password/TOTP/note fields.

Mailstore integration is reference-only. VaultService receives mail-account and
mail-search ports, derives lookup terms only from credential e-mail addresses
and service domains, and returns a strict whitelist of message locator/header
fields. Indexed message bodies, search text and content hashes are not copied
into the vault response. The mail index remains the source of truth.

## Remaining Phase-13 work

This step stabilizes the key hierarchy and recovery contract. The encrypted
vault database is still its dedicated local store. A later acceptance step can
move the encrypted vault payload/storage container behind the V2 object-store
boundary without changing the secret-class or unlock/recovery contracts.
