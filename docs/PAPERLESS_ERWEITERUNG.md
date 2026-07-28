# Dokumentenverwaltung nach Paperless-Prinzip

## 1. Bestandsaufnahme

SimpleOffice4Me ist derzeit ein Flask- und SQLite-Grundgerüst:

- Benutzerregistrierung und Anmeldung sind vorhanden.
- `database/files` ist als lokaler Dateispeicher vorgesehen.
- CLI-Kommandos importieren Bild- und PDF-Dateien und verwenden bereits
  `unpaper`, `ocrmypdf`, Tesseract, `unoconv` und `pdftk`.
- Die Startseite ist ein Platzhalter. Es gibt kein Dokumentmodell, keine
  Dokumentliste, keine Volltextsuche, keine Metadaten und keinen automatischen
  Eingang.

Die vorhandene Importlogik ist ein guter Ansatz für die OCR-Pipeline, darf
aber keine Dateinamen per Shell zusammensetzen. Alle externen Programme müssen
über `subprocess.run([...], check=True)` ohne `shell=True` gestartet werden.

## 2. Produktziel

Die Anwendung verwaltet eingehende Papier- und Digitaldokumente zentral. Jede
Quelle erzeugt einen einheitlichen Eingang. Nach Prüfung, OCR und Zuordnung ist
das Original unveränderbar abgelegt und über Volltext sowie Metadaten auffindbar.

Die Bedienung erfolgt in einer Webanwendung; Scanner, E-Mail und Smartphone
müssen keine getrennten Dokumentenbestände erzeugen.

## 3. Fachliche Anforderungen

### 3.1 Einheitlicher Eingang

Jedes eingehende Objekt wird zunächst als **Eingang** gespeichert. Ein Eingang
hat Quelle, Zeitpunkt, technischen Absender, Originaldatei, Prüfergebnis und
Verarbeitungsstatus. Erst nach der Verarbeitung entsteht ein Dokument.

| ID | Anforderung | Abnahmekriterium |
| --- | --- | --- |
| ING-01 | Datei-Upload | PDF, PNG, JPEG, TIFF und Office-Dateien können per Browser hochgeladen werden. |
| ING-02 | Scanner | Ein Netzwerk-Scanner kann in einen überwachten Ordner scannen; neue Dateien werden automatisch übernommen. |
| ING-03 | Scanner-Alternative | Ein lokaler Connector kann SANE/eSCL/AirScan-Scanner auslesen und an die HTTP-API senden. Der Scanner selbst braucht keine Spezialintegration. |
| ING-04 | Foto | Die mobile Webansicht nimmt ein Foto auf, zeigt Zuschneiden/Drehen und sendet es als Eingang. |
| ING-05 | E-Mail | Ein IMAP-Abruf übernimmt Anhänge und optional den E-Mail-Text als PDF. Absender, Datum, Betreff und Message-ID werden gespeichert. |
| ING-06 | E-Mail-Steuerung | Betreff-Tags wie `#tag:steuer`, `#typ:rechnung` und `#korrespondent:stadtwerke` setzen Vorschläge; unbekannte Angaben bleiben zur Prüfung sichtbar. |
| ING-07 | Weiterleitung | Eine eigene Importadresse, z. B. `belege@domain.tld`, genügt. Ein SMTP-Server ist nicht erforderlich, wenn ein vorhandenes Postfach per IMAP abgeholt wird. |
| ING-08 | Schnittstelle | Eine token-geschützte HTTP-API ermöglicht Scanner-Software, Skripten und Apps den Upload. |

### 3.2 Verarbeitung und Ablage

