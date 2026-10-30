# Konfigurationsregister

Stand: 2026-10-05, Codebasis `2abc3ef6a02c173304d6c23352f48497730b7068`.
Dieses Register ergänzt vorhandene Fachanleitungen. Es nennt statisch sichtbare
`SIMPLEOFFICE_`-Namen in getrackten Python-/Start-/Build-Dateien unter `app/`,
`tools/`, `.github/`, `android/apk/`, `desktop/` und im Projektstamm.
Tests und Dokumentationsbeispiele sind keine Quelle für zusätzliche Optionen.
Es ist kein Dump der Prozessumgebung: Es werden ausschließlich Namen und
Quellpfade aufgelistet, niemals tatsächlich konfigurierte Werte.

## Sicherheitsgrenzen und Prioritäten

- Funktionen zuerst über bestehende Oberflächen und Rechte konfigurieren.
  Prozessvariablen sind Betreiberkonfiguration und verleihen keine Fachrechte.
- Secrets über die vorhandene geschützte Bereitstellung setzen. Nicht in README,
  Issue, Shell-History, Screenshots, Export oder Repository-Konfiguration kopieren.
- Ein Secret-bezogener Name allein ist kein Geheimnis. Öffentliche Schlüssel,
  Bindungsmetadaten und private Tokens sind unterschiedliche Datenklassen.
- Mail-Passwortvariablen benötigen die exakte administrative Zielbindung aus
  [IMAP/SMTP](IMAP_SIEVE_EMAIL_ARCHIV.md). Keine beliebigen Prozess-Secrets als
  Mailpasswort referenzieren.
- Build-/Geräte-/Installationsvariablen sind teilweise intern verwaltet. Ein
  Fund in Quelltext ist kein Auftrag, ihn manuell zu setzen.
- Nur die [3.0-Capability-Registry](V3_BETRIEBSLEITFADEN.md) verwendet durchgehend
  die dort beschriebene Schalterauswertung. Andere Variablen besitzen eigene
  Defaults, Parser, Vorrangregeln und Grenzen in den verlinkten Quellen.

## Ergänzte Optionen und Verfeinerungen

### Ausgehende Fehlerberichte

Die automatische Übertragung datensparsamer Diagnosen ist unter **Admin →
Einstellungen → Integrationen & Dienste → Automatische Fehlerberichte** persistent
deaktivierbar. `integrations.error_reporting_enabled` ist das ausgehende Gesamt-Gate;
es schaltet weder lokale Protokolle noch den Inbound-Collector auf einem Master ab.
`SIMPLEOFFICE_ERROR_REPORTING` wird nur bis zum ersten Speichern als Legacy-Opt-out
übernommen.

### Öffentliches Federation-Verzeichnis

`integrations.federation_public_directory_enabled` steuert die nicht
authentifizierte Leseabfrage bereits explizit publizierter Peer-Profile. Der
sichere Standard ist deaktiviert. `SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY=1`
ist nur ein Legacy-Opt-in bis die persistente Admin-Einstellung gespeichert ist;
Registrierung, Auflösung und Rendezvous bleiben separat authentifiziert.

### Öffentliches Federation-Instanzprofil

`integrations.federation_profile_country` steuert nur das optionale, öffentliche
ISO-3166-Alpha-2-Land im lokalen Discovery-Profil. Der Standard ist leer;
`SIMPLEOFFICE_FEDERATION_COUNTRY` wird nur bis zum ersten Speichern migriert.

### Tiefe Archivindexierung

Die Ressourcen-Grenzen werden in [archive_indexer.py](../app/archive_indexer.py)
ausgewertet. Der tiefe Archivindex ist zusätzlich als persistente Admin-Einstellung
`media.archive_deep_index_enabled` verfügbar. MB-Grenzen verwenden im Code
`1024**2` Bytes.

