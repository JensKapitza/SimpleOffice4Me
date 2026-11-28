# Persistenter Datenlogger für Dateien, Linux und HTTP-Sensoren

## Zweck und Nutzen

Der Datenlogger bildet konfigurierbare Messkanäle mit Zeitstempel, Einheit,
Beschreibung, Farbe und Zugriffsrechten. Ein Kanal kann manuell oder regelmäßig
aus Dateibeständen, Linux-Systemwerten, `lm-sensors -j` oder einer numerischen
Eigenschaft einer HTTP/JSON-Antwort gespeist werden. Die Weboberfläche zeigt
Verläufe, normalisiert mehrere unterschiedlich skalierte Reihen zum Vergleich
und bietet einen authentifizierten JSON-Export. Die Messdaten bleiben in
`<Dokumentwurzel>/.simpleoffice-meta/datalogger.sqlite3` persistent.

Jeder angezeigte und exportierte Messwert nennt seine Quelle, den Quellentyp und
die konfigurierte Messgröße beziehungsweise den JSON-Pfad. Manuelle Eingaben
werden ausdrücklich als solche gekennzeichnet; dadurch bleiben gleichartige
Werte aus mehreren Sensoren unterscheidbar.

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
  abzulehnen. Das optionale HTTP-Header-Token wird daher direkt in der
  Quellenkonfiguration gespeichert (nie in Umgebungsvariablen) und beim Anlegen
  nie wieder angezeigt. Ältere `header_env`-Verweise werden ignoriert; das Token
  muss erneut eingetragen werden.
