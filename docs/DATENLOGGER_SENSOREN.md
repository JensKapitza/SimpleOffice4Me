# Persistenter Datenlogger für Dateien, Linux und HTTP-Sensoren

## Zweck und Nutzen

Der Datenlogger bildet konfigurierbare Messkanäle mit Zeitstempel, Einheit,
Beschreibung, Farbe und Zugriffsrechten. Ein Kanal kann manuell oder regelmäßig
aus Dateibeständen, Linux-Systemwerten, `lm-sensors -j` oder einer numerischen
Eigenschaft einer HTTP/JSON-Antwort gespeist werden. Die Weboberfläche zeigt
Verläufe, normalisiert mehrere unterschiedlich skalierte Reihen zum Vergleich
und bietet einen authentifizierten JSON-Export. Die Messdaten bleiben in
`<Dokumentwurzel>/.simpleoffice-meta/datalogger.sqlite3` persistent.

## Standards und daraus abgeleitete Entscheidungen

- [RFC 8259 Abschnitt 9](https://www.rfc-editor.org/rfc/rfc8259.html#section-9)
  verlangt, dass Parser gültiges JSON akzeptieren, und erlaubt Größen-, Tiefen-
  und Zahlenlimits. Der Sammler akzeptiert JSON über den Standardparser, liest
  höchstens 1 MiB und übernimmt nur endliche numerische Werte. Ein einfacher
  Objekt-/Array-Pfad ersetzt gefährliches `eval`; dies folgt auch den
  Sicherheitshinweisen aus [Abschnitt 12](https://www.rfc-editor.org/rfc/rfc8259.html#section-12).
- [RFC 9110 Abschnitt 9.3.1](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.1)
  definiert GET als sichere Abfrage einer Repräsentation. Automatische Quellen
  verwenden ausschließlich GET, folgen keinen Umleitungen und senden keinen
  Request-Body.
- Zugangsdaten in HTTP-URIs sind nach [RFC 9110 Abschnitt 4.2.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-4.2.4)
  abzulehnen. Geheimnisse werden daher nicht in Kanal- oder Quellenkonfiguration
  gespeichert. Eine Quelle darf nur den Namen einer mit
  `SIMPLEOFFICE_SENSOR_` beginnenden Umgebungsvariablen referenzieren.
- HTTP-Anmeldedaten benötigen einen geschützten Transport; siehe
  [RFC 9110 Abschnitt 11](https://www.rfc-editor.org/rfc/rfc9110.html#section-11).
  Für entfernte Sensoren ist HTTPS erforderlich; bewusstes HTTP ist nur für
  ausdrücklich freigegebene lokale Geräte vorgesehen.

Nicht implementiert sind OpenMetrics/Prometheus-Exposition, MQTT, automatische
Erkennung im Netz und automatische Löschung/Aggregation alter Messwerte. Diese
Grenzen vermeiden unbemerkte Freigaben, Netzwerkscans und Datenverlust.

## Bedienung und Quellen

Unter **Datenlogger** wird zuerst ein Kanal angelegt. Eigentümer und Administratoren
können Lesende und Bearbeitende als vorhandene Benutzernamen eintragen.
Bearbeitende dürfen Werte und Quellen ändern; Lesende sehen Verlauf und Export.

- Linux: `load1`, `load5`, `load15`, `memory_used_percent`,
  `disk_used_percent` (Pfad) oder `temperature_c`.
- Dateien: relativer Pfad innerhalb der Dokumentwurzel, `count`, `total_bytes`
  oder `mtime`; Symlinks werden nicht verfolgt und der Lauf endet nach dem
  konfigurierten Eintragslimit.
- HTTP/JSON: URL und Pfad wie `room.temperature` oder `sensors[0].value`.
  Zulässige Ziele stehen kommasepariert in
  `SIMPLEOFFICE_SENSOR_ALLOWED_HOSTS`; sicherer Standard ist
  `localhost,127.0.0.1,::1`. Beispiel:

  ```sh
  export SIMPLEOFFICE_SENSOR_ALLOWED_HOSTS="sensor-keller.lan,192.168.10.42"
  export SIMPLEOFFICE_SENSOR_KELLER_TOKEN="Bearer …"
  ```

  Im Formular wird nur `SIMPLEOFFICE_SENSOR_KELLER_TOKEN` angegeben, nie sein
  Wert. Antworten sind auf 1 MiB und Abfragen auf fünf Sekunden begrenzt.
- `lm-sensors`: auf Debian/Ubuntu etwa `sudo apt install lm-sensors`, danach
  administrativ `sudo sensors-detect`; `sensors -j` zeigt die verwendbaren
  JSON-Pfade. SimpleOffice führt ausschließlich `sensors -j` ohne Shell aus.

## Prozess-, Fehler- und Ausfallverhalten

`tools.datalogger_worker` läuft getrennt und niedrig priorisiert. Langsame
Dateisysteme oder Sensoren belegen daher keinen WSGI-Thread und verzögern weder
Anmeldung noch Weboberfläche. Pro Durchlauf sind höchstens 100 fällige Quellen
aktiv. Statuscodes wie `host_denied`, `http_failed`, `entry_limit` oder
`lm_sensors_missing` sind in der Kanalansicht sichtbar; Antworttexte,
Zugangswerte und Stacktraces werden dort und im Audit nicht gespeichert.
Ein Fehler verwirft nur diesen Messpunkt; der nächste Intervalllauf bleibt aktiv.
Eine exklusive Prozesssperre verhindert doppelte Messungen, falls die Anwendung
versehentlich mehrfach mit derselben Dokumentwurzel gestartet wird.

## Sicherheit, Datenschutz und Rechte

Standardmäßig sieht nur der Eigentümer einen Kanal. Administratoren dürfen im
Rahmen ihrer Systemverantwortung alle Kanäle verwalten. HTTP-Ziele brauchen eine
Host-Whitelist (SSRF-Schutz), Umleitungen sind gesperrt, Dateipfade bleiben in
der Dokumentwurzel, Symlinks werden übersprungen, Zahlen müssen endlich sein.
Die SQLite-Datenbank und ihr WAL gehören in die normale verschlüsselte Sicherung
der Dokumentwurzel. Messwerte können Personen- oder Betriebsdaten sein; deshalb
sollten Kanäle sparsam freigegeben und Sensorbezeichnungen ohne Geheimnisse
gewählt werden.

## Migration, Sicherung und Deaktivierung

Die Änderung ist additiv und benötigt keine Datenmigration. Der Dienst lässt
sich vor dem Start mit `SIMPLEOFFICE_DATALOGGER=0` deaktivieren; vorhandene Daten
und die manuelle Oberfläche bleiben erhalten. Einzelne Quellen können in der UI
pausiert werden, ohne Messpunkte zu löschen. Zur Rückkehr genügt es, den Dienst
zu deaktivieren und den Navigationsbereich nicht zu verwenden. Ein konsistentes
Backup entsteht per SQLite-Backup oder bei gestopptem Dienst durch Kopieren der
DB samt `-wal`/`-shm`.

## Tests und bekannte Grenzen

Automatisierte Tests decken Persistenz, Audit, manuelle und automatische Werte,
Leser-/Bearbeiterrechte, Feature-Sperren, Pfad-Traversal, Eintragslimits,
HTTP-Whitelist, JSON-Pfade, Fehlercodes, Prozessdeaktivierung und JSON-Export ab.
Der Vergleich normalisiert Reihen und ist daher nicht für absolute Einheiten
gedacht. Hochfrequente Telemetrie, verteilte Worker, Downsampling, Alarme und
eine Prometheus-Schnittstelle bleiben spätere Ausbaustufen.
