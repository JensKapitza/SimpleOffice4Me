# Getrennte WebDAV-Zugänge pro Gerät

## Zweck und Nutzen

SimpleOffice vergibt für WebDAV keine normalen Anmeldekennwörter. Jeder
Desktop-Client erhält ein eigenes, zufälliges App-Passwort mit Bezeichnung,
Rechteumfang und Ablaufdatum. Dadurch kann beispielsweise nur der verlorene
Laptop widerrufen werden, ohne LibreOffice auf dem Arbeitsplatz oder den
lesenden Backup-Client abzumelden.

Zwei Rechteumfänge stehen zur Verfügung und können zusätzlich auf genau einen
vorhandenen Ordner samt Unterordnern begrenzt werden:

- **Nur lesen** erlaubt `OPTIONS`, `PROPFIND`, `GET` und `HEAD`. Das ist für
  Prüfung, Export und lesende Backups gedacht.
- **Lesen und schreiben** erlaubt zusätzlich `PUT`, `DELETE`, `MKCOL`,
  `COPY`, `MOVE`, `LOCK` und `UNLOCK`. LibreOffice und bidirektionale
  FreeFileSync-Abläufe benötigen diesen Umfang.

Ein Benutzer kann höchstens zehn gleichzeitig aktive Zugänge besitzen. Die
Gültigkeit beträgt wahlweise 30, 90 oder 365 Tage. App-Passwörter werden nur
unmittelbar nach dem Erzeugen angezeigt.

## Bedienung und Konfiguration

1. **Einstellungen → WebDAV-Zugang einrichten** öffnen. Dort kann unabhängig
   von einem einzelnen Dokument ein allgemeiner Zugang für den vollständigen,
   dem Benutzer erlaubten Dokumentbaum erzeugt werden. Alternativ führt
   **In LibreOffice bearbeiten** an einem Dokument zur gerätebezogenen Ansicht.
2. Unter **Neuen Gerätezugang anlegen** eine eindeutige Bezeichnung wie
   `LibreOffice Büro-Laptop` vergeben.
3. Den kleinsten notwendigen Rechteumfang und die Gültigkeit wählen.
4. Das einmal angezeigte App-Passwort in den Zielclient übernehmen.
5. Optional einen vorhandenen relativen Ordner wählen. Ein leerer Wert erlaubt
   aus Kompatibilitätsgründen alle Dokumente.
6. Als Benutzername den SimpleOffice-Benutzernamen und als Adresse die
   gerätespezifische WebDAV-Adresse aus der Zugangstabelle verwenden.

Ein allgemeines App-Passwort ist nicht an eine Datei gebunden und kann in
mehreren Pfaden verwendet werden. Es bleibt bis zum gewählten Ablaufdatum oder
Widerruf gültig. Die leere Pfadgrenze bedeutet dabei nicht öffentliche
Freigabe: Benutzertrennung, Dokumentrechte, Locks und Aufbewahrungssperren
werden bei jeder Anfrage weiterhin geprüft.

Die vollständige Sicherheits- und RFC-Auswertung der Ordnergrenze steht in
[WEBDAV_ORDNERZUGAENGE_RFC3744.md](WEBDAV_ORDNERZUGAENGE_RFC3744.md).

Die Client-Einrichtung für LibreOffice, Nautilus, Windows Explorer, Finder und
FreeFileSync steht in
[WEBDAV_DATEIVERWALTUNG.md](WEBDAV_DATEIVERWALTUNG.md). Jeder dort eingerichtete
Client sollte einen eigenen Zugang erhalten. Ein Gerätezugang kann in der
Liste sofort einzeln widerrufen werden; **Alle Zugänge widerrufen** beendet
den WebDAV-Zugriff dieses Benutzers vollständig.

## Primärstandards und abgeleitete Entscheidungen

### HTTP Basic Authentication – RFC 7617

