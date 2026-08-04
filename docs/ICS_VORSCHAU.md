# ICS-Dateien vor dem Import prüfen

## Zweck und Nutzen

Die Kalenderseite kann eine iCalendar-Datei vor dem eigentlichen Import rein
lesend prüfen. Die Vorschau zeigt Termine, UIDs, Beginn, Ende und Status sowie
Kompatibilitätshinweise. Dadurch fallen fehlende Pflichtfelder, nicht
umgerechnete Zeitzonen und noch nicht unterstützte Serienfunktionen auf, bevor
SimpleOffice4Me Kalenderdaten verändert.

## Bedienung und Konfiguration

1. Unter `/documents/calendar` **ICS importieren** öffnen.
2. Eine `.ics`-Datei auswählen.
3. **Datei prüfen** wählen. Zu diesem Zeitpunkt wird nichts importiert.
4. Die Zusammenfassung und Hinweise prüfen, zum Kalender zurückkehren und den
   Import mit **Datei importieren** ausdrücklich starten, falls das Ergebnis
   passt. Der Browser verlangt dabei gegebenenfalls eine erneute Dateiauswahl.

Die Vorschau ist ohne zusätzliche Konfiguration aktiv. Sie verarbeitet
höchstens 1 MiB und 200 `VEVENT`-Einträge pro Anfrage. Direkter Import bleibt
weiterhin verfügbar und wird nicht automatisch durch eine Vorschau bestätigt.

## Voraussetzungen und Protokollkompatibilität

Die Datei muss UTF-8-kodierter iCalendar-Inhalt mit mindestens einem
`VEVENT`-Block sein. Die Vorschau entfaltet fortgesetzte Inhaltszeilen nach
[RFC 5545](https://datatracker.ietf.org/doc/html/rfc5545#section-3.1) und zeigt
unter anderem `UID`, `SUMMARY`, `DTSTART`, `DTEND`, `STATUS`, `RRULE` und
`RECURRENCE-ID`. Damit lassen sich Exporte aus Google Kalender, Thunderbird und
anderen iCalendar-Anwendungen vorab beurteilen.

Die Anzeige ist bewusst genauer als der derzeitige Import: Sie warnt, wenn
`TZID`-Zeiten nur angezeigt und nicht umgerechnet werden, lokale „floating“
Zeiten keine Zeitzone enthalten, Wiederholungsregeln nicht expandiert werden,
einzelne Serienausnahmen nicht angewendet werden oder ein Lebenszyklusstatus
vor dem Import geprüft werden sollte. Eine Warnung bedeutet nicht zwangsläufig
eine ungültige Datei, sondern macht eine mögliche Abweichung vom aktuellen
Importverhalten sichtbar.

## Sicherheit, Datenschutz, Rechte und Freigaben

- Die Vorschau ist nur nach Anmeldung erreichbar und übernimmt die bestehende
  Sitzungs- und Rechteprüfung der Kalenderseite.
- Die Datei wird ausschließlich im Arbeitsspeicher der Anfrage gelesen. Sie
  wird nicht im Dokumentenspeicher, in der Sitzung, im Audit-Protokoll oder bei
  einem externen Dienst abgelegt.
- Dateiname und Inhalte werden in der HTML-Ausgabe maskiert. Größen- und
  Ereignisgrenzen verhindern unbegrenzte Speicher- und Darstellungsarbeit.
- Die Vorschau legt keine Termine an, verändert keine Eigentümer oder
  Freigaben und erzeugt deshalb auch keine Änderungsrevision. Erst der getrennt
  ausgelöste Import schreibt private Termine und protokolliert sie wie bisher.
- Es werden keine Zugangsdaten benötigt und keine Daten an Google,
  Thunderbird oder andere externe APIs übertragen.

## Fehler- und Ausfallverhalten

Leere Dateien, ungültiges UTF-8, Nullbytes, fehlende `VEVENT`-Blöcke sowie
überschrittene Größen- oder Ereignisgrenzen werden verständlich abgewiesen.
Einzelne Termine mit fehlendem Titel, fehlendem Beginn oder ungültiger Zeit
erscheinen als nicht importierbar; andere Termine derselben Datei können
weiterhin geprüft werden. Bei jedem Vorschaufehler bleiben bestehende
Kalenderdaten unverändert.

## Tests

Automatisierte Tests prüfen das Entfalten von Inhaltszeilen, maskierte Texte,
UTC-, lokale und `TZID`-Zeiten, Warnungen für Serien und Status, ungültige
Termine, beide Schutzgrenzen, deutsche und englische Anzeige sowie die zentrale
Garantie, dass die Webvorschau keine Kalenderdatei anlegt oder verändert.

## Bekannte Grenzen

- Die Vorschau simuliert keinen vollständigen Import und erkennt keine
  Überschneidungen mit bereits gespeicherten Terminen.
- `VTIMEZONE`-Definitionen werden nicht ausgewertet; `TZID` wird nur angezeigt.
- Wiederholungen und einzelne Serienausnahmen werden nicht als Instanzen
  berechnet.
- Anhänge, Teilnehmer, Alarme und proprietäre Erweiterungen werden nicht
  angezeigt.
- Die Datei wird zwischen Vorschau und Import nicht serverseitig aufbewahrt.
  Daher kann der Browser eine erneute Auswahl verlangen; genau dies verhindert
  einen unbeabsichtigten Import nach einer bloßen Prüfung.

## Deaktivierung und Rückkehr zum vorherigen Verhalten

Der bestehende direkte Import ist unverändert. Benutzer können **Datei prüfen**
einfach auslassen. Für eine vollständige technische Deaktivierung können die
Schaltfläche und die Route `/documents/calendar/import/preview` entfernt
werden; gespeicherte Termine, Datenformate, Freigaben und
Aufbewahrungsregeln müssen nicht migriert oder zurückgesetzt werden.
