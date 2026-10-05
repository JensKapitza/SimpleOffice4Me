# Dokumentations- und Issue-Prüfung für 3.0

Prüfdatum: **2026-10-05**. Codebasis: `main` bei
`2abc3ef6a02c173304d6c23352f48497730b7068` (PR #585).
Umfang: Dokumentation, Logo und GitHub-Issue-Pflege; keine Änderungen an
Anwendungscode, Tests, Abhängigkeiten, Migrationen oder Laufzeitkonfiguration.

## Nachprüfung offener Issues — 2026-10-05

Code- und Ticketbasis dieser Nachprüfung ist `main`
`f90087b0` (gemergter Federation-PR #595). Die ursprüngliche
Dokumentationsprüfung unten bleibt ein historischer Nachweis ihres damaligen
Umfangs. Die Nachprüfung erweitert die CI um die inzwischen veröffentlichte
Python-Runtime RC3; sie verändert keine Anwendungslogik und erteilt keine
Release- oder externe Gerätefreigabe.

| Ticket | Nachgewiesen erledigt | Verbleibender Umfang / Entscheidung |
|---|---|---|
| #588, geschlossen | MIME-Suchkorrektur #597 und Legacy/Shadow/V2-Nachprüfung #599 gemergt | Erledigt; keine zweite Implementierung |
| #587 | Befund zu vorzeitigem `last_uid` im aktuellen Code bestätigt | Offen: persistente Recovery für Speicher, Herkunftsmetadaten, Anhänge und Prozessabbruch; bloßes Verschieben der UID-Zuweisung reicht nicht |
| #586 | MCP-Server, Tokens/Rechte/Audit; Schemakorrektur und API-Verträge #591 sowie Desktop-/Mobil-Bedienprüfung #592 gemergt | Offen: optionale LiteLLM-Betriebsintegration, zentrale Administration, Health und Fehler-/Recovery-Abnahme |
| #474 | Bisherige Standards-Baseline und CI bestehen; offizieller RC3-Tag und GitHub-Actions-Linux-Runtime sind veröffentlicht | Zusätzliche CI von RC2 auf RC3 aktualisiert; fortlaufender Tracker bleibt offen |
| #471 | StoragePort-Federation-Katalog #594 und sichere Mail-Vorgang-Policy/Benutzerzuordnung #595 gemergt | Offen: weitere Enumeration/Readmodels, Rollback-/Restore-Nachweise und gesperrtes destruktives Legacy-Cleanup |
| #483 | Repository-Reader #540 und folgende API-/APK-Prüfungen vorhanden | Offen: praktische APK-, Touch-, Back-, Resume- und Geräteabnahme; Browseransicht ersetzt diese nicht |
| #505 / #506 | 16 Teilissues geschlossen; vorhandene Capability-/Fallback-Matrix #549 und Dokumentation #589 | Offen: gemeinsamer Release-Commit mit realen Upgrade-/Recovery-/Client-/Geräte-/Lastnachweisen; Paketversion und Release-Freigabe unverändert |
| #330 | Umfangreiche Software-/CI-Nachweise; SFTP-Abschlusskorrektur #598 gemergt | Offen: reale Plattform-, Netzwerk-, Hardware-, Last- und Langzeitabnahme; vorhandenen Dokumentations-PR #600 berücksichtigen |

Keines der acht noch offenen Tickets ist allein durch Dokumentationspflege
oder erfolgreiche CI vollständig erledigt. Nachweise und Lösungs-PRs werden
am jeweiligen Ticket verknüpft; historische Testergebnisse werden nicht als
neue externe Abnahme umgedeutet.

## Ergebnis und Versionsgrenze

Für die acht zentralen Funktionsfreigaben aus
[`app/access_control.py`](../app/access_control.py) und alle 16 registrierten
3.0-Capabilities bestehen Fachreferenzen. Die bisher nur teilweise verlinkte
Dokumentationsübersicht erhält ein vollständiges Register der Markdown-Dateien
unter `docs/`. Neue Einstiegspunkte bündeln Betrieb und Konfigurationsnamen.

Vor der Überarbeitung waren **53 im Anwendungscode erwähnte Optionsnamen**
nicht in README oder den damaligen Fachanleitungen genannt. Das neue Register
enthält **180 statisch sichtbare Namen** im ausdrücklich angegebenen
Anwendungs-/Werkzeug-/Start-/Build-Umfang; darunter auch interne Namen und
dynamische Präfixe. Archiv-, OSM-, Personalsync-, Geräte- und Secret-Bindungs-
Optionen werden erläutert. Alle 3.0-Schalter sind mit Funktion und Fachreferenz
aufgeführt.

Das ist eine dokumentierte Abdeckung der Funktionsbereiche, Dateireferenzen
und statischen Konfigurationsnamen. Es ist **kein Nachweis, dass jede Route,
jeder Sonderfall und jede externe Integration vollständig beschrieben oder
praktisch abgenommen ist**. Zwei konkrete Abweichungen wurden reproduziert,
in den Fachanleitungen korrigiert und als Codekorrektur-Issues erfasst.

Die deklarierte Paketversion ist weiterhin **2.0.0**. Die geschlossenen
3.0-Funktions-Issues #489–#504 bedeuten keine Freigabe der Gesamtrelease.
[#505](https://github.com/JensKapitza/SimpleOffice4Me/issues/505) und
[#506](https://github.com/JensKapitza/SimpleOffice4Me/issues/506) bleiben offen.
Historische Release-Prüfungen behalten Datum, Commit und damalige Testzahlen.

## Funktionsabdeckung und Einstiegspunkte

| Vorhandener Bereich | Maßgebliche Dokumentation |
|---|---|
| Dokumente, Vorschau, Index und Suche | [Vorschau](DOKUMENTVORSCHAU_INDEXDIENST.md), [Retrieval](DOKUMENTSUCHE_RETRIEVAL.md), [Indexdienst](PERFORMANCE_INDEXDIENST.md), [Video](VIDEO_VORSCHAU.md) |
| Kontakte, CRM und Freigaben | [Kontakte](KONTAKTE.md), [Kontaktupdates](EXTERNE_KONTAKTUPDATES_UND_CARDDAV.md), [CRM 3.0](V3_CRM.md) |
| Kalender, CalDAV und Aufgaben | [Kalender](KALENDER.md), [Scheduling](CALDAV_SCHEDULING_RFC6638.md), [Serien](KALENDER_SERIEN_RFC5545.md), [Workboard](V3_WORKBOARD.md) |
| Mail, SMTP, Sieve, Vorgänge und Anhänge | [Mailbetrieb](IMAP_SIEVE_EMAIL_ARCHIV.md), [Vorgänge](MAIL_VORGAENGE.md), [ClamAV](ANHAENGE_CLAMAV.md), [Sicherheitsreview](MAIL_SECURITY_REVIEW.md) |
| WebDAV und Desktop-/SFTP-Zugänge | [WebDAV](WEBDAV_DATEIVERWALTUNG.md), [Ordnerrechte](WEBDAV_ORDNERZUGAENGE_RFC3744.md), [VFS/SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| Synchronisation, Federation und Recovery | [Sync](SYNC_FEDERATION.md), [Trust](FEDERATION_DISCOVERY_TRUST.md), [Federation 3.0](V3_FEDERATION.md), [Backup](BACKUP.md), [Recovery](V2_RECOVERY.md) |
| Projekte, Zeit und Abrechnung | [Projektzeiten](PROJEKTZEITEN_UND_ABRECHNUNGSGRUPPEN.md), [Tracker](PROJECT_TRACKER.md), [Finance](V3_FINANCE.md), [Rechnungsprüfung](XRECHNUNG_VALIDATION.md) |
| Datenlogger und Sensoren | [Persistenter Datenlogger](DATENLOGGER_SENSOREN.md) |
| Rollen, Einstellungen, Administration | [Rechte/Audit](BENUTZER_RECHTE_FEHLERPROTOKOLL.md), [Einstellungen](EINSTELLUNGEN.md), [Health](V3_HEALTH.md) |
| Relations, Activity, Jobs, Policy, Suche, Kontext, Inbox, Automation | [3.0-Capability-Tabelle und jeweilige Fachreferenz](V3_BETRIEBSLEITFADEN.md) |
| Vault und vertrauliche Credentials | [Vault-Web](V2_VAULT_WEB.md), [Secret-Object-Store](V2_VAULT_OBJECT_STORE.md), [Schlüsselmodell](V2_PASSWORD_VAULT.md) |
| Android, Bücherregal und Offline | [Android](ANDROID.md), [Integration](ANDROID_INTEGRATION.md), [Reader](DIGITALES_BUECHERREGAL.md) |
| Mini Services, Audio, Telefonie und Bildschirm | [Gemeinsamer Betrieb](MINI_SERVICES.md), [Abnahme](MINI_SERVICES_EXTERNAL_ACCEPTANCE.md), [Audio](AUDIO_STREAMER.md), [Telefonie](TELEPHONY.md), [Bildschirm](screen-sharing-miracast.md) |
| Chat, Shopping, Inventar, Ortstermine, Gamification | [Chat](CHAT.md), [Shopping](SHOPPING.md), [Inventar](INVENTAR_MARKETPLACE_SUCHEN.md), [Ortstermine](SITE_VISITS.md), [Datenqualität](DATA_QUALITY_GAMIFICATION.md) |
| MCP und Erweiterungen | [Bestehender MCP-Server](CHATGPT_MCP.md), [Extension-API](V3_EXTENSIONS.md); zusätzlicher LiteLLM-/Betriebsumfang in #586 noch offen |
| Installation, Updates, Produktion und Sicherheit | [Erststart](ERSTSTART_UND_DESKTOP_SETUP.md), [Updates](GIT_FREIES_UPDATE.md), [Produktion](PRODUKTIONSBETRIEB.md), [Security](SECURITY.md), [CRA](CRA.md) |

## Konkrete Korrektur-Issues

### #587: IMAP-Fortschritt vor erfolgreicher Speicherung

Die bisherige Mailanleitung versprach, dass eine UID erst nach atomarer
Speicherung und Metadatenregistrierung als bearbeitet gespeichert wird.
`ImapArchive.archive()` erhöht den Fortschritt jedoch vorher. Mit bestehendem
FakeIMAP, temporärem MailStore, synthetischem Speicherfehler für UID 7 und
erfolgreicher Archivierung der nachfolgenden UID 8:

| Beobachtung | Ergebnis |
|---|---|
| Erster Lauf | `examined=2`, `archived=1`, Fehler für UID 7 |
| Persistierter Checkpoint | `last_uid=8` |
| Zweite Suche | `UID 9:*`; Mock liefert entsprechend der rückwärts interpretierten Bereichsgrenze die höchste UID 8 |
| Zweiter Lauf | `examined=1`, `archived=0`, `duplicates=1`, keine Fehler; UID 7 wird nicht erneut angefordert |

IMAP-Bereiche werden unabhängig von der Reihenfolge ihrer Grenzen ausgewertet.
Bei einem Start oberhalb der höchsten UID kann `N:*` die höchste bestehende
UID erneut enthalten. Die Reproduktion berücksichtigt dies ausdrücklich und
belegt die Lücke anhand der früher fehlgeschlagenen UID 7, nicht anhand einer
angenommenen leeren Folgesuche.

Die Quelle bleibt unverändert, das lokale Archiv kann eine Lücke behalten.
[#587](https://github.com/JensKapitza/SimpleOffice4Me/issues/587) beschreibt
Recovery, Metadaten-/Anhangszustände und Abnahme. Das ist eine eigenständige
Fortschrittskorrektur, kein Duplikat des Legacy-Cleanup-Trackers #471.

### #588: geschlossener EML-Suchumfang #74 bleibt bei Encoding unvollständig

Der Abschlusskommentar von #74 verweist auf PR #79 und die MIME-Vorschau plus
Text-Backfill. Eine synthetische EML mit RFC-2047-Betreff und Base64-Text wurde
über `DocumentStore.scan()` und `refresh_missing_text(..., force=True)` indexiert.
Die Vorschau decodiert korrekt; `text:Überseehafen` und `text:Kranführer`
finden nichts. Die Kontrollabfrage `text:sender` findet das Dokument.

Der `.eml`-Zweig von `_file_text()` liefert rohe UTF-8-Dateiinhalte.
[#588](https://github.com/JensKapitza/SimpleOffice4Me/issues/588) verlangt die
Wiederverwendung vorhandener MIME-Helfer, sicheren Reindex und negative Tests.
Die Retrieval-Anleitung benennt die Grenze und verspricht keine bislang
unimplementierten `subject:`-/`from:`-Operatoren.

## Offene und geschlossene Issues

Vor Anlage der Korrekturen wurden alle **54 abrufbaren Issues** geprüft:
**47 geschlossen, 7 offen**, insgesamt **86 Kommentare**. Offene PRs: keine.
Nach Anlage von #587/#588 sind es neun offene Issues. Der Stand ist eine
Momentaufnahme; spätere GitHub-Änderungen verändern diese Zahlen.

Bei geschlossenen Issues wurden Ziel, Beschreibung, vorhandene
Abschlusskommentare und fachliche Zuordnung gesichtet. Historische offene
Checkboxen oder überholte Statusabsätze allein belegen keinen heutigen Defekt.
Die konkreten Mailbefunde wurden zusätzlich mit aktuellen Codepfaden und
synthetischen Daten geprüft. Bei abgeschlossenen manuellen Fehlerreports wurde
kein neuer Laufzeitfehler allein aus dem alten Bericht unterstellt.

| Geschlossene Issues | Einordnung dieser Prüfung |
|---|---|
| #489–#504 | Alle 16 additiven 3.0-Bausteine im Betriebsleitfaden zugeordnet; Gesamtfreigabe bleibt #505/#506 |
| #74 | MIME-Vorschau vorhanden; Encoding-Suche reproduzierbar unvollständig → #588 |
| #306, #378 | Architektur-/Cutover-Abschluss ersetzt keine Mail-Checkpoint-Recovery → gesonderter Befund #587 |
| #310 | Historischer Body nennt noch laufende #457; Kommentar beschreibt Abschlussstrang #458. Aktuelle Vault-Object-Store-Dokumentation vorhanden; kein Wiederöffnen allein aufgrund des alten Texts |
| #313, #314 | Historische V2-Architekturplanung; weiter gültige Cutover-/Recovery-/Cleanup-Dokumente verlinkt |
| #482, #485, #528, #534 | S3/Inbox, Ortstermine, Mail-Vorgänge und Aufgaben-Freigabe haben bestehende Fachgrundlagen; kein neuer paralleler Implementierungsauftrag |
| #75, #89, #387, #402 | Kontakte/CRM, Foto-/QR-Erfassung und CardDAV; bestehende Kontakt-/Audit-/Interoperabilitätsdokumente zugeordnet |
| #322, #363, #383, #423 | Android-/Standards-/Hotspot-/Trust-Arbeiten; fortlaufende Abnahmegrenzen nicht mit historischen Closures gleichgesetzt |
| #285, #286, #364, #391, #392, #421 | Media, Bildschirm, Shopping, Chat, Relay und Firewall im Dateiregister auffindbar; reale Geräte-/Netzabnahme bleibt eigener Nachweis |
| #237, #242, #245, #246, #315, #236 | Historische manuelle Laufzeitfehler; kein neuer Reproduktionsnachweis aus dieser Dokumentationsprüfung |
| #78 | Verworfenes temporäres Issue; kein fachlicher Korrekturauftrag |

Bestehende offene Tracker weiterverwenden: #330 Mini-Service-Abnahme,
#471 Legacy-Cleanup, #474 Standards-/Runtime-Beobachtung, #483 praktische
Reader-Abnahme, #505 Roadmap, #506 Release Gate und #586 zusätzliche
LiteLLM-/MCP-Betriebsintegration. Kein Tracker wird durch diese
Dokumentationsarbeit vorzeitig geschlossen.

## Prüfung auf dokumentierte Secrets

Der vorhandene Scanner `tools/check_secret_leaks.py` meldete keine
hochkonfidenten Treffer im Repository. Seine `scan_text`-Regeln wurden zusätzlich
ohne Ausgabe von Inhaltswerten auf folgende Dokumentationsdaten angewandt:

- 163 Markdown-Dateien: README und damaliger Bestand unter `docs/`;
- 570 eindeutige historische Markdown-Blobs aus der erreichbaren Git-Historie
  von README und `docs/` bis zum geprüften Commit;
- alle 54 Issue-Beschreibungen und 86 abgerufenen Kommentare.

Ergebnis: **0 Treffer** für die vorhandenen Schlüssel-/Tokenmuster und
hoch-entropischen Secret-Literale. Nicht gelesen wurden Vault-Dateien,
Prozessumgebungswerte oder produktive Mail-/Benutzerdaten. Das Logo enthält
keine derartigen Daten.

Die Regeln erkennen nicht sämtliche kurzen Passwörter, unbekannten
Tokenformate, Bildinhalte oder private/externe Artefakte. Gelöschte oder nicht
erreichbare Git-Objekte und mehr als 100 Kommentare pro Issue wären nicht in
diesem Umfang enthalten; die abgerufenen Issues hatten jeweils weniger als
100 Kommentare. Ein Nullbefund ist keine allgemeine Secret-Freiheitsgarantie.
Bei einem tatsächlichen Fund: Wert nicht in ein öffentliches Issue kopieren,
über [Security-Prozess](SECURITY.md) melden und betroffene Zugangsdaten rotieren;
bloßes Löschen aus der aktuellen Anleitung genügt bei historischer Exposition
nicht.

## Validierung dieser Dokumentationsänderung

Geprüft werden relative Links der geänderten Dokumente, das vollständige
Markdown-Dateiregister, alle 16 Capability-Zuordnungen, statische
Konfigurationsnamen, Secret-Scan und Whitespace-/Projektregeln. Die beiden
Fehlerreproduktionen verwenden temporäre synthetische Daten und verändern
keine Repository-Quelldatei. Ein PR dokumentiert CI-Ergebnisse separat.
Diese Prüfung führt keine produktive Migration, keinen echten Mailversand und
keine externe Geräte- oder Release-Abnahme durch.

Das [Logo](assets/README.md) ist KI-generierte Dokumentationsgestaltung in der
bestehenden Markenfamilie. „2080“ ist eine kreative Referenz, kein Prüfdatum.
Funktions- und Sicherheitsbehauptungen bleiben belegpflichtig.
