# SimpleOffice4Me 3.0 release-gate evidence

## Current documentation assessment — 2026-10-05

Documentation/code-read basis: `main` at
`2abc3ef6a02c173304d6c23352f48497730b7068` (PR #585).
The package still declares version 2.0.0. The new
[3.0 operator guide](V3_BETRIEBSLEITFADEN.md) lists all sixteen registered,
default-off capabilities and distinguishes implementation from release approval.

The [documentation and closed-issue review](DOKUMENTATIONSPRUEFUNG_3_0.md)
records two reproduced mail correctness findings (#587/#588), corrected
documentation and secret-scan scope. This documentation-only assessment adds
no production-data, client/device, workload or worker-crash acceptance evidence.
The release decision remains **not approved**; #505/#506 stay open. The earlier
test counts below describe their dated snapshots and are not new test results.

## Historical repository assessment — 2026-10-02

Production-code basis: `main` at `f7c04a14083772f0a97df8214f43105ba63e94f5`
(PR #548). All eleven PR workflows passed, including Python 3.10/3.14/3.15 RC,
browser/P2P/HTML5, Android, desktop, Docker, Debian and security gates. The local
full suite passed 2,709 tests with 11 optional skips. The browser run covered
141 URLs and 129 HTML pages with no missing static routes or console/page/server
errors. This browser evidence does not prove real-device accessibility.

`tests/test_v3_release_gate_matrix.py` adds a repeatable integration fixture for
#506. It migrates an existing document into authoritative V2, retains
CardDAV/vCard, CalDAV/VEVENT and VTODO resources, a persistent automation rule
and a queued job, then exercises 27 configurations:

- all capabilities off;
- relations/activity only;
- UI capabilities on with automation off;
- automation with jobs off, and automation with jobs queued without a worker;
- S3 on (all other profiles keep S3 off);
- legacy and v3 Federation discovery together;
- Android offline with the requesting user's project permission denied;
- health/extensions enabled without configured external integrations;
- all sixteen registered 3.0 capabilities on;
- each capability individually disabled while the other fifteen remain on;
- final rollback to all capabilities off.

| Matrix observation | Repository result |
|---|---|
| Additive auth migration executed twice per profile | PASS; existing accounts and authenticated paths remain usable |
| Migrated document and standard-client resources | PASS; original document bytes, vCard/VEVENT/VTODO bytes and ETags remain unchanged |
| S3 enabled/disabled | PASS; existing SigV4 test helper reads the migrated document only when the overlay is enabled |
| Search and entity context | PASS; enabled routes resolve the authorized document; disabled routes return 404; a denied user cannot read, resolve or find it |
| UI/API fallback | PASS; optional overview pages remain usable; disabled automation/job mutation routes reject requests |
| Automation without jobs/worker | PASS; the existing synchronous executor creates the task; the unrelated persistent job remains queued |
| Deactivation and rollback | PASS (fixture); individual flags and final all-off preserve the rule, job and domain data |

Run with `python -m unittest discover -s tests -p test_v3_release_gate_matrix.py -v`.
The test is also discovered by the existing full CI suite. It uses existing
stores, migration paths, Flask routes and the S3 signing helper; no new runtime
dependency or parallel transport is introduced.

**Release decision remains not approved.** This controlled fixture does not
establish production-data upgrade/backfill recovery, real external-client or
peer compatibility, worker-process crash recovery, Android device acceptance,
representative-load performance, or independent deactivation on an installed
upgraded target. Federation coexistence here checks discovery contracts;
object-transfer and replay/security remain covered by their existing suites.
Do not close #505 or #506 based on these fixture results alone.

## Historical assessment snapshot — 2026-10-01

- Repository: `JensKapitza/SimpleOffice4Me`
- Main commit assessed: `3788d9d46e6abf4a669bdd356ddd5b0c9ac3936d`
- Assessment date: 2026-10-01
- GitHub state at assessment: no open pull requests; PR #540 (Android bookshelf) and PR #541 (mail-case Federation) are merged. Issues #483, #505 and #506 remain open; #483 awaits practical APK acceptance.
- Release decision: **not approved**. Passing repository tests do not replace the target-system, client, hardware, workload, and upgrade evidence listed below.

`PASS (repository)` means an automated repository check passed against the snapshot above. It does not claim that a production deployment or an external client was tested. `OPEN (external)` means evidence must come from an actual supported installation, client, device, peer, or representative workload. `PARTIAL` means repository tests cover part of the criterion but not the complete release acceptance.

## Repository checks run

The release evidence uses the hosted pull-request CI run for commit `9c622d850413d03c9b85ea3b69729888381102ce`. Its Git tree is identical to assessed `main` commit `3788d9d46e6abf4a669bdd356ddd5b0c9ac3936d`. All ten configured PR workflows completed successfully. The three Python matrix jobs each ran 2,703 tests with 15 skips. This includes the Android bookshelf and mail-case Federation changes merged in PRs #540 and #541.

| Check | Result |
|---|---|
| Python 3.10 / 3.14 / 3.15.0-rc.2 full suite | PASS (hosted CI): 2,703 tests per interpreter, 15 skipped, zero failures |
| Ruff (`ruff check app tools tests *.py`) | PASS (hosted Extended quality gates) |
| Project policy, source/function size, Python compile | PASS (hosted CI); compile also passed locally |
| Secret scan, CRA checks, CycloneDX SBOM generation and JSON parse | PASS (hosted dependency/security workflows); CRA check also passed locally |
| `pip-audit` | PASS (hosted CI): no known vulnerable dependencies. The local project is not published on PyPI and is skipped by advisory lookup; this is not a source-code audit. |
| Extended repository hygiene, JSON, shell syntax, conflict/whitespace checks | PASS (hosted Extended quality gates) |
| Mini Services log audit and one-iteration lifecycle benchmark | PASS (repository evidence from the previous assessment). The benchmark excludes cold start, protocol load, LAN throughput and total RSS, and does not measure 3.0 search/relations/jobs. |
| Pinned KoSIT/XRechnung integration (`XRechnung 3.0.2`, KoSIT validator 1.6.3) | PASS (hosted CI): all 9 validator/install tests, including official positive and negative fixtures |
| Android APK ARM32/ARM64, Desktop Windows/macOS/Linux, Docker and Debian 12 builds | PASS (hosted CI). Build success does not prove installation or use on a real device/client. |

The 15 skipped tests are optional integration checks. A local raw-interpreter run outside an installed project environment also failed the isolated packaging test because its subprocess intentionally ignores `PYTHONPATH`; hosted CI installs the project first and that test passed in all three Python jobs. No real Android device, production database, external S3 implementation, mail/Federation peer, or desktop client was connected for this assessment.

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

- **PASS (repository)** — central-policy, legacy authorization, extension capability denial, S3 object authorization, Android offline owner/scope/conflict, secret redaction, XML, path traversal and Federation trust/security suites passed in the full suite. The merged mail-case Federation tests also cover peer-bound identity mapping, active-account revalidation, participant ACLs, replay/idempotency and opaque EML references. Reader tests cover document authorization, version-bound reading state, unsafe EPUB paths and active content.
- **OPEN (external)** — validate effective permissions on a representative migrated deployment and review generated activity/job/health/extension logs from real secrets and real integrations. Automated redaction tests do not replace that deployment review.

### E. Failure and recovery

- **PASS (repository, simulated)** — tests cover persistent-job retry/lease behavior, inbox/S3 recovery, interrupted V2 operations, finance finalization state, Android version conflicts, Federation failures, mail-case outbox retry/idempotency and unavailable optional services.
- **OPEN (external)** — terminate/restart the deployed worker while jobs are active; disconnect real event/extension/Federation/S3 dependencies; interrupt a migration process; and verify operator-visible diagnosis and recovery without a permanently partial business document.

### F. Performance

- **PASS (repository, bounded behavior)** — tests exercise pagination, result limits, query bounds, indexes, streaming document paths, and health-check timeouts where implemented.
- **OPEN (external)** — measure global search, entity context, relations, activity and job queues against a representative production-sized dataset, including query counts, response latency, memory and recovery under load. The Mini Services benchmark is unrelated and cannot satisfy this requirement.

### G. Disable and rollback

- **PASS (repository, feature-level)** — capabilities default off and feature-specific tests verify disabled behavior; V2 keeps rollback/recovery data and legacy protocol tests remain available. The Android APK build succeeds for both supported ABIs, but the separate Android bookshelf implementation in #483 remains open until the APK is practically tested on an Android device or emulator.
- **PARTIAL** — code and fixtures show that deactivation need not delete the new data, but the full independent disable sequence has not been performed on an upgraded installation.
- **OPEN (external)** — separately disable 3.0 UI, automation, plugins, Android offline and Federation v3 on an upgraded system; confirm old domain stores and CardDAV/CalDAV/VTODO, document and invoice paths remain usable without deleting new data.

## Required to approve #506

Attach dated evidence for each external item above to the intended release commit, including the tested build/commit, deployment configuration, client/device/peer versions, dataset/workload characteristics, results, failures, and recovery actions. Keep #506 open until all open and partial items are closed with that evidence. Keep roadmap #505 open until its own Definition of Done and the selected 3.0 release gate are both satisfied.
