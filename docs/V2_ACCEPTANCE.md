# V2 final acceptance

Date: 2026-09-25

This document is the final repository-side acceptance record for #306 and #314.
It distinguishes completed V2 requirements from deliberately retained
compatibility paths. Retained paths are not treated as silently completed work;
they have explicit boundaries and removal criteria in #471.

## Release baseline

- Current accepted baseline before this acceptance PR: main commit
  `e649c9fded7211f10fed51a1fbae7c8884bbafc7` (PR #469).
- All nine repository workflows for PR #469 completed successfully: tests and
  dependency audit, extended quality gates, security quick wins, CodeQL, OSSAR,
  Scorecard, Android build, Desktop build and Docker build.
- Repository policy includes the committed-secret gate
  `python tools/check_secret_leaks.py .` in CI.

The acceptance PR must also pass the repository CI before it is merged and the
trackers are closed.

## #314 Definition of Done

| Requirement | Acceptance evidence | Status |
| --- | --- | --- |
| Architecture boundaries documented and visible in code | `docs/ARCHITECTURE_V2.md`, `app/v2/contracts.py` | complete |
| CI fully green | nine green workflows on PR #469; acceptance PR required green before close | complete after merge gate |
| V1 -> V2 reproducibly tested | `docs/V2_MIGRATION_ACCEPTANCE.md`, `tests/test_v2_migration_acceptance.py` | complete |
| Recovery without main application | `docs/V2_RECOVERY.md`, `docs/V2_ENCRYPTED_RECOVERY.md`, independent `simpleoffice-v2-recovery` | complete |
| Storage, Blob and Crypto responsibilities separated | StoragePort/ObjectCatalog, BlobStore/EncryptedBlobStore and CryptoService have separate contracts | complete |
| Federation through persistent authorized jobs | `docs/V2_FEDERATION_JOBS.md`, `app/v2/jobs.py`, authorization/policy tests | complete |
| File Browser/VFS use common storage contracts | `docs/V2_FILE_BROWSER.md`, `docs/V2_OVERLAY.md` | complete |
| Audit contains no secret payloads | `tests/test_v2_audit_adapter.py`, `tests/test_password_vault_audit.py` | complete |
| Password Vault uses V2 security boundaries | `docs/V2_PASSWORD_VAULT.md`, `docs/V2_VAULT_OBJECT_STORE.md`, PR #458 | complete |
| Legacy paths removed or explicitly transitional | `docs/V2_LEGACY_CLEANUP.md`, #471 | transitional by design |
| Public repository checked for accidental secrets | CI secret-leak gate plus project policy | complete |

## #306 acceptance principles

1. **Failure tolerance / recovery:** versioned BlobStore, integrity inventory,
   erasure-recovery contract and portable recovery descriptors provide recovery
   without depending on the normal application database.
2. **Ciphertext confidentiality:** payload AEAD and encrypted chunk storage keep
   plaintext unavailable to storage/relay peers without key material.
3. **Independent disaster recovery:** encrypted inventory/verify/export and
   trustee/recovery checks run through the independent recovery CLI.
4. **Explicit granular P2P sharing:** V2 capability grants separate read, store,
   relay, metadata and delegate rights with scope, expiry and revocation.
5. **Requester may go offline:** persistent Federation desired-state jobs survive
   restart and do not depend on the controller's live HTTP session.
6. **Alternative arrival satisfies target state:** `mark_verified(...)` accepts
   verified local import/direct-peer arrival and completes the same desired-state
   job without duplicate transport.
7. **Filesystem/name differences preserve identity:** LogicalObjectId is separate
   from StorageLocation; V2 metadata aliases model case, Unicode and length
   differences explicitly.
8. **Metadata provenance/trust retained:** original, observed, imported and
   federated channels remain distinct and local trust does not rewrite source
   provenance.
9. **Delegated trust bounded and auditable:** grants are scoped, expiring,
   revocable and non-transitive by default; route-policy decisions are audited.
10. **Established crypto and threat model:** AES-256-GCM, Argon2id, explicit
    domain separation and key hierarchy are documented in
    `docs/V2_CRYPTO_THREAT_MODEL.md`; no custom cipher/KDF is introduced.

## Recovery/Federation end-to-end acceptance

The implemented recovery path is bounded as follows:

- portable encrypted recovery descriptor contains ciphertext references and
  authenticated manifest data, but no plaintext digest or raw key material;
- peer availability/fetch requires normal Federation authentication, signed
  request/replay protection and a descriptor-scoped authorization reference;
- the recovery CLI can query and fetch verified ciphertext without Flask or an
  unlocked normal web session;
- fetched chunks are verified against descriptor binding, size and ciphertext
  digest before they are cached;
- storage/relay rights do not imply read rights.

Federation transfer acceptance is bounded as follows:

- jobs are persistent, idempotent and restart-safe;
- authorization scope cannot expand during progress;
- deny-first route policy is re-evaluated before progress;
- completion is based on verified target state rather than sender activity;
- session-scoped equality tokens avoid exposing stable cross-peer plaintext
  block hashes on the V2 dedup path;
- V2 dedup lookup is document-scoped and peer-signed; the former stable
  hash-addressed V2 manifest/block route is retired, removing it as a general
  content-presence oracle.

## Explicit post-V2 transitions

The following are intentionally retained and transferred to #471:

- synchronized DocumentStore compatibility projection for current read models,
  access-policy namespace and selected derived workflows;
- retained plaintext compatibility data for rollback while the cleanup gate is
  false;
- video transcoding now uses verified short-lived StoragePort materialization
  and raw video playback uses verified StoragePort range reads; other direct
  managed-file consumers remain tracked by #471;
- legacy SOFP/federation-transfer paths for older peers;
- stronger OPRF/PSI equality and signed assertions about undisclosed remote
  relationships are optional future hardening, not V2.0 acceptance requirements.

These transitions have explicit removal criteria. They do not change the V2
source of truth and must not be used to claim full data-root encryption-at-rest
before the compatibility projection is removed.

## Acceptance result

Repository-side V2 requirements of #306 and #314 are accepted once this change
is merged with all required CI gates green. #471 remains open for post-release
compatibility cleanup and optional protocol hardening.

## 2.0 release cutover

The repository release line moves to **2.0.0** in the dedicated release-cutover
change documented in `docs/V2_RELEASE_CUTOVER.md`. This changes the application
release identity, not the Phase-15 cleanup safety contract: #471 remains
fail-closed until persistent plaintext projection consumers are removed.
