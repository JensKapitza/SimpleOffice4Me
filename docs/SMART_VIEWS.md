# Smart Views und Sammelordner

## Dynamische Ordneransichten

Die Dokumenteinstellungen können Ordner anhand der aktuellen Dokumentpfade
dynamisch anzeigen. Die Ansicht erzeugt für jeden gefundenen Ordner keinen
persistenten Sammeldatensatz. Gleichnamige Ordner aus verschiedenen Pfaden
bleiben getrennt; sie werden nur auf ausdrückliche Bestätigung logisch
zusammengeführt.
Namenskollisionen werden serverseitig nach Unicode-NFC-Normalisierung und
ohne Beachtung der Groß-/Kleinschreibung bestimmt.

Smart-View-Notizen, Zuordnungen und Extraktionsregeln werden pro angemeldetem
Benutzer unter `.simpleoffice-meta/smart-folder-views/` gespeichert. Ein
Ordner muss ausdrücklich aktiviert werden, bevor eigene Notizen oder Regeln
dauerhaft gespeichert werden. Dokumente lassen sich auch einer bereits
entdeckten, noch nicht aktivierten Ansicht logisch zuordnen. Eine logische
Zuordnung ändert den Originalpfad und die Dokument-ID nicht; physisches
Verschieben bleibt eine getrennte Aktion.

Beim Web-Ordnerimport erhalten Dateien zusätzlich eine Herkunfts-ID für den
Import und relativen Quellordner. Die ID bleibt als Dokumentmetadatum erhalten;
die Herkunfts-Smart-View bleibt daher auch nach einem physischen Verschieben
auffindbar. Unabhängige Importvorgänge bekommen getrennte IDs und werden nicht
automatisch fusioniert.

Aktivierte Sammelordner können Aufgaben über den bestehenden `TodoStore`
erhalten. Aufgaben referenzieren die stabile Sammelordner-ID; bei einer
logischen Fusion bleiben Quell-IDs als Aliase dem Ziel zugeordnet, sodass
Aufgaben nicht umgeschrieben oder verloren werden.

## Explizite Fusion

Vor einer Fusion zeigt die Vorschau Quell- und Zielpfad, Mitglieder, Notizen,
Aufgabenzahlen, Regeln und Regelkonflikte. Die Aktion verlangt die Eingabe
`ZUSAMMENFUEHREN`. Originaldateien werden dabei nicht verschoben. Zuordnungen,
Notizen mit Herkunft und kompatible Regeln werden atomar gespeichert;
konfligierende Regeln blockieren die Fusion. Der bisherige Quellpfad wird als
Alias auf das Ziel abgebildet, sodass auch später dort abgelegte Dokumente in
der Zielansicht erscheinen. Das Auditereignis (Benutzer, Quelle, Ziel,
Dokumentanzahl und SHA-256-Fingerabdruck der Dokument-IDs) wird atomar mit der
Änderung gespeichert. Das Protokoll ist auf 10.000 Ereignisse begrenzt; danach
wird eine weitere Fusion sicher abgewiesen, statt ältere Auditdaten zu löschen.

## Begrenzte strukturierte Auswertung

Regeln lesen einzelne JSON-Schlüssel, CSV-Spalten oder einfache XML-Elementpfade
aus verifizierten Dokumenten. Es werden keine Ausdrücke, Skripte oder
benutzerdefinierten Regex ausgeführt. XML wird mit `defusedxml` geparst;
DTD-/Entity-Inhalte werden abgewiesen. Die Auswertung begrenzt Eingaben auf
100 Dateien, 256 KiB je Datei und 8 MiB je Anforderung, höchstens 20 Regeln und
500 Zeichen je Ergebniszelle.

Der CSV-Export escaped Formelzellen, wird als `text/csv` mit `nosniff` und
`Cache-Control: private, no-store` ausgeliefert und enthält nur die
Dokumente/Ansichten des angemeldeten Benutzers.

Aktivierte Sammelordner können 1, 4 oder 6 vorhandene Dokumente für eine
persistente Vorschauraster-Auswahl markieren. Die Auswahl referenziert nur
Dokument-IDs; Vorschaudateien bleiben die bestehenden hashgebundenen privaten
Dokument-Caches. Die Auswahloberfläche zeigt höchstens die ersten 200 Mitglieder.

Für ausgewählte Mitglieder kann die optionale V3-Warteschlange Vorschauen und/
oder OCR/Texterkennung neu erzeugen. Jobs sind pro Benutzer auf zwei aktive und
50 neue Aufträge pro Tag, global auf 500 aktive und 1.000 neue Aufträge pro Tag
begrenzt; pro Auftrag sind höchstens 100 Dateien, 128 MiB je Datei und 512 MiB
insgesamt zulässig. Der Status zeigt abgeschlossene/gesamte Dateien; fertige
Jobdatensätze älter als 30 Tage werden bei einer späteren Einreihung bereinigt.
Der Dienst ist standardmäßig deaktiviert:
`SIMPLEOFFICE_V3_JOBS_ENABLED=1` setzen und den Worker mit
`python -m tools.v3_job_worker --root <DOCUMENT_ROOT>` starten.

## Grenzen dieser Ausbaustufe

Die Fusion ist eine transaktionale **logische** Zuordnung und verschiebt keine
Originaldateien. Batch-Aktionen verwenden die optionale Hintergrundwarteschlange;
ohne aktivierten und gestarteten Worker bleiben Aufträge deaktiviert bzw. in der
Warteschlange. Native Geräteabnahme erfolgt separat nach Issue #823.
