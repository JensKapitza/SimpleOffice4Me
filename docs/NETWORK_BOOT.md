# Netzwerkboot: HTTP/PXE und TFTP

## Zweck

SimpleOffice liefert iPXE-Skripte, Kernel, Initrd und Images aus. HTTP/PXE bleibt
Teil des Webservers, TFTP gehört zum vorhandenen Mini-Services-Worker.
Föderations-Peers können Inhalte anbieten oder Kopien speichern; diese Rollen
sind unabhängig von der lokalen öffentlichen Auslieferung.

## Voraussetzungen

Ein Client mit PXE/iPXE, passende Bootdateien und ein erreichbares lokales Netz.
Bootloader und Betriebssystem-Images werden nicht automatisch heruntergeladen.
TFTP nutzt ausschließlich Python-Standardbibliotheken. Port 69 benötigt auf Linux
gezielt eingerichtete Dienstrechte; SimpleOffice erhöht seine Rechte nicht selbst.
Der normale Webprozess benötigt diese Rechte nicht.

## Standardbetrieb

`./start.sh` startet Webserver und Worker. Netzwerkboot bleibt standardmäßig aus,
bis passende Dateien und ein Bootprofil eingerichtet sind. Unter
**Mini Services → Netzwerkboot** liegen Einstellungen und Peer-Rollen.
Die Dienstübersicht bietet Start, Stop, Neustart, Status und Dateisuche.

HTTP/PXE-Start speichert `enabled: true`. Stop speichert `false`, sperrt öffentliche
HTTP-Dateien sofort und beendet über den Worker auch die zugehörige TFTP-Auslieferung.
Neustart validiert und lädt die Konfiguration erneut; er startet keinen zweiten
Webserver. Bereits laufende HTTP-Downloads können fertig werden. Der persistente
Schalter gilt auch beim nächsten Start von SimpleOffice; ein eigener Autostart-
Schalter oder Prozess für die HTTP-Routen wäre redundant.

TFTP kann zusätzlich separat gestartet/gestoppt werden. Dafür müssen
`enabled`, `tftp_enabled` und die TFTP-Aktivierung in der Worker-Steuerung gesetzt
sein. `./start.sh mini-services status` zeigt den Zustand des Workers.

## Konfiguration

Basis ist `SIMPLEOFFICE_MINI_SERVICES_CONFIG`, sonst die gemeinsame
`instance/mini-services.json`-Position. Boot-Einstellungen liegen daneben unter
`mini-services/network-boot/settings.json`, Dateien unter
`mini-services/network-boot/assets/`. Der vorhandene Legacy-Pfad wird von Web und
Worker einheitlich berücksichtigt. Der erweiterte Editor verwendet genau das
bestehende JSON-Format; fehlerhafte Eingaben bleiben zur Korrektur sichtbar.

Unter **Bootprofile** lassen sich Profile ohne JSON anlegen und bearbeiten.
„Neues Profil“ öffnet ein leeres Formular; ein Profilname lädt dessen Werte.
Kernel/Initrd, ISO oder externes iPXE-Skript werden als Bootverfahren ausgewählt.
Vorhandene Dateien werden vorgeschlagen; relative Pfade bleiben manuell eingebbar.
Architekturcodes werden kommagetrennt eingegeben. Alle bestehenden Profilfelder
sind verfügbar und verwenden weiterhin denselben Validator.

„Als Standardprofil verwenden“ aktualisiert die Standardauswahl. Wird das
Standardprofil entfernt, wird die Auswahl geleert; Dateien bleiben erhalten.
Eine neue Profil-ID darf keine vorhandene ID überschreiben. Fehlgeschlagene
Eingaben bleiben sichtbar. Der Aktivierungszustand von HTTP/TFTP und die übrigen
Einstellungen werden durch Profiländerungen nicht verändert.

