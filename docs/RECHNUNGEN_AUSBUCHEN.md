# Uneinbringliche Rechnungen ausbuchen

Eine finalisierte Rechnung wird bei einer uneinbringlichen Forderung nicht
gelöscht und nicht nachträglich verändert. Die Funktion **Forderung ausbuchen**
erfasst stattdessen einen getrennten, nachvollziehbaren Vorgang. Dadurch bleiben
Rechnungsnummer, PDF, ursprünglicher Brutto-/Netto-Betrag und Umsatzsteuer
unverändert erhalten.

## Ablauf

In der Rechnungsansicht kann eine offene, teilweise bezahlte oder überfällige
Forderung ganz oder teilweise ausgebucht werden. Erfasst werden:

- Grund und optionale Aktennotiz; bei „Sonstiger Grund“ ist die Notiz Pflicht
- Ausbuchungsdatum
- ursprünglich offene Forderung
- ausgebuchter Betrag
- ausführender Benutzer und Erfassungszeitpunkt
- optional der Stopp jeder weiteren Beitreibung

Als Gründe stehen Todesfall, Insolvenz, unbekannter Verzug, unwirtschaftliche
Beitreibung, Kulanz und ein begründungspflichtiger sonstiger Grund zur Auswahl.

Der Beitreibungsstopp ist nur zusammen mit der vollständigen Ausbuchung der noch
beitreibbaren Restforderung zulässig. So kann keine nicht ausgebuchte Forderung
versehentlich aus den offenen Posten verschwinden. Eine Teil-Ausbuchung bleibt
als `partial` beziehungsweise nach dem Fälligkeitsdatum als `overdue` sichtbar.

## Status und Historie

Eine vollständig ausgebuchte Rechnung erhält den fachlichen Status
`written_off`. Ihr offener Betrag für Zahlung und Mahnung ist dann null; weitere
Zahlungen oder automatische Guthabenverrechnungen werden abgewiesen. Die
Ausbuchung steht sowohl in der Rechnungshistorie als auch in der globalen
Änderungshistorie. Bereits vorhandene Zahlungs- und Gutschriftbuchungen werden
bei der verbleibenden Forderung berücksichtigt.

Die Rechnungsübersicht zeigt ausgebuchte Rechnungen mit einem eigenen Zähler und
Filter. Sie werden nicht mehr als regulär offen oder überfällig gezählt.

## Speicherung und Aktualisierung

Ausbuchungen werden als append-only Einträge im bestehenden JSON-Datensatz der
Rechnung gespeichert. Es ist keine Datenbankmigration erforderlich. Ältere
Rechnungen ohne Feld `write_offs` bleiben kompatibel und werden wie bisher
berechnet.

Die Funktion ersetzt keine steuerliche oder rechtliche Prüfung. Insbesondere
kann die umsatzsteuerliche Behandlung einer uneinbringlichen Forderung vom
Einzelfall und vom anwendbaren Recht abhängen; die Anwendung ändert deshalb die
ursprünglichen Steuerbeträge nicht automatisch.