| Anforderung | Stufe | Umsetzung |
|---|---|---|
| Ein Basic-Challenge benötigt einen `realm`; `charset="UTF-8"` ist optional. | MUST / MAY – [RFC 7617 §2 und §2.1](https://www.rfc-editor.org/rfc/rfc7617.html#section-2) | `401` liefert den stabilen Realm `SimpleOffice4Me Documents` und kündigt UTF-8 an. Die erzeugten Geheimnisse selbst bestehen aus clientfreundlichem ASCII. |
| Benutzername und Kennwort werden als Base64-kodiertes `user-pass` übertragen; Base64 ist keine Verschlüsselung. | MUST / Sicherheitsfolge – [§2](https://www.rfc-editor.org/rfc/rfc7617.html#section-2), [§4](https://www.rfc-editor.org/rfc/rfc7617.html#section-4) | Basic Auth wird für kompatible Desktop-Clients verwendet, darf im entfernten Betrieb aber ausschließlich über HTTPS veröffentlicht werden. |
| Basic Auth sollte nicht ohne HTTPS für schützenswerte Daten verwendet werden. | SHOULD NOT – [§4](https://www.rfc-editor.org/rfc/rfc7617.html#section-4) | TLS-Terminierung und ein gültiges Zertifikat sind Betriebsvoraussetzung. HTTP bleibt nur für lokale Entwicklung möglich; die Anwendung dokumentiert dies und legt keine Zugangsdaten in URLs ab. |
| Kennwörter sollten weder im Klartext noch als ungesalzener Digest gespeichert werden. | SHOULD – [§4](https://www.rfc-editor.org/rfc/rfc7617.html#section-4) | Gespeichert werden ausschließlich zufälliger Salt und `scrypt`-Hash. Das App-Passwort wird nach dem Erzeugen nicht erneut lesbar gemacht. |
| Clients dürfen Zugangsdaten innerhalb desselben Protection Space wiederverwenden. | MAY – [§2.2](https://www.rfc-editor.org/rfc/rfc7617.html#section-2.2) | Dokument- und Dateibaum-URLs liegen unter demselben HTTPS-Ursprung und Realm. Ein Passwort gilt trotzdem nur für den zugehörigen Benutzerpfad. |

### WebDAV und HTTP-Autorisierung

| Anforderung | Stufe | Umsetzung |
|---|---|---|
| WebDAV setzt eine HTTP-Autorisierung vor geschützten Methoden voraus und darf fremde Ressourcen nicht offenlegen. | Sicherheitsanforderung – [RFC 4918 §20.1](https://www.rfc-editor.org/rfc/rfc4918.html#section-20.1) | Fehlende oder ungültige Zugangsdaten erhalten `401`; ein fremder Benutzerpfad erhält nach erfolgreicher Anmeldung `404`. |
| Eine autorisierte Anfrage kann dennoch wegen unzureichender Rechte mit `403 Forbidden` scheitern. | Semantik – [RFC 9110 §15.5.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.4) | Ein lesender Gerätezugang erhält für jede schreibende WebDAV-Methode `403`. Die Anfrage erreicht keine Sperr-, Datei- oder Auditmutation. |
| `OPTIONS` beschreibt die für das Ziel verfügbaren Kommunikationsoptionen. | SHOULD – [RFC 9110 §9.3.7](https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.7), [RFC 4918 §18](https://www.rfc-editor.org/rfc/rfc4918.html#section-18) | Der `Allow`-Header wird je Zugang berechnet. Ein Lesezugang kündigt keine Schreibmethoden an; ein Schreibzugang meldet DAV-Klassen 1 und 2 sowie den vollständigen unterstützten Methodensatz. |

Der Geräteumfang ist bewusst keine Implementierung von WebDAV ACL (RFC 3744).
Er begrenzt den gesamten Zugang eines Clients. Dokumentbezogene Freigaben und
Aufbewahrungssperren bleiben zusätzliche, engere Prüfungen und werden nicht
erweitert.

## Sicherheit, Datenschutz, Rechte und Audit

- Zugangsdaten sind standardmäßig nicht vorhanden und müssen bewusst erzeugt
  werden. Normale Login-Kennwörter, Cookies und OAuth-Token werden nicht
  wiederverwendet.
- Jeder Datensatz enthält nur eine nicht geheime Selektor-ID, Bezeichnung, Umfang, Erzeuger,
  Zeitpunkte, Salt und `scrypt`-Hash. Weder Oberfläche noch Audit enthalten das
  App-Passwort.
- Neue App-Passwörter tragen die Selektor-ID als Präfix. Dadurch prüft der
  Server auch bei zehn Geräten nur einen teuren `scrypt`-Hash statt alle
  Datensätze; das begrenzt die Last fehlerhafter Anmeldeversuche. Der gesamte
  Präfix-und-Geheimnis-Wert wird gehasht und muss vollständig geschützt werden.
- Bezeichnungen sind auf 80 druckbare Zeichen begrenzt. Rechtewerte und
  Ablaufzeiten werden serverseitig gegen feste Positivlisten geprüft.
- Ablaufzeiten werden als UTC-Zeitpunkte gespeichert und bei jeder Anmeldung
  geprüft. Ein abgelaufener Zugang erhält sofort `401`, auch wenn ein Client
  seine Basic-Auth-Daten zwischengespeichert hat.
- Erzeugung und Widerruf werden mit handelndem Benutzer, Gerätekennung,
  Bezeichnung, Umfang und Zeitpunkt in der Git-basierten Revisionhistorie
  protokolliert. Geheimnis und Hash erscheinen nicht im Audit-Snapshot.
- Ein Lesezugang wird vor `LOCK` und allen Dateiänderungen abgewiesen. Er kann
  deshalb weder fremde Sperren erzeugen noch Schreibrechte über einen
  Lock-Token erlangen.
- App-Passwörter dürfen nur in den geschützten Passwortspeicher des jeweiligen
  Betriebssystems oder Clients übernommen werden. Sie gehören nicht in
  Kommandozeilen, URLs, Protokolle oder gemeinsam genutzte Konfigurationsdateien.

## Fehler- und Ausfallverhalten

- `401 Unauthorized`: Passwort fehlt, ist falsch, abgelaufen oder widerrufen.
  Der Antwort-Challenge enthält Realm und UTF-8-Hinweis.
- `403 Forbidden`: Anmeldung ist gültig, der Gerätezugang ist aber nur lesend
  und die angeforderte Methode würde Serverzustand verändern.
- `404 Not Found`: Der authentifizierte Benutzer versucht einen fremden
  Benutzerpfad zu erreichen; dadurch wird dessen Existenz nicht bestätigt.
- Ein Widerruf wird unter derselben exklusiven Dateisperre wie die Erzeugung
  atomar gespeichert. Parallele Anmeldungen sehen entweder den alten oder den
  neuen vollständigen Zustand, nie eine teilweise JSON-Datei.
- Bei vollem Limit oder ungültigen Eingaben wird kein Geheimnis gespeichert.
  Bereits vorhandene Gerätezugänge bleiben unverändert.

## Migration und Rückwärtskompatibilität

Es ist keine Datenbankmigration nötig. Die bisherige Einzelzugangsstruktur in
`webdav-credentials.json` wird weiterhin gelesen und als uneingeschränkter
Schreibzugang **Bestehender Desktop-Zugang** ohne nachträglich erfundenes
Ablaufdatum angezeigt. Beim nächsten Anlegen oder Widerrufen wird der Datensatz
atomar in die Mehrgeräte-Struktur überführt; Salt und Hash bleiben erhalten,
sodass der bestehende Client weiterarbeiten kann.

Die WebDAV-URLs, ETags, Lock-Token, Dokument-IDs und gespeicherten Dateien
ändern sich nicht. Es erfolgt keine Rechteausweitung, Datenmigration oder
Änderung von Aufbewahrungsfristen.

## Tests

Automatisiert geprüft werden:

- parallele Gültigkeit mehrerer Gerätezugänge und isolierter Widerruf;
- Lesezugriff über `OPTIONS`, `PROPFIND` und `GET` sowie `403` für `PUT`,
  `MKCOL` und `LOCK`;
- Ablaufprüfung, falsche Kennwörter und Benutzerpfadtrennung;
- unveränderte Anmeldung mit der alten Einzelzugangsstruktur;
- Positivlisten für Umfang, Bezeichnung und maximal 365 Tage;
- Begrenzung auf zehn aktive Zugänge;
- einmalige Geheimnisanzeige sowie Ausschluss von Hash und Salt aus HTML und
  Audit;
- weiterhin alle WebDAV-Pfad-, Lock-, ETag-, Versions-, Rechte- und
  Interoperabilitätstests.

## Bekannte Grenzen und Rückkehr

- Ein Gerätezugang kann genau einen Ordner samt Nachkommen oder den gesamten
  benutzergebundenen WebDAV-Baum umfassen. Für mehrere getrennte Ordner werden
  getrennte Zugänge benötigt; dies hält Widerruf und Audit eindeutig.
- Abgelaufene Metadaten bleiben sichtbar, bis sie widerrufen werden. Das dient
  Nachvollziehbarkeit und ändert keine Aufbewahrungsregel.
- WebDAV ACL, OAuth für Desktop-Clients, Client-Zertifikate und automatische
  Passwortrotation sind nicht implementiert.
- **Widerrufen** deaktiviert nur das gewählte Gerät. **Alle Zugänge
  widerrufen** stellt das frühere vollständig deaktivierte Verhalten her,
  ohne Dateien, Versionen oder Auditdaten zu löschen.