| Option | Standard / Bedeutung |
|---|---|
| `version` | Schemaschlüssel, automatisch `1` |
| `enabled` | `false`; gemeinsame öffentliche Boot-Auslieferung |
| `tftp_enabled` | `false`; zusätzliche TFTP-Auslieferung |
| `tftp_bind` | `127.0.0.1`; konkrete lokale IPv4 für LAN-Betrieb auswählen, kein Wildcard-Bind |
| `tftp_port` | `69`; gültiger Bereich 1–65535 |
| `tftp_timeout` | `3` Sekunden; Bereich 1–60 |
| `tftp_retries` | `5`; Bereich 1–20 |
| `tftp_max_file_bytes` | 256 MiB; Bereich 1 KiB–4 GiB |
| `http_base_url` | leer; HTTP-Anfragen verwenden ihre Request-Basis. Für DHCP/PXE eine erreichbare HTTP(S)-Basis konfigurieren |
| `default_profile` | leer; ID eines vorhandenen aktiven Profils |
| `bios_loader` | `undionly.kpxe` |
| `uefi_x64_loader` | `ipxe.efi` |
| `uefi_arm64_loader` | `ipxe-arm64.efi` |
| `profiles` | leere Liste; maximal 200 Profile mit eindeutiger ID |

Profilfelder: `id`, `label`, `enabled` (Standard `true`), `mode`
(`kernel`, `iso`, `chain`), `architectures` (Standard `[0,7,9,11]`), `kernel`,
`initrd`, `iso`, `chain_url`, `kernel_args`. Dateien sind relative Namen innerhalb
des Asset-Verzeichnisses. URLs benötigen HTTP(S), dürfen keine Zugangsdaten oder
Leerzeichen enthalten. Profil- und Loader-Auswahl müssen zur Client-Architektur passen.

Beispiel für vorhandene Dateien `linux/vmlinuz` und `linux/initrd.img`:

```json
{
  "enabled": true,
  "default_profile": "linux",
  "profiles": [{
    "id": "linux",
    "label": "Linux",
    "mode": "kernel",
    "kernel": "linux/vmlinuz",
    "initrd": "linux/initrd.img",
    "kernel_args": "ip=dhcp"
  }]
}
```

Zurücksetzen stellt die deaktivierten Standardwerte wieder her; Dateien bleiben
erhalten. Netzwerkboot-Einstellungen aktivieren keinen DHCP-Server automatisch.

## Discovery

Scan findet lokale Bootdateien, zählt Treffer und speichert Zeitpunkt/Fehler.
UI-Scans lesen maximal 512 Dateieinträge und hashen keine großen Images.
Föderationstransfers behalten ihre SHA-256-Prüfung. Es gibt keine Behauptung, dass
PXE-Clients oder fremde TFTP-Server dadurch im Netz entdeckt wurden.

## Ports

HTTP verwendet den bestehenden Webserver-/Reverse-Proxy-Port (normal 8080).
TFTP verwendet UDP 69 beziehungsweise den konfigurierten Port sowie dynamische
UDP-Transferports. DHCP/PXE-Ankündigungen stammen vom separat aktivierten DHCP-
Dienst. HTTP/PXE hat deshalb keinen eigenen PID, Socket oder Laufzeit-Zähler;
die API nennt den Eigentümer `web` und dessen Prozess-ID.

## Security

Öffentliche Boot-Routen sind bewusst für Clients ohne Webanmeldung zugänglich,
aber nur bei aktivierter Auslieferung. Bootdateien dürfen daher keine Secrets
enthalten. Ein defektes Konfigurationsformat sperrt die öffentlichen Routen.
Administration erfordert Anmeldung, Adminrechte und CSRF-Schutz. Föderations-
Zugriffe benötigen die vorhandene Föderationsauthentifizierung und Peer-Regeln;
gespeicherte Kopien aktivieren die öffentliche Auslieferung nicht.

