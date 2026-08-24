# Projektzeiten und Abrechnungsgruppen

## Zweck und Nutzen

Projektzeiten werden intern minutengenau gespeichert. Die Eingabe trennt Stunden
und Minuten, damit `1 Stunde, 2 Minuten` nicht irrtümlich als Dezimalzahl `1,02`
interpretiert wird. Mehrere Zeitbuchungen lassen sich zu einer Abrechnungsgruppe
zusammenfassen. So können beispielsweise die internen Arbeitsschritte A und B
jeweils eine Stunde enthalten, während auf der Rechnung nur „Installation“ mit
1 Stunde 30 Minuten erscheint.

## Daten- und Rechteverhalten

- Einzelbuchungen bleiben mit Aufgabe, Datum, Minuten, Notiz, Ersteller und
  Erstellzeit unverändert erhalten.
- Eine aktive Einzelbuchung darf höchstens einer Abrechnungsgruppe angehören.
- Gruppentitel, Bestandteile und interne Notizen werden in der Oberfläche nur
  dem Ersteller der Gruppe angezeigt.
- Die rechnungssichere Projektion enthält ausschließlich Positionstext und
  abrechenbare Minuten. Sie enthält weder interne Notizen noch die Zuordnung der
  Einzelbuchungen.
- Jede Änderung wird als vollständiger Projektschnappschuss in der bestehenden
  Revisionshistorie protokolliert.

## Bedienung

Zeit wird an einer Aufgabe mit zwei ganzzahligen Feldern gebucht: Stunden von 0
bis 24 und Minuten von 0 bis 59. Im Bereich „Zeitgruppen und Abrechnung“ werden
die gewünschten Buchungen gewählt, ein interner Gruppenname, der Rechnungstext
und die tatsächlich abzurechnende Dauer angegeben.

Die Ansicht „Rechnungssichere Daten“ liefert genau die Positionen, die eine
spätere Rechnung übernehmen darf. Gruppierte Einzelbuchungen erscheinen dort
nicht zusätzlich und können deshalb nicht doppelt abgerechnet werden.

## Migration und Rückwärtskompatibilität

Bestehende Projekte und Zeitbuchungen bleiben unverändert lesbar. Projekte ohne
`time_groups` werden wie Projekte mit einer leeren Gruppenliste behandelt. Die
Programmierschnittstelle akzeptiert für ältere Aufrufer weiterhin Dezimalstunden;
die Weboberfläche verwendet ausschließlich die eindeutige Stunden-/Minuten-Eingabe.

## Tests und bekannte Grenzen

Automatisierte Tests prüfen exakte Minuten, das Format `HH:MM`, die
Zusammenfassung zu genau einer Rechnungsposition, den Schutz der internen
Gruppendetails und die Verhinderung doppelter Gruppierung. Eine direkte Übergabe
an ein bestimmtes Buchhaltungssystem ist bewusst noch nicht enthalten; dafür
steht die kleine, geheimnisfreie JSON-Projektion bereit.
