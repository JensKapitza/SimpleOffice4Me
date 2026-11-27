# Serielles Terminal und ältere Bürogeräte

## Einrichten und prüfen

1. SimpleOffice **auf dem Rechner mit dem Adapter** starten, dort über
   `localhost` anmelden und in Desktop-Chrome den **Datenlogger → Serielles
   Terminal** öffnen. Dieser Einstieg ist für direkte Loopback-Verbindungen
   freigegeben. Kein Gerätezugriff über einen Proxy, LAN-Hostnamen oder Server.
   Android-WebView und eine automatisierte Erfassung ohne geöffnetes Terminal sind keine Zusagen
   dieser Fassung.
2. Die Baudrate, Daten-/Stoppbits, Parität und gegebenenfalls RTS/CTS laut
   Gerätehandbuch einstellen. **Port auswählen** öffnet die Browserauswahl:
   Windows listet die verfügbaren COM-Ports, Linux z. B. USB-Seriell und USB CDC.
   Kein Zugriff auf einen gerade durch ein anderes Programm belegten Port.
   Bei Linux die Berechtigung für den konkreten Port über die Betriebssystem-
   Richtlinie vergeben, nicht den Browser als root ausführen.
3. Die Empfangsansicht dekodiert UTF-8. Zum Senden Text mit optionalem
   Zeilenende oder exakte Hex-Bytes verwenden. Der optionale Mitschnitt enthält
   Zeitstempel, Richtung und Base64 der Originalbytes als JSONL (kein automatischer
   Upload). Der Browser stoppt bei 4 MiB Nutzdaten oder 8192 Datensätzen. Den
   Mitschnitt vor dem Schließen der Seite herunterladen.
4. Hardwareprüfung: Zwei eigene kompatible Adapter über passende Schnittstellen
   verbinden, am zweiten Port ein lokales Terminal öffnen und in beide Richtungen
   Text und Binärdaten übertragen. Empfang, Versand, Abziehen des Adapters und
   Wiedereinstieg prüfen. RS-232-Signalpegel und GPIO/UART mit 3,3 V sind nicht
   dasselbe; keinen RS-232-Ausgang direkt an den Mikrocontroller anschließen.

## ESP32/Pico: vom Testprogramm zum Messkanal

Den passenden [offiziellen MicroPython-Build](https://micropython.org/download/)
für das konkrete Board installieren, nicht für ein ähnliches Board. Bei bereits
installiertem MicroPython lokalen Port in SimpleOffice trennen und auf dem
Rechner mit [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html)
testen. Den Port unten durch z. B. `COM4` oder `/dev/ttyACM0` ersetzen:

```sh
python -m pip install mpremote
mpremote connect PORT run examples/serial/sensor_demo.py
# Optional: ein einzeln ausgewähltes MicroPython-Paket aufs Board übertragen.
mpremote connect PORT mip install micropython-umqtt.simple
```

Das Skript liefert einen synthetischen Zähler. Erst nach Test und Sicherung eines
vorhandenen Startprogramms kann es dauerhaft als `main.py` übernommen werden:
`mpremote connect PORT fs cp examples/serial/sensor_demo.py :main.py`.
Nach Freigabe des Ports im Webterminal verbinden; für den seriellen REPL ist
115200 Baud beim üblichen ESP32-UART ein Ausgangspunkt. Digitaler Eingang,
I²C, SPI und BLE hängen von Board, Firmware und Sensor ab. Zum Einstieg einen
einzelnen Messwert je Zeile als `SO4M,21.5` ausgeben. Der Benutzer wählt einen
beschreibbaren Kanal und übernimmt den letzten gültigen Wert ausdrücklich.
Die Serverfreigaben und CSRF-Prüfung des Datenloggers gelten weiter.

Kein automatisches Flashen, keine Paketausführung durch SimpleOffice. Für
spätere Dauererfassung: explizit pro Kanal aktivieren, feste Zuordnung,
Rate-/Puffergrenzen, Geräteausfall und Wiederanlauf testen. Chat und Dateien
über den Draht erst als separates, ausdrücklich gekoppeltes Protokoll mit
Größenlimit, Prüfsumme und Annahme am Empfänger, nicht als Netzwerkadapter oder
als automatisches Weiterleiten von Terminaldaten. Noch nicht implementiert.

## Alte Drucker und Scanner

| Gerät | USB | Netzwerk |
| --- | --- | --- |
| Drucker | Auf dem Rechner mit USB-Anschluss als CUPS- bzw. Windows-Warteschlange einrichten. | Vorhandene IPP-/CUPS- oder Windows-Warteschlange verwenden. Alte Geräte über einen vom Betriebssystem unterstützten Treiber oder eine eingerichtete Druckfreigabe anbinden. |
| Dokumentenscanner | Mit vorhandenem SANE- oder Herstellertreiber lokal als PDF oder Bild speichern. | Netzwerkscanner mit dem vorhandenen Treiber oder über Scan-to-Folder in einen dafür vorgesehenen Ordner speichern lassen. eSCL/AirScan nur einsetzen, wenn das Modell es unterstützt. |

In SimpleOffice unter **Drucker einrichten** die Warteschlange wählen und erst
eine Testseite drucken. Nicht jedes ältere Gerät akzeptiert Druckdaten über
RS-232. Siehe [PrinterShare](PRINTERSHARE_FEDERATION.md) und
[Docker/CUPS](DEPLOYMENT.md). Beim Scan unter **Dateien und PDFs erfassen** die
fertige Datei oder den Scanordner auswählen. SimpleOffice sucht weder das
Netzwerk noch USB-Anschlüsse automatisch ab.

- Nächster Schritt für wiederkehrende Scans: nach verifiziertem Gerätemodell und
  Betriebssystem ein erlaubtes Scannerprofil. Erst geschlossene Dateien sammeln,
  vor dem Import gegen Dateiart und Größe prüfen und bereits erfolgreich
  importierte Dateien anhand einer stabilen Identität erkennen. Kein
  unbeschränkter Zugriff auf lokale Verzeichnisse und kein voll privilegierter
  Container. Direktes Auslösen des Scanners ist in diesem Stand nicht enthalten.