Uploads werden in eindeutige private temporäre Dateien geschrieben und atomar
ersetzt; parallele Transfers teilen keine `.part`-Datei. TFTP ist ausschließlich
lesend, Transfers sind begrenzt. Pfade außerhalb des Asset-Verzeichnisses werden
abgewiesen. Kein automatisches Root und keine Änderungen am NetworkManager.

## Fehlerdiagnose

| Anzeige / Problem | Prüfung und nächste Aktion |
|---|---|
| HTTP/PXE wartet | Aktives Standardprofil auswählen |
| HTTP/PXE eingeschränkt | Profilpfade und Dateirechte prüfen; nach Bereitstellen der Datei erholt sich der Status beim nächsten Abruf |
| Öffentlicher Abruf 404 | Auslieferung aktiviert? Datei/Profil vorhanden? |
| Öffentlicher Abruf 503 | JSON-Konfiguration und Dateirechte prüfen, erneut speichern |
| TFTP startet nicht | Konkrete lokale Bind-Adresse, Portkonflikt und Dienstrechte prüfen |
| Client lädt keine Datei | Firewall, Client-Architektur und aus Clientsicht erreichbare HTTP-Basis prüfen |
| Scan fehlgeschlagen | Asset-Verzeichnis prüfen und Suche wiederholen |
| Peer-Transfer fehlgeschlagen | Peer erreichbar, Rolle/Freigabe richtig, Authentifizierung und Speicherrechte gültig? Audit enthält Fehlertyp ohne rohe Exception |

## API

- `GET /api/mini-services/http-boot`: strukturierter Status, lokale Dateiprüfung,
  Fähigkeiten, Konfiguration und letzter Scan.
- `POST /api/mini-services/http-boot/{start,stop,restart,scan}`: direkte Antwort des
  Web-Eigentümers; kein Worker-Postfach für HTTP-Routen.
- `POST /admin/mini-services/network-boot/settings`: vorhandenes JSON als Formular-
  Feld `boot_json`, oder `action=reset`; Admin und CSRF erforderlich.
- `POST /admin/mini-services/network-boot/profiles`: geführte Profilfelder,
  `original_id` beim Bearbeiten, `action=save|delete`; Admin und CSRF erforderlich.
- `GET /network-boot/ipxe?profile=<id>` und `GET|HEAD /network-boot/files/<pfad>`:
  aktivierte öffentliche Auslieferung, Dateiabrufe unterstützen Range-Requests.
- `/federation/v1/network-boot/manifest`, `/assets/<pfad>` und
  `/storage/{assets/<pfad>,settings}`: bestehender authentifizierter Föderationspfad.

## Plattformen

Linux und Windows verwenden dieselben Python-HTTP/TFTP-Implementierungen;
Firewall und Rechte für niedrige Ports müssen auf dem Zielsystem geprüft werden.
Android/WebView kann die Administration bedienen. Ein privilegierter Android-
PXE/TFTP-Server ist kein unterstützter Standardbetrieb. Reale PXE-Clients und
Windows-Hardware wurden in dieser Umgebung nicht getestet.

## Einschränkungen und Tests

Der HTTP-Healthcheck prüft registrierte Routen und Dateien des Standardprofils,
nicht den Booterfolg einer Maschine oder externe Chain-Ziele. TFTP ist kein
verschlüsselter Dateitransfer. Fremde Bootdateien und Images bleiben Aufgabe des
Administrators. Ein gültiges Profil ersetzt keine Prüfung der gewählten
Bootdateien und Kernel-Parameter auf dem tatsächlichen Client.

`tests.test_network_boot_lifecycle`, `tests.test_mini_security`,
`tests.test_mini_services` und `tests.test_mini_control_api`: 47 Tests erfolgreich,
einschließlich Auth/CSRF, deaktivierter Auslieferung, Range-Requests, fehlenden
Dateien, Recovery, privater paralleler Uploads und Scan ohne Image-Hashing.
