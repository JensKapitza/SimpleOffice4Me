# SimpleOffice4Me 3.0 – Evolution Contract

Version 3.0 is introduced as an additive evolution of the current V2/Post-V2 system. A 3.0 component must never become a hidden startup dependency of an existing function.

## Mandatory rollout sequence

For data/schema changes use:

1. **expand** – add new tables/columns/services without removing the old path;
2. **backfill** – optional, restartable and idempotent;
3. **parallel compatibility** – adapters or dual-read/dual-write only where required and with reconciliation;
4. **cutover** – explicitly reviewed and feature/capability gated;
5. **cleanup** – later, in a separate change after the rollback window.

Destructive migration and replacement must not happen in the same rollout step.

## Capability rules

- All 3.0 capabilities are disabled by default.
- Server-side code is authoritative; hiding a UI control alone is never a security boundary.
- Unknown or unavailable optional capabilities fail closed and must degrade to the existing path or a clear unavailable state, not an internal server error.
- Capability state is visible in the authenticated runtime inventory without exposing environment values or secrets.
- A module may depend on a **port/contract**, but not on an unfinished implementation. A fallback adapter must remain available until cutover.

## Compatibility rules

- Existing routes, file formats, standards and external clients remain valid until a separately reviewed deprecation/cutover.
- Existing domain stores remain authoritative unless the owning issue explicitly completes a cutover.
- No direct cross-module table access is introduced when a domain service/port can be used.
- Rollback must not require deleting newly written data.
- New dependencies require explicit approval.
- Permission checks fail closed. Migration must never widen access.

## API and schema versioning

External or cross-module contracts carry an explicit version. Additive fields are preferred. Breaking changes require a new version or a compatibility adapter plus a documented deprecation window.

## Definition of done for a 3.0 change

- default configuration preserves the existing application behavior;
- upgrade/migration is idempotent;
- the new capability can be disabled without data loss;
- missing optional capabilities are handled deliberately;
- permission and negative cases are covered;
- old-path regression tests remain green;
- documentation names fallback, rollback and operational limits.

## Parallel work that remains valid

The 3.0 rollout does not invalidate the existing follow-up work in **#471**, the S3/Inbox work around **#482**, the Android reader in **#483**, or the site-visit work in **#485**. These components may continue independently and later attach to 3.0 ports.

## Removal policy

Legacy paths are removed only after their replacement has been enabled and observed, compatibility requirements are satisfied, rollback evidence exists, and a separate cleanup change is reviewed.
