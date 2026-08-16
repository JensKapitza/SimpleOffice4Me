# Sichere HTML-Beschreibungen im Kalender

## Zweck und Nutzen

SimpleOffice4Me erkennt formatierte Terminbeschreibungen aus Thunderbird, zeigt sie nach sicherer Bereinigung an und hält parallel eine nutzbare Reintextfassung vor. Benutzer können in der Weboberfläche zwischen formatierter und reiner Textansicht wählen. Die ursprüngliche Kalenderdatei bleibt zu Diagnosezwecken erhalten, wird aber nie ungeprüft als HTML ausgegeben.

## Standards und Entwurfsentscheidungen

- [RFC 5545, Abschnitt 3.1](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.1) definiert Content-Lines und Parameter. Der Trenndoppelpunkt wird deshalb nur außerhalb quotierter Parameterwerte erkannt.
- [RFC 5545, Abschnitt 3.8.1.5](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.1.5) definiert `DESCRIPTION` als TEXT. SimpleOffice4Me exportiert daher immer eine Reintextbeschreibung.
- [RFC 5545, Abschnitt 3.2.1](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.2.1) erlaubt mit `ALTREP` einen URI auf eine alternative Darstellung, schreibt aber kein eingebettetes HTML vor. Externe `ALTREP`-URIs werden aus Datenschutz- und SSRF-Gründen nicht automatisch abgerufen.
- Thunderbirds verbreitete Erweiterung `X-ALT-DESC;FMTTYPE=text/html` ist keine IETF-Standardproperty. Sie wird für Interoperabilität zusätzlich importiert und exportiert. Auch `DESCRIPTION;FMTTYPE=text/html` wird defensiv erkannt.

## Sicherheit, Datenschutz und Rechte

HTML wird beim Import und Speichern auf Textstruktur, Listen, Überschriften, Code, Zitate und Links begrenzt. Skripte, Styles, Bilder, Iframes, SVG/MathML, Ereignisattribute und unbekannte Elemente werden entfernt. Links dürfen nur `https`, `http` oder `mailto` verwenden und erhalten `rel="noopener noreferrer"`. Dadurch werden insbesondere Skriptausführung, Tracking-Pixel und `javascript:`-Links verhindert. Die bestehenden Kalender-Lese- und Schreibrechte sowie Audit-Einträge gelten unverändert für beide Darstellungen.

## Bedienung und Kompatibilität

Im Terminformular stehen Reintext, optionales HTML und die bevorzugte Webansicht zur Verfügung. Beim Export erhalten Thunderbird und andere Clients weiterhin `DESCRIPTION`; kompatible Clients können zusätzlich `X-ALT-DESC` verwenden. Clients ohne diese Erweiterung sehen nur Reintext. Datum/Zeit-Felder erhalten lokale Werte ohne ISO-Zonenoffset, wie vom HTML-Feld `datetime-local` verlangt; Speicherung und CalDAV-Zeitpunkt bleiben unverändert.

## Fehlerverhalten, Migration und Rückkehr

Bestehende Termine bleiben ohne Migration reine Texttermine. Ungültiges oder vollständig entferntes HTML fällt auf Reintext zurück. Das Abschalten der formatierten Ansicht erfolgt pro Termin über „Reintext“; das Leeren des HTML-Felds entfernt die Alternativdarstellung. Externe Ressourcen werden nicht geladen. Bewusste Grenzen sind CSS, Bilder, Tabellen, Anhänge im HTML, Remote-`ALTREP`, vollständige HTML-Dokumente und proprietäre Formatattribute.

## Tests

Automatisierte Tests prüfen Thunderbird-Import und -Roundtrip, Reintextableitung, XSS- und URL-Bereinigung, quotierte Doppelpunkte, Webformular-Auswahl, Zeitzonenwerte in `datetime-local`, Rechte/Audit sowie die Rückwärtskompatibilität reiner `DESCRIPTION`-Termine.
