# Globale Kontakt-Änderungshistorie

## Zweck und Nutzen

Die globale Änderungshistorie bündelt Feldänderungen aller Kontakte, die der
angemeldete Benutzer verwalten darf. Sie erleichtert die Prüfung, wer einen
Namen, eine Adresse, Telefonnummer oder ein benutzerdefiniertes Feld geändert
hat, ohne jeden Kontakt einzeln öffnen zu müssen.

## Bedienung und Konfiguration

Unter **Kontakte → Änderungshistorie** stehen eine Freitextsuche sowie Filter
nach Bearbeiter und Feld bereit. Die Suche berücksichtigt Kontaktname,
Kontakt-ID, Feld, Altwert, Neuwert, Zeitpunkt und Benutzer. Ergebnisse werden
absteigend nach Zeitpunkt angezeigt und in Seiten mit höchstens 50 Einträgen
aufgeteilt. Es ist keine Konfiguration erforderlich. Deutsch und Englisch
werden über die vorhandene Spracheinstellung unterstützt.

## Voraussetzungen, Sicherheit und Datenschutz

Die Auswertung erfolgt lokal aus `.simpleoffice-meta/contacts.json`. Es werden
keine Daten an externe Dienste übertragen, keine Zugangsdaten benötigt und
keine Suchbegriffe dauerhaft gespeichert. Eingaben und gespeicherte Werte
werden durch die normale Template-Escapierung als Text ausgegeben.

## Rechte und Freigaben

Vor der Suche wird dieselbe Rechteprüfung wie in der Kontaktliste angewendet.
Eigentümer sehen ihre eigenen Kontakte; Verwalter sehen nur ausdrücklich mit
ihnen geteilte Kontakte. Änderungen fremder, nicht freigegebener Kontakte
werden weder in Ergebnissen noch in Filteroptionen berücksichtigt. Die Ansicht
ist rein lesend und ändert keine Freigaben oder Kontaktfelder.

## Import-, Export- und Protokollkompatibilität

Das Kontakt-, vCard- und CardDAV-Format bleibt unverändert. Änderungen aus der
Weboberfläche, CardDAV und Importen erscheinen gemeinsam, sofern sie bereits
einen Feldhistorieneintrag erzeugen. Die Ansicht ist kein Exportformat und
stellt keine neue externe API bereit.

## Fehler- und Ausfallverhalten

Ungültige Seitenzahlen fallen auf die erste Seite zurück; zu große Seitenzahlen
werden auf die letzte vorhandene Seite begrenzt. Leere oder nicht passende
Filter zeigen einen verständlichen Leerzustand. Pro Anfrage werden höchstens
100 Einträge aus der Store-Schnittstelle zurückgegeben, die Oberfläche fordert
50 an. Dadurch bleibt die Antwort auch bei großen Historien begrenzt.

## Tests und bekannte Grenzen

Automatisierte Tests prüfen Rechteisolierung, Suche, Benutzer- und Feldfilter,
Pagination und die maximale Ergebnisgröße. Der Kontakt speichert derzeit
höchstens 200 Feldänderungen; ältere, bereits aus dieser Historie entfernte
Einträge können daher nicht angezeigt werden. Eine unveränderbare externe
Audit-Datenbank und ein CSV-Export sind nicht Bestandteil dieser Änderung.

## Deaktivierung und Rückkehr

Die Funktion kann durch Rückkehr zur vorherigen Programmversion deaktiviert
werden. Es gibt keine Migration, keine zusätzlichen gespeicherten Felder und
keine Änderung an Aufbewahrungsregeln; bestehende Kontakte und Revisionen
bleiben vollständig kompatibel.
