# Security-Triage-Protokoll · 2026-11-21 (Issue #874)

Stand: 2026-11-21, Branch `docs/security-triage`, Commit-Basis `3a374f94` (origin/main).
Inventur: `gh api --paginate "repos/JensKapitza/SimpleOffice4Me/code-scanning/alerts?state=open&per_page=100"`.

## 1. Alert-Bestand (317 offen, alle `refs/heads/main`)

| Quelle | Severity | Regel | Anzahl | Entscheidung |
|---|---|---|---|---|
| CodeQL | high | `py/path-injection` | 143 | Follow-up [#1154](https://github.com/JensKapitza/SimpleOffice4Me/issues/1154) |
| CodeQL | medium | `py/stack-trace-exposure` | 64 | Follow-up [#1155](https://github.com/JensKapitza/SimpleOffice4Me/issues/1155) |
| ESLint | warning | `@microsoft/sdl/no-inner-html` | 34 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| Scorecard | medium | `PinnedDependenciesID` | 17 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| Bandit | warning | `B110` (try/except/pass) | 15 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| CodeQL | high | `py/weak-sensitive-data-hashing` | 9 | begründet dokumentiert, s. §2 |
| CodeQL | medium | `py/url-redirection` | 5 | Follow-up [#1155](https://github.com/JensKapitza/SimpleOffice4Me/issues/1155) |
| Checkov | note | `CKV_SECRET_6` | 3 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| ESLint | warning | `unicorn/consistent-destructuring`, `import/no-unused-modules`, `B112`, `@microsoft/sdl/no-insecure-url` | 11 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| Checkov/Docker | warning | `AC_DOCKER_0053` (2), `CKV_DOCKER_3` | 3 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| CodeQL | high | `py/clear-text-storage-sensitive-data` | 2 | Follow-up [#1154](https://github.com/JensKapitza/SimpleOffice4Me/issues/1154) (V2-Pfad prüfen) |
| Scorecard | medium | `SecurityPolicyID` | 1 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| CodeQL | high | `py/clear-text-logging-sensitive-data` | 1 | Follow-up [#1155](https://github.com/JensKapitza/SimpleOffice4Me/issues/1155) |
| CodeQL | high | `py/polynomial-redos` | 1 | Follow-up [#1154](https://github.com/JensKapitza/SimpleOffice4Me/issues/1154) |
| CodeQL | high | `py/reflective-xss` (#449) | 1 | Follow-up [#1155](https://github.com/JensKapitza/SimpleOffice4Me/issues/1155) |
| Diverse | div. | `GHSA-…` (Cargo, medium), `CKV_GHA_7`, `FuzzingID`, `VulnerabilitiesID`, `CIIBestPracticesID` (low), `CodeReviewID`, `BranchProtectionID` | 6 | Follow-up [#1156](https://github.com/JensKapitza/SimpleOffice4Me/issues/1156) |
| Dependabot | — | API: HTTP 403 (deaktiviert) | n/a | NICHT als sauber werten, s. §3 |
| Secret-Scanning | — | per API mit aktuellem Token nicht abfragbar (Scope fehlt) | n/a | NICHT als sauber werten, s. §3 |

## 2. Verifizierte Stichproben (Code gelesen)

- `py/weak-sensitive-data-hashing` in `simpleoffice_connection_relay.py:291`:
  HMAC-SHA1 ist durch das TURN-REST-Protokoll (coturn) vorgegeben, kein
  frei gewähltes Passwort-Hashing; im Code mit `codeql[…]`-Markierung und
  Kommentar dokumentiert. Übrige 8 Treffer: 1× Testdatei
  (`tests/test_connectivity_relay.py:117`, synthetische Vektoren). Disposition:
  protokollbedingt akzeptiert, kein Fix; Restrisiko: SHA1-Kollisionen sind für
  TURN-Kurzzeit-Credentials ohne praktische Relevanz. Verbleib: Vollabgleich
  aller 9 Treffer in #1154.
- `py/reflective-xss` #449 (`app/webdav_part_2.py:363`): gemeldete Zeile zeigt
  aktuell auf Multipart-Byterange-Response (Positionsdrift seit Analyse).
  Datenfluss neu zuordnen, siehe #1155. Nicht dismissen.
- `py/url-redirection`: Inventory-Senken prüfen `/inventory/`-Präfix + CR/LF;
  Chat-Senke (`app/chat_routes.py:506`) zielt auf SIP-URI aus Telefonie-Konfig.
  Härtung/Tests siehe #1155.
- `py/clear-text-storage` (`app/document_store_core.py:93`): generischer
  `atomic_json_write`-Sink; Abgleich gegen verschlüsselten V2-Pfad siehe #1154.
- `SECURITY.md` existiert; Scorecard bemängelt fehlende Kontaktadresse —
  Eigentümer-Entscheidung erforderlich (#1156), kein Platzhalter eingetragen.

## 3. Nicht verifizierbar (keine Negativ-Aussage)

- Dependabot-Alerts sind für das Repository deaktiviert (HTTP 403) — Bestand
  unbekannt, kein „keine Dependency-Befunde“.
- Secret-Scanning per API mit dem Triage-Token nicht abfragbar — Bestand
  unbekannt, kein „keine Secrets“.
- Grüner CI-Lauf ≠ keine offenen Alerts (317 offene Alerts bei grünem Main).

## 4. Offene Punkte

- #1154 (path-injection/ReDoS/Storage), #1155 (XSS/Redirect/Logging),
  #1156 (Supply-Chain/Scorecard) abarbeiten; danach Alert-Bestand erneut abfragen.
- Dependabot-/Secret-Scanning-Abdeckung klären (#1156).
- Kein Alert wurde in dieser Triage dismissed oder gemergt (Vorgabe: PRs nur öffnen).

## 5. Nachtrag 2026-11-24: #1154 py/path-injection-Disposition (48 Alerts)

Stand: CodeQL-API, Main inkl. #1185-#1211; 48 offene `py/path-injection`
(statt 143 am 21.11. – Rest durch seitherige Merges erledigt). Jeder Alert
wurde per Datenfluss gegen Safe-Helper geprüft (4 parallele Triagen +
Integrator-Verifikation des GAP-Funds im Code).

### GAP (1 Fundstelle, 2 Alerts) – gefixt

- `app/business_document_generation.py:143,145` (`_store_generated_pdf`):
  `contact_id` (persistierbar via Kontaktformular, MCP `upsert_contact`,
  vCard/JSContact-`uid`, ohne Formatprüfung) wurde roh in den Ablagepfad
  interpoliert; `mkdir -p` + PDF-Write konnten mit `../../../../x` aus
  der Root entkommen (authentifiziert). Fix: `resolve_for_write_under`
  vor jedem Schreiben; Regressionstests `tests/test_business_path_traversal.py`
  (3/3 grün mit Fix, 3/3 rot ohne Fix).

### COVERED (47 Alerts, je mit Helfer + Restrisiko)

- WebDAV (22: part_6 alle, part_2 alle, part_3:473): hinter
  `_authenticate` + Credential-/VFS-ACL-Gates; Pfade via `_tree_path`,
  `_destination`, `_document_path` (`resolve_file_under`), Mutationen via
  `VFS.resolve`/`write_bytes`. TOCTOU kippt höchstens Statuscodes.
  Restrisiko: `part_2:248`-Fallback nutzt `path.open` statt
  `open_file_under` (nur servererzeugte Pfade, kein Exploit) – Härtungs-Notiz.
- DocumentStore (10): `_safe_managed_relative_path`, `resolve_file_under`,
  VFS-ACLs, Katalogschlüssel ohne FS-Zugriff. `part_1:693`
  (`scan_and_get_document`) scannt bewusst auch externe Pfade (Feature mit
  `[external]`-Markierung, Test vorhanden); alle Aufrufer intern.
  Restrisiko: Containment dort nur per Docstring-Vertrag; Aufrufer-Audit
  liegt vor, kein Request-Pfad gefunden.
- Screen (8) + remote_app_catalog (1): `_path`-Allowlist
  `[A-Za-z0-9_-]{1,80}` + festes `.json` (Traversal strukturell
  unmöglich); Katalog admin-only mit Absolut-.exe-Prüfung + Blocklist +
  SHA-256-gepinntem Launch-Recheck. Restrisiko: TOCTOU `is_file`→`open`
  nur innerhalb Admin-Vertrauensgrenze.
- Business-Editor (3): UUID-Allowlist für `template_id`,
  `ASSET_NAME`-Allowlist für Assets, Registry-Lookup, Admin-Gates.
  Restrisiko: keine direkten Traversal-Regressionstests (Happy-Path only).

### Testlücken (keine Blocker, dokumentiert)

Fehlende gezielte Traversal-Regressionstests: screen_support
(`../`-404), Editor-UUID/Asset-Strings, Copy-Ziel-Traversal,
Extraktor-Pfade, WebDAV-Symlink-Reject direkt. Generische Abdeckung
besteht (`test_safe_paths.py`, Symlink-/Traversal-Tests).

Kein Alert wurde pauschal dismissed.