| Variable | Standard | Bedeutung / zulässige numerische Grenzen |
|---|---|---|
| `SIMPLEOFFICE_ARCHIVE_DEEP_INDEX` (Legacy-Migration) | `auto` | Gilt nur bis `media.archive_deep_index_enabled` gespeichert wurde; enabled Werte umgehen niemals CPU-/RAM-Prüfungen |
| `media.archive_max_load_per_cpu` (persistente Admin-Einstellung) | `0.75` | Lastschwelle pro CPU; `0.1–8.0` |
| `SIMPLEOFFICE_ARCHIVE_MAX_LOAD_PER_CPU` (Legacy-Migration) | `0.75` | Gilt nur bis `media.archive_max_load_per_cpu` gespeichert wurde; ungültige Werte nutzen den Standard, Ausreißer werden auf `0.1–8.0` begrenzt |
| `media.archive_min_free_memory_mb` (persistente Admin-Einstellung) | `384` | Mindestmenge freien RAM; `64–65536 MiB` |
| `SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB` (Legacy-Migration) | `384` | Gilt nur bis der persistente RAM-Grenzwert gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_disk_reserve_mb` (persistente Admin-Einstellung) | `2048` | Freier Speicherplatz, der neben der 10-%-Scratch-Grenze erhalten bleibt; `128–1048576 MiB` |
| `SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB` (Legacy-Migration) | `2048` | Gilt nur bis die persistente Datenträgerreserve gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_member_yield_ms` (persistente Admin-Einstellung) | `2` | Pause je verarbeitetem Archiveintrag; `0–100 ms` |
| `SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS` (Legacy-Migration) | `2` | Gilt nur bis `media.archive_member_yield_ms` gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_max_members` (persistente Admin-Einstellung) | `20000` | Maximale Zahl katalogisierter Archiveinträge; `100–200000` |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS` (Legacy-Migration) | `20000` | Gilt nur bis `media.archive_max_members` gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_max_member_mb` (persistente Admin-Einstellung) | `512` | Größter Eintrag, dessen Inhalt extrahiert wird; `1–8192 MiB` |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB` (Legacy-Migration) | `512` | Gilt nur bis `media.archive_max_member_mb` gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_max_text_mb` (persistente Admin-Einstellung) | `8` | Gesamtes abgeleitetes Volltextbudget pro Archiv; `1–64 MiB` |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB` (Legacy-Migration) | `8` | Gilt nur bis `media.archive_max_text_mb` gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_max_text_per_member_mb` (persistente Admin-Einstellung) | `2` | Textbudget je Eintrag; `1–32 MiB` |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_PER_MEMBER_MB` (Legacy-Migration) | `2` | Gilt nur bis `media.archive_max_text_per_member_mb` gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_max_content_files` (persistente Admin-Einstellung) | `500` | Höchstzahl der Einträge für Inhaltsverarbeitung; `1–10000` |
| `SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES` (Legacy-Migration) | `500` | Gilt nur bis `media.archive_max_content_files` gespeichert wurde; Ausreißer werden begrenzt |
| `media.archive_max_compression_ratio` (persistente Admin-Einstellung) | `200` | Verhältnis unkomprimierter zu komprimierter Größe; `10–10000`; greift ab 1 MiB je Eintrag |
| `SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO` (Legacy-Migration) | `200` | Gilt nur bis `media.archive_max_compression_ratio` gespeichert wurde; Ausreißer werden begrenzt |
| `SIMPLEOFFICE_ARCHIVE_OCR` | `0` | Legacy-Startwert bis `media.archive_ocr_enabled` unter `/admin/settings/documents-media` gespeichert wird; optionale OCR für unterstützte Bild-Einträge |

Erzwungene tiefe Indexierung hebt nicht Größen-, Pfad-, Scratch- oder
Kompressionsschutz auf. Das Scratch-Budget nutzt höchstens ein Zehntel des
freien Speichers und berücksichtigt zusätzlich die Reserve. Erhöhte Limits
zuerst an repräsentativen, nicht produktiven Archiven prüfen.

Mindest-RAM und Datenträgerreserve sind zusätzlich unter **Admin → Einstellungen
→ Dokumente & Medien** persistent konfigurierbar. Die Legacy-Variablen
`SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB` und
`SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB` werden nur bis zum Speichern der jeweiligen
Einstellung eingelesen; Änderungen gleichen vollständige abgeleitete
Archivindizes beim nächsten Indexlauf an. Die Pause je Archiveintrag gilt für
neu gestartete Indexläufe; `SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS` wird nur bis zum
Speichern dieses Werts als Startwert verwendet. Die Kataloggrenze gilt beim
nächsten Indexlauf; größere Archive werden als Teilindex markiert. Eintragsgröße
und Inhaltsanzahl begrenzen nur die Textextraktion: übersprungene Einträge bleiben
im Katalog und Originalarchive unverändert. Das Textbudget je Eintrag (Standard
2 MiB; zulässig 1–32 MiB) ist unter **Dokumente & Medien** persistent einstellbar;
die Legacy-Variable wird nur bis zum Speichern dieses Werts verwendet.

Die optionale OCR für Bilddateien in Archiven ist unter **Admin → Einstellungen
→ Dokumente & Medien** verfügbar. Sie ist standardmäßig deaktiviert und benötigt
das lokal installierte Tesseract. Die Einstellung gilt für neue bzw. erneut
gestartete Archivindizes; ein vorhandener vollständiger Index wird durch das
Deaktivieren nicht rückwirkend geändert. `SIMPLEOFFICE_ARCHIVE_OCR` wird nur als
Legacy-Startwert verwendet, bis die persistierte Medien-Einstellung gespeichert
wird. Größen-, Laufzeit- und Scratch-Limits bleiben unabhängig davon aktiv.

### OSM, Personaldaten, Chat und Druckfreigaben

| Variable | Standard / Grenze | Verwendung |
|---|---|---|
| `SIMPLEOFFICE_OSM_DOWNLOAD_RETRIES` | `5`, effektiv 1–10 | Legacy-Startwert bis eine persistierte Admin-Einstellung existiert; [OSM-Download](../app/osm_address_storage.py) |
| `SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB` | `8`, effektiv 0–64 | Legacy-Startwert; 0 deaktiviert OSM-Downloads, danach maßgeblich ist `integrations.osm_max_download_gib` unter `/admin/settings/integrations`; [OSM-Download](../app/osm_address_storage.py) |
| `SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS` | `300`, effektiv 30–3600 | Legacy-Timeout bis eine persistierte Admin-Einstellung existiert; danach maßgeblich ist `integrations.osm_download_read_timeout_seconds` unter `/admin/settings/integrations`; [OSM-Download](../app/osm_address_storage.py) |
| `SIMPLEOFFICE_OSM_FILTER_TIMEOUT` | Legacy-Startwert bis `integrations.osm_filter_timeout_seconds` unter `/admin/settings/integrations` gespeichert wird | [OSM-Aufbau](../app/osm_address_build.py) |
| `SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT` | Legacy-Startwert bis `integrations.osm_export_idle_timeout_seconds` unter `/admin/settings/integrations` gespeichert wird | [OSM-Aufbau](../app/osm_address_build.py) |
| `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC` | `1` | Legacy-Startwert bis `integrations.personnel_time_autosync_enabled` gespeichert wird; `0/false/no/off` deaktivieren; fachliche Freigaben bleiben erforderlich |
| `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC_SECONDS` | `300`, effektiv 60–3600 | Legacy-Startwert bis `integrations.personnel_time_autosync_seconds` gespeichert wird; [Personalsynchronisierung](../app/personnel_time_analytics.py) |
| `SIMPLEOFFICE_CHAT_ATTACHMENT_MAX_BYTES` | Mindestens 1 Byte, höchstens 4 GiB; leer/ungültig bedeutet globales Upload-Limit | Legacy-Startwert für `media.chat_attachment_max_bytes`; nach Speicherung ist **Admin → Einstellungen → Dokumente & Medien** maßgeblich. |
| `SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL` | `60`, effektiv 30–3600 | Legacy-Startwert bis `integrations.printershare_purge_interval_seconds` gespeichert wird; [PrinterShare](../app/printershare.py) |

Dies sind lokale Verarbeitungslimits, keine Zusage über tatsächliche
Gesamtlaufzeit, RAM-Verbrauch, Netzleistung oder Mail-/Anhangsfreigabe.

OSM-Lese-Timeout, maximales Quelldownload-Limit und Downloadversuche sind unter
**Admin → Einstellungen → Integrationen & Dienste** persistent konfigurierbar.
Die Größenbegrenzung wird vor dem Request anhand der gemeldeten Dateigröße und
erneut für jeden tatsächlich gelesenen Datenblock geprüft. Ein Limit von 0
deaktiviert Downloads, bevor ein Netzwerkzugriff erfolgt. Die Retry-Anzahl
begrenzt Wiederholungen; der bestehende exponentielle Warteplan bleibt auf
höchstens 10 Sekunden begrenzt. Gespeicherte Einstellungen gelten für neue
Downloads und haben Vorrang vor `SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS`,
`SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB` und `SIMPLEOFFICE_OSM_DOWNLOAD_RETRIES`.
Diese Environment-Werte werden nur bis zum Speichern der persistenten
Einstellungen als begrenzte Legacy-Startwerte verwendet.

Das ClamAV-Zeitlimit ist im Administrationsbereich **Integrationen & Dienste**
unter **ClamAV-Sicherheitsprüfung** persistent konfigurierbar. Der gespeicherte
Wert gilt für neu gestartete Prüfungen und hat Vorrang vor
`SIMPLEOFFICE_CLAMAV_TIMEOUT`; der Environment-Wert dient nur der Migration,
solange keine persistierte Einstellung existiert.

Die globale Hintergrund-Synchronisation der Personaldaten und ihr Prüfintervall
sind unter **Admin → Einstellungen → Integrationen & Dienste →
Personaldaten-Synchronisation** persistent konfigurierbar. Die gespeicherten
Werte haben Vorrang vor `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC` und
`SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC_SECONDS`; diese Variablen dienen nur als
begrenzte Startwerte, solange die jeweilige Einstellung noch fehlt. Die
Einstellung ersetzt nicht die separate Freigabe einer Synchronisation pro Peer
und Person.

Das PrinterShare-Bereinigungsintervall ist unter **Admin → Einstellungen →
Integrationen & Dienste → PrinterShare-Aufbewahrungsbereinigung** persistent
konfigurierbar. Es steuert nur den Prüfzyklus für bereits abgelaufene Druckjobs,
nicht deren Aufbewahrungsfrist. Die Einstellung wird beim nächsten Start des
Webprozesses übernommen; bis zur Speicherung dient
`SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL` als begrenzter Legacy-Startwert.

### Zugangsdaten, Geräte und Betriebsmetadaten

- `SIMPLEOFFICE_ANDROID_ACCOUNT` bezeichnet die lokale Android-Identität.
  `SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN` ist ein Secret für den vorhandenen
  Loopback-Bootstrap, kein öffentliches Login-Token; [Prüfung](../app/android_auth.py).
- `SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY` wählt das Artefakt-Repository.
  `SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN_FILE` wird vor dem direkten
  `SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN` berücksichtigt. Beides sicher bereitstellen;
  keine Tokenwerte in den Dokumenten verwenden; [Artefaktkonfiguration](../app/software_artifact_config.py).
- `SIMPLEOFFICE_RESOURCE_COMMANDER_PEER_TOKENS` ist ein geheimes JSON-Mapping.
  Der dynamische Name `SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN_<PEER>` hat Vorrang:
  Peer-ID in Großbuchstaben, nicht alphanumerische Zeichen als Unterstrich,
  Rand-Unterstriche entfernt. Eigene Commander-Tokens, kein Rückfall auf
  Federation-Tokens; [Peer-Credentials](../app/resource_peer_credentials.py).
  Der Registereintrag mit abschließendem `_` bezeichnet diesen Präfix.
- `SIMPLEOFFICE_FEDERATION_DIRECTORY_TOKEN` und `SIMPLEOFFICE_LICENSE_MASTER_TOKEN`
  sind dienstspezifische Secrets; `SIMPLEOFFICE_FEDERATION_PUBLIC_KEY` ist
  dagegen öffentlicher Profilinhalt. Nicht gegenseitig ersetzen.
  `SIMPLEOFFICE_FEDERATION_REFUSE_BAD_CLIENT` steuert die entsprechende
  Client-Ablehnung; [Lizenz-/Clientprüfung](../app/license_routes.py).
- `SIMPLEOFFICE_RESTORE_ROOT` begrenzt den Wiederherstellungsort;
  [Recovery-Pfadprüfung](../app/replication_store.py) beachten.
- `SIMPLEOFFICE_CONTAINER_ROLE` trennt unter anderem Web- und Error-Relay-Rolle.
  `SIMPLEOFFICE_LOG_STDERR_ONLY` steuert die Logausgabe. Diese Rollen-/Logoptionen
  ersetzen weder Authentifizierung noch sensible Datenfilterung.
- `SIMPLEOFFICE_BUILD_BRANCH`, `SIMPLEOFFICE_BUILD_EPOCH`,
  `SIMPLEOFFICE_BUILD_NUMBER`, `SIMPLEOFFICE_BUILD_REVISION`,
  `SIMPLEOFFICE_VERSION` und `SIMPLEOFFICE_INSTALLATION_ID` sind Herkunfts-/
  Laufzeitmetadaten. Keine Version 3.0 vortäuschen, indem nur ein Wert geändert wird.
- `SIMPLEOFFICE_MUSTANG_JAR` wählt den Validator-Pfad und
  `SIMPLEOFFICE_GITHUB_ERROR_LABEL` das Fehler-Issue-Label. Einen Validator-Pfad
  nur auf geprüfte Artefakte richten; keine Validierung umgehen.

## Register der statisch sichtbaren Namen

Die Quelle benennt eine Fundstelle; weitere Nutzer desselben Namens können
existieren. Defaults und Vorrang aus der Implementierung und passenden
Fachanleitung übernehmen. Auch intern verwaltete Namen und dynamische Präfixe
sind enthalten; das Register garantiert keine vollständige Inventarisierung
frei wählbarer Passwortvariablennamen oder aller externen Systemvariablen.

| Name | Quelle | Vorhandene Fachreferenz |
|---|---|---|
| `SIMPLEOFFICE_ALLOW_PUBLIC_REGISTRATION` (Legacy; keine direkte Aktivierung, Kontoanträge administrativ steuern) | [__init__.py](../app/__init__.py) | [Konten und Einladungen](KONTOANTRAEGE_UND_EINLADUNGEN.md) |
| `SIMPLEOFFICE_LOGIN_QR` | [login_qr.py](../app/login_qr.py) | [Login-QR-Codes](LOGIN_QR_CODES.md) |
| `SIMPLEOFFICE_LOGIN_QR_LAN` | [login_qr.py](../app/login_qr.py) | [Login-QR-Codes](LOGIN_QR_CODES.md) |
| `SIMPLEOFFICE_LOGIN_QR_PORTS` | [login_qr.py](../app/login_qr.py) | [Login-QR-Codes](LOGIN_QR_CODES.md) |
| `SIMPLEOFFICE_LOGIN_QR_URLS` | [login_qr.py](../app/login_qr.py) | [Login-QR-Codes](LOGIN_QR_CODES.md) |
| `SIMPLEOFFICE_AMAZON_ACCESS_KEY` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_AMAZON_PARTNER_TAG` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_AMAZON_SECRET_KEY` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_ANDROID` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [ANDROID_INTEGRATION](ANDROID_INTEGRATION.md) |
| `SIMPLEOFFICE_ANDROID_ABI` | [android-apk-build.yml](../.github/workflows/android-apk-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ANDROID_ACCOUNT` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ANDROID_PYTHON` | [android-apk-build.yml](../.github/workflows/android-apk-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_DEEP_INDEX` | [archive_settings.py](../app/archive_settings.py) | Legacy-Migrationswert; Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_LOAD_PER_CPU` | [archive_settings.py](../app/archive_settings.py) | Legacy-Migrationswert; Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_PER_MEMBER_MB` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB` (Legacy-Migration) | [archive_settings.py](../app/archive_settings.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_OCR` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BACKGROUND_INDEX` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_BUILD_BRANCH` | [software_distribution_core.py](../app/software_distribution_core.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BUILD_EPOCH` | [docker-build.yml](../.github/workflows/docker-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BUILD_NUMBER` | [docker-build.yml](../.github/workflows/docker-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BUILD_REVISION` | [docker-build.yml](../.github/workflows/docker-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_CHAT_ATTACHMENT_MAX_BYTES` | [settings_store.py](../app/settings_store.py) | Legacy-Migration; die persistente Einstellung wird in [chat_routes.py](../app/chat_routes.py) angewendet |
| `SIMPLEOFFICE_CLAMAV_SCANNER` | [attachment_security.py](../app/attachment_security.py) | [ANHAENGE_CLAMAV](ANHAENGE_CLAMAV.md) |
| `SIMPLEOFFICE_CLAMAV_TIMEOUT` | `120`, effektiv 5–900 | Legacy-Startwert bis `integrations.clamav_timeout_seconds` unter `/admin/settings/integrations` gespeichert wird; [ClamAV-Anhänge](ANHAENGE_CLAMAV.md) |
| `SIMPLEOFFICE_CONTAINER_ROLE` | [ci.yml](../.github/workflows/ci.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DATALOGGER` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [DATENLOGGER_SENSOREN](DATENLOGGER_SENSOREN.md) |
| `SIMPLEOFFICE_DATALOGGER_TICK_SECONDS` (Legacy-Import) | [datalogger_store.py](../app/datalogger_store.py) | [DATENLOGGER_SENSOREN](DATENLOGGER_SENSOREN.md); bevorzugt über Datenlogger → Worker-Einstellungen konfigurieren |
| `SIMPLEOFFICE_DATA_DIR` | [runtime_entry.py](../desktop/python-setup/runtime_entry.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DESKTOP` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DOCUMENT_ADMINS` | [virtual_filesystem.py](../app/virtual_filesystem.py) | [ERSTSTART_UND_DESKTOP_SETUP](ERSTSTART_UND_DESKTOP_SETUP.md) |
| `SIMPLEOFFICE_DOCUMENT_ROOT` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [ERSTER_START](ERSTER_START.md) |
| `SIMPLEOFFICE_EBAY_CLIENT_ID` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_EBAY_CLIENT_SECRET` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_EBAY_MARKETPLACE_ID` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_ERROR_RELAY_ENABLED` | [ci.yml](../.github/workflows/ci.yml) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_ERROR_REPORTING` (Legacy-Migration) | [github_error_reporter.py](../app/github_error_reporter.py) | Persistente Präferenz `integrations.error_reporting_enabled`; [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_ERROR_REPORT_URL` | [android-apk-build.yml](../.github/workflows/android-apk-build.yml) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_EXTENSION_HTTP_ALLOWLIST` | [v3_extensions.py](../app/v3_extensions.py) | [V3_EXTENSIONS](V3_EXTENSIONS.md) |
| `SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK` | [federation_discovery_endpoint.py](../app/federation_discovery_endpoint.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS` | [federation_discovery_endpoint.py](../app/federation_discovery_endpoint.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY` | Legacy-Startwert für die optionale Admin-Einstellung `integrations.federation_autoscan_country` | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS` | `21600`, effektiv 3600–604800; Legacy-Startwert für `integrations.federation_autoscan_seconds` | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_BOOTSTRAP_URLS` | [federation_discovery_service.py](../app/federation_discovery_service.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_COUNTRY` (Legacy-Migration) | [federation_local_profile.py](../app/federation_local_profile.py) | Persistente Präferenz `integrations.federation_profile_country`; [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_DIRECTORY_TOKEN` | [federation_discovery_http.py](../app/federation_discovery_http.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_FEDERATION_FINGERPRINT` | [federation_local_profile.py](../app/federation_local_profile.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_LABEL` | [federation_local_profile.py](../app/federation_local_profile.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_LAN_ADDRESS` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [AUDIO_STREAMER](AUDIO_STREAMER.md) |
| `SIMPLEOFFICE_FEDERATION_LAN_PORTS` | [federation_discovery_lan.py](../app/federation_discovery_lan.py) | [AUDIO_STREAMER](AUDIO_STREAMER.md) |
| `SIMPLEOFFICE_FEDERATION_PEER_ID` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [CHAT](CHAT.md) |
| `SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY` (Legacy-Migration) | [federation_discovery_http.py](../app/federation_discovery_http.py) | Persistente Präferenz `integrations.federation_public_directory_enabled`; [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_PUBLIC_KEY` | [federation_local_profile.py](../app/federation_local_profile.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_FEDERATION_PUBLIC_URL` | [federation_local_profile.py](../app/federation_local_profile.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_REFUSE_BAD_CLIENT` | [license_routes.py](../app/license_routes.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_FEDERATION_TOKEN` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [DEPLOYMENT](DEPLOYMENT.md) |
| `SIMPLEOFFICE_FIREWALL_AGENT_STATE` | [simpleoffice_firewall_agent.py](../simpleoffice_firewall_agent.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_FIREWALL_SOCKET` | [simpleoffice_firewall.py](../simpleoffice_firewall.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_GITHUB_ARTIFACT_REPOSITORY` | [software_artifact_config.py](../app/software_artifact_config.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN` | [software_artifact_config.py](../app/software_artifact_config.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_GITHUB_ARTIFACT_TOKEN_FILE` | [software_artifact_config.py](../app/software_artifact_config.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_GITHUB_ERROR_LABEL` | [github_error_reporter.py](../app/github_error_reporter.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_GITHUB_ERROR_REPORTING` | [ci.yml](../.github/workflows/ci.yml) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_GITHUB_ERROR_REPOSITORY` | [github_error_reporter.py](../app/github_error_reporter.py) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_GITHUB_ERROR_TOKEN` | [github_error_reporter.py](../app/github_error_reporter.py) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_GITHUB_ERROR_TOKEN_FILE` | [github_error_reporter.py](../app/github_error_reporter.py) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_GITHUB_ERROR_TOKEN_SOURCE` | [ci.yml](../.github/workflows/ci.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_GOOGLE_AUTO_PROVISION` | [__init__.py](../app/__init__.py) | [SECURITY](SECURITY.md) |
| `SIMPLEOFFICE_GOOGLE_CALENDAR_ACCOUNTS_JSON` | [google_calendar_sync.py](../app/google_calendar_sync.py) | [GOOGLE_KALENDER_SYNC](GOOGLE_KALENDER_SYNC.md) |
| `SIMPLEOFFICE_GOOGLE_CLIENT_ID` | [__init__.py](../app/__init__.py) | [GOOGLE_DRIVE_SYNC](GOOGLE_DRIVE_SYNC.md) |
| `SIMPLEOFFICE_GOOGLE_CLIENT_SECRET` | [__init__.py](../app/__init__.py) | [GOOGLE_DRIVE_SYNC](GOOGLE_DRIVE_SYNC.md) |
| `SIMPLEOFFICE_GOOGLE_CREDENTIALS_FILE` | [__init__.py](../app/__init__.py) | [GOOGLE_DRIVE_SYNC](GOOGLE_DRIVE_SYNC.md) |
| `SIMPLEOFFICE_GOOGLE_DRIVE_MAX_MIB` | [google_drive_sync.py](../app/google_drive_sync.py) | [GOOGLE_DRIVE_SYNC](GOOGLE_DRIVE_SYNC.md) |
| `SIMPLEOFFICE_GOOGLE_REDIRECT_URI` | [__init__.py](../app/__init__.py) | [GOOGLE_DRIVE_SYNC](GOOGLE_DRIVE_SYNC.md) |
| `SIMPLEOFFICE_HOST` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [DEPLOYMENT](DEPLOYMENT.md) |
| `SIMPLEOFFICE_INDEX_DELAY_SECONDS` | [index_worker.py](../tools/index_worker.py) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_INDEX_NICE` | [index_worker.py](../tools/index_worker.py) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_INDEX_RECONCILE_SECONDS` | [index_worker.py](../tools/index_worker.py) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_INDEX_YIELD_MS` | [index_worker.py](../tools/index_worker.py) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_INSTALLATION_ID` | [system_identity.py](../app/system_identity.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_INSTANCE_DIR` | [runtime_entry.py](../desktop/python-setup/runtime_entry.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_LICENSE_MASTER_TOKEN` | [license_routes.py](../app/license_routes.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_LOG_STDERR_ONLY` | [applogging.py](../app/applogging.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_MAIL_CASE_FEDERATION_WORKER` | [mail_case_federation_runtime.py](../app/mail_case_federation_runtime.py) | [MAIL_VORGAENGE](MAIL_VORGAENGE.md) |
| `SIMPLEOFFICE_MAIL_ENV_CREDENTIAL_BINDINGS` | [mail_env_credentials.py](../app/mail_env_credentials.py) | [IMAP_SIEVE_EMAIL_ARCHIV](IMAP_SIEVE_EMAIL_ARCHIV.md) |
| `SIMPLEOFFICE_MAX_UPLOAD_MIB` | [__init__.py](../app/__init__.py) | [DATEI_WIEDERHERSTELLUNG](DATEI_WIEDERHERSTELLUNG.md) |
| `SIMPLEOFFICE_MCP` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [CHATGPT_MCP](CHATGPT_MCP.md) |
| `SIMPLEOFFICE_MINI_SERVICES_AUTOSTART` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [DEPLOYMENT](DEPLOYMENT.md) |
| `SIMPLEOFFICE_MINI_SERVICES_CONFIG` | [simpleoffice_mini_core.py](../simpleoffice_mini_core.py) | [MINI_SERVICES](MINI_SERVICES.md) |
| `SIMPLEOFFICE_MINI_SERVICES_PYTHON` | [start.sh](../start.sh) | [MINI_SERVICES](MINI_SERVICES.md) |
| `SIMPLEOFFICE_MUSTANG_JAR` | [business_document_generation.py](../app/business_document_generation.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_NATIVE_PACKAGES` | [start.sh](../start.sh) | [MINI_SERVICES_REVIEW](MINI_SERVICES_REVIEW.md) |
| `SIMPLEOFFICE_OCR_THREADS` | [document_store_core.py](../app/document_store_core.py) | [ERSTER_START](ERSTER_START.md) |
| `SIMPLEOFFICE_OSM_CITY` | [launcher.py](../tools/launcher.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_DOWNLOAD_RETRIES` | [osm_address_storage.py](../app/osm_address_storage.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT` | Legacy-Startwert bis `integrations.osm_export_idle_timeout_seconds` unter `/admin/settings/integrations` gespeichert wird | [OSM_INDEX_UND_RECHNUNGSFINALISIERUNG](OSM_INDEX_UND_RECHNUNGSFINALISIERUNG.md) |
| `SIMPLEOFFICE_OSM_FILTER_TIMEOUT` | Legacy-Startwert bis `integrations.osm_filter_timeout_seconds` unter `/admin/settings/integrations` gespeichert wird | [OSM-Aufbau](../app/osm_address_build.py) |
| `SIMPLEOFFICE_OSM_FORCE` | [launcher.py](../tools/launcher.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_INDEX` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB` | [osm_address_storage.py](../app/osm_address_storage.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS` | [osm_address_storage.py](../app/osm_address_storage.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_REGION` | [launcher.py](../tools/launcher.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_OSM_REINDEX_ON_START` | [start.bat](../start.bat) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC` | [personnel_time_analytics.py](../app/personnel_time_analytics.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC_SECONDS` | [personnel_time_analytics.py](../app/personnel_time_analytics.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_PIPER_MODEL` | [audio_output_engine.py](../app/audio_output_engine.py) | [AUDIO_OUTPUT](AUDIO_OUTPUT.md) |
| `SIMPLEOFFICE_PORT` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [AUDIO_STREAMER](AUDIO_STREAMER.md) |
| `SIMPLEOFFICE_PREVIEWS` | [preview_service.py](../app/preview_service.py) | [DOKUMENTVORSCHAU_INDEXDIENST](DOKUMENTVORSCHAU_INDEXDIENST.md) |
| `SIMPLEOFFICE_PREVIEW_MAX_BYTES` | [preview_service.py](../app/preview_service.py) | [DOKUMENTVORSCHAU_INDEXDIENST](DOKUMENTVORSCHAU_INDEXDIENST.md) |
| `SIMPLEOFFICE_PREVIEW_MAX_PIXELS` | [preview_service.py](../app/preview_service.py) | [DOKUMENTVORSCHAU_INDEXDIENST](DOKUMENTVORSCHAU_INDEXDIENST.md) |
| `SIMPLEOFFICE_PREVIEW_TIMEOUT_SECONDS` | [preview_service.py](../app/preview_service.py) | [DOKUMENTVORSCHAU_INDEXDIENST](DOKUMENTVORSCHAU_INDEXDIENST.md) |
| `SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL` | [printershare.py](../app/printershare.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_PROJECT_REPO_ROOTS` | [project_git.py](../app/project_git.py) | [PROJECT_TRACKER](PROJECT_TRACKER.md) |
| `SIMPLEOFFICE_RESOURCE_COMMANDER_PEER_TOKENS` | [resource_peer_credentials.py](../app/resource_peer_credentials.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN` | [resource_commander_access.py](../app/resource_commander_access.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_RESOURCE_COMMANDER_TOKEN_` | [resource_peer_credentials.py](../app/resource_peer_credentials.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_RESTORE_ROOT` | [replication_store.py](../app/replication_store.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_RSYNC_ENABLED` (Legacy-Import / direkter SFTP-Starter) | [sftp_service.py](../app/sftp_service.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md); bevorzugt über Mini Services → SFTP konfigurieren |
| `SIMPLEOFFICE_RSYNC_MAX_BYTES` | Legacy-Startwert bis `network.rsync_max_bytes` unter `/admin/settings/network` gespeichert wird | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_RSYNC_MAX_FILES` | Legacy-Startwert bis `network.rsync_max_files` unter `/admin/settings/network` gespeichert wird | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_RSYNC_TIMEOUT` | `3600`, effektiv 30–86400 | Legacy-Startwert bis `network.rsync_timeout_seconds` unter `/admin/settings/network` gespeichert wird; [Rsync-Sitzungen](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_RUN_XRECHNUNG_INTEGRATION` | [ci.yml](../.github/workflows/ci.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_S3_CLOCK_SKEW_SECONDS` | [__init__.py](../app/__init__.py) | [S3_OVERLAY](S3_OVERLAY.md) |
| `SIMPLEOFFICE_S3_MAX_STAGING_MIB` | [__init__.py](../app/__init__.py) | [S3_OVERLAY](S3_OVERLAY.md) |
| `SIMPLEOFFICE_S3_MAX_UPLOAD_MIB` | [__init__.py](../app/__init__.py) | [S3_OVERLAY](S3_OVERLAY.md) |
| `SIMPLEOFFICE_S3_OVERLAY_ENABLED` | [__init__.py](../app/__init__.py) | [S3_OVERLAY](S3_OVERLAY.md) |
| `SIMPLEOFFICE_S3_OVERLAY_REGION` | [__init__.py](../app/__init__.py) | [S3_OVERLAY](S3_OVERLAY.md) |
| `SIMPLEOFFICE_SECRET_KEY` | [secret_key.py](../app/secret_key.py) | [ERSTER_START](ERSTER_START.md) |
| `SIMPLEOFFICE_SECURITY_ADMINS` | [documents_core.py](../app/documents_core.py) | [ANHAENGE_CLAMAV](ANHAENGE_CLAMAV.md) |
| `SIMPLEOFFICE_SENSOR_` | [datalogger_collectors.py](../app/datalogger_collectors.py) | [DATENLOGGER_SENSOREN](DATENLOGGER_SENSOREN.md) |
| `SIMPLEOFFICE_SENSOR_ALLOWED_HOSTS` | [datalogger_collectors.py](../app/datalogger_collectors.py) | [DATENLOGGER_SENSOREN](DATENLOGGER_SENSOREN.md) |
| `SIMPLEOFFICE_SERVER_PEER_ID` | [server-deb-build.yml](../.github/workflows/server-deb-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_SERVER_PUBLIC_URL` | [server-deb-build.yml](../.github/workflows/server-deb-build.yml) | [SITE_VISITS](SITE_VISITS.md) |
| `SIMPLEOFFICE_SFTP_BIND` | [sftp_server.py](../app/sftp_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_SFTP_HOST_KEY` | [documents_core.py](../app/documents_core.py) | [ERSTSTART_UND_DESKTOP_SETUP](ERSTSTART_UND_DESKTOP_SETUP.md) |
| `SIMPLEOFFICE_SFTP_MAX_BYTES` (Legacy-Import / direkter SFTP-Starter) | [sftp_service.py](../app/sftp_service.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md); bevorzugt über Mini Services → SFTP konfigurieren |
| `SIMPLEOFFICE_SFTP_MAX_CLIENTS` (Legacy-Import / direkter SFTP-Starter) | [sftp_service.py](../app/sftp_service.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md); bevorzugt über Mini Services → SFTP konfigurieren |
| `SIMPLEOFFICE_SFTP_PASSWORD_AUTH` (Legacy-Import / direkter SFTP-Starter) | [sftp_service.py](../app/sftp_service.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md); bevorzugt über Mini Services → SFTP konfigurieren |
| `SIMPLEOFFICE_SFTP_PORT` | [sftp_server.py](../app/sftp_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_SIP_BIND` | [simpleoffice_sip_runtime.py](../simpleoffice_sip_runtime.py) | [MINI_SERVICES_REVIEW](MINI_SERVICES_REVIEW.md) |
| `SIMPLEOFFICE_SKIP_INVOICE_VALIDATOR_BOOTSTRAP` | [install_invoice_validator.py](../tools/install_invoice_validator.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_SKIP_XRECHNUNG_VALIDATOR_BOOTSTRAP` | [install_xrechnung_validator.py](../tools/install_xrechnung_validator.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_SMTP_FROM` | [calendar_store.py](../app/calendar_store.py) | [KALENDER](KALENDER.md) |
| `SIMPLEOFFICE_SMTP_HOST` | [calendar_store.py](../app/calendar_store.py) | [KALENDER](KALENDER.md) |
| `SIMPLEOFFICE_SMTP_PASSWORD` | [calendar_store.py](../app/calendar_store.py) | [KALENDER](KALENDER.md) |
| `SIMPLEOFFICE_SMTP_PORT` | [calendar_store.py](../app/calendar_store.py) | [KALENDER](KALENDER.md) |
| `SIMPLEOFFICE_SMTP_STARTTLS` | [calendar_store.py](../app/calendar_store.py) | [KALENDER](KALENDER.md) |
| `SIMPLEOFFICE_SMTP_USER` | [calendar_store.py](../app/calendar_store.py) | [KALENDER](KALENDER.md) |
| `SIMPLEOFFICE_HTTPS` | [__init__.py](../app/__init__.py) | [PROXY_HTTPS](PROXY_HTTPS.md) |
| `SIMPLEOFFICE_TRUSTED_PROXY_HOPS` | [__init__.py](../app/__init__.py) | [CHATGPT_MCP](CHATGPT_MCP.md) |
| `SIMPLEOFFICE_UPDATE_REF` | [release_updater.py](../tools/release_updater.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_V2_STORAGE_PASSWORD_FILE` | [runtime_keys.py](../app/v2/runtime_keys.py) | [V2_ENCRYPTED_RUNTIME_CUTOVER](V2_ENCRYPTED_RUNTIME_CUTOVER.md) |
| `SIMPLEOFFICE_V3_ACTIVITY_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_ANDROID_OFFLINE_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [ANDROID](ANDROID.md) |
| `SIMPLEOFFICE_V3_AUTOMATION_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_CRM_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_ENTITY_CONTEXT_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_EXTENSIONS_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_FEDERATION_ENABLED` | [v3_capabilities.py](../app/v3_capabilities.py) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_FINANCE_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_HEALTH_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_INBOX_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_JOBS_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_POLICY_ENABLED` | [v3_capabilities.py](../app/v3_capabilities.py) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_RELATIONS_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_SAMPLE_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_SEARCH_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_V3_WORKBOARD_ENABLED` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [V3_BETRIEBSLEITFADEN](V3_BETRIEBSLEITFADEN.md) |
| `SIMPLEOFFICE_VERSION` | [__init__.py](../app/__init__.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_VIDEO_PREVIEW_FRAMES` | [video_settings.py](../app/video_settings.py) | [VIDEO_VORSCHAU](VIDEO_VORSCHAU.md) |
| `SIMPLEOFFICE_WEBDAV_CLAMAV` | [__init__.py](../app/__init__.py) | [WEBDAV_DATEIVERWALTUNG](WEBDAV_DATEIVERWALTUNG.md) |
| `SIMPLEOFFICE_WEBDAV_QUARANTINE_MIB` | [__init__.py](../app/__init__.py) | [WEBDAV_UPLOADS_CLAMAV](WEBDAV_UPLOADS_CLAMAV.md) |
| `SIMPLEOFFICE_WEBDAV_QUOTA_MIB` (Legacy-Initialwert; persistierte Einstellung unter Einstellungen → Netzwerk & Synchronisation hat Vorrang) | [__init__.py](../app/__init__.py), [settings_store.py](../app/settings_store.py) | [WEBDAV_QUOTA_UND_LOCKS](WEBDAV_QUOTA_UND_LOCKS.md) |
| `SIMPLEOFFICE_WORKER_ROOT` | [launcher.py](../tools/launcher.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT` | [start.bat](../start.bat) | [PRODUKTIONSBETRIEB](PRODUKTIONSBETRIEB.md) |
| `SIMPLEOFFICE_WSGI_THREADS` | [runtime_entry.py](../desktop/python-setup/runtime_entry.py) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_ZUGFERD_VALIDATOR` | [business_document_generation.py](../app/business_document_generation.py) | [OSM_INDEX_UND_RECHNUNGSFINALISIERUNG](OSM_INDEX_UND_RECHNUNGSFINALISIERUNG.md) |
