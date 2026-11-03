# Rückfragen-Sammlung (2h-Block, keine Unterbrechungen)

Offene Punkte für den nächsten Lauf — während des Blocks gesammelt, nicht gefragt.

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
7. FinTS-Produkt-ID, Sensor-Secrets, Piper-Modell, Extension-Allowlist, Upload-Quarantäne.
- Offen: Diese Reihenfolge ok? S3/Quarantäne evtl. mit Upload-Limit-Welle bündeln?

## 4. Merge-Policy
- `git pull --rebase` vs Merge-Commits bei divergentem Branch? (Bisher: Merge, kein Rewrite.)
