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

Die normale Dokumentindexierung extrahiert PDF-Text zuerst mit `pdftotext`, mit `pypdf` als Python-Fallback. Die Scan-OCR rendert **jede PDF-Seite** mit `pdftoppm` in Stapeln von höchstens zehn Seiten und einer maximalen Kantenlänge von 1.800 Pixeln. Schlägt ein Stapel fehl, werden seine Seiten einzeln erneut gerendert, damit ein Fehler nicht alle nachfolgenden Seiten überspringt. Tesseract erhält pro Seite bis zu 30 Sekunden; jeder Renderaufruf hat ein 90-Sekunden-Limit. Es gibt keine Seitenobergrenze und kein Gesamtzeitlimit für die OCR eines Dokuments. Dokumente mit vielen Seiten können deshalb längere Indexläufe benötigen. Sind `pdftoppm` oder Tesseract nicht installiert, kann keine Scan-OCR ausgeführt werden; die normale Textindexierung läuft weiter.

Textbasierte PDF-Suche umfasst den extrahierten Indextext. Ein erneuter Scan beziehungsweise `refresh_missing_text` aktualisiert fehlenden oder veralteten extrahierten Text.

Im PDF-Reader kann über **S/W-PDF mit OCR** eine separate Schwarz-Weiß-Fassung heruntergeladen werden. Das Original wird nicht geändert. Ghostscript wandelt alle PDF-Seiten in Graustufen um; **jede Seite** wird einzeln gerendert und mit Tesseract verarbeitet. Die erkannte OCR-Ebene wird als PDF-Textebene mit unsichtbarem Zeichenmodus eingebettet: Sie bleibt durchsuchbar und kopierbar, wird aber nicht über den sichtbaren Seiteninhalt gemalt. Bereits vorhandene Textzeilen bleiben erhalten; identische OCR-Zeilen werden nicht doppelt ergänzt. Schlägt die Konvertierung oder OCR einer Seite fehl, wird keine teilweise erzeugte Datei ausgeliefert. Eingabe und Ausgabe sind jeweils auf 1 GiB (1024 MiB) begrenzt; Ghostscript hat ein 15-Minuten-Limit, pro OCR-Seite gelten 30 Sekunden und pro Seitenrender 90 Sekunden. Es gibt keine feste Seitenobergrenze. Für diese Funktion werden Ghostscript, Poppler (`pdftoppm`) und Tesseract benötigt. Der Export wird als Download geliefert und legt keine zweite Dokumentkopie im Bestand an.

## EPUB

EPUB wird ohne Entpacken in das Dateisystem verarbeitet. XML wird mit `defusedxml` geparst. Publisher-HTML, CSS und Skripte werden nicht direkt ausgeführt; Kapitel werden als bereinigter, reflow-fähiger Text neu aufgebaut. Externe Ressourcen werden nicht geladen.

Sicherheitsgrenzen:
- maximal 100 MiB Archivgröße
- maximal 5.000 ZIP-Einträge
- maximal 20 MiB pro Eintrag
- maximal 200 MiB deklarierte entpackte Gesamtdaten
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
