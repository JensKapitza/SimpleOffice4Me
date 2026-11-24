# Digitales Bücherregal und integrierter Dokument-Reader

## Zweck

Das digitale Bücherregal zeigt ausschließlich bereits vorhandene, für den angemeldeten Benutzer sichtbare PDF- und EPUB-Dokumente. Es ist von der physischen Bibliotheks-/Inventarfunktion getrennt. Originaldateien bleiben im bestehenden DocumentStore bzw. hinter dem StoragePort autoritativ; der Reader erzeugt keine zweite dauerhafte Dokumentablage.

## Daten und Versionen

Lesestand und Seiten-/Kapitelanmerkungen liegen benutzerbezogen im vorhandenen Dokument-Zugriffs-Sidecar unter `.simpleoffice-meta/document-access/`. Der Inhaltsstand wird aus Prüfsumme und Dokumentversion gebildet. Nach einer Änderung der Quelldatei wird ein alter Lesestand als veraltet markiert und nicht automatisch auf die neue Version angewendet. Alte Anmerkungen bleiben sichtbar, aber ihr Sprungziel wird bei abweichender Inhaltsversion deaktiviert.

Allgemeine Dokumentnotizen bleiben unverändert. Reader-Anmerkungen sind eine eigene, positionsgebundene Ebene mit Typ `Hinweis`, `Frage` oder `Zusammenfassung`, optionaler markierter Passage und stabilem Locator. PDF verwendet die Seite. EPUB verwendet die stabile Spine-ID sowie Kapitelindex/Offset als Positionsanker.

## Bücherregal

Der Einstieg **Bücherregal** befindet sich in der Dokumentnavigation. Berücksichtigt werden PDF und EPUB aus dem vorhandenen Dokumentindex, also auch Dokumente, die über Upload oder Inbox in den normalen Bestand gelangt sind. Die vorhandene Dokument-Sichtbarkeitsprüfung bleibt maßgeblich.

Filter:
- Format PDF/EPUB
- ungelesen / in Arbeit / fertig
- Suche über Titel, Autor, Pfad, eigene Reader-Anmerkungen und den bereits indexierten Dokumenttext einschließlich OCR
- Sortierung nach zuletzt gelesen, Titel, Autor oder Fortschritt

Die Suche verwendet ausschließlich den Text im vorhandenen Dokumentindex und liest oder rendert beim Aufruf keine Originaldateien erneut. Trefferinhalte werden nicht in der Regalansicht ausgegeben. Die vorhandene Sichtbarkeitsprüfung des Dokumentstores gilt weiterhin.

EPUB-Titel und Autor werden beim sicheren Öffnen aus dem Package-Dokument gelesen und als abgeleitete Reader-Metadaten zwischengespeichert. PDF-Titel und Autor werden über die bereits vorhandene `pypdf`-Abhängigkeit gelesen. Manuelle Korrekturen werden nur als Dokumentattribute gespeichert; das Original bleibt unverändert.

## PDF

Im normalen Browser wird die bereits authentifizierte Dokument-Preview-Route verwendet. In der Android-APK rendert die native Bridge PDF-Seiten mit Android `PdfRenderer`; eine externe Reader-App ist dafür nicht erforderlich. Die Bridge akzeptiert nur Loopback-URLs der lokalen SimpleOffice-Instanz, übernimmt die authentifizierte WebView-Session und begrenzt Datei- und Rendergröße.

Funktionen:
- Vor/Zurück und direkte Seiteneingabe
- aktuelle und gesamte Seitenzahl
- Zoom über die Reader-Werkzeuge
- Vollbild
- persistenter Lesestand
- positionsgebundene Anmerkungen

Die Reader-Grenze liegt bei 1 GiB (1024 MiB) pro PDF. Die separate Web-Uploadgrenze beträgt standardmäßig 512 MiB; für Uploads bis zur Reader-Grenze muss `SIMPLEOFFICE_MAX_UPLOAD_MIB` entsprechend erhöht und der vorgeschaltete Proxy angepasst werden. Passwortgeschützte Dateien werden verständlich abgelehnt, sofern sie sich nicht ohne Passwort öffnen lassen.

### OCR und durchsuchbare Scans

