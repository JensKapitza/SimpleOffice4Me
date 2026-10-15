# SimpleOffice4Me 3.0 release-gate evidence

## Current repository update — 2026-10-15 (main `8a71b7d0`)

Implementation basis: `main` at
`8a71b7d0144979c2b70e2de75b6c095c0a9f3108`. Package metadata still
declares version 2.0.0. This update records repository work only; it does not
approve or declare the 3.0 release.

PR #711 (`d0f2eef3`) admits CPython 3.15 final through runtime policy,
packaging, launchers, CI and operator documentation while continuing to reject
prereleases. The final-runtime gate does not change the separate Canonical
Ubuntu 22.04 Python 3.10 exception or establish production deployment
compatibility.

PRs #712 (`775efc69`), #713 (`1f8383e8`) and #714 (`84c7829f`) complete
the repository-side one-time-code pairing bootstrap from #679. A claimant signs
the canonical peer identity, persistent Ed25519 key/fingerprint, timestamp,
nonce and code. The issuing administrator can create, inspect and revoke only
the non-secret lifecycle state; the claimant administrator must confirm the
pinned identity and destination before sending the signed claim. Codes and
digests are excluded from responses, audit details and persistent claimant
state. Unknown or changed keys, stale/replayed requests, blocked peers, invalid
codes and identity drift fail closed.

This bootstrap deliberately does not grant trust, enable a peer, install a
shared connection token or add data capabilities. Existing local approval and
deny-by-default capability policy remain authoritative. The successful complete
PR gates covered the Python 3.14 and 3.15 application suites, dependency and
security scans, CodeQL, OSSAR, Scorecard, Android, Desktop, Docker and packaging.
These hosted contracts do not prove operation with an external peer or a
production network.

PR #715 (`4fbfb68d`) adds a real two-container restart/recovery smoke to the
Ubuntu 26.04 Docker job. It starts two instances with independent identities and
volumes on the isolated test network, verifies that a global bearer and an
unpaired transfer are rejected, establishes separate local approvals, exercises
the bidirectional pending path, restarts one peer and verifies persistent
duplicate detection. Failure diagnostics include container state and logs, and
the disposable containers, network and volumes are always removed. All seven
workflow groups passed on the final head, including both Python application
runs, Docker restart smoke, Android, Desktop, server packaging, dependency and
security gates, CodeQL, OSSAR and Scorecard.

That smoke is reproducible hosted-Docker process and persistence evidence. It is
not evidence for a real LAN, an external peer, hardware, representative load,
long-running operation or the optional explicitly authorized relay. Issue #679
therefore remains open for those remaining scope and acceptance points.

PR #716 (`8a71b7d0`) adds a bounded administrator-only BibTeX book import to
the existing ObjectStore, authentication, CSRF and audit paths. The data-only
parser accepts UTF-8 `@book` records and the fields emitted by the existing
export, but rejects macros, concatenation, duplicate fields, unsupported entry
types, malformed records, oversized values and partial imports. Existing ISBNs
are skipped; bibliographic notes round-trip without inventing shelf locations.
A CodeQL review finding on exception-text disclosure was fixed before merge by
returning a stable error and logging only the exception type.

The final #716 head passed all seven workflow groups, including the complete
Python 3.14/3.15 application suites, Extended Quality, dependency/security
checks, CodeQL, OSSAR, Scorecard, Android, Desktop, Docker, Debian/Windows
packaging and XRechnung. Local parser black-box and syntax checks passed; Ruff
was not installed locally and is therefore evidenced only by CI. No external
BibTeX application, physical camera/printer or Android device was used.

Issue #658 remains open for the work/edition/copy model, wider capture workflows
and real camera/printer/Android acceptance. The broader external
production-data upgrade/rollback, client/device/peer, representative-load and
long-run evidence in #505/#506 also remains outstanding. Release approval
remains **not approved**.


## Earlier repository update — 2026-10-14 (main `bbc228a2`)

Implementation basis: `main` at `bbc228a2c6ec34e73b30df3356c01597c1674921`.
Package metadata still declares version 2.0.0. This update records repository
work only; it does not approve or declare the 3.0 release.

PR #701 (`d0f952b0`) adds an administrator-only Calibre metadata archive import
beside the existing library workflow. It reuses the ObjectStore, skips duplicate
ISBNs, bounds archive and row sizes, and does not execute OPF files. PR #699
(`58156ea8`) adds a CSRF-protected PNG preview using the existing label renderer
without printing. PR #700 (`badbbddc`) adds server-side author, genre, location,
and free-text filters; visible filtered books drive existing label, preview,
PDF, and A4 flows. When no rows are selected, the same active filters scope CSV,
RIS, BibTeX, and Calibre exports. Explicit selections take precedence, the
500-row limit remains, and a filter with zero matches never falls back to the
whole catalog. A manual Chromium smoke check confirmed the filter form reaches
the library page and the visible selection reflects the match.

