# Rückfragen-Sammlung (2h-Block, keine Unterbrechungen)

Offene Punkte für den nächsten Lauf — während des Blocks gesammelt, nicht gefragt.

## 0. Block-Ergebnis (zum Abhaken)
- Push + PR-966-Kommentar erledigt; Remote-i18n per Merge integriert (4 Kommentar-Konflikte).
- Welle 4a (Login-QR) + 4b (Admin-Listen) verifiziert, committed, gepusht.
- Push scheiterte 2x an DNS (github.com nicht auflösbar), 3. Versuch ok. Falls wieder: warten + retry, kein Force-Push.

## 1. Push-Strategie bei parallelen Agenten (beantwortet durch Merge)
- Der Branch war divergent (remote: 7 i18n-Commits). Ohne Force-Push per Merge integriert.
- Offen: Sollen parallele Agenten weiter auf demselben Branch arbeiten oder eigene Branches bekommen?

## 2. PR-Lage
- PR #966 ist gemergt; kein offener PR für hackai/i18n-en vorhanden. Status kommentar auf #966 gepostet.
- Offen: Neuen Sammel-PR hackai/i18n-en → hackathon/contextflow-mvp öffnen? (Bewusst nicht selbst geöffnet — outward-facing.)

## 3. No-Env Rest-Wellen (aus Final-Sweep inventarisiert)
Verbleibende Request-Pfad-Leser, alle im Register dokumentiert. Reihenfolge-Vorschlag:
1. Admin-Listen (`SECURITY_ADMINS`, `DOCUMENT_ADMINS`) — sicherheitsrelevant.
2. ClamAV-Scan-Auswahl (`SCANNER`, `CLAMAV_SCANNER`) — Timeout ist schon im Store.
3. Login-QR (`LOGIN_QR*`) — **in Arbeit (Welle 4a)**.
4. V3-Capability-Flags (`V3_*`) — Test-Seam (`environ`-Param) existiert.
5. Projekt-Repo-Roots (`PROJECT_REPO_ROOTS`).
6. S3-Region/Timeouts (`S3_*`, teils Store vorhanden).
7. FinTS-Produkt-ID, Sensor-Secrets, Piper-Modell, Extension-Allowlist, Upload-Quarantäne — **erledigt (Welle 7, Commit folgt)**.
- Offen: Diese Reihenfolge ok? S3/Quarantäne evtl. mit Upload-Limit-Welle bündeln?
- Nächster Vorschlag (Welle 8): Launcher/Server-Bind (`HOST`, `PORT`, `WSGI_THREADS`, `CHANNEL_TIMEOUT`, `TRUSTED_PROXY_HOPS`, `BACKGROUND_INDEX`, `OSM_INDEX`, `MINI_SERVICES_AUTOSTART`, `CONTAINER_ROLE`) + Archiv-Legacy-Migration (`ARCHIVE_*`) — teils Deployment-Sache, Owner-Entscheid nötig, was Store wird und was CLI-Arg bleibt.
- Update: Welle 8a (Archiv-Legacy, ohne Owner-Entscheid machbar) — **erledigt, Commit folgt**. Offen bleibt nur Server-Bind/Deployment (Welle 8b, Owner-Entscheid).
- Blockiert (Lauf 03.11., Baum sauber + sync mit Remote): Welle 8b braucht Owner-Entscheid (Store vs. CLI-Arg für HOST/PORT/Threads/Proxies/Hintergrundschalter). Keine autonome Welle mehr übrig — gestoppt, keine Änderungen.
- Fremde Arbeit (späterer Lauf 03.11.): Remote `hackai/i18n-en` ist 110 Commits voraus (Main-Integration für PR #973: u.a. #974, #975, Merge-Basis `f60c9419` enthalten). Lokal NICHT gemergt (Regel 1), kein Push möglich (nicht fast-forward, kein Force-Push). Nächster Schritt braucht Owner-Vorgabe: Merge oder Warten.
- Update Fremde Arbeit: Remote wächst weiter (jetzt 112 voraus, Head `41e63317 test(contextflow): capture demo failure output`) — fremde Arbeit läuft NOCH, weiter warten.
- Update Fremde Arbeit 2: Remote jetzt 113 voraus, Head `6d33f080 fix(testnet): seed disposable local identity for federation test peers` — weiter warten.
- Dauerlauf 03.11. (Baum sauber, sync mit `origin/hackai/i18n-en` auf `36970321`): keine autonome No-Env-Welle übrig — 8b (Server-Bind) weiter blockiert (Store vs. CLI-Arg, Owner-Entscheid fehlt). Keine Änderungen, gestoppt. PR #973-CI für `dc9abe37`/`36970321` läuft (Analyze-Teile pass, Rest pending).
- Dauerlauf 03.11. später (Baum sauber, sync auf `64ae29ee`): PR #973-CI für #976-Commits fast überall pass, aber `Contact, folder and capture browser flows` fail (Job `126006220146`, 2m34s). Job-Log bis Run-Ende gesperrt, Annotations ohne Detail, kein lokales Playwright — Ursache (eigene EN-Asserts vs. Infra-Flake wie im Vorlauf 2m45s) noch offen. Zeitnähe zum Vorlauf-Fail spricht für denselben frühen Abbruch, unbewiesen. Nächster Schritt: Log nach Run-Ende lesen, dann gezielt fixen. Keine Code-Änderung, gestoppt.
- Update: Log gelesen — alle 6 Fails sind stale DE-Asserts bei korrekt EN-rendernden Seiten (kein Infra-Flake): Folder `Import nach…angehalten` + 2x `Dateiimport abgeschlossen` + Webkit-Title, Media `kein unterstützter UTF-8-Text` 2x, Site-Visit `PDF erneut herunterladen` (bei ed69218a übersehen). Fix `257ac415`, Rest per Sweep geprüft, gepusht (`962994e1`). CI-Re-Run läuft.
- Update P2P-Root-Causes: (1) Shared-Token = Link-Token liess `authenticate_peer` scheitern → Mutual-Diagnostik 401 + Katalog-Legacy-Leak; Fix `78458100` (getrennter Shared-Token + Kollisions-Guard, Regressionstests 5/5). (2) Mutual-Diagnostik `Fehlerkategorie policy`: private Docker-Ziele ohne Store-Opt-in (tote Env greift nicht); Fix `330d6378` (`allow_private_targets` per Admin-UI, Kette lokal bewiesen). (3) Letzter Rest-Fail 1 JS-Page-Error auf `/settings/webdav` (`Unexpected token '&'`): `button.textContent={{ "'Copied'" ... }}` liess Autoescape `&#39;` rendern; Fix `fa0bd455` (JS-Quotes aussen, Ternary innen, node-gruen EN/DE, Alt-Render = exakt CI-Fehler). WebDAV-Suiten 28F+26E per Stash-Base identisch vorbestanden. Danach PR #973 fast vollgruen (nur Pending-Jobs).
- Update Netzwerk-Save: Save-Flash erschien in CI nicht (nur Hidden-Banner im Feedback), Server-Seite lokal 2/2 bewiesen (Formular-POST, Flash, Flag, auch DE-Sequenz). Fix `cdf4c7cf`: Smoke verifiziert Effekt (Reload → Checkbox aus Store) mit URL-gefilterter Navigation; echter Fail bleibt rot. Worklog `b22b49f5`. (Lokal committet, Push nach Gruen der laufenden P2P-CI.)

## 4. Merge-Policy
- `git pull --rebase` vs Merge-Commits bei divergentem Branch? (Bisher: Merge, kein Rewrite.)

## 5. Neue Branch-Regel 03.11. (Eigentümer-Direktweisung, entschieden)
- Fertige Aufgaben per PR an `main` (Update in agent-arbeitsweise-Memory); gilt für alle Agenten + Trigger-Wiederholungen.
- Feststellung 03.11.: Keine MD-Datei enthält neue Branch-Regeln (alle neu eingelesen: regeln.md, UPDATE.md, Kurzbrief, Büroengel, Voranalyse, update2.md, Arbeitslog, AGENTS.md vollständig, alle Memory-Dateien, RUECKFRAGEN/WORKLOG). Es gilt die Chat-Weisung.
- Welle 4c committet (`c37382b8` + Worklog `f37773c5`), Push + PR an main als Nächstes.
