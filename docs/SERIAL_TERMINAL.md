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

## Drei Geräte: zwei Sensoren und ein Gateway

**Erster Aufbau:** `Pico W A → Gateway ← Pico W B`; das Gateway liefert beide
Werte über eine gemeinsame Adresse an SimpleOffice. Zum Start reichen drei
WLAN-fähige Boards mit MicroPython (Pico W/Pico 2 W, optional ESP32 mit passendem
MicroPython). Der Pico ohne W braucht eine andere Anbindung. Für den Anfang ist
kein zusätzlicher Sensor nötig: `demo` liefert bekannte Testwerte, ein RP2040
kann optional die ungefähre Chiptemperatur liefern (nicht Raumtemperatur).
Für ein echtes I²C-/SPI-/GPIO-Modul wird nur die Messfunktion im Knotenskript
ausgetauscht.

1. Im Router drei feste DHCP-Zuordnungen setzen, zum Beispiel
   `.31` für Knoten A, `.32` für B und `.30` für das Gateway. Den Gerätenetz-
   Zugang nur im eigenen isolierten Sensor-LAN nutzen. Die Standard-Ports sind
   `8080`. Keine öffentliche Freigabe, kein Port-Forwarding.
2. Aus `examples/serial/pico_gateway/node_settings.example.py` für jedes Gerät
   eine eigene, **nicht eingecheckte** Datei `node_settings.py` erstellen. Auf
   A und B: `ROLE = "node"`, jeweils `NODE_ID = "room_a"` bzw. `"room_b"`,
   `ALLOW_FROM_IP` auf das Gateway setzen und unterschiedliche `DEMO_VALUE`
   eintragen. Auf dem Gateway: `ROLE = "gateway"`, `SOURCES` mit den beiden
   Knotenadressen und `ALLOW_FROM_IP` mit der tatsächlichen LAN-Quell-IP von
   SimpleOffice setzen. WLAN-Name und Passwort nur in die privaten Kopien.
3. Bei installiertem MicroPython für jedes Board den richtigen USB-Port
   einsetzen und den Port im Terminal zuvor schließen. Bestehende Startdatei
   sichern. Dann die drei Dateien lokal übertragen:

   ```sh
   mpremote connect PORT fs cp examples/serial/pico_gateway/sensor_link.py :sensor_link.py
   mpremote connect PORT fs cp PFAD_ZUR_PRIVATEN/node_settings.py :node_settings.py
   mpremote connect PORT fs cp examples/serial/pico_gateway/main.py :main.py
   mpremote connect PORT reset
   ```

4. Vom SimpleOffice-Rechner aus `curl http://GATEWAY_IP:8080/status` prüfen.
   Unter **Datenlogger → Worker-Einstellungen** nur die Gateway-IP zusätzlich
   freigeben und den vorhandenen Datenlogger-Worker aktiv halten. Bei Docker
   von der tatsächlichen Worker-Umgebung testen: Ein Standard-Bridge-Netz
   verwendet für ausgehende LAN-Verbindungen meist die LAN-IP des Hosts.
   Bei 403 die tatsächlich am Gateway sichtbare Absender-IP prüfen; die
   Freigabe nicht auf andere Geräte oder ganze Netze ausweiten.
   Als Admin unter **Datenlogger → Serielles Terminal → Pico-Gateway schnell
   einrichten** die feste IPv4-Adresse, den Port und bei Bedarf die Einheit
   eingeben. **Pico-Kanäle anlegen** erstellt zwei private Messkanäle mit dem
   60-Sekunden-Takt und optional zwei getrennte Verfügbarkeitskanäle.
   Erneutes Einrichten derselben Adresse und Ports belässt bestehende Kanäle
   sowie deaktivierte Quellen unverändert. Das Formular ändert die Hostliste
   nicht und kontaktiert kein Gerät unmittelbar; der Worker prüft die
   Erlaubnis weiterhin bei jeder Abfrage. Für vorhandene oder anders
   benannte Kanäle können die **HTTP/JSON-Sensor**-Quellen mit
   **nodes.room_a.value** und **nodes.room_b.value** weiterhin manuell
   zugeordnet werden. SimpleOffice fragt nur das Gateway ab. Die Knoten
   schicken ihren Wert auf dessen Anfrage.
5. Für einen separaten Verfügbarkeitsverlauf nutze zusätzlich
   **nodes.room_a.available** und **nodes.room_b.available** als eigene
   HTTP/JSON-Kanäle. Der Wert ist `1` für eine gültige Antwort und `0`,
   wenn das Gateway den Knoten nicht abfragen kann. Bei ausgeschaltetem
   Knoten A fehlt dessen Messwert; B bleibt abrufbar. Ein Ausfall des Gateways
   selbst erzeugt einen Abfragefehler, keine erfundene `0` für jeden Knoten.
   Nach Wiederanlauf läuft der Verlauf weiter. Aktiviere beim Anlegen der
   HTTP/JSON-Quelle optional **Pico-Zustandswechsel protokollieren**.
   Auf der Seite des Verfügbarkeitskanals erscheinen die letzten 20 bestätigten
   Zustandswechsel; ein erster Wert und wiederholte gleiche Werte erzeugen
   kein Ereignis. Das Gateway verwendet für
   nahe beieinander liegende Abfragen standardmäßig höchstens eine Sekunde lang dieselbe
   Stichprobe (`GATEWAY_CACHE_MS`, `0` schaltet die Wiederverwendung ab).
   Der Datenlogger sammelt Stichproben, keinen lückenlosen Messverlauf.

**Andere Anschlüsse:** Ein Raspberry Pi mit vorkonfiguriertem Ethernet kann
mit denselben drei Dateien und `BIND_IP` als Gateway laufen
(`python3 main.py` im Projektordner neben der privaten Konfiguration). Für
ein Board ohne WLAN ist USB-seriell mit `sensor_demo.py` und der manuellen
Messwertübernahme im Terminal der vorhandene Einstieg. Ein Ethernet-Modul direkt
am Pico und automatische USB-Erfassung sind noch keine getesteten Betriebsarten.

**Sicherheit und Föderation:** Die Beispielsoftware ist HTTP ohne
Verschlüsselung oder kryptografischen Identitätsnachweis. Feste IPs und ein
isoliertes Netz begrenzen die Reichweite, ersetzen aber keine Authentifizierung.
Die Beispielhardware daher nicht in öffentliche oder gemeinsam genutzte Netze
stellen und keine sensitiven Daten darüber übertragen. Zwei Knoten hinter
einem Gateway bilden die erste Kaskade. Zwischen mehreren SimpleOffice-Instanzen
werden Messkanäle in diesem Stand nicht automatisch repliziert. Wenn Standorte
verbunden werden, nur explizit freigegebene Messkanäle zwischen gekoppelten
Instanzen oder über einen bereits gesicherten privaten Netzweg verbinden; keine
Sensor-HTTP-Ports ins Internet freigeben.