PRs #699, #700, and #701 each passed 27 CI checks; three optional jobs were
skipped for each run. Local verification after integrating these slices passed
72 library tests, Ruff, compile checks, and the repository's relevant size and
diff gates. The browser check was a UI smoke test, not physical printer or
Android-device acceptance.

PR #708 (`23aee932`) adds only the secure storage/lifecycle foundation for
federation pairing: one-time 60-bit codes are stored as HMAC digests, bound to
the expected peer, limited to 1–15 minutes, and protected against replay,
replacement, revocation, oversized input, and caller-controlled time. Concurrent
claims allow one winner. The full CI suite, CodeQL, dependency/security scans,
OSSAR, Android, Desktop, Docker, and other required checks passed; 294 local
Federation tests, Ruff, compilation, and diff checks passed. This does not add
claim/status HTTP endpoints or explicit bilateral administrator confirmation,
and does not grant trust, activation, or capabilities.

PR #709 (`bbc228a2`) integrates bounded RIS book import for issue #658. It
reuses the existing ObjectStore, administrator/authentication/CSRF checks,
ISBN duplicate policy, and audit path. UTF-8 `BOOK`/`EBOOK` records are limited
to 4 MiB and 500 records; imported `N1` notes survive a later RIS export without
creating a shelf location. A title-less record rejects the complete upload
before any objects are created. The six targeted RIS HTTP contracts and all 78
library tests passed locally, along with Ruff and `git diff --check`. At merge,
the full Python application, separate Python CodeQL, static quality, security,
dependency audit, OSSAR, Scorecard, Android, Desktop, Docker, and XRechnung
checks had passed. The six standard CodeQL language-matrix jobs were still
queued at that time and are recorded as pending, not passed. This does not
establish interoperability with every RIS vendor or external 3.0 acceptance.

Issue #658 remains open for broader library model/provider/capture requirements
and real printer/Android acceptance. Issue #679 remains open for authenticated
pairing, conscious confirmation at both peers, capability deny-by-default
verification, failure/restart recovery, and any explicitly authorized relay.
The external production upgrade/rollback, client/device, representative-load,
and hardware evidence in #505/#506 also remains outstanding. Release approval
remains **not approved**.


## Earlier repository update — 2026-10-14