Die normale Dokumentindexierung extrahiert PDF-Text zuerst mit `pdftotext`, nutzt bei leerem Ergebnis oder Fehler zusätzlich den `pypdf`-Fallback und führt unabhängig davon lokale OCR über alle PDF-Seiten aus. Die OCR rendert Seiten mit `pdftoppm` in Stapeln von höchstens zehn Seiten, bis 3.508 Pixel Kantenlänge (etwa 300 dpi bei A4) und in Graustufen. Die Erkennungsbilder werden mit automatischem Kontrast und leichter Schärfung aufbereitet; pro Seite gilt ein Limit von 24 Megapixeln. RapidOCR/ONNX wird lokal zuerst versucht. Bei wenig erkanntem Text oder niedriger Konfidenz ergänzt Tesseract die Erkennung; wenn RapidOCR fehlt, ausfällt oder keinen Text erkennt, dient Tesseract als Fallback. Die Ergebnisse werden zeilenweise zusammengeführt. RapidOCR verwendet die im installierten OCR-Extra enthaltenen Modelle; fehlende Modelle führen zum lokalen Fallback, sie werden zur Laufzeit nicht aus dem Internet geladen. Schlägt ein Renderstapel fehl, werden die Seiten einzeln erneut gerendert, damit ein Fehler nicht alle nachfolgenden Seiten überspringt. Tesseract erhält pro Seite bis zu 30 Sekunden je Sprachversuch; jeder Renderaufruf hat ein 90-Sekunden-Limit. Es gibt keine Seitenobergrenze und kein Gesamtzeitlimit für die OCR eines Dokuments. Dokumente mit vielen Seiten können deshalb längere Indexläufe benötigen. Seitenbytes werden nicht an externe OCR-Dienste gesendet.

Textbasierte PDF-Suche umfasst nativen PDF-Text und OCR-Text im SQLite-Volltextindex. Die Suche-Seite bietet **Fehlende oder veraltete Texte/OCR nacharbeiten**: PDFs mit älterer OCR-Pipeline-Version und unvollständige Extraktionen werden neu verarbeitet; anschließend wird ihr Suchindex aktualisiert. Dieser Backfill läuft mit den lokal installierten Engines. Eine neue Installation oder aktualisierte OCR-Pipeline benötigt daher keine Online-OCR.

Im PDF-Reader kann über **S/W-PDF mit OCR** eine separate Graustufenfassung heruntergeladen werden. Das Original wird nicht geändert. Ghostscript wandelt alle PDF-Seiten in Graustufen um und erhält Vektortext; jede Seite wird für die OCR einzeln auf höchstens 3.508 Pixel Kantenlänge gerendert, mit einem Limit von 24 Megapixeln pro Seite. Die Erkennungsfassung wird in Graustufen mit automatischem Kontrast und leichter Schärfung aufbereitet. Ist das optionale lokale RapidOCR/ONNX-Extra verfügbar, wird es zuerst verwendet; dessen Modelle werden aus der lokalen Paketinstallation geladen und nicht zur Laufzeit nachgeladen. Bei wenig erkanntem Text oder niedriger Konfidenz ergänzt Tesseract die Textebene; wenn RapidOCR fehlt, fehlschlägt oder keinen Text erkennt, dient Tesseract als Fallback. Beide Engines verarbeiten die Seiten lokal, und die PDF-Bilder werden nicht an einen OCR-Dienst übertragen. Tesseract versucht Deutsch und Englisch und fällt auf Englisch zurück, wenn deutsche Sprachdaten fehlen.

Der erkannte Text kommt als unsichtbare PDF-Textebene hinter den sichtbaren Seiteninhalt: Er bleibt durchsuchbar und kopierbar, wird aber nicht über das Bild gemalt. Vorhandener PDF-Text bleibt erhalten; nur abweichende erkannte Zeilen werden ergänzt. Leere Seiten oder Illustrationen ohne erkannte Wörter bleiben erhalten; bei einem echten Konvertierungs-, OCR- oder Parserfehler wird keine teilweise erzeugte Datei ausgeliefert. Eingabe und Ausgabe sind jeweils auf 1 GiB (1024 MiB) begrenzt; Ghostscript hat ein 15-Minuten-Limit, Tesseract pro Versuch 30 Sekunden und pro Seitenrender 90 Sekunden. Es gibt keine feste Seitenobergrenze. Für die Funktion werden Ghostscript und Poppler (`pdftoppm`) benötigt; zusätzlich mindestens eine OCR-Engine (RapidOCR aus dem optionalen `ocr`-Extra oder Tesseract samt Sprachdaten). Der Export wird als Download geliefert und legt keine zweite Dokumentkopie im Bestand an.

## Lokal vorlesen: PDF und Text

Öffne ein PDF oder eine UTF-8-`.txt`-Datei über **Im Reader öffnen** in der Dokumentansicht. **Vorlesen** liest die im Reader gewählte PDF-Seite beziehungsweise den aktuellen Textabschnitt. **Pause**, **Fortsetzen** und **Stopp** steuern die Wiedergabe. Wenn der Browser nach der Vorbereitung nicht automatisch spielt, startet **Fortsetzen** das fertige Audio. Ein Seitenwechsel beendet die bisherige Wiedergabe.

