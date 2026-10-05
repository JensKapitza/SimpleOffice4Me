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

### Tiefe Archivindexierung

Diese Grenzen werden in [archive_indexer.py](../app/archive_indexer.py)
ausgewertet. MB-Grenzen verwenden im Code `1024**2` Bytes.

| Variable | Standard | Bedeutung / zulässige numerische Grenzen |
|---|---|---|
| `SIMPLEOFFICE_ARCHIVE_DEEP_INDEX` | `auto` | `0/false/no/off`: aus; `1/true/yes/on`: Last-/RAM-Prüfung übergehen; sonst automatische Last-/RAM-Prüfung |
| `SIMPLEOFFICE_ARCHIVE_MAX_LOAD_PER_CPU` | `0.75` | Lastschwelle pro CPU; effektiv mindestens `0.1` |
| `SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB` | `384` | Mindest-RAM; 64–65536 |
| `SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB` | `2048` | Freier Scratch-Speicher als Reserve; 128–1048576 |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS` | `20000` | Maximal berücksichtigte Einträge; 100–200000 |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB` | `512` | Maximalgröße eines Eintrags; 1–8192 |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB` | `8` | Gesamtes Textbudget; 1–64 |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_PER_MEMBER_MB` | `2` | Textbudget je Eintrag; 1–32 |
| `SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES` | `500` | Anzahl für Inhaltsverarbeitung; 1–10000 |
| `SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO` | `200` | Begrenzung des Kompressionsverhältnisses; 10–10000 |
| `SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS` | `2` | Pause je Eintrag in ms; 0–100 |
| `SIMPLEOFFICE_ARCHIVE_OCR` | `0` | Optionale OCR für unterstützte Bild-Einträge |

Erzwungene tiefe Indexierung hebt nicht Größen-, Pfad-, Scratch- oder
Kompressionsschutz auf. Das Scratch-Budget nutzt höchstens ein Zehntel des
freien Speichers und berücksichtigt zusätzlich die Reserve. Erhöhte Limits
zuerst an repräsentativen, nicht produktiven Archiven prüfen.

### OSM, Personaldaten, Chat und Druckfreigaben

| Variable | Standard / Grenze | Verwendung |
|---|---|---|
| `SIMPLEOFFICE_OSM_DOWNLOAD_RETRIES` | `5`, effektiv 1–10 | Downloadversuche; [OSM-Download](../app/osm_address_storage.py) |
| `SIMPLEOFFICE_OSM_MAX_DOWNLOAD_GIB` | `8` | Maximaler Quelldownload in GiB; [OSM-Download](../app/osm_address_storage.py) |
| `SIMPLEOFFICE_OSM_READ_TIMEOUT_SECONDS` | `300`, mindestens 30 | Lese-Timeout; [OSM-Download](../app/osm_address_storage.py) |
| `SIMPLEOFFICE_OSM_FILTER_TIMEOUT` | `21600`, effektiv 3600–86400 | Filter-Timeout in Sekunden; [OSM-Aufbau](../app/osm_address_build.py) |
| `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC` | `1` | Runtime-Autosync; `0/false/no/off` deaktivieren; fachliche Freigaben bleiben erforderlich |
| `SIMPLEOFFICE_PERSONNEL_TIME_AUTOSYNC_SECONDS` | `300`, effektiv 60–3600 | Poll-Intervall; [Personalsynchronisierung](../app/personnel_time_analytics.py) |
| `SIMPLEOFFICE_CHAT_ATTACHMENT_MAX_BYTES` | Uploadlimit der App, Fallback 512 MiB | Chat-Anhangslimit in Bytes, mindestens 1 und höchstens das App-Uploadlimit; [Chatroute](../app/chat_routes.py) |
| `SIMPLEOFFICE_PRINTERSHARE_PURGE_INTERVAL` | `60`, effektiv 30–3600 | TTL-Bereinigung in Sekunden; [PrinterShare](../app/printershare.py) |

