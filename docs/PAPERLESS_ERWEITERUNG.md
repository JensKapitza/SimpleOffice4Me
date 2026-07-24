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
| DOK-08 | Aufbewahrung | Löschen erfolgt zweistufig: Papierkorb mit Frist, danach physische Löschung. Aufbewahrungsfristen können je Dokumenttyp gesetzt werden. |

### 3.3 Bedienung

- Eingangskorb mit Vorschau, OCR-Status und Fehleranzeige.
- Dokumentliste mit Suche, Filtern, Sortierung und Mehrfachbearbeitung.
- Detailansicht mit PDF-/Bildvorschau, OCR-Text, Metadaten, Original und
  Änderungsverlauf.
- Schnelle Aktionen: drehen, erneut OCR ausführen, teilen, zusammenführen,
  tags setzen, archivieren und in den Papierkorb legen.
- Responsive Oberfläche; Fotos vom Smartphone und der Upload müssen ohne App
  funktionieren.

### 3.4 Benutzer, Rechte und Nachvollziehbarkeit

| ID | Anforderung |
| --- | --- |
| SEC-01 | Rollen `admin`, `bearbeiten`, `lesen` und optionale Bereiche/Mandanten begrenzen Sichtbarkeit und Änderungen. |
| SEC-02 | Jede Änderung an Metadaten, Datei, Berechtigung oder Löschung wird mit Benutzer, Zeitpunkt, Alt- und Neuwert protokolliert. |
| SEC-03 | Kennwörter werden ausschließlich gehasht gespeichert; der Flask-Secret-Key kommt aus einer Umgebungsvariable und nicht aus dem Quelltext. |
| SEC-04 | Uploads erhalten Zufallsnamen, Größen- und MIME-Prüfung sowie Virenscan-Hook. Dateien werden nie direkt aus dem Benutzernamen oder ursprünglichen Dateinamen ausgeführt. |
| SEC-05 | Sicherung umfasst Datenbank, Originale, erzeugte PDFs und Konfiguration. Eine Wiederherstellung wird dokumentiert und getestet. |

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