| ID | Anforderung | Abnahmekriterium |
| --- | --- | --- |
| DOK-01 | Originalerhalt | Die ursprüngliche Datei wird unverändert mit SHA-256-Prüfsumme abgelegt. |
| DOK-02 | Normalisierung | Unterstützte Formate werden in PDF/A oder ein vereinbartes Langzeitformat überführt. Fehlerhafte Konvertierungen verbleiben im Eingang mit Fehlermeldung. |
| DOK-03 | OCR | Bild-PDFs und Fotos werden mit deutscher und englischer Texterkennung verarbeitet. Der erkannte Text wird gespeichert und durchsucht. |
| DOK-04 | Dubletten | Gleiche Prüfsumme wird nicht doppelt importiert; ähnliche Dokumente werden lediglich als Hinweis markiert. |
| DOK-05 | Metadaten | Jedes Dokument besitzt mindestens Titel, Dokumentdatum, Importdatum, Dokumenttyp, Korrespondent, Tags und Quelle. |
| DOK-06 | Vorschläge | OCR-Text, E-Mail-Betreff und Absender erzeugen Vorschläge für Typ, Korrespondent und Tags; der Benutzer bestätigt oder korrigiert sie. |
| DOK-07 | Suche | Volltextsuche sowie Filter nach Zeitraum, Typ, Korrespondent, Tag, Quelle und Bearbeitungsstatus stehen zur Verfügung. |
| DOK-08 | Aufbewahrung | Jede zutreffende Aufbewahrungsregel wird einzeln berechnet. Physisch löschbar ist ein Dokument erst, wenn **alle** Regeln abgelaufen sind; praktisch gilt somit stets das späteste Fristende. |
| DOK-09 | Fristenquellen | Regeln können für Dokumenttyp, Korrespondent, Tags, Benutzer/Organisation, manuelle Einzelfälle und Verknüpfungen hinterlegt werden. |
| DOK-10 | Fristbeginn | Der Fristbeginn ist je Regel konfigurierbar: Dokumentdatum, Importdatum, Tag-Zeitpunkt, Ereignisdatum oder ein manuell gesetztes Datum. |
| DOK-11 | Verknüpfte Dokumente | Eine aufbewahrungsrelevante Verknüpfung überträgt die längste aktive Frist transitiv auf alle erreichbaren Dokumente. Kein Dokument der Verknüpfungsgruppe wird vorher gelöscht. |

Aktueller Stand der PDF-Erkennung: Bereits eingebetteter PDF-Text wird ohne
zusätzliche Systeminstallation mit `pypdf` gelesen. Ist `pdftotext`
vorhanden, wird es weiterhin bevorzugt. Reine Scan-PDFs benötigen für OCR
weiterhin `pdfimages` und Tesseract beziehungsweise später den vorgesehenen
OCR-Worker. Der portable Fallback begrenzt Dateien auf 100 MiB und 2.000 Seiten,
damit ein Scanlauf nicht unbegrenzt Ressourcen belegt.

### 3.2.1 Aufbewahrungsregeln und Fristenlogik

Eine Frist ist kein einzelnes Feld am Dokument, sondern ein nachvollziehbar
ausgewerteter Regelbefund. Zu einem Dokument können mehrere Regeln gleichzeitig
gelten. Jede Regel erzeugt ein eigenes `retention_until`.

**Löschregel:** Ein Dokument darf nur gelöscht werden, wenn für das Dokument
und alle transitiv über aufbewahrungsrelevante Verknüpfungen erreichbaren
Dokumente keine aktive Frist und kein Löschstopp mehr vorliegt. Gleichwertig:
das effektive Fristende ist das Maximum aller Fristenden in dieser Gruppe.

| Regel | Start | Ergebnis |
| --- | --- | --- |
| Tag `2026` mit Dauer 8 Jahre | Zeitpunkt, zu dem das Tag gesetzt wurde | Tag am 15.03.2026 gesetzt → Fristende 15.03.2034. |
| Korrespondent `Peter` mit Dauer 4 Jahre | Dokumentdatum, alternativ konfigurierbarer Start | Peter-Dokument vom 01.04.2026 → Fristende 01.04.2030. |
| Dokumenttyp `Rechnung` mit Dauer 10 Jahre | Geschäftsjahresende oder Dokumentdatum, je Regel | darf die kürzere Peter-Regel übersteuern. |
| Verknüpfte Rechnung | Fristende eines verknüpften Dokuments | ein Beleg, der mit der Rechnung verbunden ist, erbt deren spätere Frist. |