- HTTP-Anmeldedaten benötigen einen geschützten Transport; siehe
  [RFC 9110 Abschnitt 11](https://www.rfc-editor.org/rfc/rfc9110.html#section-11).
  Für entfernte Sensoren ist HTTPS erforderlich; bewusstes HTTP ist nur für
  ausdrücklich freigegebene lokale Geräte vorgesehen.

Nicht implementiert sind OpenMetrics/Prometheus-Exposition, MQTT, automatische
Erkennung im Netz und automatische Löschung/Aggregation alter Messwerte. Diese
Grenzen vermeiden unbemerkte Freigaben, Netzwerkscans und Datenverlust.

## Bedienung und Quellen

### Serverdiagnose und Auswertung

Auf einem Linux-Server unter **Datenlogger → Server-Messungen mit Vorlagen
einrichten** eine Vorlage auswählen. Sie legt je Administratorkonto einen
privaten Kanal mit Quellenintervall 60 Sekunden an. Erneutes Öffnen dupliziert
nichts. Freier/Gesamtspeicher beziehen sich auf die Dokumentwurzel, Uptime auf
`/proc/uptime`. Bei Containern können Linux-Systemwerte den Host beschreiben;
`load1` ist keine CPU-Auslastung in Prozent. Diese Vorlagen messen weder andere
Datenträger noch SMART, sofern nicht separat eingerichtet.

**Kanal auswerten** öffnet standardmäßig die letzten sieben UTC-Kalendertage.
Die Datumsfelder grenzen Tabelle, Grafik und Export gemeinsam ein: Das
Enddatum ist einschließlich. Der zuletzt gespeicherte Wert ist separat von
Erfassungsfehlern sichtbar. Nur echte gültige Werte erscheinen als Punkte,
fehlende Werte werden nicht als Null ergänzt. Die Fehlerliste zeichnet neue
fehlgeschlagene Quellenabfragen auf; ein nicht laufender Worker erzeugt dort
kein künstliches Ereignis. Die Sekundäransicht zum Vergleich skaliert jede
Reihe separat und ist kein Vergleich absoluter Messwerte.

CSV listet Messwerte und protokollierte Erfassungsfehler mit UTC-Zeitstempel,
Kanal, Quelle, Einheit, Status und Fehlercode. Ein Fehler hat keinen Messwert.
JSON liefert dieselbe Zeitspanne und listet Erfassungsfehler separat. Text und
PDF liefern eine lesbare Zusammenfassung mit Anzahl, Bereich, Mittelwert und
den letzten Fehlermeldungen. Alle Formate verwenden die Kanalrechte und sind
bei übergroßen Zeiträumen begrenzt. Beim bisherigen direkten `data.json` ohne
Datumsparameter bleiben die alten API-Parameter gültig. Berichtplanung, Versand
und Schwellwertregeln sind in dieser Ausbaustufe nicht aktiv.

Der optionale lokale Hilfsaufruf `python -m tools.datalogger_smart_snapshot`
liest eine vorhandene JSON-Ausgabe von `smartctl` über Standardeingabe. Er gibt
nur gültige Zustands-, Temperatur- und Laufzeitwerte aus; keine Seriennummern
oder Rohmeldungen. Er ruft weder SMART-Befehle noch `sudo` selbst auf. Die
Einbindung einer administrativ bereitgestellten Quelle und die Prüfung mit
echter Hardware bleiben separat erforderlich.

Unter **Datenlogger** wird zuerst ein Kanal angelegt. Eigentümer und Administratoren
können Lesende und Bearbeitende als vorhandene Benutzernamen eintragen.
Bearbeitende dürfen Werte und Quellen ändern; Lesende sehen Verlauf und Export.

- Linux: `load1`, `load5`, `load15`, `memory_used_percent`,
  `disk_used_percent` (Pfad) oder `temperature_c`.
- Dateien: relativer Pfad innerhalb der Dokumentwurzel, `count`, `total_bytes`
  oder `mtime`; Symlinks werden nicht verfolgt und der Lauf endet nach dem
  konfigurierten Eintragslimit.
- HTTP/JSON: URL und Pfad wie `room.temperature` oder `sensors[0].value`.
  Zulässige Ziele stehen in den Worker-Einstellungen des Datenloggers
  (Datenlogger → Worker-Einstellungen → erlaubte Sensor-Hosts, einer pro Zeile
  oder Komma-getrennt); sicherer Standard ist `localhost,127.0.0.1,::1`.
  Beispiel: `sensor-keller.lan` eintragen und im Quellenformular das
  HTTP-Header-Token hinterlegen. Antworten sind auf 1 MiB und Abfragen auf fünf
  Sekunden begrenzt.
- `lm-sensors`: auf Debian/Ubuntu etwa `sudo apt install lm-sensors`, danach
  administrativ `sudo sensors-detect`; `sensors -j` zeigt die verwendbaren
  JSON-Pfade. SimpleOffice führt ausschließlich `sensors -j` ohne Shell aus.

### Android: eine Bewegung messen und speichern

In der APK unter **Datenlogger → Android-Bewegung messen** zeigt die Seite an,
ob Beschleunigungssensor und Gyroskop auf dem Gerät verfügbar sind. Tippe auf
einen Sensor, um genau eine frische Messung zu sehen. Beschleunigung umfasst
die Schwerkraft (m/s²), Drehgeschwindigkeit wird in rad/s angezeigt. Beide Werte
sind gerichtungslose Beträge, keine Achsen, keine GPS-Daten und kein Verlauf.

Erst **Messwert speichern** legt den Wert im ausgewählten, bearbeitbaren Kanal
passender Einheit ab. Verwende bei Bedarf „Messkanal anlegen“ mit `m/s²` oder
`rad/s`. Der Kanal nutzt die Speicherzeit und behandelt dies als manuelle
Beobachtung, nicht als Hardware-Attest. Wenn die App in den Hintergrund wechselt,
verwirft sie die ungespeicherte Vorschau. Es gibt keinen Dienst im Hintergrund
und keine zusätzliche Manifest-Berechtigung oder Föderationsfreigabe.

Der APK-Workflow prüft die Sensorvorschau und das Speichern in einem
eigens dafür gestarteten Android-Emulator mit seiner virtuellen Sensorhardware. Das ersetzt
keine Messung auf einem physischen Telefon. Der Prüflauf kann in einer frischen
CI-AVD mit `bash tools/run_android_nfc_fallback_acceptance.sh <debug.apk> "" --motion`
nachgestellt werden; außerhalb von CI ist anstelle der leeren Zeichenkette eine
frische Android-Emulator-Seriennummer anzugeben.

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

Administratoren können das globale Pollintervall unter **Datenlogger →
Worker-Einstellungen** ändern. Der Standard beträgt 5 Sekunden; zulässig sind
1–60 Sekunden. Das Intervall bestimmt, wie oft der getrennte Worker nach fälligen
Quellen sucht; das jeweilige Messintervall der einzelnen Quellen bleibt davon
unabhängig. Eine Änderung wirkt beim nächsten Worker-Zyklus; ein bereits
laufender Wartezyklus kann noch bis zu 60 Sekunden dauern. Der Wert wird in der
Datenlogger-SQLite-Datei gespeichert. `SIMPLEOFFICE_DATALOGGER_TICK_SECONDS`
bleibt als Übergangswert für vorhandene Installationen und direkte Workerstarts
wirksam, bis ein Administrator den Wert erstmals speichert.

## Tests und bekannte Grenzen

Automatisierte Tests decken Persistenz, Audit, manuelle und automatische Werte,
Leser-/Bearbeiterrechte, Feature-Sperren, Pfad-Traversal, Eintragslimits,
HTTP-Whitelist, JSON-Pfade, Fehlercodes, Prozessdeaktivierung und JSON-Export ab.
Der Vergleich normalisiert Reihen und ist daher nicht für absolute Einheiten
gedacht. Hochfrequente Telemetrie, verteilte Worker, Downsampling, Alarme und
eine Prometheus-Schnittstelle bleiben spätere Ausbaustufen.