Dies sind lokale Verarbeitungslimits, keine Zusage über tatsächliche
Gesamtlaufzeit, RAM-Verbrauch, Netzleistung oder Mail-/Anhangsfreigabe.

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
| `SIMPLEOFFICE_ALLOW_PUBLIC_REGISTRATION` | [__init__.py](../app/__init__.py) | [SECURITY](SECURITY.md) |
| `SIMPLEOFFICE_AMAZON_ACCESS_KEY` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_AMAZON_PARTNER_TAG` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_AMAZON_SECRET_KEY` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_ANDROID` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [ANDROID_INTEGRATION](ANDROID_INTEGRATION.md) |
| `SIMPLEOFFICE_ANDROID_ABI` | [android-apk-build.yml](../.github/workflows/android-apk-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ANDROID_ACCOUNT` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ANDROID_BOOTSTRAP_TOKEN` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ANDROID_PYTHON` | [android-apk-build.yml](../.github/workflows/android-apk-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_DEEP_INDEX` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_DISK_RESERVE_MB` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_COMPRESSION_RATIO` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_CONTENT_FILES` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_LOAD_PER_CPU` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBERS` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_MEMBER_MB` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_MB` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MAX_TEXT_PER_MEMBER_MB` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MEMBER_YIELD_MS` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_MIN_FREE_MEMORY_MB` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_ARCHIVE_OCR` | [archive_indexer.py](../app/archive_indexer.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BACKGROUND_INDEX` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_BUILD_BRANCH` | [software_distribution_core.py](../app/software_distribution_core.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BUILD_EPOCH` | [docker-build.yml](../.github/workflows/docker-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BUILD_NUMBER` | [docker-build.yml](../.github/workflows/docker-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_BUILD_REVISION` | [docker-build.yml](../.github/workflows/docker-build.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_CHAT_ATTACHMENT_MAX_BYTES` | [chat_routes.py](../app/chat_routes.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_CLAMAV_SCANNER` | [attachment_security.py](../app/attachment_security.py) | [ANHAENGE_CLAMAV](ANHAENGE_CLAMAV.md) |
| `SIMPLEOFFICE_CLAMAV_TIMEOUT` | [attachment_security.py](../app/attachment_security.py) | [ANHAENGE_CLAMAV](ANHAENGE_CLAMAV.md) |
| `SIMPLEOFFICE_CONTAINER_ROLE` | [ci.yml](../.github/workflows/ci.yml) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DATALOGGER` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [DATENLOGGER_SENSOREN](DATENLOGGER_SENSOREN.md) |
| `SIMPLEOFFICE_DATALOGGER_TICK_SECONDS` | [datalogger_worker.py](../tools/datalogger_worker.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DATA_DIR` | [runtime_entry.py](../desktop/python-setup/runtime_entry.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DESKTOP` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_DOCUMENT_ADMINS` | [virtual_filesystem.py](../app/virtual_filesystem.py) | [ERSTSTART_UND_DESKTOP_SETUP](ERSTSTART_UND_DESKTOP_SETUP.md) |
| `SIMPLEOFFICE_DOCUMENT_ROOT` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [ERSTER_START](ERSTER_START.md) |
| `SIMPLEOFFICE_EBAY_CLIENT_ID` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_EBAY_CLIENT_SECRET` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_EBAY_MARKETPLACE_ID` | [inventory_marketplace.py](../app/inventory_marketplace.py) | [INVENTAR_MARKETPLACE_SUCHEN](INVENTAR_MARKETPLACE_SUCHEN.md) |
| `SIMPLEOFFICE_ERROR_RELAY_ENABLED` | [ci.yml](../.github/workflows/ci.yml) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_ERROR_REPORTING` | [github_error_reporter.py](../app/github_error_reporter.py) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_ERROR_REPORT_URL` | [android-apk-build.yml](../.github/workflows/android-apk-build.yml) | [GITHUB_ERROR_REPORTING](GITHUB_ERROR_REPORTING.md) |
| `SIMPLEOFFICE_EXTENSION_HTTP_ALLOWLIST` | [v3_extensions.py](../app/v3_extensions.py) | [V3_EXTENSIONS](V3_EXTENSIONS.md) |
| `SIMPLEOFFICE_FEDERATION_ALLOW_LOOPBACK` | [federation_discovery_endpoint.py](../app/federation_discovery_endpoint.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_ALLOW_PRIVATE_TARGETS` | [federation_discovery_endpoint.py](../app/federation_discovery_endpoint.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_AUTOSCAN_COUNTRY` | [federation_discovery_runtime.py](../app/federation_discovery_runtime.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_AUTOSCAN_SECONDS` | [federation_discovery_runtime.py](../app/federation_discovery_runtime.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_BOOTSTRAP_URLS` | [federation_discovery_service.py](../app/federation_discovery_service.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_COUNTRY` | [federation_local_profile.py](../app/federation_local_profile.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_DIRECTORY_TOKEN` | [federation_discovery_http.py](../app/federation_discovery_http.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_FEDERATION_FINGERPRINT` | [federation_local_profile.py](../app/federation_local_profile.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_LABEL` | [federation_local_profile.py](../app/federation_local_profile.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
| `SIMPLEOFFICE_FEDERATION_LAN_ADDRESS` | [android_runtime.py](../android/apk/app/src/main/python/android_runtime.py) | [AUDIO_STREAMER](AUDIO_STREAMER.md) |
| `SIMPLEOFFICE_FEDERATION_LAN_PORTS` | [federation_discovery_lan.py](../app/federation_discovery_lan.py) | [AUDIO_STREAMER](AUDIO_STREAMER.md) |
| `SIMPLEOFFICE_FEDERATION_PEER_ID` | [manual-browser-screenshots.yml](../.github/workflows/manual-browser-screenshots.yml) | [CHAT](CHAT.md) |
| `SIMPLEOFFICE_FEDERATION_PUBLIC_DIRECTORY` | [federation_discovery_http.py](../app/federation_discovery_http.py) | [FEDERATION_DISCOVERY_TRUST](FEDERATION_DISCOVERY_TRUST.md) |
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
| `SIMPLEOFFICE_OSM_EXPORT_IDLE_TIMEOUT` | [osm_address_build.py](../app/osm_address_build.py) | [OSM_INDEX_UND_RECHNUNGSFINALISIERUNG](OSM_INDEX_UND_RECHNUNGSFINALISIERUNG.md) |
| `SIMPLEOFFICE_OSM_FILTER_TIMEOUT` | [osm_address_build.py](../app/osm_address_build.py) | Quelle und Ergänzungen oben |
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
| `SIMPLEOFFICE_RSYNC_ENABLED` | [documents_core.py](../app/documents_core.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_RSYNC_MAX_BYTES` | [rsync_server.py](../app/rsync_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_RSYNC_MAX_FILES` | [rsync_server.py](../app/rsync_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_RSYNC_TIMEOUT` | [rsync_server.py](../app/rsync_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
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
| `SIMPLEOFFICE_SFTP_MAX_BYTES` | [sftp_server.py](../app/sftp_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
| `SIMPLEOFFICE_SFTP_MAX_CLIENTS` | [sftp_server.py](../app/sftp_server.py) | [GOODSYNC_FREEFILESYNC](GOODSYNC_FREEFILESYNC.md) |
| `SIMPLEOFFICE_SFTP_PASSWORD_AUTH` | [sftp_server.py](../app/sftp_server.py) | [VIRTUELLES_DATEISYSTEM_SFTP](VIRTUELLES_DATEISYSTEM_SFTP.md) |
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
| `SIMPLEOFFICE_WEBDAV_QUOTA_MIB` | [__init__.py](../app/__init__.py) | [ERSTER_START](ERSTER_START.md) |
| `SIMPLEOFFICE_WORKER_ROOT` | [launcher.py](../tools/launcher.py) | Quelle und Ergänzungen oben |
| `SIMPLEOFFICE_WSGI_CHANNEL_TIMEOUT` | [start.bat](../start.bat) | [PRODUKTIONSBETRIEB](PRODUKTIONSBETRIEB.md) |
| `SIMPLEOFFICE_WSGI_THREADS` | [runtime_entry.py](../desktop/python-setup/runtime_entry.py) | [PERFORMANCE_INDEXDIENST](PERFORMANCE_INDEXDIENST.md) |
| `SIMPLEOFFICE_ZUGFERD_VALIDATOR` | [business_document_generation.py](../app/business_document_generation.py) | [OSM_INDEX_UND_RECHNUNGSFINALISIERUNG](OSM_INDEX_UND_RECHNUNGSFINALISIERUNG.md) |