Eine Verknüpfung besitzt daher einen Typ und das Flag
`propagates_retention`. Für normale Hinweise kann dieses Flag ausgeschaltet
werden. Für Anlagen, Anhänge, Belege, Vertragsnachträge, Antworten und
zusammengehörende Vorgänge ist es standardmäßig eingeschaltet.

Änderungen an Tags, Korrespondenten, Dokumenttyp, Regeln oder Verknüpfungen
lösen eine Neuberechnung aus. Die Anwendung speichert dabei sowohl die
einzelnen Regelbefunde als auch das effektive Fristende, damit eine Löschsperre
erklärbar bleibt. Abgelaufene Fristen werden nicht entfernt, sondern als
abgelaufen protokolliert.

### 3.3 Bedienung

- Eingangskorb mit Vorschau, OCR-Status und Fehleranzeige.
- Dokumentliste mit Suche, Filtern, Sortierung und Mehrfachbearbeitung.
- Detailansicht mit PDF-/Bildvorschau, OCR-Text, Metadaten, Original und
  Änderungsverlauf.
- Schnelle Aktionen: drehen, erneut OCR ausführen, teilen, zusammenführen,
  tags setzen, archivieren und in den Papierkorb legen.
- Responsive Oberfläche; Fotos vom Smartphone und der Upload müssen ohne App
  funktionieren.

### 3.4 Anmeldung und Identitäten

| ID | Anforderung |
| --- | --- |
| AUTH-01 | Lokale Konten bleiben vollständig nutzbar; Kennwörter werden gehasht gespeichert. |
| AUTH-02 | OAuth2/OpenID Connect wird generisch unterstützt. Google, Microsoft, GitHub oder ein eigener Identity-Provider sind nur Konfigurationen desselben OIDC-Connectors. |
| AUTH-03 | API-Tokens sind an Benutzer oder technische Quellen gebunden, haben Rechtebereiche, Ablaufdatum, Widerruf und einen lesbaren Namen. Scanner erhalten keinen Benutzerzugang. |
| AUTH-04 | PAM-Login kann auf einem Linux-Server aktiviert werden und ordnet lokale Linux-Benutzer Gruppen/Rollen zu. Es ist nur für eine vertrauenswürdige lokale Installation vorgesehen. |
| AUTH-05 | HTTP-Basic/`htpasswd` wird durch Apache oder Nginx vor der Anwendung unterstützt. Der Reverse Proxy übergibt ausschließlich über eine vertrauenswürdige interne Verbindung den bestätigten Benutzernamen. |
| AUTH-06 | Mehrere Anmeldearten können auf dieselbe interne Identität abgebildet werden, damit ein Benutzer z. B. lokal oder mit Google anmelden kann, ohne getrennte Rechte zu erhalten. |

`htaccess` ist keine eigene Anmeldeart der Flask-Anwendung, sondern die
Konfiguration eines vorgeschalteten Webservers. Das Konzept unterstützt sie
deshalb über Reverse-Proxy-Authentifizierung, nicht über das Speichern von
`htpasswd`-Kennwörtern in der Anwendung.

### 3.5 Benutzer, Rechte und Nachvollziehbarkeit

