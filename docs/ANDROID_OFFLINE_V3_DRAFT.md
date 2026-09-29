# Android Offline 3.0 – Kern

Issue #503 wird schrittweise umgesetzt, weil die bestehende APK kein Remote-WebView-Client ist: Sie startet eine vollständige SimpleOffice-Instanz lokal auf Android. Ein Remote-Sync darf deshalb nicht still eine zweite Autorität oder einen unkontrollierten Vollabzug erzeugen.

Dieser erste Baustein stellt den lokalen, additiven Cache-/Outbox-Kern bereit. Er ist über die bereits vorhandene Capability `v3.android_offline` weiterhin standardmäßig deaktiviert und verändert keine bestehenden Domain-Stores.

## Sicherheits- und Datenmodell

- Offline-Daten sind immer einem Benutzer und einem expliziten Workset zugeordnet.
- Ein Workset enthält höchstens 500 ausdrücklich ausgewählte Entity-Referenzen.
- Nur `document`, `project`, `task` und `calendar_event` sind aktuell offline erlaubt.
- Kontakte, Finance und Mail bleiben in diesem Stand online-only.
- Cache-Payloads verwenden pro Entity-Typ eine feste Feld-Whitelist. Unbekannte Felder werden abgelehnt.
- Ein Cache-Eintrag benötigt eine Server-Version oder ETag und besitzt eine begrenzte Retention von maximal 30 Tagen.
- Entfernt der Benutzer eine Entity aus einem Workset, wird deren Cache-Eintrag ebenfalls entfernt.
- Benutzer sehen über den Store ausschließlich ihren eigenen Cache. `purge_except(...)` ermöglicht dem Android-Bootstrap später, Daten eines vorherigen Kontos beim Accountwechsel zu entfernen.
- `purge_principal(...)` ist der Löschpfad für lokale Offline-Daten eines Kontos.

## Outbox

Als erste mutierende Offline-Aktion wird ausschließlich der Aufgabenstatus unterstützt.

Jeder Eintrag enthält:

- eine Operation-ID,
- Workset und Task-ID,
- die Basisversion vom Server,
- den neuen Status,
- einen Digest für Idempotenz,
- den lokalen Zustand `pending`, `syncing`, `applied`, `conflict` oder `failed`.

Die gleiche Operation-ID mit identischem Inhalt ist idempotent. Wird dieselbe ID mit verändertem Inhalt erneut verwendet, wird sie abgelehnt. Ein Versionskonflikt bleibt als `conflict` sichtbar; es gibt kein stilles Last-Write-Wins.

## Bewusste Grenze dieses Teil-PRs

Der vorhandene Android-Build arbeitet ausschließlich gegen das lokale Flask-Backend. Deshalb implementiert dieser Teil noch keinen Remote-Transport zu einer autoritativen SimpleOffice-Serverinstanz und bewirbt noch keinen vollständigen Offline-Sync.

Der nächste technische Schritt ist ein klar authentifizierter Sync-Transport zwischen der eingebetteten Android-Instanz und einer ausdrücklich konfigurierten autoritativen Serverinstanz. Dieser Transport muss Rechte auf dem Server erneut prüfen, nur die ausgewählten Worksets übertragen und Konflikte anhand der gespeicherten Basisversion zurückmelden.

Issue #503 bleibt bis zu diesem Transport, der Android-Bedienung und der praktischen Netzverlust-/Accountwechsel-Abnahme offen.
