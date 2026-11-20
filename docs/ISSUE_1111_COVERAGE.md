# Issue #1111 – Universal Object, Storage and Library Management: Coverage

This document maps the specification in
[#1111](https://github.com/JensKapitza/SimpleOffice4Me/issues/1111)
to the implemented slices. Each slice ships with blackbox HTTP tests,
passing quality gates and a Soll-Ist entry in `agenten_mesh.md`.

Status legend: **Done** (merged), **In review** (open PR, CI running),
**Open** (planned follow-up with reason).

## P1 – Data model and storage

| Spec item | Status | Implementation |
|---|---|---|
| Stable object IDs, free typing (Object default, Book second) | Done (pre-existing) | `ObjectStore`: UUID + sequence, free `type` |
| Physical copies with own IDs, own location, own state | Done | [#1116](https://github.com/JensKapitza/SimpleOffice4Me/pull/1116): generic copies; books keep their flow |
| Hierarchical storage locations (building → box) | Done (pre-existing) | `LibraryStore` with path/code/assignment |
| Move whole boxes recursively, keep paths | In review | [#1112](https://github.com/JensKapitza/SimpleOffice4Me/pull/1112) |
| Location templates (shelf, cabinet, box, drawers) | Done | [#1114](https://github.com/JensKapitza/SimpleOffice4Me/pull/1114) |
| Bulk quantities vs single pieces, serials | Done | [#1115](https://github.com/JensKapitza/SimpleOffice4Me/pull/1115) |
| Bulk booking with reasons | In review | [#1133](https://github.com/JensKapitza/SimpleOffice4Me/pull/1133) |
| System vs manual metadata values per field | In review | [#1118](https://github.com/JensKapitza/SimpleOffice4Me/pull/1118) |
| QR labels for locations | In review | [#1121](https://github.com/JensKapitza/SimpleOffice4Me/pull/1121) |
| Scan modes: store and remove | In review | [#1130](https://github.com/JensKapitza/SimpleOffice4Me/pull/1130) |
| Scan modes: relocate, count | Open | Needs [#1112](https://github.com/JensKapitza/SimpleOffice4Me/pull/1112) and [#1117](https://github.com/JensKapitza/SimpleOffice4Me/pull/1117) merged |
| Change history | Done (pre-existing) | Events + `RevisionHistory` |
| Photo capture with batch assignment | Done (pre-existing) | Capture inbox + batch flows |

## P2 – Import, inventory, care

| Spec item | Status | Implementation |
|---|---|---|
| CSV mass import with preview, duplicates, error report | In review | [#1119](https://github.com/JensKapitza/SimpleOffice4Me/pull/1119) |
| CSV quantity/unit columns | Open | Needs [#1119](https://github.com/JensKapitza/SimpleOffice4Me/pull/1119) merged |
| Inventory with difference report | In review | [#1117](https://github.com/JensKapitza/SimpleOffice4Me/pull/1117) |
| Object relations (accessories, kits) | In review | [#1120](https://github.com/JensKapitza/SimpleOffice4Me/pull/1120) |
| Virtual collections without relocation | In review | [#1122](https://github.com/JensKapitza/SimpleOffice4Me/pull/1122) |
| Temporary locations with return dates | In review | [#1124](https://github.com/JensKapitza/SimpleOffice4Me/pull/1124) |
| Object conditions and lifecycle | In review | [#1126](https://github.com/JensKapitza/SimpleOffice4Me/pull/1126) |
| Location label sheets (media + A4) | In review | [#1132](https://github.com/JensKapitza/SimpleOffice4Me/pull/1132) |
| Warranty periods and purchase value | In review | [#1128](https://github.com/JensKapitza/SimpleOffice4Me/pull/1128) |
| Maintenance overview | In review | [#1125](https://github.com/JensKapitza/SimpleOffice4Me/pull/1125) |
| Receipt/invoice linking | Done (pre-existing) | Document attach/detach on `documents/object_detail` |
| Photo recognition (ML) | Open | Needs model infrastructure; capture + OCR exist |

## P3 – Lending, stock, federation

| Spec item | Status | Implementation |
|---|---|---|
| Object lending with due dates and history | In review | [#1123](https://github.com/JensKapitza/SimpleOffice4Me/pull/1123) |
| Reservations with date ranges | In review | [#1129](https://github.com/JensKapitza/SimpleOffice4Me/pull/1129) |
| Lending-vs-reservation check | Open | Needs [#1123](https://github.com/JensKapitza/SimpleOffice4Me/pull/1123) merged |
| Minimum stock with warnings | In review | [#1127](https://github.com/JensKapitza/SimpleOffice4Me/pull/1127) |
| Per-object federation sharing flag | In review | [#1131](https://github.com/JensKapitza/SimpleOffice4Me/pull/1131) |
| Rights per identity/group/peer | Open | Needs trust-model design (multi-PR architecture) |
| Offline sync | Open | Architecture topic after queue drain |
| E-mail reminders | Open | Needs mail-subsystem design (config exists, no sender/queue) |

## Verification

Every slice above ships `tests/test_*_http.py` blackbox tests that drive
real HTTP routes (create → act → assert via GET/JSON), plus the standard
gates: `check_project_policy.py`, file/function size checks, `compileall`,
`unittest` (new + neighbor suites) and `ruff`.