### Stimme für den Reader einrichten

1. Stelle die lokale Piper-CLI im `PATH` des SimpleOffice-Prozesses bereit und prüfe dort `piper --help`.
2. Lege ein passendes Stimmenmodell (`.onnx` und die dazugehörige `.onnx.json`) für den Server lesbar ab. In einem Container muss der Pfad **im Container** existieren.
3. Trage im Admin-Bereich unter **Dokumente & Medien → Piper-Stimmenmodell (Pfad)** den vollständigen Dateipfad ein und speichere. Prüfe zuerst eine kurze TXT-Datei mit **Vorlesen** und danach eine Scan-Seite mit **OCR und vorlesen**. Dafür ist keine Aktivierung im Kinderbereich erforderlich.

Die Instanz verwendet die separat installierte lokale Piper-CLI und das in den Medien-Einstellungen hinterlegte lokale ONNX-Stimmenmodell. Bei englischer Oberfläche wird das optionale englische Modell bevorzugt. Modelle und Engine werden nicht mitgeliefert oder bei der Wiedergabe heruntergeladen. Für PDF verwendet **Vorlesen** die vorhandene Textebene über `pdftotext`. Reine Scanseiten lassen sich mit **OCR und vorlesen** direkt lesen: Poppler rendert nur die im Reader gewählte Seite, Tesseract erkennt den Text lokal, und Piper liest ihn vor. Vorhandener Text bleibt unverändert. Hat dieselbe Seite auch eingebettete Bilder, ergänzt die ausdrückliche OCR-Aktion erkannte Zeilen, die noch nicht im vorhandenen Text stehen. Leere Seiten melden, dass kein Text erkannt wurde. Die Aktion speichert keine PDF-Kopie dauerhaft und verändert weder Original noch Index. Für eine separat durchsuchbare Datei bleibt **S/W-PDF mit OCR** verfügbar.

Für die Sprachausgabe gelten 64 MiB pro PDF, 256 KiB pro TXT, höchstens 5.000 Zeichen je Seite oder Abschnitt und 8 MiB WAV. Größere PDFs zum Vorlesen in kleinere Dateien teilen. Der Server kopiert das PDF für die Sprachausgabe einmal in sein privates temporäres Verzeichnis, prüft die Bytes beim Kopieren und entfernt die Kopie vor der Stimmerzeugung. Auch bei verschlüsseltem Dokumentspeicher enthält diese kurzlebige Datei Klartext. Das Betriebssystem muss ausreichend Platz im temporären Verzeichnis bereitstellen (pro Prozess höchstens zwei aktive Vorleseaufträge, bis zu 64 MiB je PDF). Ein 10-Sekunden-Budget begrenzt die laufende Kopie zwischen Schreibblöcken; ein einzelner blockierter Speicherzugriff ist nicht abbrechbar. PDF-Textextraktion hat sechs Sekunden Zeit, die Suche nach eingebetteten Bildern vier Sekunden und die OCR höchstens 24 Sekunden (einschließlich Sprachauswahl und Rendering; Bildkante maximal 2.200 Pixel), die vorhandene Piper-Engine bis zu 45 Sekunden. Private Sprachdaten werden nicht an einen externen Dienst geschickt. Audio wird nur temporär erzeugt, ohne Servercache bereitgestellt und nach der Wiedergabe im Browser freigegeben; ein Zugriff setzt die aktuelle Dokumentberechtigung voraus.

Für die direkte Seiten-OCR müssen auf dem **SimpleOffice-Server** `pdftotext`, `pdftoppm`, `pdfimages` (Poppler) und `tesseract` sowie `deu` und/oder `eng` verfügbar sein. Prüfen lässt sich das mit `tesseract --list-langs`. Stehen beide Pakete bereit, steht die Sprache der Oberfläche bei der Erkennung an erster Stelle; steht nur eines bereit, wird es verwendet. Debian/Ubuntu: `sudo apt install poppler-utils tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng ghostscript`. Ghostscript wird nur für die getrennte S/W-Kopie benötigt. Das Docker-Image bringt diese OCR-/PDF-Werkzeuge und beide Sprachpakete mit; die lokale Piper-Installation und ein konfiguriertes Modell sind weiterhin separat erforderlich. Unter Windows, macOS und auf einer selbst gehosteten Android-Instanz müssen kompatible Werkzeuge im Server-`PATH` liegen; die APK enthält sie nicht.

