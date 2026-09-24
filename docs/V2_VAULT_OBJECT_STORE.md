# V2 Password Vault payload object store

## Scope

Credential payloads are moved out of the password-vault SQLite database into a
dedicated V2 BlobStore/ObjectCatalog boundary. SQLite remains the small transactional
index for user, credential identity, revision, timestamps and the opaque current
object/version reference.

This store is intentionally separate from the normal document V2 root:

`.simpleoffice-meta/password-vault-objects/<actor-hash>/.simpleoffice-v2/`

The actor directory is SHA-256 scoped. Normal document catalogs, indexing, sharing and
federation therefore cannot enumerate or grant access to vault payload objects. On
POSIX systems both the vault-object container and the actor-specific root are forced
to mode `0700`, so local users cannot traverse the encrypted SECRET store or its
metadata.

## Encryption boundary

The blob content is not credential plaintext. PasswordVault first creates the existing
AES-GCM credential envelope with AAD bound to user, entry ID and revision. Only
`nonce + ciphertext` are serialized into the secret V2 object store.

Consequences:

- no credential plaintext is persisted in SQLite or the V2 payload store;
- the dedicated BlobStore adds integrity/version/chunk handling without inventing a
  second credential encryption primitive;
- random physical chunk IDs and random catalog locations avoid content-addressed
  identities and normal content deduplication;
- the vault key remains separate from object-store metadata.

## Atomic write model

A credential mutation uses create-before-commit:

1. validate and AES-GCM encrypt the normalized credential;
2. create a new immutable V2 payload object;
3. open an immediate SQLite transaction;
4. verify the expected previous credential revision still matches;
5. commit only the new object/version reference.

If step 3-5 fails, the old database reference remains authoritative. The new ciphertext
object is an unreferenced orphan and can be reclaimed later. No partially updated
credential is published.

Older payload objects are retained as encrypted historical storage material. They are
not referenced by the current credential row and are not exposed through vault reads.

## Migration

Existing rows with legacy SQLite `nonce/ciphertext` values are migrated on successful
password or recovery unlock:

1. authenticate/decrypt the legacy row with its existing AAD;
2. require valid JSON object content;
3. create the V2 secret payload object;
4. atomically replace SQLite payload bytes with the object/version reference.

A bad legacy ciphertext fails closed before its database row changes. Imported legacy
backup rows use the same migration path on first successful unlock.

## Backup compatibility

The external encrypted vault backup format remains unchanged. For object-backed rows,
export reads only the stored encrypted envelope and writes the existing nonce/ciphertext
fields into the portable backup. It does not require the master password and never
exports plaintext credentials.

Import still accepts the existing backup format into compatibility rows. First unlock
verifies and migrates those rows into the secret object store. This preserves offline
recovery and avoids coupling the backup format to local object IDs.

## Security class

This store implements the existing SECRET policy intent:

- no normal content indexing;
- no automatic federation;
- no normal document read grants;
- separate vault unlock;
- no content deduplication based on plaintext.

Any future remote replication of these objects must be explicitly authorized as vault
recovery/storage and must not reuse document-sharing defaults.