| ID | Anforderung |
| --- | --- |
| SEC-01 | Rechte werden getrennt vergeben: `index_lesen` (Name, Pfad, Tags und Status), `taggen`, `inhalt_lesen` (Vorschau, OCR, Download), `schreiben`, `loeschen`, `regeln_verwalten` und `admin`. |
| SEC-02 | Ein Benutzer mit `index_lesen` und `taggen`, aber ohne `inhalt_lesen`, kann ein Dokument nach Name auffinden und taggen, sieht aber weder Datei, Vorschau noch OCR-Text. |
| SEC-03 | Eine Verzeichnispolicy gilt für ihren gesamten Teilbaum, bis eine darunterliegende Policy sie gezielt überschreibt. |
| SEC-04 | Die Verzeichnispolicy liegt als leicht lesbare Flag-Datei `.simpleoffice-folder.json` im jeweiligen Ordner. Sie enthält stabile Ordner-ID, Vererbungsmodus, Gruppen und Rechte. |
| SEC-05 | Nach Verschieben oder Umbenennen scannt ein Abgleichslauf Policy-Dateien und Dokument-IDs neu ein. Fehlt eine Datei oder ist sie fehlerhaft, startet die Anwendung eingeschränkt, protokolliert den Fehler und rekonstruiert den Index; Rechte werden niemals stillschweigend erweitert. |
| SEC-06 | Jede Änderung an Metadaten, Datei, Berechtigung oder Löschung wird mit Benutzer, Zeitpunkt, Alt- und Neuwert protokolliert. |
| SEC-07 | Kennwörter werden ausschließlich gehasht gespeichert; der Flask-Secret-Key kommt aus einer Umgebungsvariable und nicht aus dem Quelltext. |
| SEC-08 | Uploads erhalten Zufallsnamen, Größen- und MIME-Prüfung sowie Virenscan-Hook. Dateien werden nie direkt aus dem Benutzernamen oder ursprünglichen Dateinamen ausgeführt. |
| SEC-09 | Sicherung umfasst Datenbank, Originale, erzeugte PDFs und Konfiguration. Eine Wiederherstellung wird dokumentiert und getestet. |

### 3.6 Dateibasierte Ablage, Index und Selbstreparatur

**Empfehlung:** Originaldateien und ihre fachlichen Metadaten bleiben
dateibasiert. SQLite ist weiterhin der beste lokale, transaktionale und
volltextfähige Index; er ist jedoch nur ein jederzeit neu erzeugbarer Cache,
nicht die einzige Wahrheit. Ein Datenbankserver ist für den Start nicht
erforderlich.

| Baustein | Ablage | Zweck |
| --- | --- | --- |
| Original und Archiv-PDF | reguläre Verzeichnisstruktur | dauerhaft lesbare Dokumente, auch ohne Anwendung. |
| Dateimetadaten | Dateisystem-Extended-Attributes (`user.simpleoffice.*`), wenn verfügbar | stabile Dokument-ID, SHA-256, Tags und Zeitpunkte direkt an der Datei. |
| Fallback-Metadaten | versteckter, versionierter Sidecar-Speicher `.simpleoffice-meta/` | funktioniert auf Dateisystemen oder Sicherungen ohne Extended Attributes. |
| Ordnerpolicy | `.simpleoffice-folder.json` im Ordner | Rechte und Teilbaum-Vererbung, unabhängig von einer zentralen Datenbank. |
| Suchindex | SQLite mit FTS5 | Wegwerfbarer Cache für Volltext, Filter, Rechteauflösung und schnelle Suche. |
| Chronik | append-only `events.ndjson`, optional zusätzlich SQLite-Ansicht | revisionsfähige Ereignisse und Wiederaufbau nach Datenbankverlust. |

Tags werden nicht in den Dateinamen eingebaut. Das führt bei Umbenennungen,
Sonderzeichen und vielen Tags zu Fehlern. Sie werden als Extended Attribute
gespeichert; fällt diese Funktion weg, verwendet die Anwendung Sidecars. Die
Datei-ID und SHA-256 werden ebenfalls dort abgelegt. Bei einer Dateikopie ohne
Metadaten erkennt der Abgleich die Datei über die Prüfsumme erneut.

Beim Anlegen eines Ordners erzeugt oder prüft die Anwendung die
`.simpleoffice-folder.json`. Beim ersten Start und nach einem Abbruch läuft ein
idempotenter Abgleich:

1. Verzeichnisbaum, Policy-Dateien und Dokument-Metadaten einlesen.
2. Fehlende oder veraltete SHA-256-Werte berechnen; vorhandene Werte prüfen.
3. Bewegte Dateien über stabile Dokument-ID erkennen, ersatzweise über Hash.
4. SQLite-Index, Volltextwarteschlange und effektive Rechte erneut aufbauen.
5. Abweichungen als Chronik-Ereignis dokumentieren; keine fremde oder
   widersprüchliche Datei automatisch löschen.

