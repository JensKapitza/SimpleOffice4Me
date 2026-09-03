# Mietobjekte und nachvollziehbare Betriebskostenabrechnung

Das Modul `/rentals` verwendet vorhandene SimpleOffice-Objekte als Mietobjekte und vorhandene Kontakte als Mieter. Stammdaten werden nicht dupliziert.

## Modell

- Mietobjektgruppen fassen mehrere `object_id` für gemeinsame Kosten zusammen.
- Mietverhältnisse verbinden `object_id` und `contact_id` mit einem Zeitraum, optionalem Mietvertrag und Federation-Peer.
- Kostengruppen haben freie Namen; jede Position hat Betrag, Kostenzeitraum, Quelle und Verteilungsschlüssel.
- Vorauszahlungen, Zahlungen, Gutschriften und alte offene Beträge werden als einzelne Kontobuchungen geführt.
- Handeingaben sind zulässig, benötigen aber immer einen Herkunfts-/Begründungstext.

## Verteilungsschlüssel

Unterstützt werden `direct`, `equal`, `area`, `percent`, `shares`, `consumption`, `persons`, `person_days` und `manual`.

`person_days` integriert datierte Personenzahlen als Personenzahl × Kalendertage. Kostenpositionen mit abweichendem Zeitraum werden zunächst taggenau auf den Abrechnungszeitraum gekürzt. Danach erfolgt die Objektverteilung und anschließend die zeitanteilige Mieterzuordnung. Nicht vermietete Anteile bleiben als Leerstand/Vermieteranteil sichtbar und werden nicht auf andere Mieter umgelegt.

Geldbeträge werden mit `Decimal` gerechnet. Die Cent-Verteilung ist deterministisch und summenerhaltend.

## Freigabe

Bearbeitbar sind nur `draft` und `review`. Eine Freigabe friert den vollständigen Datenstand ein. Spätere Korrekturen erzeugen eine neue Version; eine freigegebene Version wird nicht still verändert.

Unter `.simpleoffice-meta/rental-approvals/<settlement-id>/vNNN/` entstehen:

- `snapshot.json` – Eingaben, Zeiträume, Schlüssel, Berechnung, Kontobuchungen und Quellen
- `snapshot.sha256` – SHA-256 des exakten JSON-Datenstands
- `Freigabe-und-Berechnungsnachweis.pdf` – Rechenweg, Verteilung, Handeingaben, Belege und Freigabe
- `Vermieter-Abrechnungsblatt.pdf` – Jahreskosten, Objektaufteilung, Leerstand und Mieter-Salden
- `Mieterabrechnung-<contact>.pdf` – nachvollziehbare Einzelabrechnung
- `Mieterpaket-<contact>.zip` – Mieter-PDF, Manifest und freigegebene Belege
- `Belege/` – beim Freigabezeitpunkt kopierte und erneut gehashte Belege
- `approval-manifest.json` – SHA-256 und Größe aller Freigabeartefakte

Wenn sich ein Beleg während der Freigabe verändert, wird die Freigabe abgebrochen.

## Mietertransparenz

Die Mieterabrechnung zeigt je Position Kostengruppe, Kostenzeitraum, Schlüsselart, Schlüsselwerte, Objektanteil, Teilzeitraum, Zeit-/Personentageanteil und den Mieterbetrag. Vorauszahlungen, Vortrag und weitere Buchungen werden separat ausgewiesen. Belege können pro Kostenposition von der Mieterweitergabe ausgeschlossen werden; Nachweise für verwendete Schlüssel werden bei relevanten Positionen ebenfalls berücksichtigt.

## Federation

Der Versand ist technisch gesperrt, solange die Abrechnung nicht freigegeben ist. Das freigegebene Mieter-ZIP wird als normales content-addressed Federation-Dokument übertragen. Der zu einem Mietverhältnis hinterlegte Peer kann automatisch verwendet werden. Peer-Richtlinien für `rentals.send` beziehungsweise `documents.send` werden vor dem Versand geprüft. Download und Federation-Transfer werden mit SHA-256 protokolliert.
