# HTTPS-LAN-Proxy und Bildschirm-Viewer

## Startverhalten

Die gespeicherte Sektion `lan_proxy` in
`<Dokumentordner>/.simpleoffice-meta/settings.json` steuert einen eigenständigen
HTTPS-Listener. **Admin → Einstellungen → Server** kann ihn sofort starten oder
stoppen und unabhängig vom Webserver für Autostart aktivieren. Die Einstellung
gilt für den Standard-Launcher, das eingebettete Desktop-Backend und andere
WSGI-Starts, sobald der Server die erste Anfrage bearbeitet.

- Das Web-Backend kann an `127.0.0.1` gebunden bleiben.
- Der Proxy bindet an eine einzelne Loopback- oder private Schnittstellen-IP,
  zum Beispiel `192.168.1.50:8081`. So lässt sich die Firewall auf genau diese
  Adresse und diesen Port beschränken.
- Für Docker wird `compose.lan-proxy.yaml` zusätzlich geladen. Der Proxy bindet
  innerhalb des isolierten Container-Netzwerks an `0.0.0.0`; veröffentlicht
  wird der Port nur an die in `SIMPLEOFFICE_LAN_PROXY_PUBLISH_ADDRESS`
  eingetragene Host-IP.
- Der Proxy benötigt ein gültiges, von den Clients vertrautes TLS-Zertifikat.
  Sind beide TLS-Dateinamen leer, erzeugt die Anwendung beim Start ein dauerhaftes
  selbstsigniertes Zertifikat für die konfigurierte IP. Im Docker-Wildcard-Modus
  muss es zu `SIMPLEOFFICE_LAN_PROXY_PUBLIC_HOST` passen. Clients müssen dem
  Zertifikat ausdrücklich vertrauen; die TLS-Prüfung wird nie abgeschaltet.
  Explizite Zertifikats- und Schlüsseldateien bleiben unterstützt. Die Einstellungen
  enthalten nur Dateinamen; Dateien liegen im TLS-Ordner
  `SIMPLEOFFICE_LAN_PROXY_TLS_DIR` (Standard auf dem Host:
  `~/.simpleoffice4me/lan-proxy`, im bestehenden Docker-Overlay:
  `/run/simpleoffice`).
  Ein eigener TLS-Ordner muss als absoluter Pfad über
  `SIMPLEOFFICE_LAN_PROXY_TLS_DIR` festgelegt werden. Er darf nicht für Gruppe
  oder andere Benutzer beschreibbar sein und muss außerhalb des Dokumentordners
  liegen. Der Schlüssel darf für Gruppe oder andere Benutzer nicht lesbar sein.
  Bereits gespeicherte absolute Pfade innerhalb dieses Ordners werden beim Lesen
  auf relative Dateinamen umgestellt. Für ältere Pfade außerhalb des Ordners
  zeigt die Einstellungsseite einen Migrationshinweis; Dateien dort ablegen und
  die Felder auf relative Dateinamen umstellen.
- Änderungen und Start/Stop gelten sofort. Ein belegter Port oder eine nicht
  verfügbare Bind-Adresse erscheint als Fehlerstatus; es gibt keinen stillen
  Portwechsel.

Alle HTTP-Methoden und Pfade werden an einen festen Loopback-Endpunkt
weitergeleitet. Host-, Schema- und Clientdaten vertraut das Backend nur nach
Prüfung eines zufälligen Prozess-Tokens. Der HTTPS-Listener setzt Cookies auf
`Secure`; Anmeldung und CSRF-Prüfung bleiben erforderlich. HTTP-Upgrades,
CONNECT und TRACE werden nicht weitergeleitet.

### Docker-Konfiguration

Die TLS-Variante mit automatisch erzeugtem Zertifikat nutzt die persistente
Anwendungsdaten-Volume. Host-Zertifikatspfade sind dafür nicht erforderlich.

1. In `deploy/docker/.env` `SIMPLEOFFICE_LAN_PROXY_PUBLISH_ADDRESS` (eine
   konkrete Host-IP) und `SIMPLEOFFICE_LAN_PROXY_PUBLIC_HOST` (genau die IP oder
   DNS-Adresse, die Clients verwenden) eintragen.
2. Basisdatei und Auto-TLS-Overlay starten:

   ```sh
   docker compose --env-file deploy/docker/.env \
     -f deploy/docker/compose.yaml \
     -f deploy/docker/compose.lan-proxy-auto-tls.yaml up -d
   ```