Die Anwendung kann danach mit einem teilweise fehlenden Index starten. Nicht
aufgelöste Konflikte bleiben im Wartungsbereich sichtbar und können erst nach
Prüfung freigegeben werden.

### 3.7 Scan-Chronik und Dublettenanalyse

| ID | Anforderung |
| --- | --- |
| AUD-01 | Jeder Scanlauf und jede gefundene Datei erzeugt ein unveränderbares Ereignis mit Zeit, Quelle, Pfad, Dateigröße, Hash, Dokument-ID und Bearbeitungsstatus. |
| AUD-02 | `first_seen_at` wird beim ersten Auftreten eines SHA-256 dauerhaft gespeichert; spätere Funde erhöhen nur Zähler und führen die Fundorte. |
| AUD-03 | Die Ansicht zeigt identische Dateien pro Hash, ihren kanonischen Speicherort, Anzahl der Kopien und erstmals/zuletzt gesehen. |
| AUD-04 | Symlinks werden standardmäßig nicht verfolgt. Sie werden als Verweis erfasst; Zielpfad, Geräte-/Inode-ID und ein möglicher Zyklus werden protokolliert. |
| AUD-05 | Gleiche Verzeichnisbäume werden über einen Merkle-Hash aus relativen Pfaden und Datei-Hashes erkannt. Bind-Mounts und erneut eingehängte Bäume werden zusätzlich über Geräte-/Inode-ID markiert. |
| AUD-06 | Zur Vermeidung von Dubletten schlägt die Anwendung je Fall vor: Referenz auf das vorhandene Dokument, relativer Symlink, Hardlink nur bei gleichem Dateisystem oder bewusste unabhängige Kopie. Nichts davon erfolgt automatisch ohne Regel/Freigabe. |

Die Chronik beantwortet damit: Wann wurde ein Hash erstmals gesehen? Wie viele
identische Kopien existieren? Welche Pfade zeigen nur per Symlink auf dieselbe
Datei? Und ob ein ganzer Verzeichniszweig bereits an anderer Stelle vorhanden
ist.

## 4. Technischer Zuschnitt

### Empfohlene Architektur

```text
Scanner / Browser / Smartphone / IMAP / API
                 │
                 ▼
          Eingangskorb (immutable)
                 │
       Worker: prüfen, konvertieren, OCR
                 │
                 ▼
   Dokument + Original + PDF/A + OCR + Metadaten
                 │
                 ▼
       Weboberfläche / Suche / Berechtigungen
```

Die Webanfrage darf OCR und Konvertierung nicht selbst ausführen. Sie legt
nur einen Eingang an. Ein Worker verarbeitet wartende Einträge und kann bei
einem Fehler sicher erneut starten.

### Datenmodell (Kern)

- `document`: ID, Titel, Datum, Erstelldatum, Status, Korrespondent,
  Dokumenttyp, Originalpfad, Archivpfad, Prüfsumme, OCR-Text, Eigentümer.
- `document_tag` und `tag`: Mehrfachzuordnung von Tags.
- `correspondent` und `document_type`: zentral gepflegte Stammdaten.
- `inbox_item`: Quelle, Importzeit, Quelldaten (z. B. E-Mail-Message-ID),
  Status, Fehler, temporäre Datei, Prüfsumme.
- `document_file`: Original, Konvertat, Vorschaubild und abgeleitete Dateien.
- `audit_event`: Benutzer, Aktion, Zeitpunkt, Objekt, vorher/nachher.
- `retention_rule`: Geltungsbereich, Filter (z. B. Tag `2026` oder
  Korrespondent `Peter`), Dauer, Fristbeginn und Priorität/Status.
- `retention_evaluation`: Dokument, auslösende Regel, Fristbeginn,
  Fristende, Berechnungszeitpunkt und Begründung.
- `document_link`: Quell- und Zieldokument, Beziehungstyp und
  `propagates_retention`.
- `legal_hold`: expliziter Löschstopp mit Grund, Beginn, optionalem Ende und
  Freigabe durch berechtigte Benutzer.
