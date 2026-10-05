# Dokumentationsübersicht

<img src="assets/simpleoffice-documentation-logo.png" width="140" alt="Dokumentordner auf Petrol mit goldenen Verbindungen">

## Einstieg, Version 3.0 und Konfiguration

- [3.0-Betriebsleitfaden: alle 16 Capabilities, Rechte, Worker und Rollback](V3_BETRIEBSLEITFADEN.md)
- [Konfigurationsregister: statische Namen, neue Erläuterungen und Secret-Grenzen](KONFIGURATIONSREGISTER.md)
- [Dokumentationsprüfung: Funktionen, geschlossene Issues und Secret-Scan](DOKUMENTATIONSPRUEFUNG_3_0.md)
- [Release Gate: datierte Nachweise und offene externe Abnahmen](V3_RELEASE_GATE.md)
- [Bestehender MCP-Zugang für ChatGPT und andere Clients](CHATGPT_MCP.md)
- [Dokumentationslogo und Gestaltung](assets/README.md)

Die Paketversion ist derzeit 2.0.0; die additive 3.0-Release ist noch nicht
freigegeben. Einzelne geschlossene Funktions-Issues ersetzen das Release Gate
nicht. Funktions-, Benutzer-/Rollen- und Objektfreigaben bleiben maßgeblich.

## Kontakte und Kalender

- [Kontaktverwaltung, vCard und CardDAV](KONTAKTE.md)
- [Globale Kontakt-Änderungshistorie](KONTAKT_AUDIT.md)
- [Kalender, Buchungen, ICS und CalDAV-Planung](KALENDER.md)
- [Mehrere Kalender und CalDAV: RFC-Auswertung und Umsetzung](CALDAV_RFC_IMPLEMENTIERUNG.md)
- [Serientermine, Ausnahmen und Zeitzonen nach RFC 5545](KALENDER_SERIEN_RFC5545.md)
- [Lokale Erinnerungen nach RFC 5545 und RFC 9074](KALENDER_ERINNERUNGEN_RFC5545_9074.md)
- [Terminmetadaten und Konferenzzugänge nach RFC 5545/7986](KALENDER_METADATEN_RFC5545_7986.md)
- [Optionaler Google-Kalender-Abgleich mit Sync-Token und Konfliktschutz](GOOGLE_KALENDER_SYNC.md)
- [iTIP-Terminplanung und Einladungen nach RFC 5546](ITIP_RFC_TERMINPLANUNG.md)
- [CalDAV Scheduling, Inbox/Outbox und Free/Busy nach RFC 6638](CALDAV_SCHEDULING_RFC6638.md)

Weitere fachliche und betriebliche Dokumente liegen in diesem Verzeichnis; die
wichtigsten Einstiegspunkte sind zusätzlich im [Projekt-README](../README.md)
verlinkt.

## Dateien und Desktop-Integration

- [S3-Overlay für Dokumente und Inbox-Imports](S3_OVERLAY.md)