3. In den Server-Einstellungen `0.0.0.0` als Proxy-Bind-Adresse und die
   TLS-Dateinamen leer lassen. Die automatisch erstellten Zertifikatsdateien
   bleiben in `/var/lib/simpleoffice4me/lan-proxy-tls` über Containerneustarts
   erhalten. Der veröffentlichte Port ist standardmäßig `8081`; der Proxy-Port
   in der UI muss ebenfalls `8081` sein.
4. Nur diesen Port für das gewählte LAN-Profil in der Host-Firewall freigeben.
   Das Web-Backend bleibt über die Standard-Compose-Datei an Loopback des Hosts
   veröffentlicht. Keine Router-Portfreigabe ins Internet einrichten.

Für eigene Zertifikate bleibt `compose.lan-proxy.yaml` verfügbar. Dort die
Host-Dateipfade `SIMPLEOFFICE_LAN_PROXY_TLS_CERT` und
`SIMPLEOFFICE_LAN_PROXY_TLS_KEY` setzen, die Einstellungen auf `lan-proxy.crt`
und `lan-proxy.key` zeigen lassen und sicherstellen, dass der Containerbenutzer
`simpleoffice` (UID/GID `10001`) den Schlüssel lesen kann (zum Beispiel
Host-Eigentümer `root:10001`, Modus `0640`). Nicht beide TLS-Overlays gleichzeitig
verwenden.

## Bildschirm

Beim Start der Bildschirmfreigabe wird zusätzlich ein Zuschauer-Link
`/screen/view/<Verbindungscode>` angeboten. Die Seite wählt die Empfängerrolle
automatisch und nutzt denselben WebRTC-Signalisierungskanal.

- Der lokale Link verwendet den aktuellen Browser-Ursprung.
- Der LAN-Link wird nur bei laufendem Proxy mit HTTPS-Ursprung angezeigt.
- Beide Links erfordern derzeit eine SimpleOffice-Anmeldung.
- Der Verbindungscode ist nur für die aktuelle Sitzung gültig und wird nach
  deren Ende ungültig.
- Mehrere parallele Empfänger werden vom derzeitigen WebRTC-Sender nicht
  zuverlässig unterstützt.

## Sicherheit und Einschränkungen

Der Listener ist HTTPS-only. Wenn keine gültigen expliziten Dateien vorliegen
und die automatische TLS-Erzeugung fehlschlägt, startet er nicht; der lokale
Betrieb bleibt verfügbar und die Statusseite zeigt den Fehler. Im Netz
wird ausschließlich eine `https://`-URL bekanntgegeben. HTTP ist kein
Fallback. Browser-Bildschirmaufnahme benötigt einen sicheren Kontext.

Der Proxy ist ein eigener Listener im Anwendungsprozess und kein
Mini-Services-Worker. Mini Services zeigt den Laufzeitstatus. Zertifikatsausgabe,
Gastlinks und mehrere Zuschauer sind nicht Teil dieses Funktionsumfangs.

Auf der Android-APK und auf Termux ohne installiertes `aiohttp` verwendet der
Proxy einen Standardbibliothek-Fallback. Er nimmt Anfragen mit `Content-Length`
an; chunked Requests und Upgrades werden in diesem Fallback abgelehnt. Der
Android-Runtime behält zusätzlich seinen bisherigen LAN-Peer-Endpunkt für
Connect-QR und Federation; der HTTPS-Proxy ersetzt diesen separaten Endpunkt
nicht.

## Prüfpunkte vor Freigabe

1. HTTPS-Proxy von einem zweiten Gerät im selben LAN mit vertrautem Zertifikat
   aufrufen.
2. Prüfen, dass Standardwerte keinen LAN-Port öffnen.
3. Bildschirmfreigabe und Empfang im zweiten Browser mit Login prüfen.
4. Portkonflikt, mehrere Netzwerkschnittstellen, Firewall und Neustart prüfen.
5. Python-Proxy- und Browser-Screen-Share-Tests sowie CI ausführen.

Die Admin-Seite und Mini Services lesen den Live-Status des Prozess-Managers.
Ein älterer Electron-Heartbeat wird für bereits installierte Versionen noch
ausgewertet. Statuswerte: `running`, `disabled`, `error`, `stopped`, `stale`,
`unknown`.
