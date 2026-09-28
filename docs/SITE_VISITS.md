# Ortstermine

Ortstermine erfassen Räume, Geräte, Verbindungen, Stromwerte, Beobachtungen und Belege. Ein Ortstermin erzeugt zusätzlich eine verknüpfte Aufgabe. Felder dürfen leer bleiben, wenn etwas vor Ort nicht bekannt oder nicht prüfbar ist; freie Befunde und zusätzliche Befundfelder bilden unerwartete Situationen ab.

## Ablauf

1. Ortstermin in der Arbeit-Navigation anlegen und Stammdaten ergänzen.
2. Räume anlegen, optional einen Grundriss als Bild hochladen und Geräte im Raumplan positionieren.
3. Geräte, Ports, Verbindungen und elektrische Eckdaten erfassen. Eine nicht bekannte Leistung bleibt unbekannt und wird nicht als null Watt dargestellt.
4. Befunde mit Belegklasse, Quelle und Sicherheit festhalten. Beziehungen können als Ursache, Folge, Unterstützung, Widerspruch oder Behebung verbunden werden. Zyklen sind gesperrt.
5. Befunde prüfen und für den Bericht einzeln aufnehmen oder mit Grund auslassen. Nur bestätigte oder behobene Befunde werden in PDF-Berichte übernommen.
6. Vorher-/Nachher-Stände speichern und vergleichen, etwa vor und nach einer Reparatur.

## Kamera, Standort und QR-Label

Die Seite nutzt nur ausdrücklich angeforderte Browserfunktionen. Geolocation fragt erst nach Klick die Gerätefreigabe an. Kamera und Barcode-Erkennung werden nur nach Nutzeraktion gestartet; der Videostream wird nach dem Scan beendet. Funktionen hängen von Browser, Berechtigung und sicherem Kontext (HTTPS) ab. Grundrisse, Fotos und Dateien werden als Terminanhänge abgelegt. Maximalgröße ist 16 MiB.

Für QR-Labels muss `SIMPLEOFFICE_SERVER_PUBLIC_URL` (oder `SIMPLEOFFICE_FEDERATION_PUBLIC_URL`) auf die von den Mitarbeitenden erreichbare Server-URL gesetzt sein. Das Label führt zur konkreten Gerätedokumentation, enthält aber keine Zugangsdaten.

## LAN-Scan und Grenzen

Der Scan läuft auf dem Anwendungsserver, nicht im Browser des Mitarbeitenden. Das Zielnetz muss vom Server aus erreichbar sein; ein Server außerhalb des Kunden-LANs kann dieses daher nicht direkt scannen. Für entfernte bzw. segmentierte Netze ist ein separat abgesicherter lokaler Scan-Agent erforderlich. Der Scan erfordert eine ausdrückliche Checkbox, akzeptiert ausschließlich private IPv4-Netze von `/24` bis `/30`, höchstens 254 Hosts und bis zu 32 TCP-Ports. Vordefinierte Gruppen umfassen Web, E-Mail, Dateifreigaben, Fernzugriff und Datenbanken; zusätzliche Ports können als Liste angegeben werden. Geprüft wird durch normale TCP-Verbindungsversuche mit begrenzter Parallelität und Zeitüberschreitung – nicht durch frei definierte Raw-Pakete oder einen SYN/ACK-Scan. Das ist ohne erhöhte Paket-Socket-Rechte plattformübergreifend nutzbar, verursacht aber normalen TCP-Verbindungsaufbau. Es gibt keine UDP-, ICMP-Ping-, Betriebssystem-, Hersteller-, Sicherheitslücken- oder Exfiltrationsanalyse. Der Scan ist nur in Netzen mit eigener Berechtigung zu starten. Ein offener Port beweist weder eine Kompromittierung noch Datenabfluss.

## Geheimnisse und Bericht

Zugangsdaten werden verschlüsselt gespeichert und müssen aktiv über eine angemeldete Sitzung angezeigt werden. Aufrufe werden protokolliert. Passwörter erscheinen weder im QR-Code noch in Snapshots oder Berichten. PDF-Berichte führen Geräte, erfasste Ports, Verbindungen, Stromsummen (mit separater Anzahl unbekannter Leistungswerte), ausgewählte Befunde, Belegklassen und Grenzen auf. Stromsummen sind Bestandsnotizen, keine Messung von Leitungsbelastbarkeit oder Elektroprüfung.