- `identity` und `identity_login`: interne Person sowie Zuordnung zu lokalem
  Konto, OIDC-Subject, PAM-Name, Reverse-Proxy-Name oder API-Token.
- `permission_grant`: Verzeichnis-ID, Benutzer/Gruppe, getrennte
  Rechteflags und Vererbungsregel.
- `file_fingerprint`: Dokument-ID, SHA-256, Größe, Dateisystem-ID, Inode,
  kanonischer Pfad und Zeitpunkte `first_seen_at`/`last_seen_at`.
- `scan_event`: append-only Ereignis für Scanläufe, Funde, Verschiebungen,
  Dubletten, Symlinks, Reparaturen und Fehler.

Für einen Einzelplatz genügt SQLite mit FTS5 zunächst. Bei gleichzeitigem
E-Mail-Abruf, mehreren Nutzern oder hohem Scanaufkommen ist PostgreSQL die
sinnvollere Datenbank. Binärdateien bleiben im Dateisystem oder S3-kompatiblen
Speicher, nicht in SQLite-Blobs.

## 5. Eingangswege: empfohlene Reihenfolge

1. **Browser-Upload und überwachte Scanfreigabe**: günstig, robust und mit
   praktisch jedem Scanner nutzbar.
2. **IMAP-Abruf**: übernimmt Rechnungen und Schriftverkehr ohne Mailserver-
   Änderung. Erst nach erfolgreicher Archivierung wird eine Mail markiert oder
   in einen verarbeiteten Ordner verschoben.
3. **Mobile Webkamera**: für einzelne Belege; Bilder werden clientseitig
   gedreht und serverseitig entzerrt.
4. **HTTP-API und SANE/eSCL-Connector**: für Direktintegration und spätere
   Automatisierung.

Ein direkter Scanner-Anschluss ist daher optional. Die überwachte Freigabe ist
die beste und günstigste Startlösung; ein API-Upload ist die sauberste Lösung
für spezialisierte Scanner-Workflows.

## 6. Umsetzungsphasen

### Phase 1 – nutzbares MVP

- Datenmodell, Migrationen, sicherer Dateispeicher und Eingangskorb.
- Browser-Upload, überwachte Scanfreigabe und PDF-/Bildvorschau.
- asynchroner OCR-Worker mit `ocrmypdf` und Tesseract.
- Titel, Datum, Tags, Korrespondenten, Volltextsuche und Papierkorb.
- Benutzerrollen, Audit-Protokoll und Backup-Anleitung.

### Phase 2 – automatisierte Eingänge

- IMAP-Abruf mit Anhängen, Metadaten und Betreff-Tags.
- Mobile Kameraansicht und tokenisierte Upload-API.
- Dublettenerkennung, Regelwerk für automatische Zuordnung und Fehler-Queue.

### Phase 3 – Komfort und Integrationen

- SANE/eSCL-Connector, Dokument teilen/zusammenführen, Vorlagen für
  Ablageregeln und optionales E-Mail-Antworten aus der Dokumentansicht.
- Mehrmandantenfähigkeit, externe Speicherziele und detaillierte
  Aufbewahrungsregeln.

## 7. Nicht-Ziele der ersten Version

- Eigenes E-Mail-Postfach oder vollständiger Mailclient.
- Rechtssichere, revisionssichere Archivierung ohne gesonderte Prüfung der
  organisatorischen und gesetzlichen Anforderungen.
- KI-gestützte automatische Buchhaltung ohne bestätigbare Zuordnung.

## 8. Entscheidungsbedarf vor der Implementierung

1. Einzelplatz oder mehrere Personen mit getrennten Bereichen?
2. Soll der Speicher lokal, auf NAS oder S3-kompatibel liegen?
3. Welches bestehende Postfach soll per IMAP abgeholt werden und in welchem
   Rhythmus?
4. Reichen Watch-Folder und Upload für die Scanner, oder wird ein bestimmtes
   Modell direkt angebunden?
5. Welche Dokumenttypen und Aufbewahrungsfristen sind erforderlich?