Die gleiche Steuerung ist in der Vorschau von E-Mails verfügbar: im Postfach nach dem Öffnen einer Nachricht, im lokalen Archiv, in berechtigten lokalen Mail-Vorgängen und in der EML-Dokumentvorschau. Gelesen wird nur der Nachrichtenkörper, keine Anhänge oder externen Bilder. Eine Postfachnachricht muss noch im bereits geladenen flüchtigen Cache sein; **Vorlesen** verbindet sich nicht erneut zu IMAP oder einem Föderations-Peer. Für EML gelten 16 MiB Gesamtgröße und höchstens 5.000 Zeichen lesbarer Text. Ist eine Quelle nicht mehr vorhanden oder der Zugriff inzwischen entzogen, wird kein Audio ausgeliefert.

## EPUB

EPUB wird ohne Entpacken in das Dateisystem verarbeitet. XML wird mit `defusedxml` geparst. Publisher-HTML, CSS und Skripte werden nicht direkt ausgeführt; Kapitel werden als bereinigter, reflow-fähiger Text neu aufgebaut. Externe Ressourcen werden nicht geladen.

Das Inhaltsverzeichnis wird aus dem EPUB-3-Navigationsdokument (`nav`) oder – bei EPUB-2-Dateien – aus der referenzierten NCX-Datei gelesen. Einträge führen zum zugehörigen Kapitel der Lesereihenfolge. Externe, ungültige oder nicht zur Lesereihenfolge gehörende Ziele werden ignoriert; wenn kein brauchbares Inhaltsverzeichnis vorhanden ist, zeigt der Reader die Kapitel der Lesereihenfolge mit generischen Namen.

Sicherheitsgrenzen:
- maximal 100 MiB Archivgröße
- maximal 5.000 ZIP-Einträge
- maximal 20 MiB pro Eintrag
- maximal 200 MiB deklarierte entpackte Gesamtdaten
- XML maximal 256 Elemente tief und 50.000 XML-Elemente je XML-Datei
- maximal 1.000 Kapitel
- Traversal-, absolute und mehrdeutige ZIP-Pfade werden abgewiesen
- verschlüsselte/DRM-markierte EPUBs werden abgewiesen
- DTD/XXE wird durch `defusedxml` blockiert
- Cover werden nur als JPEG, PNG, GIF oder WebP und maximal 10 MiB ausgeliefert
- SVG-Cover und sonstige aktive Formate werden nicht in der App-Origin ausgeführt

## Rechte und Auslieferung

Alle Reader-, Kapitel-, Cover-, Fortschritts- und Anmerkungsrouten benötigen eine angemeldete Sitzung und die bestehende Dokumentberechtigung. Dokumente werden nochmals über `DocumentStore.get_document()` auf Sichtbarkeit geprüft. Inhaltsbytes werden für die Parser über den bestehenden StoragePort materialisiert und verifiziert. Reader-Antworten sind privat und nicht für öffentlichen Cache vorgesehen.

Dokumentinhalte und markierte Passagen werden nicht in technische Logs geschrieben. Audit-Ereignisse enthalten Dokument-ID, Format, Version, Fortschritt beziehungsweise Anmerkungs-ID.

## Android und Offline

Der Android-Back-Button verwendet weiterhin die bestehende WebView-Historie. Die Reader-Oberfläche ist responsiv und für Hoch-/Querformat ausgelegt.

Das Bücherregal behauptet keine generelle Offline-Verfügbarkeit. Der separat vorhandene Android-Offline-Arbeitsbereich aus 3.0 wird nicht automatisch mit vollständigen Büchern befüllt. Reader-Inhalte bleiben online, bis ein ausdrücklich versionsgebundener Dokument-Prefetch für diesen Fachpfad aktiviert wird.

## Abhängigkeiten

Es wurden keine neuen Python-Abhängigkeiten eingeführt. Verwendet werden die bereits vorhandenen Pakete `pypdf`, `reportlab`, `Pillow` und `defusedxml`, Androids Plattformklasse `PdfRenderer` sowie – falls installiert – die Systemwerkzeuge Poppler (`pdftotext`, `pdftoppm`), Ghostscript und Tesseract für PDF-Textindexierung, OCR und den S/W-Export.

## Abnahmegrenze

Automatisierte Tests decken Sichtbarkeit, Lesestand, Inhaltsversionswechsel, Anmerkungen, ZIP-Traversal, verschlüsselte EPUBs, aktive EPUB-Inhalte, Cover-Auslieferung, Parserfehler und den nativen Android-Bridge-Vertrag ab. Die praktische APK-Abnahme auf einem realen Android-Gerät bleibt zusätzlich erforderlich; sie kann durch Repository-CI nicht ersetzt werden.
