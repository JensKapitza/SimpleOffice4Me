# Peer-Verwaltung, Meldungen und signierte Blacklists

Unter **Federation → Peer-Verwaltung** können Administratoren auf dem Master
und auf jeder anderen Instanz Peers sperren, Meldungen prüfen und Blacklists
ausdrücklich abonnieren. Der bestehende Build-Lizenzmaster wird als Master
gekennzeichnet; seine Lizenz- und Abrechnungsidentität wird nicht verändert.
Die Moderationsfunktionen sind auch auf normalen Peers verfügbar.

## Eigene Sperren

Eine Sperre benötigt eine genaue Peer-ID, einen Grund und eine Bestätigung.
Sie ist dauerhaft oder läuft nach 1 bis 8760 Stunden ab. Eigene Sperren bleiben
standardmäßig lokal. Nur mit **veröffentlichen** erscheinen Peer-ID, Grund,
Erstellungszeit und Ablauf in der signierten Blacklist. Deshalb keine
Dokumentinhalte, personenbezogenen Sachverhalte oder Zugangsdaten in einen
veröffentlichten Grund schreiben.

Eine aktive Sperre übersteuert die konfigurierte Aktivierung eines Peers,
ohne seine Konfiguration, Schlüssel, Dateien oder Historie zu löschen.
Sie verhindert neue peer-authentifizierte Aufrufe, Chat/Druck-Autorisierung
und ausgehende Transfers, filtert Directory-/Rendezvous-Ergebnisse und
verhindert Signalnachrichten zu gesperrten Empfängern. V2-Routen prüfen
gesperrte Ziele und Relays mit der vorhandenen deny-first Policy. Vorhandene
V2-Capabilities werden widerrufen; aktive V2-Jobs und persistente SOFP-Transfers
werden gestoppt. Ein bereits laufender einzelner HTTP-Request wird dadurch
nicht rückwirkend abgebrochen.

Das Entsperren entfernt nur die eigene Moderationssperre. Separate lokale
V2-Scope-/Objektsperren und Sperren abonnierter Quellen bleiben bestehen.
Widerrufene Freigaben und gestoppte Jobs werden nicht automatisch erneuert.
Sperren sind an Peer-IDs gebunden, nicht an IP-Adressen. Neue Identitäten mit
neuen IDs benötigen eigene Prüfung; dies ist keine netzwerkweite Firewall.

## Master und andere Quellen abonnieren

1. Den Master oder einen anderen Peer als aktivierten Federation-Peer mit
   eigenem, eindeutigem Token einrichten.
2. Auf der Quelle in der Peer-Verwaltung Peer-ID, öffentlichen
   Ed25519-Schlüssel und Fingerprint anzeigen und über einen unabhängigen,
   vertrauenswürdigen Weg vergleichen.
3. Auf dem Empfänger die genaue Quell-Peer-ID und den geprüften öffentlichen
   Schlüssel eintragen und die Übernahme ausdrücklich bestätigen.
4. **Synchronisieren** wählen. Die vollständige signierte Liste wird geprüft
   und atomar übernommen. Der letzte Synchronisierungszeitpunkt ist sichtbar.

Das Abonnement pinnt den Schlüssel unabhängig von Discovery-Metadaten.
Ein anderer Discovery-Schlüssel kann den Pin nicht ersetzen. Für einen
bewussten Schlüsselwechsel zuerst die Quelle entfernen, den neuen Schlüssel
prüfen und neu abonnieren. Dabei werden die bisherigen Sperren dieser Quelle
entfernt; eigene Sperren bleiben erhalten.

Die Synchronisierung erfolgt in dieser Version **manuell**, nicht automatisch.
Offline oder bei ungültigen Antworten bleibt die zuletzt akzeptierte Liste
wirksam, bis eine neuere Liste eintrifft, der jeweilige Ban abläuft oder die
Quelle ausdrücklich entfernt wird. Dies verhindert, dass ein Netzwerkfehler
eine Sperre aufhebt. Ausfälle und verspätete Entsperrungen erfordern daher
eine sichtbare manuelle Prüfung.

Listen tragen eine Ed25519-Signatur, eine persistente Revision und ein
24 Stunden gültiges Transport-Zeitfenster. Ältere Revisionen, widersprüchliche
Inhalte derselben Revision, manipulierte Signaturen, falsche Aussteller,
Duplikate und Sperren der eigenen Instanz werden abgewiesen. Listen enthalten
höchstens 5000 Einträge und die Antwort ist auf 4 MiB begrenzt. Eine erneute
Abfrage derselben Revision ist zulässig. Eine neuere Liste kann nur die
Sperren ihrer eigenen Quelle aufheben. Übernommene Sperren werden nicht als
eigene Entscheidungen erneut veröffentlicht.

## Meldungen und Entscheidung

