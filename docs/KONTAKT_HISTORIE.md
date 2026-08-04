# Kontakt-Historie nach Benutzer und Feld

## Zweck und Nutzen

Die Kontakt-Detailseite kennzeichnet jede Feldänderung mit einer stabil aus dem
Benutzernamen abgeleiteten Farbe. Zusätzlich lässt sich der Verlauf nach
Benutzer und Feld filtern. Damit ist bei gemeinsam verwalteten Kontakten sofort
erkennbar, wer beispielsweise E-Mail-Adresse, Telefonnummer oder Firmenname
geändert hat. Benutzername, Altwert, Neuwert und Zeitpunkt bleiben weiterhin
als Text sichtbar; Farbe ist nur eine zusätzliche Orientierung.

## Bedienung und Konfiguration

Unter **Kontakte → Kontakt öffnen → Änderungsverlauf** stehen die Auswahlen
**Benutzer** und **Feld** bereit. Beide Filter können kombiniert werden. Die
Anzeige nennt die Zahl der sichtbaren und gesamten Änderungen. Es ist keine
Konfiguration, kein externer Dienst und kein Browser-Speicher erforderlich.

## Voraussetzungen, Sicherheit und Datenschutz

Die Funktion verwendet ausschließlich die bereits mit dem Kontakt ausgelieferte
Historie und arbeitet lokal im Browser. Sie sendet keine Kontakt- oder
Benutzerdaten an Dritte, speichert keine Auswahl dauerhaft und benötigt keine
Zugangsdaten. Text wird nur über sichere DOM-Eigenschaften verarbeitet; die
Filterung erzeugt kein ausführbares HTML aus Kontaktdaten.

## Rechte und Freigaben

Die bestehende Rechteprüfung der Kontakt-Detailseite bleibt maßgeblich. Nur
Eigentümer und ausdrücklich eingetragene Verwalter können den Kontakt und seine
Historie sehen. Die Filter ändern weder Freigaben noch Schreibrechte und machen
keine fremden Kontakte sichtbar.

## Formate und Kompatibilität

Das gespeicherte Kontaktformat, die Audit-Historie, vCard und CardDAV bleiben
unverändert. Die Bedienung benötigt JavaScript; ohne JavaScript bleibt der
vollständige Änderungsverlauf mit Benutzernamen, Alt- und Neuwerten sichtbar,
nur Farben und Filter fehlen. Aktuelle Browser mit standardmäßiger
DOM-Unterstützung sind ausreichend.
Beschriftungen und Trefferzahl folgen der gewählten deutschen oder englischen
Oberflächensprache.

## Fehler- und Ausfallverhalten

Kann das Skript nicht geladen werden, gehen keine Daten verloren und es wird
nichts verändert. Mehrere Benutzer können wegen der begrenzten Farbpalette
dieselbe Farbe erhalten; der ausgeschriebene Benutzername bleibt deshalb immer
sichtbar. Ein leerer Filtertreffer wird verständlich angezeigt.

## Tests und bekannte Grenzen

Automatisierte Tests prüfen Filter-Markierungen, Benutzerzuordnung, sicheres
Setzen der Trefferzahl und den Verzicht auf `innerHTML`. Die Filter arbeiten
nur innerhalb eines einzelnen Kontakts. Eine globale, kontaktübergreifende
Audit-Suche ist nicht Bestandteil dieser Änderung.

## Deaktivierung und Rückkehr

Zur Deaktivierung kann das Skript aus der Kontakt-Detailseite entfernt oder auf
die vorherige Programmversion zurückgegangen werden. Es gibt keine Migration
und keine neuen gespeicherten Felder; vorhandene Historien bleiben vollständig
kompatibel.