- [Systemwerkzeuge, ClamAV und Vorschau-Konverter](SYSTEMWERKZEUGE_VORSCHAU.md)
- [Schnelle Dokument- und Retrieval-Suche mit UND/ODER/NICHT/XOR/NOR und Teilstrings](DOKUMENTSUCHE_RETRIEVAL.md)
- [Erststart-Assistent für Windows, Linux, DAV und SFTP](ERSTSTART_UND_DESKTOP_SETUP.md)
- [Virtuelles Dateisystem mit Ordnerrechten, SFTP/SSHFS und rsync](VIRTUELLES_DATEISYSTEM_SFTP.md)
- [GoodSync und FreeFileSync sicher über SFTP](GOODSYNC_FREEFILESYNC.md)
- [Hierarchische WebDAV-Dateiverwaltung](WEBDAV_DATEIVERWALTUNG.md)
- [Begrenzte rekursive WebDAV-Bestandsaufnahme nach RFC 4918](WEBDAV_REKURSIVE_PROPFIND_RFC4918.md)
- [Portable WebDAV-Dateinamen für Windows, macOS und Linux](WEBDAV_PORTABLE_DATEINAMEN.md)
- [Effizienter WebDAV-Änderungsabgleich nach RFC 6578](WEBDAV_SYNC_RFC6578.md)
- [WebDAV-Eigenschaften und Metadaten nach RFC 4918](WEBDAV_EIGENSCHAFTEN_RFC4918.md)
- [WebDAV-Zeitstempel und ausgewählte Windows-Dateieigenschaften](WEBDAV_ZEITSTEMPEL_MS_WDVME.md)
- [WebDAV-Speichergrenzen nach RFC 4331 und robuste Locks](WEBDAV_QUOTA_UND_LOCKS.md)
- [Ressourcengenaue WebDAV-If-Bedingungen nach RFC 4918](WEBDAV_IF_HEADER_RFC4918.md)
- [Einheitliche HTTP-Vorbedingungen für WebDAV nach RFC 9110](WEBDAV_HTTP_VORBEDINGUNGEN_RFC9110.md)
- [Rekursive WebDAV-Ordnersperren nach RFC 4918](WEBDAV_COLLECTION_LOCKS_RFC4918.md)
- [WebDAV-Ordner rekursiv kopieren und verschieben nach RFC 4918](WEBDAV_ORDNER_COPY_MOVE_RFC4918.md)
- [Vorhandene WebDAV-Dateien sicher per MOVE ersetzen](WEBDAV_SICHERES_MOVE_ERSETZEN.md)
- [WebDAV-Ordner rekursiv und wiederherstellbar löschen nach RFC 4918](WEBDAV_ORDNER_LOESCHEN_RFC4918.md)
- [Fortsetzbare WebDAV-Downloads und HTTP-Validatoren nach RFC 9110](WEBDAV_DOWNLOADS_RFC9110.md)
- [WebDAV-Übertragungsintegrität mit Content- und Repr-Digest nach RFC 9530](WEBDAV_INTEGRITAET_RFC9530.md)
- [Getrennte WebDAV-Gerätezugänge](WEBDAV_ZUGAENGE.md)
- [Ordnergebundene Gerätezugänge nach RFC 3744](WEBDAV_ORDNERZUGAENGE_RFC3744.md)
- [WebDAV-Principal- und Rechteerkennung nach RFC 3744/5397](WEBDAV_PRINCIPAL_RECHTE_RFC3744_5397.md)
- [Serverseitige WebDAV-Suche nach RFC 5323 und TagSpaces-Ansätzen](WEBDAV_SUCHE_RFC5323_TAGSPACES.md)
- [Sichere Datei- und Inhaltswiederherstellung](DATEI_WIEDERHERSTELLUNG.md)
- [LibreOffice über WebDAV](LIBREOFFICE_WEBDAV.md)
- [Bestätigte Mail-Anhänge und ClamAV](ANHAENGE_CLAMAV.md)
- [IMAP-Client, Sieve und unveränderliches E-Mail-Archiv](IMAP_SIEVE_EMAIL_ARCHIV.md)
- [Digitales Bücherregal mit PDF-/EPUB-Reader](DIGITALES_BUECHERREGAL.md)
- [Kollaborative Mail-Vorgänge, Rechte und individueller Lesestatus](MAIL_VORGAENGE.md)
- [ClamAV-Prüfung vor WebDAV-Uploads](WEBDAV_UPLOADS_CLAMAV.md)
- [Eigenständig umgesetzte Ansätze aus TagSpaces](TAGSPACES_ANSAETZE.md)

## Projekte und Abrechnung

- [Minutengenaue Projektzeiten und private Abrechnungsgruppen](PROJEKTZEITEN_UND_ABRECHNUNGSGRUPPEN.md)

## Betrieb

- [Persistenter Datenlogger für Dateien, Linux und HTTP/JSON-Sensoren](DATENLOGGER_SENSOREN.md)

- [Benutzerrechte und datensparsame Fehlerprotokolle](BENUTZER_RECHTE_FEHLERPROTOKOLL.md)
- [Produktionsbetrieb mit Waitress](PRODUKTIONSBETRIEB.md)
- [HTTPS und Reverse Proxy](PROXY_HTTPS.md)

## Vollständiges Markdown-Dateiregister