Jeder aktivierte Peer mit eindeutigem Token kann eine andere Peer-ID beim
Master oder bei einem anderen eingerichteten Peer melden. Der Empfänger sieht
den authentifizierten Reporter und den Grund. Eine Meldung erzeugt noch keinen
Ban. Ein Administrator kann sie ablehnen oder bestätigen; bei Bestätigung
wird eine eigene lokale Sperre gesetzt und optional veröffentlicht.

Reporter werden aus einem HMAC-signierten Peer-Nachweis bestimmt. Ein
Reporter-Feld im JSON oder ein gemeinsamer Bearer-Token ersetzt diesen
Nachweis nicht. Nonces verhindern Replay. Doppelte Meldungen desselben
Reporters für dieselbe Peer-ID liefern dieselbe Meldungs-ID. Es sind höchstens
50 offene Meldungen je Reporter und 1000 gespeicherte Meldungen zulässig.
Erledigte Meldungen können mit Bestätigung entfernt werden, um Platz zu
schaffen. Review und die lokale Sperre werden in einer SQLite-Transaktion
gespeichert; konkurrierende Entscheidungen überschreiben einander nicht.

## Protokoll und Legacy-Grenzen

- `GET /federation/v1/moderation/blacklist`: signierte eigene veröffentlichte Liste.
- `POST /federation/v1/moderation/reports`: exakt `peer_id` und `reason`;
  maximal 4096 Bytes. Antworten enthalten keine internen Exception-Texte.
- Beide Endpunkte verlangen den vorhandenen `SOFP-PEER-V1`-Nachweis mit
  Peer-ID, Zeitstempel, Nonce und body-gebundener Signatur. Der Token darf
  weder leer sein noch von mehreren konfigurierten Peers verwendet werden.
- Verwaltungsaktionen erfordern lokale Admin-Anmeldung, CSRF und Bestätigung.
- Änderungen und Entscheidungen erscheinen im Federation-Ereignisprotokoll.

Alte globale Bearer-Tokens erlauben keine zuverlässige Zuordnung zu einem
Peer. Solange eine Moderationssperre aktiv ist, akzeptieren Legacy-Dokument-,
Kontakt-,Katalog-,Block-,Mail-,Software- und Directory-Aufrufe deshalb nur
zusätzlich identifizierte, nicht gesperrte Peers. Ein bloßer Peer-ID-Header
reicht nicht. Aktualisierte Python-Federation-HTTP-Clients ergänzen in einem
App-Kontext beim Zugriff auf konfigurierte Peers automatisch den bestehenden
Peer-Nachweis. Ältere Clients und Skripte ohne App-Kontext müssen aktualisiert
werden oder bleiben bei aktiver Blacklist gesperrt. Öffentliche, ausdrücklich
freigegebene Directory-Listings bleiben lesbar und zeigen gesperrte IDs nicht.
Andere Protokolle mit eigenen Capability-/Peer-Nachweisen behalten ihre
zusätzlichen Scope-Prüfungen.

## Prüfung und Grenzen

`tests/test_federation_moderation.py` und
`tests/test_federation_moderation_http.py` prüfen eigene und fremde Sperren,
lokale V2-Denies, Transfers, Relays, Ablauf, Signatur-/Revisionsfehler,
Schlüssel-Pins, Directory/Rendezvous, Meldungen, Replay, Reporter-Spoofing,
Token-Grenzen, Größenlimits sowie Admin-/CSRF-/Bestätigungsprüfung.
Praktische Mehrgeräte-, Offline- und Browser-Abnahme bleibt zusätzlich
erforderlich; Mock- und CI-Prüfungen ersetzen sie nicht.

Peer-HMAC-Zugänge müssen sich auch von den lokalen globalen Federation- und
Directory-Bearer-Tokens unterscheiden. Ein global bekannter Zugang kann keine
Peer-Identität beweisen und wird hierfür abgelehnt, selbst wenn er nur einem
konfigurierten Peer zugewiesen wurde. Nach Umstellung auf getrennte Geheimnisse
können zugelassene Peers wieder signierte Aufrufe senden; Sperren bleiben wirksam.
Moderationsdaten, gepinnte Quellschlüssel und Meldungen sind keine generische
S3-Projektion und bleiben der ausdrücklich autorisierten Verwaltung vorbehalten.

Legacy-Transferworker prüfen Sperren vor Vorbereitung, weiteren Chunks und der
abschließenden Statusabfrage. Statusupdates lesen den aktuellen Transfer und die
Sperre in einer gemeinsamen Schreibtransaktion; verspätete Worker dürfen einen
gesperrten Transfer weder fortsetzen noch als erfolgreich markieren. Gesperrte
eingehende Transfers nehmen keine weiteren Chunks an. Ein bereits laufender
Netzwerkaufruf kann noch enden; die Sperre beendet folgende Protokollschritte,
nicht einen bereits an den Netzwerkstack übergebenen Request. Abgeschlossene
Transferhistorie und vorhandene Dateien werden durch die Sperre nicht gelöscht.
