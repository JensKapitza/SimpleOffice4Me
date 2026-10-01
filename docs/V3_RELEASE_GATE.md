# SimpleOffice4Me 3.0 release-gate evidence

## Assessment snapshot

- Repository: `JensKapitza/SimpleOffice4Me`
- Main commit assessed: `d698b23bf33f33e464472b67387ea4083dca6328`
- Assessment date: 2026-10-01
- GitHub state at start: no open pull requests; issues #505 and #506 open; no issue-506 branch.
- Release decision: **not approved**. Passing repository tests do not replace the target-system, client, hardware, workload, and upgrade evidence listed below.

`PASS (repository)` means an automated repository check passed against the snapshot above. It does not claim that a production deployment or an external client was tested. `OPEN (external)` means evidence must come from an actual supported installation, client, device, peer, or representative workload. `PARTIAL` means repository tests cover part of the criterion but not the complete release acceptance.

## Repository checks run

The full workflow-equivalent test and quality checks were run locally on Linux with Python 3.12.14. The configured GitHub Actions matrix additionally requires Python 3.10, 3.14, and 3.15.0-rc.2; those interpreters were not available in this environment, so their hosted CI results remain authoritative for those versions.

| Check | Result |
|---|---|
| `python -m unittest discover -s tests -v` | PASS (repository): 2,677 tests passed; 11 skipped; no failures, 217.354 s |
| Ruff (`ruff check app tools tests *.py`) | PASS (repository) |
| Project policy, source/function size, Python compile | PASS (repository) |
| Secret scan, CRA checks, CycloneDX SBOM generation and JSON parse | PASS (repository) |
| `pip-audit` | PASS for audited dependencies: no known vulnerabilities. The local `simpleoffice4me==2.0.0` project is not published on PyPI and is skipped by the advisory lookup; this is not a source-code audit. |
| Extended repository hygiene: Python compile, JSON, shell syntax, conflict/whitespace checks | PASS (repository) |
| Mini Services log audit and one-iteration lifecycle benchmark | PASS (repository). The benchmark explicitly excludes process cold start, protocol load, LAN throughput and total RSS, and covers DHCP/DNS/TFTP/SIP rather than 3.0 search/relations/jobs. |
| Pinned KoSIT/XRechnung integration (`XRechnung 3.0.2`, KoSIT validator 1.6.3) | PASS (repository): all 9 validator/install tests, including official positive and negative fixtures |
| Docker image, Docker relay, Debian 12 package workflows | NOT RUN locally: Docker is unavailable. Hosted CI must supply those build/runtime checks where triggered. |

Skipped unit tests are optional integration checks; notably, the KoSIT official-fixture test was rerun separately with its pinned runtime and passed. No real Android device, production database, external S3 implementation, mail/federation peer, or desktop client was connected for this assessment.

## Release matrix

### A. Upgrade and migration

- **PARTIAL** — migration and recovery code is exercised by `test_v2_migration_acceptance.py`, `test_v2_migration_preflight.py`, and the V2 storage/recovery suite. These tests use controlled fixtures.
- **OPEN (external)** — upgrade a copy of each supported current V2/Post-V2 installation with realistic user data, then start twice and verify data, permissions, audit history, and indexes without manual correction.
- **OPEN (external)** — record repeat-start/idempotence and interrupted-backfill recovery from the target installation. Fixture tests are not evidence of a completed customer-data migration.

### B. Capability and fallback combinations

- **PASS (repository, component-level)** — `test_v3_capabilities.py` checks disabled-by-default, unknown-capability fail-closed behavior, and fallback dispatch. Feature-specific suites exercise disabled routes and unavailable optional services.
- **PARTIAL** — related coverage includes automation/jobs (`test_v3_automation.py`, `test_v3_jobs.py`, `test_host_services_and_automation.py`), S3 on/off (`test_s3_overlay.py`), legacy/v3 Federation (`test_federation_compatibility.py`, `test_v3_federation.py`), offline policy/conflict (`test_v3_android_offline.py`), and optional ClamAV/indexing paths.
- **OPEN (external)** — practically exercise the complete requested combinations on an installed deployment: all flags off; relations/events only; UI without automation; automation with worker stopped/unreachable; S3 on/off against a real S3 client; legacy and v3 federation with a real peer; Android without offline grant; and missing ClamAV/indexer/integration configuration. Current automated cases do not constitute one end-to-end cross-product run.

### C. Standards and existing clients

- **PASS (repository)** — CardDAV/vCard: CardDAV, Thunderbird compatibility, contact round-trip and field-policy tests. CalDAV/VTODO: CalDAV, calendar recurrence/scheduling, and TODO round-trip tests. Document/V2 storage: WebDAV, verified materialization, ranges and V2 adapter suites. Invoices: PDF/XML/XRechnung fixture tests and the pinned KoSIT run. S3 and legacy Federation: their local protocol, signature, pagination, and compatibility suites.
- **OPEN (external)** — verify actual Thunderbird and LibreOffice clients, the supported external S3 clients, an existing V2-backed installation, and legacy Federation peers across the release configuration. Server-side fixtures cannot prove every client interoperability detail.

### D. Security

- **PASS (repository)** — central-policy, legacy authorization, extension capability denial, S3 object authorization, Android owner/scope/conflict, secret redaction, XML, path traversal and Federation trust/security suites passed in the full suite.
- **OPEN (external)** — validate effective permissions on a representative migrated deployment and review generated activity/job/health/extension logs from real secrets and real integrations. Automated redaction tests do not replace that deployment review.

### E. Failure and recovery

- **PASS (repository, simulated)** — tests cover persistent-job retry/lease behavior, inbox/S3 recovery, interrupted V2 operations, finance finalization state, Android version conflicts, Federation failures, and unavailable optional services.
- **OPEN (external)** — terminate/restart the deployed worker while jobs are active; disconnect real event/extension/Federation/S3 dependencies; interrupt a migration process; and verify operator-visible diagnosis and recovery without a permanently partial business document.

### F. Performance

- **PASS (repository, bounded behavior)** — tests exercise pagination, result limits, query bounds, indexes, streaming document paths, and health-check timeouts where implemented.
- **OPEN (external)** — measure global search, entity context, relations, activity and job queues against a representative production-sized dataset, including query counts, response latency, memory and recovery under load. The Mini Services benchmark is unrelated and cannot satisfy this requirement.

### G. Disable and rollback

- **PASS (repository, feature-level)** — capabilities default off and feature-specific tests verify disabled behavior; V2 keeps rollback/recovery data and legacy protocol tests remain available.
- **PARTIAL** — code and fixtures show that deactivation need not delete the new data, but the full independent disable sequence has not been performed on an upgraded installation.
- **OPEN (external)** — separately disable 3.0 UI, automation, plugins, Android offline and Federation v3 on an upgraded system; confirm old domain stores and CardDAV/CalDAV/VTODO, document and invoice paths remain usable without deleting new data.

## Required to approve #506

Attach dated evidence for each external item above to the intended release commit, including the tested build/commit, deployment configuration, client/device/peer versions, dataset/workload characteristics, results, failures, and recovery actions. Keep #506 open until all open and partial items are closed with that evidence. Keep roadmap #505 open until its own Definition of Done and the selected 3.0 release gate are both satisfied.
