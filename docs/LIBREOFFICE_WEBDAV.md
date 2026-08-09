# Dokumente mit LibreOffice über WebDAV bearbeiten

## Zweck und Nutzen

SimpleOffice stellt vorhandene Dokumente als schreibbare WebDAV-Ressourcen bereit. Ein Dokument kann in LibreOffice geöffnet, mit `Strg+S` gespeichert und ohne manuellen Neu-Upload wieder in SimpleOffice übernommen werden. Jede inhaltliche Änderung erhält eine Hash-Prüfung, eine gesicherte Vorgängerdatei und einen Audit-Eintrag.

Der vollständige hierarchische Dateibaum für Nautilus, Explorer, Finder und
FreeFileSync ist in [WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md)
beschrieben.

## Schnellstart

1. Dokument in SimpleOffice öffnen und **In LibreOffice bearbeiten** wählen.
2. **LibreOffice-Zugriff aktivieren** und das einmal angezeigte App-Passwort kopieren.
3. Die WebDAV-Adresse kopieren.
4. In LibreOffice **Datei → Öffnen** wählen und die Adresse in „Dateiname“ einsetzen.
5. Den SimpleOffice-Benutzernamen und das App-Passwort eingeben.
6. Änderungen mit `Strg+S` speichern.

Alternativ kann der Dienst einmalig über **Datei → Remote öffnen → Dienste verwalten → WebDAV** eingerichtet werden. Host, Port und Root-Pfad werden aus der angezeigten HTTPS-Adresse übernommen. Diese Bedienung entspricht der offiziellen LibreOffice-Hilfe zu [WebDAV-Remote-Dateien](https://help.libreoffice.org/latest/de/text/shared/guide/cmis-remote-files-setup.html) und zum [Öffnen und Speichern über WebDAV/HTTPS](https://help.libreoffice.org/latest/de/text/shared/guide/digitalsign_receive.html).

## Konfiguration und Voraussetzungen

- Eine installierte LibreOffice-Version mit WebDAV-Unterstützung.
- Eine von LibreOffice erreichbare SimpleOffice-URL.
- Außerhalb von `localhost` gültiges HTTPS. Hinter einem Reverse Proxy muss `SIMPLEOFFICE_TRUSTED_PROXY_HOPS` korrekt gesetzt sein.
- Pro Gerät ein separat erzeugtes WebDAV-App-Passwort mit Schreibumfang. Das normale Login-Passwort und OAuth-Token werden nicht an LibreOffice gegeben. Details zu Ablauf und Einzelwiderruf stehen in [WEBDAV_ZUGAENGE.md](WEBDAV_ZUGAENGE.md).
- Die bestehende Upload-Grenze `SIMPLEOFFICE_MAX_UPLOAD_MIB` gilt auch für `PUT`.

Der Link enthält weder Passwort noch Session-Cookie. Ein neuer Gerätezugang lässt vorhandene Zugänge unverändert. Einzelnes **Widerrufen** beendet nur diesen Zugang; **Alle Zugänge widerrufen** beendet den gesamten WebDAV-Zugriff. Bereits geöffnete lokale Arbeitskopien werden dadurch nicht gelöscht.

## RFC 4918: ausgewählte Anforderungen und Umsetzung

Maßgeblich ist [RFC 4918 – Web Distributed Authoring and Versioning](https://www.rfc-editor.org/rfc/rfc4918.html).

| Norm | Abschnitt | Konsequenz in SimpleOffice |
|---|---|---|
| Ein DAV-Server **MUST** die unterstützten Klassen über `DAV` melden. | [§10.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.1) | `OPTIONS` meldet Klassen `1, 2` und alle unterstützten Methoden. |
| `PROPFIND` **MUST** für DAV-Ressourcen verfügbar sein; `Depth` ist auszuwerten. | [§9.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.1) | Sammlungen und Dateien liefern `207`; nur `Depth: 0` und `1` werden akzeptiert. |
| Ein erfolgreicher exklusiver Write-Lock **MUST** konkurrierende Schreibzugriffe verhindern. | [§6.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.1), [§9.10](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.10) | `LOCK` erzeugt einen undurchsichtigen Token; fremde oder tokenlose `PUT` erhalten `423`. |
| Lock-Token **MUST** über `If` bei schreibenden Methoden eingereicht werden. | [§7.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-7.6), [§10.4](https://www.rfc-editor.org/rfc/rfc4918.html#section-10.4) | SimpleOffice erkennt Token aus `If` und `Lock-Token`; `UNLOCK` prüft Token und Benutzer. |
| Server **MUST NOT** einen Lock mit unendlicher Lebensdauer voraussetzen; Timeouts dürfen angepasst werden. | [§6.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.6) | Timeouts werden auf 60 bis 3600 Sekunden begrenzt; Standard sind 30 Minuten. |
| Autorisierte Änderungen sollen den gespeicherten Zustand konsistent ersetzen. | [§9.7](https://www.rfc-editor.org/rfc/rfc4918.html#section-9.7) | `PUT` schreibt exklusiv in eine temporäre Datei und ersetzt das Original atomar. |
| Server **SHOULD** ETags erhalten, wenn die Ressource unverändert bleibt. | [§8.6](https://www.rfc-editor.org/rfc/rfc4918.html#section-8.6) | Starke ETags bestehen aus SHA-256; unveränderte Saves erzeugen keine Revision. |

Zusätzlich folgt die Vorbedingungsprüfung [HTTP Semantics RFC 9110 §13](https://www.rfc-editor.org/rfc/rfc9110.html#section-13): Ein veraltetes `If-Match` erhält `412 Precondition Failed`. Die Prüfung wird innerhalb derselben Dateisperre wie das Schreiben wiederholt.

Lesende Zugriffe unterstützen außerdem `Range`, `If-Range`, `If-None-Match`,
`If-Modified-Since` und die zugehörigen 206/304/412/416-Antworten. LibreOffice
und der verwendete WebDAV-Mount können dadurch abgebrochene Downloads sicher
fortsetzen und unveränderte Inhalte ohne erneute Übertragung erkennen. Details
und Grenzen stehen in [WEBDAV_DOWNLOADS_RFC9110.md](WEBDAV_DOWNLOADS_RFC9110.md).

### Bewusst nicht in der direkten Dokument-URL implementiert

- `MKCOL`, `DELETE`, `MOVE` und `COPY` bleiben auf der stabilen Einzeldatei-URL gesperrt. Diese Methoden stehen im getrennten hierarchischen Dateibaum zur Verfügung.
- `Depth: infinity`: reduziert Last- und Informationsrisiken.
- Gemeinsame Links oder anonyme Schreibfreigaben: WebDAV bleibt benutzergebunden.
- Feldweises oder binäres Zusammenführen paralleler Office-Dateien: Konflikte werden abgewiesen und müssen fachlich gelöst werden.
- Browser-Start über ein proprietäres URI-Schema: Die WebDAV-Adresse wird kopiert, weil Betriebssysteme nicht einheitlich ein sicheres LibreOffice-Protokoll registrieren.

## Sicherheit, Datenschutz, Rechte und Freigaben

App-Passwörter werden mit `scrypt` und zufälligem Salt gehasht. Klartext wird nur unmittelbar nach der Erzeugung angezeigt. Basic Authentication darf produktiv ausschließlich über HTTPS verwendet werden. Zugangsdaten, Dokumentinhalte und Sperrbesitzer werden an keinen externen Dienst übertragen.

Die Ressource ist an den authentifizierten Benutzernamen gebunden; Pfade anderer Benutzer antworten mit `404`. Das aktuelle Dokumentmodell erlaubt angemeldeten Benutzern dieselben Dokumente auch in der Weboberfläche. WebDAV erweitert diese Rechte nicht. Eine zukünftige feinere Dokument-ACL muss vor Auflistung und Zugriff ebenfalls ausgewertet werden.

Aufbewahrungsregeln werden nicht verändert. Ein zur Löschung vorgemerktes oder durch eine Bearbeitungsfrist gesperrtes Dokument kann über WebDAV nicht überschrieben werden (`423`).

## Speicherung, Historie und Rückwärtskompatibilität

Vor jedem veränderten `PUT` wird der bisherige Inhalt hashgeprüft unter `.simpleoffice-meta/content-versions/<Dokument-ID>/<SHA-256>` gesichert. Die sichtbare Datei behält Pfad und Dokument-ID. Metadaten enthalten die letzten 200 Inhaltsänderungen mit Zeit, Benutzer, Quelle, Größen und Hashes; die Git-basierte Revision History erhält zusätzlich einen Commit. Textindex und Dateifingerabdruck werden aktualisiert.

Es gibt keine Datenbankmigration. Installationen ohne aktiviertes App-Passwort verhalten sich unverändert. Alte Inhalte werden noch nicht als eigene Downloads in der Oberfläche angeboten; sie bleiben im kontrollierten Versionsspeicher erhalten.

## Fehler- und Ausfallverhalten

- `401`: App-Passwort fehlt oder ist falsch.
- `404`: Dokument, Dateiname oder Benutzerpfad passt nicht.
- `412`: ETag oder Datums-Vorbedingung ist veraltet; das aktuelle Dokument bleibt unverändert.
- `416`: angeforderter Downloadbereich ist ungültig oder nicht erfüllbar; die
  Antwort meldet die aktuelle Dateilänge.
- `413`: Anfrage überschreitet die Upload-Grenze.
- `423`: fremde WebDAV-Sperre oder SimpleOffice-Bearbeitungssperre.
- `409`: `UNLOCK` enthält keinen passenden Token.

Bei Fehlern vor dem atomaren Austausch bleibt das Original bestehen. Temporäre Teil-Dateien werden entfernt. Ein nicht mehr laufender Client blockiert höchstens bis zum Ablauf seines Lock-Timeouts.

## Tests

Automatisiert geprüft werden Aktivierung und Geheimnisschutz, `OPTIONS`, `PROPFIND`, `GET`, `HEAD`, Einzel-/Mehrfachbereiche, `If-Range`, ETag-/Datumsvalidatoren, `LOCK`, `PUT`, `UNLOCK`, persistierte Inhaltsrevisionen, Audit-Historie, Vorgängersicherung, ETag-Konflikte, fremde Sperren, falsche Zugangsdaten, Benutzertrennung, unbeschränkte Tiefenabfragen und Retention-Sperren. Zusätzlich läuft die vollständige Projektsuite in GitHub Actions.

## Deaktivierung und Rückkehr

**Alle Zugänge widerrufen** beendet sämtliche WebDAV-Anmeldungen sofort. Das Entfernen des WebDAV-Blueprints stellt das frühere Verhalten ohne Änderung der Dokumente wieder her. Versionsdateien sollten erst nach einer geprüften Sicherung manuell entfernt werden; die Funktion ändert keine Aufbewahrungsregel und löscht keine Revision automatisch.