Alphabetische Übersicht des Dokumentationsbestands einschließlich Betriebs-,
Architektur-, Security- und historischer Prüfberichte. Historische Berichte
behalten ihren damaligen Geltungsbereich; ihre Titel sind keine aktuelle
Release-Zusage. Dieses Register ergänzt die thematischen Einstiegspunkte oben.

- [100 wichtige Quick Wins – September 2026](100-important-wins-2026-09.md) — `100-important-wins-2026-09.md`
- [SimpleOffice4Me unter Android](ANDROID.md) — `ANDROID.md`
- [Android-Integration](ANDROID_INTEGRATION.md) — `ANDROID_INTEGRATION.md`
- [Sichere Anhänge aus E-Mail und Kalender](ANHAENGE_CLAMAV.md) — `ANHAENGE_CLAMAV.md`
- [SimpleOffice4Me V2 architecture contract](ARCHITECTURE_V2.md) — `ARCHITECTURE_V2.md`
- [Audio-Ausgabe und Durchsagen](AUDIO_OUTPUT.md) — `AUDIO_OUTPUT.md`
- [Live-Audio: Sender und Receiver](AUDIO_STREAMER.md) — `AUDIO_STREAMER.md`
- [Backups](BACKUP.md) — `BACKUP.md`
- [Benutzerrechte und datensparsame Fehlerprotokolle](BENUTZER_RECHTE_FEHLERPROTOKOLL.md) — `BENUTZER_RECHTE_FEHLERPROTOKOLL.md`
- [Bibliothek und Etikettendruck](BIBLIOTHEK_ETIKETTENDRUCK.md) — `BIBLIOTHEK_ETIKETTENDRUCK.md`
- [Mehrere Kalender und CalDAV: RFC-Auswertung und Umsetzung](CALDAV_RFC_IMPLEMENTIERUNG.md) — `CALDAV_RFC_IMPLEMENTIERUNG.md`
- [CalDAV Scheduling und Free/Busy nach RFC 6638](CALDAV_SCHEDULING_RFC6638.md) — `CALDAV_SCHEDULING_RFC6638.md`
- [CardDAV-Autoerkennung für Thunderbird](CARDDAV_DISCOVERY.md) — `CARDDAV_DISCOVERY.md`
- [Chat – Stufe 1](CHAT.md) — `CHAT.md`
- [ChatGPT und MCP](CHATGPT_MCP.md) — `CHATGPT_MCP.md`
- [Automatische Tests und Abhängigkeitsprüfung](CI_SICHERHEIT.md) — `CI_SICHERHEIT.md`
- [Connectivity Relay: STUN/TURN und HTTPS-CONNECT](CONNECTIVITY_RELAY.md) — `CONNECTIVITY_RELAY.md`
- [Cyber Resilience Act (CRA) – technische Akte](CRA.md) — `CRA.md`
- [CRA-Meldebereitschaft](CRA_REPORTING.md) — `CRA_REPORTING.md`
- [Dashboard, Bilder und Adressen](DASHBOARD_BILDER.md) — `DASHBOARD_BILDER.md`
- [Data-Quality-Gamification](DATA_QUALITY_GAMIFICATION.md) — `DATA_QUALITY_GAMIFICATION.md`
- [Sichere Datei- und Inhaltswiederherstellung](DATEI_WIEDERHERSTELLUNG.md) — `DATEI_WIEDERHERSTELLUNG.md`
- [Persistenter Datenlogger für Dateien, Linux und HTTP-Sensoren](DATENLOGGER_SENSOREN.md) — `DATENLOGGER_SENSOREN.md`
- [SimpleOffice4Me bereitstellen](DEPLOYMENT.md) — `DEPLOYMENT.md`
- [Digitales Bücherregal und integrierter Dokument-Reader](DIGITALES_BUECHERREGAL.md) — `DIGITALES_BUECHERREGAL.md`
- [Dokumentations- und Issue-Prüfung für 3.0](DOKUMENTATIONSPRUEFUNG_3_0.md) — `DOKUMENTATIONSPRUEFUNG_3_0.md`
- [Schnelle Dokument- und Retrieval-Suche](DOKUMENTSUCHE_RETRIEVAL.md) — `DOKUMENTSUCHE_RETRIEVAL.md`
- [Schnelle Dokumentvorschauen aus dem Indexdienst](DOKUMENTVORSCHAU_INDEXDIENST.md) — `DOKUMENTVORSCHAU_INDEXDIENST.md`
- [Einstellungen](EINSTELLUNGEN.md) — `EINSTELLUNGEN.md`
- [Erster Start](ERSTER_START.md) — `ERSTER_START.md`
- [Erststart und Desktop-Einrichtung](ERSTSTART_UND_DESKTOP_SETUP.md) — `ERSTSTART_UND_DESKTOP_SETUP.md`
- [Externe Kontaktupdates und CardDAV-Datenerhalt](EXTERNE_KONTAKTUPDATES_UND_CARDDAV.md) — `EXTERNE_KONTAKTUPDATES_UND_CARDDAV.md`
- [Federation Discovery und Vertrauen](FEDERATION_DISCOVERY_TRUST.md) — `FEDERATION_DISCOVERY_TRUST.md`
- [Peer-Verwaltung, Meldungen und signierte Blacklists](FEDERATION_PEER_VERWALTUNG.md) — `FEDERATION_PEER_VERWALTUNG.md`
- [SimpleOffice4Me Federation Protocol (SOFP) v1](FEDERATION_PROTOCOL.md) — `FEDERATION_PROTOCOL.md`
- [Federation: verschlüsselter P2P-Speicher](FEDERATION_ZERO_KNOWLEDGE_STORAGE.md) — `FEDERATION_ZERO_KNOWLEDGE_STORAGE.md`
- [Finanzdatenbank: Initialisierung und Updates](FINANCE_STORAGE.md) — `FINANCE_STORAGE.md`
- [Mini Services: Linux-Firewall](FIREWALL.md) — `FIREWALL.md`
- [Formulare und Warenwirtschaft](FORMULARE_WARENWIRTSCHAFT.md) — `FORMULARE_WARENWIRTSCHAFT.md`
- [Fristen, Aussonderung und verwaltete Objekte](FRISTEN_UND_OBJEKTE.md) — `FRISTEN_UND_OBJEKTE.md`
- [GitHub-Fehlerberichte](GITHUB_ERROR_REPORTING.md) — `GITHUB_ERROR_REPORTING.md`
- [Git-freies Update, Self-Deploy und Federation-Releases](GIT_FREIES_UPDATE.md) — `GIT_FREIES_UPDATE.md`
- [GoodSync und FreeFileSync über SFTP](GOODSYNC_FREEFILESYNC.md) — `GOODSYNC_FREEFILESYNC.md`
- [Google-Drive-Synchronisation](GOOGLE_DRIVE_SYNC.md) — `GOOGLE_DRIVE_SYNC.md`
- [Optionaler Google-Kalender-Abgleich](GOOGLE_KALENDER_SYNC.md) — `GOOGLE_KALENDER_SYNC.md`
- [Google-Anmeldung einrichten](GOOGLE_OAUTH.md) — `GOOGLE_OAUTH.md`
- [ICS-Dateien vor dem Import prüfen](ICS_VORSCHAU.md) — `ICS_VORSCHAU.md`
- [IMAP-, SMTP- und Sieve-Client mit unveränderlichem E-Mail-Archiv](IMAP_SIEVE_EMAIL_ARCHIV.md) — `IMAP_SIEVE_EMAIL_ARCHIV.md`
- [Inventar schneller mit Amazon und eBay ergänzen](INVENTAR_MARKETPLACE_SUCHEN.md) — `INVENTAR_MARKETPLACE_SUCHEN.md`
- [iTIP-Terminplanung nach RFC 5546](ITIP_RFC_TERMINPLANUNG.md) — `ITIP_RFC_TERMINPLANUNG.md`
- [Kalender](KALENDER.md) — `KALENDER.md`
- [Lokale Kalendererinnerungen nach RFC 5545 und RFC 9074](KALENDER_ERINNERUNGEN_RFC5545_9074.md) — `KALENDER_ERINNERUNGEN_RFC5545_9074.md`
- [Sichere HTML-Beschreibungen im Kalender](KALENDER_HTML_BESCHREIBUNGEN.md) — `KALENDER_HTML_BESCHREIBUNGEN.md`
- [Terminmetadaten und Konferenzzugänge nach RFC 5545/7986](KALENDER_METADATEN_RFC5545_7986.md) — `KALENDER_METADATEN_RFC5545_7986.md`
- [Serientermine, Ausnahmen und Zeitzonen nach RFC 5545](KALENDER_SERIEN_RFC5545.md) — `KALENDER_SERIEN_RFC5545.md`
- [Konfigurationsregister](KONFIGURATIONSREGISTER.md) — `KONFIGURATIONSREGISTER.md`
- [Kontakte und Thunderbird](KONTAKTE.md) — `KONTAKTE.md`
- [Globale Kontakt-Änderungshistorie](KONTAKT_AUDIT.md) — `KONTAKT_AUDIT.md`
- [Kontakt-Historie nach Benutzer und Feld](KONTAKT_HISTORIE.md) — `KONTAKT_HISTORIE.md`
- [Dokumente mit LibreOffice über WebDAV bearbeiten](LIBREOFFICE_WEBDAV.md) — `LIBREOFFICE_WEBDAV.md`
- [Lizenzierung und Nutzungserfassung](LICENSING.md) — `LICENSING.md`
- [Mail-Sicherheitsprüfung vom 04.10.2026](MAIL_SECURITY_REVIEW.md) — `MAIL_SECURITY_REVIEW.md`
- [Mail-Vorgänge](MAIL_VORGAENGE.md) — `MAIL_VORGAENGE.md`
- [Master-Cluster-Betrieb](MASTER_CLUSTER.md) — `MASTER_CLUSTER.md`
- [Mietobjekte und nachvollziehbare Betriebskostenabrechnung](MIETOBJEKT_ABRECHNUNG.md) — `MIETOBJEKT_ABRECHNUNG.md`
- [DHCPv4](MINI_DHCP.md) — `MINI_DHCP.md`
- [DNS](MINI_DNS.md) — `MINI_DNS.md`
- [Routing / NAT](MINI_GATEWAY.md) — `MINI_GATEWAY.md`
- [Mini Services: gemeinsamer Betrieb](MINI_SERVICES.md) — `MINI_SERVICES.md`
- [Mini Services: Abnahmestand nach den Änderungen](MINI_SERVICES_ACCEPTANCE.md) — `MINI_SERVICES_ACCEPTANCE.md`
- [Mini Services external acceptance protocol](MINI_SERVICES_EXTERNAL_ACCEPTANCE.md) — `MINI_SERVICES_EXTERNAL_ACCEPTANCE.md`
- [Mini Services failure and recovery matrix](MINI_SERVICES_FAILURE_MATRIX.md) — `MINI_SERVICES_FAILURE_MATRIX.md`
- [Mini Services – Bestandsaufnahme und Abnahme](MINI_SERVICES_REVIEW.md) — `MINI_SERVICES_REVIEW.md`
- [Netzwerkboot: HTTP/PXE und TFTP](NETWORK_BOOT.md) — `NETWORK_BOOT.md`
- [Neuerungen: Fehlerreporting](NEUERUNGEN_FEHLERREPORTING.md) — `NEUERUNGEN_FEHLERREPORTING.md`
- [Neuerungen: Offline-Installer und Software-Verteilung](NEUERUNGEN_OFFLINE_INSTALLER.md) — `NEUERUNGEN_OFFLINE_INSTALLER.md`
- [Einheitliches Objektmodell](OBJECT_MODEL.md) — `OBJECT_MODEL.md`
- [Self-Deploy und Offline-Updates über Federation](OFFLINE_SELF_DEPLOY.md) — `OFFLINE_SELF_DEPLOY.md`
- [OSM-Index und Rechnungsfinalisierung](OSM_INDEX_UND_RECHNUNGSFINALISIERUNG.md) — `OSM_INDEX_UND_RECHNUNGSFINALISIERUNG.md`
- [Dokumentenverwaltung nach Paperless-Prinzip](PAPERLESS_ERWEITERUNG.md) — `PAPERLESS_ERWEITERUNG.md`
- [Schneller Start und getrennter Dokumentindex](PERFORMANCE_INDEXDIENST.md) — `PERFORMANCE_INDEXDIENST.md`
- [PrinterShare und Federation-Druck](PRINTERSHARE_FEDERATION.md) — `PRINTERSHARE_FEDERATION.md`
- [Produktionsbetrieb mit Waitress](PRODUKTIONSBETRIEB.md) — `PRODUKTIONSBETRIEB.md`
- [Projekt-Tracker: Issues, Wiki und Git](PROJECT_TRACKER.md) — `PROJECT_TRACKER.md`
- [Projektzeiten und Abrechnungsgruppen](PROJEKTZEITEN_UND_ABRECHNUNGSGRUPPEN.md) — `PROJEKTZEITEN_UND_ABRECHNUNGSGRUPPEN.md`
- [HTTPS und Reverse Proxy](PROXY_HTTPS.md) — `PROXY_HTTPS.md`
- [Uneinbringliche Rechnungen ausbuchen](RECHNUNGEN_AUSBUCHEN.md) — `RECHNUNGEN_AUSBUCHEN.md`
- [Sicherheits-Checkliste vor Release](RELEASE_SECURITY_CHECKLIST.md) — `RELEASE_SECURITY_CHECKLIST.md`
- [S3-Overlay](S3_OVERLAY.md) — `S3_OVERLAY.md`
- [Schnelle Ansicht einzelner Dokumente](SCHNELLE_DATEIANSICHT.md) — `SCHNELLE_DATEIANSICHT.md`
- [Sicherheits- und Schwachstellenprozess](SECURITY.md) — `SECURITY.md`
- [Sicherheitsreview vom 29.08.2026](SECURITY_REVIEW_2026-08-29.md) — `SECURITY_REVIEW_2026-08-29.md`
- [Shopping / Einkaufslisten](SHOPPING.md) — `SHOPPING.md`
- [Ortstermine](SITE_VISITS.md) — `SITE_VISITS.md`
- [Synchronisation und Föderation](SYNC_FEDERATION.md) — `SYNC_FEDERATION.md`
- [Systemwerkzeuge für Virenschutz und Vorschauen](SYSTEMWERKZEUGE_VORSCHAU.md) — `SYSTEMWERKZEUGE_VORSCHAU.md`
- [Von TagSpaces inspirierte Dateiverwaltung](TAGSPACES_ANSAETZE.md) — `TAGSPACES_ANSAETZE.md`
- [SimpleOffice4Me Telefonie](TELEPHONY.md) — `TELEPHONY.md`
- [UI-Quick-Wins](UI_QUICK_WINS.md) — `UI_QUICK_WINS.md`
- [V2 final acceptance](V2_ACCEPTANCE.md) — `V2_ACCEPTANCE.md`
- [V2 baseline inventory](V2_BASELINE.md) — `V2_BASELINE.md`
- [V2 blob/content store format v1](V2_BLOB_STORE.md) — `V2_BLOB_STORE.md`
- [V2 cryptography threat model](V2_CRYPTO_THREAT_MODEL.md) — `V2_CRYPTO_THREAT_MODEL.md`
- [V2 delegation and trusted relay](V2_DELEGATION.md) — `V2_DELEGATION.md`
- [V2 encrypted blob store](V2_ENCRYPTED_BLOB_STORE.md) — `V2_ENCRYPTED_BLOB_STORE.md`
- [Independent V2 encrypted-blob recovery](V2_ENCRYPTED_RECOVERY.md) — `V2_ENCRYPTED_RECOVERY.md`
- [V2 encrypted blob runtime cutover](V2_ENCRYPTED_RUNTIME_CUTOVER.md) — `V2_ENCRYPTED_RUNTIME_CUTOVER.md`
- [Federation V2 persistent jobs](V2_FEDERATION_JOBS.md) — `V2_FEDERATION_JOBS.md`
- [V2 federation route policy](V2_FEDERATION_ROUTE_POLICY.md) — `V2_FEDERATION_ROUTE_POLICY.md`
- [V2 file browser migration](V2_FILE_BROWSER.md) — `V2_FILE_BROWSER.md`
- [V2 fragment and erasure-recovery contract](V2_FRAGMENT_RECOVERY.md) — `V2_FRAGMENT_RECOVERY.md`
- [V2 legacy-cleanup readiness](V2_LEGACY_CLEANUP.md) — `V2_LEGACY_CLEANUP.md`
- [V2 master-key profiles](V2_MASTER_KEYS.md) — `V2_MASTER_KEYS.md`
- [V2 metadata model](V2_METADATA.md) — `V2_METADATA.md`
- [V2 migration acceptance](V2_MIGRATION_ACCEPTANCE.md) — `V2_MIGRATION_ACCEPTANCE.md`
- [V2 Object Catalog](V2_OBJECT_CATALOG.md) — `V2_OBJECT_CATALOG.md`
- [V2 overlay import journal](V2_OVERLAY.md) — `V2_OVERLAY.md`
- [V2 password-vault key hierarchy](V2_PASSWORD_VAULT.md) — `V2_PASSWORD_VAULT.md`
- [V2 independent recovery CLI](V2_RECOVERY.md) — `V2_RECOVERY.md`
- [SimpleOffice4Me 2.0 release cutover](V2_RELEASE_CUTOVER.md) — `V2_RELEASE_CUTOVER.md`
- [V2 Password Vault payload object store](V2_VAULT_OBJECT_STORE.md) — `V2_VAULT_OBJECT_STORE.md`
- [Password Vault Web/UI boundary](V2_VAULT_WEB.md) — `V2_VAULT_WEB.md`
- [V3 Domain Events and Activity Stream](V3_ACTIVITY.md) — `V3_ACTIVITY.md`
- [V3 Automation](V3_AUTOMATION.md) — `V3_AUTOMATION.md`
- [Version 3.0: Funktionen und Betrieb](V3_BETRIEBSLEITFADEN.md) — `V3_BETRIEBSLEITFADEN.md`
- [CRM 3.0](V3_CRM.md) — `V3_CRM.md`
- [V3 Entity Detail Shell](V3_ENTITY_CONTEXT.md) — `V3_ENTITY_CONTEXT.md`
- [SimpleOffice4Me 3.0 – Evolution Contract](V3_EVOLUTION_CONTRACT.md) — `V3_EVOLUTION_CONTRACT.md`
- [Extension API 3.0](V3_EXTENSIONS.md) — `V3_EXTENSIONS.md`
- [Federation 3.0 transfer contract](V3_FEDERATION.md) — `V3_FEDERATION.md`
- [Finance 3.0 lifecycle](V3_FINANCE.md) — `V3_FINANCE.md`
- [System Health 3.0](V3_HEALTH.md) — `V3_HEALTH.md`
- [V3 Universal Inbox](V3_INBOX.md) — `V3_INBOX.md`
- [V3 Background Jobs](V3_JOBS.md) — `V3_JOBS.md`
- [V3 Policy Facade](V3_POLICY.md) — `V3_POLICY.md`
- [V3 entity references and relations](V3_RELATIONS.md) — `V3_RELATIONS.md`
- [SimpleOffice4Me 3.0 release-gate evidence](V3_RELEASE_GATE.md) — `V3_RELEASE_GATE.md`
- [V3 Global Search and Command Palette](V3_SEARCH.md) — `V3_SEARCH.md`
- [V3 Workboard](V3_WORKBOARD.md) — `V3_WORKBOARD.md`
- [Video-Vorschau und Video-Varianten](VIDEO_VORSCHAU.md) — `VIDEO_VORSCHAU.md`
- [Virtuelles Dateisystem, Ordnerrechte und SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) — `VIRTUELLES_DATEISYSTEM_SFTP.md`
- [Rekursive WebDAV-Ordnersperren nach RFC 4918](WEBDAV_COLLECTION_LOCKS_RFC4918.md) — `WEBDAV_COLLECTION_LOCKS_RFC4918.md`
- [Dateiverwaltung über WebDAV](WEBDAV_DATEIVERWALTUNG.md) — `WEBDAV_DATEIVERWALTUNG.md`
- [Fortsetzbare WebDAV-Downloads nach RFC 9110](WEBDAV_DOWNLOADS_RFC9110.md) — `WEBDAV_DOWNLOADS_RFC9110.md`
- [WebDAV-Eigenschaften und Metadaten nach RFC 4918](WEBDAV_EIGENSCHAFTEN_RFC4918.md) — `WEBDAV_EIGENSCHAFTEN_RFC4918.md`
- [Einheitliche HTTP-Vorbedingungen für WebDAV nach RFC 9110](WEBDAV_HTTP_VORBEDINGUNGEN_RFC9110.md) — `WEBDAV_HTTP_VORBEDINGUNGEN_RFC9110.md`
- [WebDAV-Bedingungen und ressourcengenauer Konfliktschutz nach RFC 4918](WEBDAV_IF_HEADER_RFC4918.md) — `WEBDAV_IF_HEADER_RFC4918.md`
- [WebDAV-Übertragungsintegrität nach RFC 9530](WEBDAV_INTEGRITAET_RFC9530.md) — `WEBDAV_INTEGRITAET_RFC9530.md`
- [Ordnergebundene WebDAV-Gerätezugänge nach dem Prinzip kleinster Rechte](WEBDAV_ORDNERZUGAENGE_RFC3744.md) — `WEBDAV_ORDNERZUGAENGE_RFC3744.md`
- [WebDAV-Ordner kopieren und verschieben nach RFC 4918](WEBDAV_ORDNER_COPY_MOVE_RFC4918.md) — `WEBDAV_ORDNER_COPY_MOVE_RFC4918.md`
- [Rekursives und wiederherstellbares WebDAV-Ordnerlöschen nach RFC 4918](WEBDAV_ORDNER_LOESCHEN_RFC4918.md) — `WEBDAV_ORDNER_LOESCHEN_RFC4918.md`
- [Portable Dateinamen für WebDAV-Desktop-Clients](WEBDAV_PORTABLE_DATEINAMEN.md) — `WEBDAV_PORTABLE_DATEINAMEN.md`
- [WebDAV-Principal- und Rechteerkennung nach RFC 3744 und RFC 5397](WEBDAV_PRINCIPAL_RECHTE_RFC3744_5397.md) — `WEBDAV_PRINCIPAL_RECHTE_RFC3744_5397.md`
- [WebDAV-Speichergrenzen und robuste Office-Sperren](WEBDAV_QUOTA_UND_LOCKS.md) — `WEBDAV_QUOTA_UND_LOCKS.md`
- [Begrenzte rekursive WebDAV-Bestandsaufnahme nach RFC 4918](WEBDAV_REKURSIVE_PROPFIND_RFC4918.md) — `WEBDAV_REKURSIVE_PROPFIND_RFC4918.md`
- [Vorhandene WebDAV-Dateien sicher per COPY ersetzen](WEBDAV_SICHERES_COPY_ERSETZEN.md) — `WEBDAV_SICHERES_COPY_ERSETZEN.md`
- [Vorhandene WebDAV-Dateien sicher per MOVE ersetzen](WEBDAV_SICHERES_MOVE_ERSETZEN.md) — `WEBDAV_SICHERES_MOVE_ERSETZEN.md`
- [Serverseitige WebDAV-Suche nach RFC 5323](WEBDAV_SUCHE_RFC5323_TAGSPACES.md) — `WEBDAV_SUCHE_RFC5323_TAGSPACES.md`
- [Effizienter WebDAV-Abgleich nach RFC 6578](WEBDAV_SYNC_RFC6578.md) — `WEBDAV_SYNC_RFC6578.md`
- [ClamAV-Prüfung vor WebDAV-Uploads](WEBDAV_UPLOADS_CLAMAV.md) — `WEBDAV_UPLOADS_CLAMAV.md`
- [WebDAV-Zeitstempel und ausgewählte Windows-Dateieigenschaften](WEBDAV_ZEITSTEMPEL_MS_WDVME.md) — `WEBDAV_ZEITSTEMPEL_MS_WDVME.md`
- [Getrennte WebDAV-Zugänge pro Gerät](WEBDAV_ZUGAENGE.md) — `WEBDAV_ZUGAENGE.md`
- [XRechnung validation baseline (2026)](XRECHNUNG_VALIDATION.md) — `XRECHNUNG_VALIDATION.md`
- [Dokumentationslogo](assets/README.md) — `assets/README.md`
- [Bildschirm teilen: Miracast / Wi-Fi Display](screen-sharing-miracast.md) — `screen-sharing-miracast.md`