Implementation basis at that snapshot: `main` at `c9db30b171a5661f7abc20e7bd90ea6085102fb3`
(including PRs #687, #689, #690, #692, #691 and #695). Package metadata still
declares version 2.0.0; no 3.0 release or release approval is implied.

PR #682 extends the existing administrator-only library export with RIS and
BibTeX alongside CSV. It reuses the existing local-root, selection,
authorization, and audit path. Its public HTTP contracts verify exact output,
authorization, invalid/foreign selections, size limits, and metadata injection
handling. At that merge, #658 still needed work/edition models, provider
metadata, capture and label workflows, and real Android/device acceptance.

All PR #682 gates passed on head
`c3bd6b830c3f027770d3e735b3966e5b4a7ef3e1`: Python application suite,
Desktop Linux, Android, Docker, dependency/security checks, and all six standard
CodeQL language analyses. Rust CodeQL completed successfully in 5m09s. No
CodeQL language or check was removed or suppressed; the older Rust failure was
not reproduced on the current tree. This is CI evidence at the PR head, not
external 3.0 acceptance.

PR #685 completes the Calibre CSV/OPF export slice of #658. The administrator
export reuses the canonical ObjectStore, selection validation, configured data
root, placement records, and audit path. `metadata.csv` contains Calibre's
standard catalog columns; each selected book has one metadata-only OPF sidecar.
The archive is produced in bounded temporary storage from one record pass and
does not contain original documents. Black-box HTTP contracts verify exact
metadata mapping, XML escaping, formula neutralization, XML 1.0 control filtering,
authorization, local-root confinement, selection bounds, archive contents, and
audit result. These tests do not claim a manual import into a running Calibre
installation. At this merge, #658 still needed work/edition models, metadata
providers, additional capture workflows, Zitavi import round-trip, and real
camera/printer/Android acceptance.

All required PR #685 CI gates passed on head
`d65ed17c63c547e926d5d847f663d21245862aff`: full Python application suite
(6m47s), Desktop Linux, Android ARM64, Docker, dependency/security/OSSAR, and all
six CodeQL language analyses. Rust CodeQL completed in 5m15s; the separate
Python CodeQL workflow also passed. Locally, 19 export/auth tests, 43 library
storage/OCR/printer/security tests, and 11 frontend route contracts passed.

PR #683 merged as `b99116ff6d8e361ddeeac0b74de8b41b9a8ab2d7` for issue #330.
It handles DNS/socket permission failures in LAN and SIP route probes and
documents the bounded fallback behavior. All required CI gates passed on PR
head `38b1b42bdbe7fe9c3d88e45606da68879dafa576`, including the full Python
application suite (7m42s), Desktop Linux, Android, Docker, dependency/security
checks, and all six standard CodeQL language analyses. Rust CodeQL completed
successfully in 5m09s. No CodeQL analysis was removed or suppressed. The issue
remains open for external/hardware and broader acceptance not established by
these repository tests.

## Current main verification — 2026-10-13

PR #687 merged as `0e5ad1d5f3227389902941d683d0ec728329fbe0`. It adds selected
book-label printing to the existing library page using the shared Code-128 /
Brother print path, existing inventory objects, login/CSRF protection, printer
settings, and audit store. The request validates every selected object before
printing, limits batches to 100, and reports irreversible partial progress.
The two existing label variants are barcode with readable title/author and
barcode only. All required PR checks passed; locally, 47 library, export,
OCR, HTTP, and printer-security tests passed. This establishes the software
slice only: there was no physical-printer or Android device acceptance, and
PDF/QR templates, work/edition modeling, metadata providers, and other #658
capture requirements remain open.

PR #689 merged as `3c52d6b2d8dde1319fc18a3c53404054b906c4bb` and PR #690 as
`4d4a95ef5abe2658e04656f6b707b27cb998dd2f`. The Federation UI now carries a
scanned QR profile into the existing peer editor for explicit review; a new
peer remains disabled until separately configured. Discovery updates identity
and compatibility metadata without changing an existing peer's URL, token,
enabled state, or policy, and discovered addresses remain suggestions for
manual review. The changes passed their required CI; local Federation HTTP/UI
and accessibility tests passed, and a Chromium smoke verified the direct
discovery suggestion while preserving configured values. This does not prove
bilateral short-lived pairing, real LAN/external-peer operation, production
upgrade/rollback, or the remaining #679 acceptance.

PR #692 merged as `d80ca6bc7663aa0a89e54d0fa46208d2a9d785a6`. Selected books
can also be downloaded as a print-ready PDF with one page per unique book at
the configured Brother media dimensions, using either existing barcode style.
The API reuses the canonical book records and label validation, requires login
and CSRF, and bounds each request to 100 references. Ten public HTTP contracts
cover media dimensions, page count, response type/name, deduplication,
authentication, CSRF, invalid selections, and the batch boundary. The complete
Python application CI passed in 7m40s; all CodeQL, Security/OSSAR, Android,
Desktop, and Docker gates passed. A 390px Chromium smoke confirmed the existing
library selection downloads a valid one-page PDF and announces success. This
does not establish physical-printer or Android acceptance; QR codes, preview,
sheet templates, filtered author/genre/location labels, and remaining #658
requirements stay open.

PR #691 merged as `a92e71a6f5d99c9bfacce67e80447efbc0233f9a`. The existing
ISBN lookup now returns up to three bounded metadata candidates from the
providers it actually queried. The library form shows the source and warns when
candidate titles disagree; it never auto-saves a candidate or overwrites manual
values unless the existing replace option is enabled. Public HTTP tests cover
authenticated candidate delivery, invalid ISBN rejection before provider access,
and the endpoint's `no-store` response. Local validation passed 37 targeted
inventory/library contracts plus repository policy, file/function-size, Ruff,
compile and diff checks. Full Python CI passed in 7m09s; all six CodeQL language
analyses, security/OSSAR, dependency audit, Android, Desktop, Docker and other
required checks passed. This adds provider conflict review, not the missing
real-provider, import-roundtrip, camera, printer or Android acceptance in #658.

PR #695 merged as `c9db30b171a5661f7abc20e7bd90ea6085102fb3`. The existing
selected-book label workflow now supports QR labels with or without readable
text in both direct printing and PDF. QR payloads contain the stable ObjectStore
UUID and are resolved through the existing authenticated `/library/find` scan
path. This reuses the canonical book records, Brother media settings, printer
audit, and the current authentication/CSRF path. Sixty `test_library*.py`
contracts and all required PR checks passed, including Python application,
Security/OSSAR, Android, Desktop, Docker, dependency audit, and all six CodeQL
language analyses. A CodeQL alert on an intermediate test stub was fixed before
the final green run. This does not document physical-printer/Android device
acceptance; QR preview, filtered author/genre/location labels, and other #658
requirements remain open.

PR #697 merged as `7ea6897a42bd0c887d9c2f0a01e65bcd9c3070a9`. The existing
selected-book PDF export now also offers a centered A4 3×7 sheet with 21
63.5 × 38.1 mm cells per page; further labels flow to additional pages. The
existing printer-media PDF remains the default. Public HTTP contracts verified
22 selected labels across two A4 pages (21+1 embedded label images), exact A4
dimensions, and rejection of an unknown layout. All required PR CI checks
passed, including the complete Python application suite, six CodeQL language
analyses, Security/OSSAR, Android, Desktop and Docker. Physical alignment with
specific label stock remains unverified; #658 also remains open for filtered
author/genre/location labels, wider capture and data-model requirements, and
real device acceptance.

Release approval remains **not approved**. Repository software progress does
not replace the still-open production-data upgrade/rollback, external-client/
peer/device, representative-load, hardware, and long-run acceptance in #505/#506.

## Federation test network implementation — 2026-10-13

Implementation basis: `main` at `aca00cb0bce25e2910433adf9bf6effcfdd92a10`
(PR #680). The package still declares version 2.0.0; this merge does not
represent a 3.0 release or change the release decision below.

PR #680 adds a reproducible two-instance Compose federation test network with
separate peer identities and persistent test volumes, an internal-only network,
no published host ports, healthchecks, and a public discovery smoke test. The
network was built and started locally; both containers became healthy and the
public discovery API returned each peer's independent identity and Compose
URL. This is isolated repository/runtime evidence, not a real LAN, external
peer, trust/pairing, or production deployment acceptance.

All PR #680 checks passed at head `8e25ee2c6dd69e0f6326be603ad024820a256db5`,
including the full Python application suite, Docker/Android/Desktop builds,
security scans, and all six standard CodeQL language jobs. In particular,
`Analyze (rust)` completed successfully against the current Tauri/Rust tree;
the former “CodeQL could not process any code written in Rust” result from a
stale PR ref did not recur. No CodeQL check was removed or suppressed. Main's
post-merge workflow runs were still completing at the time of this assessment;
their results must be evaluated from the linked CI runs before treating the
merge as post-merge verified.

This evidence advances only the repository-side federation test setup. Pairing
with explicit confirmation at both ends, capability-deny-by-default across
instances, restart/error/recovery behavior, external clients/devices,
production-data upgrade and rollback, and representative-load acceptance remain
open. Release approval remains **not approved**; #505/#506 stay open.

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

## Runtime tracking — 2026-10-05

The additional compatibility job now selects Python `3.15.0-rc.3` rather than
RC2. Publication was verified against the official
[CPython RC3 tag](https://github.com/python/cpython/releases/tag/v3.15.0rc3)
and the [GitHub Actions runtime release](https://github.com/actions/python-versions/releases/tag/3.15.0-rc.3-37256039299),
published on 2026-10-05 with an Ubuntu 24.04 x64 build. Canonical Ubuntu 22.04 system Python 3.10 (limited vendor exception), 3.11 and 3.14
remain in the required matrix. Issue #474 remains an ongoing tracker; RC3 is
not a final Python release or approval for SimpleOffice4Me 3.0. Historical
RC2 test counts below remain evidence for their original commits.

Issue #588 has meanwhile been resolved by merged PRs #597 and #599, including
MIME-decoded document search and Legacy/Shadow/V2 cutover coverage. Issue #587
is addressed by the subsequent recovery implementation described below. This status
update adds no external client, device, hardware or production-data acceptance.

## IMAP recovery evidence — 2026-10-05

The existing archive now durably records open UIDs before processing, binds them
to UIDVALIDITY and source configuration, and retries storage, metadata and
confirmed attachment work without duplicate documents. Old checkpoints undergo
one bounded reconciliation pass; a failed checkpoint write stops the run.
The mail page exposes pending stages and notices for superseded namespaces.

`tests/test_imap_archive_recovery.py` exercises public archive and HTTP contracts
with synthetic messages and injected I/O/process aborts, using independent byte,
count and output expectations. Coverage includes Legacy, Shadow, authoritative
and encrypted V2, migration compatibility, permissions, UID ordering/limits,
namespace changes, and restart boundaries around original and attachment commits.
These are repository-level recovery tests, not production-mailserver, real-device,
load or release approval. The remaining #505/#506 acceptance gates stay open.

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

Runtime support after upstream 3.10 EOL is defined in [PYTHON_RUNTIME_SUPPORT.md](PYTHON_RUNTIME_SUPPORT.md).
Historical 3.10 runs below are compatibility evidence, not upstream security support.
Python 3.15 final is a supported production runtime; prereleases remain rejected.

## Runtime tracking — 2026-10-14

[Python 3.15.0 final](https://www.python.org/downloads/release/python-3150/) was
published on 2026-10-09. The runtime policy, package classifiers, launchers and
CI now distinguish the final release from the former RC3 probe. Pull requests
run a complete Python 3.15 final install, policy, compile, size, dependency,
security and unit-test gate; the main-branch runtime matrix retains 3.15.
Approval does not relax the separate Canonical Ubuntu 22.04 Python 3.10
exception and does not admit alpha, beta or release-candidate interpreters.
