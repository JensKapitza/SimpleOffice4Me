# SimpleOffice4Me · Agentenaufträge

## Ausgangspunkt am 18. November 2026

Basis ist `main@e5088f18`. Bei der Vergabe lieferte `gh pr list --limit 100` keine offenen PRs. Der Arbeitsbranch heißt `improve/agenten-mesh`. Der Integrator führt die Nachweise hier zusammen und verantwortet den gemeinsamen PR.

Das Inventar umfasst 4.760 versionierte Dateien, darunter 627 unter `app/`, 224 Templates, 732 Testdateien und 2.664 statische Dateien. Die Sichtung über das Repository umfasst Struktur, Dateigrößen, lokale Webressourcen und Testlandschaft. Die fachliche Tiefenprüfung dieser Runde konzentriert sich auf Dokumente, Lesezeichen und Chat. Native Clients, Installationsskripte und weitere Fachmodule sind im Inventar erfasst. Ihre Laufzeitfunktionen sind in dieser Runde nicht abgenommen.

Die zusätzliche Python-Strukturanalyse lief mit `ast.parse` über alle 1.421 Python-Dateipfade der Basisrevision im bearbeiteten Worktree. 1.420 Dateien ließen sich mit Python 3.12 parsen. 35 davon enthalten mindestens einen Sternimport. Eine mitgelieferte Datei, `libs/jbig2enc/pdf.py`, verwendet Python-2-Printsyntax. Vor einer Änderung ist zu klären, ob Packaging oder OCR-Werkzeuge dieses Drittanbieterskript ausführen. Der Parserbefund allein belegt keinen Fehler im laufenden OCR.

## Vergabe und Reihenfolge

|Priorität|Paket und Zuständigkeit|Arbeitsgrenze und Abnahmekriterium|
|---|---|---|
|P1|Dateien · mesh_files|Eine nachgewiesene Schwachstelle im Dateiablauf beheben. Zugriffsgrenzen und vorhandene Datenformate erhalten. Wirkung am regulären Einstieg testen.|
|P1|Links · mesh_links|Einen belegten Fehler in Lesezeichen oder Schnellfreigabe beheben. Gültige Eingaben und Fehlerpfad über die Route testen.|
|P1|Dokumentvorschau · mesh_arch|Lebensdauer temporärer Dateien bei Fehlern und erfolgreicher Ausgabe prüfen. Ausschließlich Vorschau-Helfer bearbeiten, mit Dateipaket abstimmen.|
|P2|Handy-Chat · mesh_chat|Einen konkreten mobilen Bedienfehler beheben und auf schmalem Bildschirm prüfen. Bestehende Nachrichtenaktionen erhalten.|
|P1|Unabhängiges Review · mesh_qa|Grenzfälle und Zugriffsrechte prüfen, Änderungen gegenlesen, Gegenbefunde an den jeweiligen Autor geben. Keine parallelen Produktänderungen.|

Die Implementierungsrollen laufen mit `gpt-6-luna`. Der Integrator prüft Dateigrenzen, übernimmt die gemeinsame Dokumentation und testet den kombinierten Stand. Vor jeder Folgevergabe sind offene PRs und geänderte Pfade erneut abzugleichen. Bei Überlappung übernimmt ein Autor den betreffenden Code, der zweite prüft dessen Änderung.

## Ablauf für jeden Auftrag

1. **Analyse** – Einstieg, Ausgangscommit, Ist-Verhalten und reproduzierbares Beispiel mit synthetischen Daten festhalten.
2. **Planung** – Soll-Verhalten, betroffene Dateien, Abhängigkeiten, Risiken und Abnahmetests vereinbaren.
3. **Implementierung** – Zwischenstand bei eingegrenzter Ursache und nach dem Fix dokumentieren. Auswirkungen auf bisherige Nutzer und Daten angeben.
4. **Tests** – Unit-, Integrations- und Regressionsebene dem Szenario zuordnen. Befehle, Ergebnis und ausgelassene Fälle festhalten. Bei UI-Änderungen zusätzlich Browser, Viewport, Interaktion und Ergebnis dokumentieren.
5. **Soll-Ist-Vergleich** – Für jedes Abnahmekriterium erwartetes und beobachtetes Verhalten gegenüberstellen. Fehlende Nachweise bleiben offen.
6. **Review** – Unabhängigen Gegenbefund oder Freigabe mit geprüftem Umfang vermerken. Nach Änderungen die betroffenen Tests wiederholen.
7. **Abschluss** – Integrator aktualisiert dieses Register mit PR, verbleibenden Einschränkungen und Folgeauftrag. Ein Merge setzt die Prüfung des gemeinsamen PR-Stands voraus.

Jede Übergabe enthält Paket-ID, Status, Ist, Soll, Dateien, Schritte, Zwischenstände, Testbefehl und Ergebnis, Soll-Ist-Vergleich und Reviewentscheidung. Ergebnisse werden vor PR-Eröffnung unten zusammengeführt.

## Weitere Befunde

- **Architektur** · `app/documents.py` spiegelt Modulnamen einschließlich importierter Helfer per verschachtelter Schleife zwischen elf Routenmodulen. Vor einem Umbau direkte modulübergreifende Aufrufe und Test-Patchpunkte inventarisieren. Abnahme über identische Flask-Endpunkte, Methoden und bisherige Importpfade.
- **Webressourcen** · `python tools/audit_legacy_frontend.py` meldet 1.745 ältere Assets und einen textuellen Verweis im Größenprüfer. Vor einer Bereinigung dynamische URLs, Offline-Pakete und native Clients untersuchen. Abnahme über Paketgrößenvergleich und offline geladene Seiten mit funktionierenden Icons. Aus der Textsuche allein folgt keine Löschfreigabe.
- **Mobile Regressionen** · `tests/test_mobile_viewport.py` prüft unter anderem CSS- und JavaScript-Text für Tastaturabstand und Zurück-Navigation. Eine Interaktionsprüfung mit geöffnetem Editor und Wechsel der Ausrichtung ergänzt diese Verträge. Android-WebView und iOS benötigen eigene Nachweise.

## Arbeitsnachweise

### P2 · QR-Inhalt auf dem Handy manuell kopieren · 19. November 2026

| Phase | Zuständigkeit und Ergebnis |
|---|---|
| Analyse | `static/js/quick_share.js` versuchte bei fehlender oder abgelehnter Zwischenablage, Text innerhalb eines geschlossenen Details-Elements zu markieren. Der Text blieb dabei verborgen. Wiederholtes Tippen konnte die Erfolgsbeschriftung dauerhaft am Button belassen. |
| Planung | GPT-6 Luna (`filter_impl`) übernimmt Skript und Template, GPT-6 Luna (`filter_review`) prüft die Abläufe unabhängig im Browser. Der Integrator kontrolliert Texte, Bedienbarkeit ohne Tastatur und bestehende QR-Routen. Die Dateien überschneiden sich mit keinem anderen Produktpaket. |
| Implementierung und Zwischenstand | Die erste Fassung wies nur auf Tastenkürzel hin und entfernte die Meldung zeitgesteuert. Nach dem Review bleibt die Handlungsanweisung sichtbar und nennt auch das mobile Auswahlmenü. Das Skript öffnet den QR-Inhalt und markiert den Text. Erfolg wird erst nach erfolgreichem Schreiben der Zwischenablage gemeldet. Der Aktionsbutton behält seinen Namen. |
| Tests und Soll-Ist-Vergleich | Auf der angemeldeten QR-Seite in Chromium bei 360 × 800 und 1280 × 900 wurde ein mehrzeiliger Text mit Umlaut und Sonderzeichen erzeugt. Bei simulierter erfolgreicher Clipboard-API kamen die exakten Zeichen auch beim zweiten Tippen an. Bei fehlender API und abgelehntem Schreibzugriff waren der Inhalt aufgeklappt, der Text vollständig ausgewählt und die manuelle Anweisung sichtbar. |
| Abschluss | Vier kombinierte Browser- und Routenprüfungen bestanden, einschließlich temporärer QR-Inhalte, History, Benutzergrenzen, Kalendereinträgen und CSRF. Auf einem physischen Gerät ist zusätzlich zu prüfen, ob das native Auswahlmenü den markierten Text in eine andere App übernimmt. |

### P2 · Lesezeichen am Handy bedienen

| Phase | Zuständigkeit und Ergebnis |
|---|---|
| Analyse und Plan | Der Integrator grenzte den Auftrag auf die vorhandene Lesezeichenliste ein. Ihre Überschrift und Suchmaske teilten sich auch auf schmalen Geräten dieselbe Zeile. Kleine Aktionsschaltflächen erschwerten das Antippen. Die bestehenden Speicher- und Importfunktionen sowie die Leserechte bleiben Teil des regulären Bedienwegs. |
| Implementierung | Luna-Agent `links_mobile` ergänzte Klassen in `templates/bookmarks/index.html` und die seitenspezifische `static/css/bookmarks.css`. Auf Handybreite stehen Überschrift und Suche untereinander. Such- und Listenaktionen erhalten mindestens 44 CSS-Pixel Höhe. Lange Ordner und Tags können umbrechen. |
| Test und Review | Luna-Agent `links_browser` prüfte den angemeldeten Ablauf in Chromium mit synthetischen Lesezeichen; der Integrator prüfte zusätzlich die bestehenden Routentests für private Einträge, sichere URLs und die Firefox-Importvorschau. |
| Soll-Ist-Vergleich und Abschluss | Der Browsertest legte bei 360 × 800 ein Lesezeichen mit langer URL und langen ungetrennten Ordner- und Tagwerten an. Suchen, Bearbeiten, Anheften und Löschen funktionierten per Touch. Die geprüften Schaltflächen waren mindestens 44 px hoch und die Liste blieb ohne horizontalen Überlauf. Die angemeldete Ansicht wurde zusätzlich bei 1280 × 900 geöffnet. Die erste Testfassung erwartete deutsche Schaltflächentexte bei englischer Standardsprache; der isolierte Test setzt die Sprache jetzt ausdrücklich. |
| Folgeauftrag | Auf einem physischen Handy mit geöffneter Bildschirmtastatur prüfen, ob das Suchfeld nach Rückkehr aus der Bearbeitung sichtbar und die letzte Suchanfrage bequem wieder erreichbar ist. |

### P1 · Lesezeichensuche · mesh_links

**Ist und Soll.** Die Suche behandelte Prozentzeichen und Unterstriche als SQL-Platzhalter. Wer etwa nach `100%` sucht, soll nur eigene Lesezeichen mit dieser Zeichenfolge finden. **Umsetzung.** Der Agent hat in `app/browser_bookmarks.py` die Suchzeichen einschließlich des Escapezeichens maskiert. Titel, URL, Ordner und Tags behalten ihre Teilstringsuche. Im Review wurde der Test um kombinierte Sonderzeichen und einen zweiten Benutzer erweitert. Der Flask-Routentest verwendet die echte Datenbankabfrage und prüft die gerenderte Ergebnisliste. Testdatei ist `tests/test_frontend_route_contract.py`.

### Dokumentaktionen · Datei oder logischen Pfad senden

| Phase | Arbeitsstand und Nachweis |
| --- | --- |
| Analyse | Dokumente hatten Freigabelinks, der Chat bereits einen Dokumentanhang. QR und Ton waren nur über die separate Kurzfreigabe erreichbar. |
| Planung | Vier GPT-6-Luna-Agenten übernahmen die Dokumentroute mit QR/Ton, Chatintegration, Dateipaket mit Empfang und mobile Browserprüfung. Die Koordination übernahm Dokumentliste, Detailansicht, Größen- und Daueranzeige, LAN-Adresse und Review. |
| Implementierung | Eine gemeinsame Senden-Seite bereitet Pfad, Datei oder authentifizierten Link auf. Kleine Dateien enthalten Name, Bytezahl, SHA-256 und Inhalt. Im Chat wird der Pfad als Text gesendet oder das Dokument als Anhang referenziert. |
| Tests | Tests prüfen Dateipaket und Hashfehler, veraltete Ergebnisse asynchroner Prüfung, FEC-Dauer und Bytebudget, Dateidownload, Zugriff nach Rechteentzug, LAN-Proxy mit IPv6, QR-/Ton-Fallback sowie Chat ohne und mit Anhang. Der mobile Chromium-Lauf deckt die Dokumentaktionen ab. |
| Soll-Ist-Vergleich | Für QR stehen bis zu 1.100 Dateibytes zur Verfügung. Für Ton muss das gesamte Paket in 128 UTF-8-Bytes passen. Sobald es zu groß ist, zeigt die Oberfläche den Wechsel zum geschützten Download-Link an. Bei überlangen Adressen bleibt die Größenbegrenzung sichtbar. |
| Review | Die Koordination hat den expliziten Ordnerrechtecheck im Legacy-Speicherpfad, die erneute Berechtigungsprüfung beim Chatdownload und den Bezug der Geräteadresse aus dem laufenden LAN-Proxy geprüft. Die Vorbereitung erzeugt keine öffentliche Dokumentfreigabe. |
| Abschluss | Arbeitsumfang und Bedienung stehen in der Dokumentation zur schnellen Dateiansicht. Die Hardwareprüfung über Lautsprecher und Mikrofon zweier physischer Geräte bleibt offen. |

### P1 · Dokumentvorschau · mesh_arch

**Ist und Soll.** Ein Fehler beim Kopieren in den temporären Vorschauspeicher konnte die Datei bis zur Speicherbereinigung offen lassen. Die Antwort soll bei Erfolg bis zum Ende lesbar bleiben und bei einem Fehler die Ressource sofort freigeben. **Umsetzung.** Die Fehlerbehandlung in `_verified_preview_response` umfasst jetzt das Kopieren und den gesamten Antwortaufbau. Der Regressionstest provoziert einen Kopierfehler, kontrolliert das Schließen und das Weiterreichen des Fehlers. Im HTTP-Integrationstest wird die erfolgreiche Antwort explizit geschlossen. Der kombinierte Lauf von `tests.test_verified_preview_cleanup` und `tests.test_v2_document_preview_storage` bestand alle drei Tests einschließlich Cache- und Inhaltsprüfung.

### P2 · Chat lesen und Raumdaten laden · 19. November 2026

| Phase | Arbeitsstand und Nachweis |
| --- | --- |
| Analyse | Beim Lesen weiter oben fehlte ein Hinweis auf neu eingetroffene Nachrichten. Anhänge, Zustellungen, Reaktionen und Rücknahmen wurden pro Nachricht geladen. |
| Planung | Fünf GPT-6-Luna-Agenten arbeiteten parallel. `filter_impl` betreute die Oberfläche, `filter_review` die Polling-Regressionen und `filter_mobile` den mobilen Ablauf. `chat_batch` übernahm Anhänge und Zustellungen, `chat_reactions` die Dekoration der Nachrichten. Jeder Testbereich hatte einen eigenen Dateipfad. |
| Implementierung | `chat_room.js` erkennt hinzugekommene Nachrichten-IDs. Vorlage und CSS platzieren einen antippbaren Hinweis im Nachrichtenbereich. `chat_store.py` und `chat_features.py` laden Metadaten für Gruppen von bis zu 900 IDs und erhalten die Reihenfolge der Nachrichten. |
| Tests | `python -m unittest discover -s tests -p 'test_chat*.py'` bestand mit 60 Tests. Der Chromium-Test bei 390 × 844 prüft eine echte eingehende Nachricht, die Leseposition, die Erreichbarkeit des Hinweises und den Sprung ans Ende. Fünf Node-Pollingtests prüfen unter anderem reine Reaktionsänderungen. SQLite-Tests prüfen die Gruppengrenze, Raumzuordnung, Reihenfolge und Abfrageanzahl. |
| Soll-Ist-Vergleich | Am Ende folgt die Ansicht neuen Nachrichten automatisch. Weiter oben bleibt die gemessene Scrollposition erhalten und der Hinweis führt zur neuen Nachricht. Bei 300 Nachrichten sank die Zahl der SELECTs für `messages` mit anschließendem `decorate` von 1.203 auf 6 (gleicher Testraum, ohne Anhänge oder Reaktionen). Gemessen wurde die Abfrageanzahl, die Ladezeit ist offen. |
| Review | Die Koordination führte die SQL-Platzhaltererzeugung zusammen und prüfte die Bindung der IDs. Bandit, Ruff und Projektprüfungen bestanden. Der mobile Test prüft nach dem Review auch die Lage der Schaltfläche und mindestens 44 Pixel Höhe. |
| Abschluss | Lokal getestet und zum PR-Review vorbereitet. Folgepaket für die mobile QA ist die sichtbare Nachricht beim Herausfallen der ältesten Zeile aus dem 300er-Fenster. Praxistests mit Android- und iOS-Tastaturen stehen aus. |

### P2 · Leseposition beim Weiterschieben des Chatfensters

| Phase | Aufgabe und Ergebnis |
| --- | --- |
| Analyse | Bei 300 geladenen Nachrichten verdrängt Nachricht 301 die älteste Zeile. Im mobilen Chromium-Test wanderte Nachricht 150 auf dem bisherigen Stand von 24 px auf −320 px relativ zum Nachrichtenbereich. |
| Planung | Drei GPT-6-Luna-Agenten arbeiteten in getrennten Dateien. `anchor_impl` übernahm die Polling-Logik, `anchor_unit` die DOM-Regression und `anchor_test` den mobilen Integrationstest. Ziel war dieselbe sichtbare Nachricht an derselben Stelle im Verlauf. |
| Implementierung | `static/js/chat_room.js` merkt sich unmittelbar vor der Aktualisierung die Position der sichtbaren Nachrichten. Nach dem Ersetzen des Verlaufs gleicht der Chat den Versatz der ersten noch vorhandenen Nachricht aus. |
| Tests | `tests/test_chat_room_polling.mjs` prüft auch den Wegfall der ersten sichtbaren Nachricht. `tests/test_chat_room_styles_browser.py` prüft bei 390 × 844 Pixeln über den echten Abruf, dass die älteste Zeile verschwindet, die neue erscheint und die gelesene Nachricht an ihrer Position bleibt. |
| Soll-Ist-Vergleich | Der Browserfall reproduzierte vor der Korrektur 344 Pixel Versatz. Mit Korrektur hält er die Toleranz von zwei Pixeln ein. Ist keine zuvor sichtbare Nachricht mehr vorhanden, fehlt ein gemeinsamer Bezugspunkt. |
| Review | Die Koordination prüfte den Browserfall gegen beide Implementierungen und vereinfachte die Suche. Die Geometriemessung endet unterhalb des sichtbaren Bereichs. Wer am Ende liest, folgt weiter den neuesten Nachrichten. |
| Abschluss | Lokaler Regressionstest bestanden. Offene Geräteprüfung sind weiterhin die Bildschirmtastaturen auf Android und iOS. |

### P2 · Handhabung des Chats · mesh_chat

**Ist und Soll.** Das Nachrichtenfeld beanspruchte mindestens 18 rem, selbst auf kurzen Handybildschirmen. Der Senden-Knopf konkurrierte mit Dateiauswahl und Status um dieselbe Zeile. Die Höhe soll sich der verfügbaren Fläche anpassen und Senden mit dem Daumen erreichbar bleiben. **Umsetzung.** `templates/chat/room.html` lädt die raumspezifische `static/css/chat.css`. Auf schmalen Bildschirmen erhalten Senden und Dateistatus eigene Zeilen. Im unabhängigen Review fiel auf, dass die Mindesthöhe bei geöffnetem Editor die Maximalhöhe überschreiten konnte. Bei der Nachprüfung mit 375 × 300 CSS-Pixeln ergaben sich 90 px Mindest- und 114 px Maximalhöhe. Physische Bildschirmtastaturen bleiben eine eigene Prüfung.

### P2 · Folgeauftrag Chat-Aktualisierung · mesh_chat

**Ist und Soll.** Der 2,5-Sekunden-Timer konnte bei langsamer Verbindung weitere Requests starten. Zwischen Anfrage und Antwort konnte ein Leser nach oben scrollen und anschließend ans Ende zurückspringen. **Umsetzung.** `static/js/chat_room.js` lässt pro Raumseite eine laufende Abfrage zu, gibt die Sperre im Fehlerfall frei und prüft die Leseposition unmittelbar vor dem Ersetzen der Nachrichten. **Soll-Ist.** Drei ausführbare JS-Regressionen prüfen Überschneidung, Wiederholung nach Netzwerkfehler und Scrollen während der Antwort. Alle drei bestanden mit `node --test tests/test_chat_room_polling.mjs`. Die Python-Suite bindet sie in die reguläre Testentdeckung ein.

### Nächste Vergabe

|Priorität|Auftrag|Abhängigkeit und konkreter Abnahmetest|
|---|---|---|
|P1|Dokumentsuche · Datei-Agent nach Integration|Die separate Methode `search_page` filtert Treffer nach dem SQL-Seitenschnitt. FTS und SQL-Fallback mit verdeckten Treffern vor und nach sichtbaren Treffern prüfen. Nur sichtbare Treffer dürfen den Weiter-Link auslösen.|
|P2|Native Tastatur · Mobile-QA|Nach dem Chat-Browserlauf auf Android und iOS eine Nachricht bei geöffneter Tastatur und nach Drehung senden. Ausrichtung, Gerät und tatsächlich beobachteten Fokus festhalten.|
|P3|Routenarchitektur · Architektur-Agent|Zuerst die öffentlichen Importpfade und Routen von `app/documents.py` als Vertrag erfassen. Erst danach eine fachlich zusammenhängende Modulgruppe entkoppeln.|
|P3|Offline-Assets · Packaging-Agent|Vor einer Änderung Offline-Pakete, App-Ressourcen und dynamische Assetpfade gegen die oben ermittelte Liste prüfen. Mit installierter Anwendung ohne Netz testen.|

### P1 · Sichtbare Dateien in Übersicht und Eingang · mesh_files

**Ist und Soll.** Die Übersicht grenzte zunächst die rohe Katalogseite ein und entfernte danach verborgene Einträge. Eine Seite konnte dadurch leer sein, obwohl weitere sichtbare Dokumente existierten. Der Eingang benutzte einen eigenen Listenpfad. Beide Ansichten sollen Rechte bei der Seiteneinteilung berücksichtigen.

**Zwischenstände und Review.** Die erste Korrektur füllte Lücken, übernahm aber die rohe Gesamtzahl. Der unabhängige Prüfer bemerkte, dass die Anzeige dadurch Rückschlüsse auf verborgene Dokumente zuließ. Eine zweite Fassung zählte alle sichtbaren Einträge. Der Integrator beanstandete dafür einen vollständigen Metadaten-Durchlauf bei jeder Anfrage. In der überarbeiteten Fassung lesen Übersicht und Eingang über einen gemeinsamen Helfer in `app/document_store.py` bis zum nächsten sichtbaren Eintrag hinter der angeforderten Seite. Auf tiefen Seiten sind weiterhin die davorliegenden Berechtigungen zu prüfen.

**Soll-Ist.** Das Template `templates/documents/index.html` zeigt auf Zwischenseiten den sichtbaren Bereich. Eine Gesamtzahl erscheint erst, wenn sie ohne weiteren Durchlauf feststeht. `tests.test_document_store` und `tests.test_index_performance` bestanden zusammen 55 Tests, einschließlich sichtbarer Folgeseiten und verborgener Einträge am Ende der Liste. Ohne Anfragekontext bleibt die Katalogzählung für interne Aufrufer erhalten.

### Gemeinsamer Prüflauf und Freigabestand

- Der Integrator hat die Dokumentlisten-, Vorschau-, Chat-, Viewport- und Lesezeichentests gemeinsam ausgeführt. `python -m unittest tests.test_document_store tests.test_v2_document_preview_storage tests.test_verified_preview_cleanup tests.test_chat_rich_features tests.test_mobile_viewport tests.test_chat_room_styles_browser tests.test_frontend_route_contract.FrontendNavigationSmokeTests.test_bookmark_search_treats_like_wildcards_as_literal_text` ergab **58 bestanden**. Die Node-Tests sind in diesem Lauf über die Python-Suite eingebunden.
- Der Folgeauftrag an mesh_arch prüfte die lokal geladene Chat-CSS und das tatsächliche Senden in einem angemeldeten Raum mit Chromium bei 390 × 844. Die Nachricht erschien nach dem Senden im Verlauf. Der Desktopvergleich lief bei 1365 × 900. Den realen Geräteabstand am unteren Displayrand konnte die Emulation nur mit 0 px prüfen.
- `python -m unittest tests.test_frontend_route_contract -v` ergab **21 bestanden**. Playwright protokollierte beim Beenden einzelner Verbindungen Warnungen. Der separate Browserlauf mit System-Chromium konnte den Chat dennoch über HTTP bedienen.
- Projekt-Policy, Datei- und Funktionsgröße, CRA-Prüfung und die betroffenen Python-Dateien mit Ruff wurden lokal geprüft. Gesamt-CI und Freigabe erfolgen auf dem PR. Für diese Runde wurde kein Merge ausgelöst.
- Der separate Unit-Test `python -m unittest tests.test_browser_bookmarks` bestand für `%`, `_`, `!` und ihre Kombination. Der unabhängige Prüfer hat die endgültige Abbruchbedingung für beide Dokumentlisten, die Sichtbarkeitsfilter und den mobilen CSS-Grenzfall kontrolliert. Er meldete keinen lokalen Freigabeblocker. Sehr hohe angeforderte Seitennummern können weiterhin einen Durchlauf bis zum Ende des Katalogs erfordern.
- Gemeinsamer Änderungsstand und laufende CI sind in [PR #1080](https://github.com/JensKapitza/SimpleOffice4Me/pull/1080) einsehbar.
- Mobile Abschlussprüfung der Dateiverwaltung mit `python -m unittest tests.test_document_list_pagination_browser` bestanden. Im echten Flask-Frontend bei 375 × 812 sah ein normaler Benutzer von 120 Testdateien in Übersicht und Eingang jeweils den Bereich 1–100. Nach Antippen von „Weiter“ erschien der Bereich 101–118 mit insgesamt 118 sichtbaren Dateien. Beide privaten Chat-Dateien blieben verborgen. Der Test prüft auch horizontalen Überlauf.

## Folgeauftrag · Sichtbare Suchtreffer · 18. November 2026

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Auf Basis von `main@92a24452` geprüft. Die bisherige Suche wandte die Rechteprüfung erst auf eine fertige Indexseite an. Auch die Schnell- und Projektsuche verwenden diesen Einstieg. Der vorherige PR #1080 war nach erfolgreichen Pflichtprüfungen gemergt. |
| Planung | `search_impl` übernimmt die Store-Implementierung, `search_test` eine separate Regression mit privaten Chat-Dokumenten, `search_review` die unabhängige Prüfung. Alle drei arbeiten mit GPT-6 Luna. Der Integrator prüft den echten Handy-Browserablauf. |
| Implementierung | Sichtbarkeit vor dem Seitenschnitt prüfen und beim ersten zusätzlichen sichtbaren Treffer stoppen. Die Zwischenfassung verwendete mehrere Indexabfragen mit Offset. Im Review wurde ein gemeinsamer Cursor für die Dauer einer Suche gefordert, damit der Index innerhalb eines Aufrufs konsistent gelesen wird. |
| Tests | `python -m unittest tests.test_document_search_visibility tests.test_document_store tests.test_index_performance tests.test_search_query tests.test_document_list_pagination_browser`. Enthalten sind private Chat-Dokumente zwischen sichtbaren Treffern, eine leere Suche, eine extrem hohe Seitennummer, XOR und eine SQLite-Tabelle ohne FTS. |
| Soll-Ist-Vergleich | In Chromium bei 375 × 812 wurden alle 118 sichtbaren Dateien aus 120 Testdateien über die regulären Weiter-Links gefunden. Die Seiten enthielten 25, 25, 25, 25 und 18 Treffer. Beide privaten Dateien blieben verborgen. Auf der letzten Seite gab es keinen Weiter-Link. |
| Review | Der unabhängige Prüfer kontrollierte Berechtigungen, Speicherverbrauch und beide Abfragepfade. Die Endfassung sortiert explizit nach Index-Zeilen-ID. Ohne Benutzerkontext bleiben Limit und Offset in SQLite. Bei bestehenden Indizes ohne FTS ist der SQL-Ersatz separat getestet. |
| Abschluss und Folgearbeit | Für tiefe Seiten muss die Suche die Leserechte der vorangehenden Treffer erneut prüfen. Ein Folgeauftrag für Performance-Messungen soll abhängig vom Anteil privater Dokumente die Metadatenzugriffe und Laufzeiten auf Seite 1 und auf tiefen Seiten vergleichen. |

Der kombinierte lokale Lauf bestand alle 72 Tests. Ruff, Projekt-Policy, Datei- und Funktionsgrößen, CRA und `git diff --check` waren erfolgreich.

## Folgeauftrag · Kosten der sichtbaren Suchseiten · 18. November 2026

**Analyse.** Die Suche liest Metadaten zu jedem Indexkandidaten, bis der erste weitere sichtbare Treffer feststeht. Für eine tiefe Seite müssen auch die vorangehenden sichtbaren Treffer gezählt werden. Die Zahl der Dateizugriffe hängt daher von den Berechtigungen und der Trefferreihenfolge ab.

**Planung und Zuständigkeiten.** Basis ist der Suchfix `1ab113a3` aus PR #1081. `search_impl` übernimmt das reproduzierbare Messwerkzeug, `search_test` die unabhängigen Zugriffs- und Ergebnisprüfungen, `search_review` das methodische Review. Alle drei Agenten verwenden GPT-6 Luna. Der Integrator führt die Messung aus und gleicht die Ergebnisse mit den Regressionstests ab.

**Implementierung und Review.** Das Werkzeug `tools/benchmark_document_search.py` erzeugt je Berechtigungsverteilung einen temporären SQLite-Index und synthetische Dateien. Es misst den Store-Aufruf im Flask-Anfragekontext eines normalen Benutzers. Der Zähler erfasst Aufrufe zum Lesen der JSON-Dokumentmetadaten. Physische Datenträgerzugriffe können wegen Betriebssystem-Caches abweichen. Beim ersten Probelauf fiel ein Importfehler auf. Ein eigener CLI-Integrationstest prüft jetzt den vollständigen Prozess, die JSON-Ausgabe und die Isolation vom konfigurierten Dokumentordner.

**Soll-Ist-Vergleich.** Lokaler Lauf mit `python -m tools.benchmark_document_search --documents 200 --page-size 10 --hidden-fractions 0,0.5,0.9 --pages 1,8 --repeats 5`. Alle Dokumente erfüllten die Suchanfrage. Private Chat-Dateien waren gleichmäßig verteilt. Die Zahl der Metadatenzugriffe war in allen fünf Wiederholungen identisch.

| Private Treffer | Seite 1 · Zugriffe / mittlere Dauer | Seite 8 · Zugriffe / mittlere Dauer | Sichtbare Ergebnisse auf Seite 8 |
|---|---:|---:|---:|
| 0 % | 11 / 0,96 ms | 81 / 3,24 ms | 10 |
| 50 % | 22 / 1,22 ms | 162 / 5,25 ms | 10 |
| 90 % | 110 / 3,46 ms | 200 / 5,96 ms | 0 |

Bei 90 % privaten Treffern gibt es insgesamt nur zwei sichtbare Seiten. Die direkt angeforderte Seite 8 erschöpft den Index. Die Zeiten betreffen einzelne lokale Suchaufrufe mit Messzähler. Aufbau, HTTP-Rendering, Netzwerklatenz und gleichzeitige Anfragen sind ausgeklammert.

**Tests und Abschluss.** Die Regressionen prüfen Seiteninhalte und den Abbruch nach dem nächsten sichtbaren Treffer bei 10, 50 und 90 % privaten Dokumenten. Die Laufzeit ist umgebungsabhängig. Als nächstes lässt sich ein Fortsetzungscursor für den regulären Weiter-Link bewerten. Abnahmekriterium wäre, dass Seite 2 bereits gezählte Treffer nicht erneut liest und nach einem Rechteentzug weiterhin ausschließlich aktuell sichtbare Dateien liefert.

`python -m unittest tests.test_document_search_cost tests.test_document_search_visibility tests.test_search_query` bestand lokal alle 18 Tests. Ruff, Projekt-Policy, Datei- und Funktionsgrößen sowie `git diff --check` waren erfolgreich.

## Folgeauftrag · Weiterblättern mit aktueller Rechteprüfung

**Analyse und Planung.** Der Code baut auf dem Messpaket in PR #1082 und dem Suchfix in PR #1081 auf. Die Messung mit 50 % privaten Treffern ergab 162 Metadatenzugriffe für den direkten Sprung auf Seite 8. Für den Weiter-Link genügt die Position des letzten ausgegebenen Treffers. Drei GPT-6-Luna-Agenten bearbeiten Store, Regressionstests und unabhängiges Review. Der Integrator übernimmt HTTP-Anbindung, Linkschutz und Browsertest.

**Zwischenstand.** Das Review identifizierte ein Problem beim Bearbeiten eines bereits angezeigten Dokuments. Der Index entfernte seinen Eintrag und legte ihn an einer neuen Position an. Das konnte denselben Treffer auf einer Folgeseite erneut erscheinen lassen. Die Korrektur muss die Position bei gewöhnlichen Metadatenänderungen erhalten. Eine Berechtigung wird bei jeder Anfrage neu geprüft.

**Umsetzung und Soll-Ist-Vergleich.** Der Weiter-Link übergibt eine für eine Stunde gültige verschlüsselte Position, gebunden an Benutzer, Suchtext, Zielseite und Archiv. Bei ungültigem Link greift die herkömmliche Seitennummer. Indexeinträge behalten beim Bearbeiten ihre Position. Bereits ausgegebene Treffer müssen beim Weiterklicken nicht erneut gelesen werden. Auf Seite 8 des Vergleichs mit 200 Dokumenten, zehn Treffern pro Seite und 50 % privaten Dateien ergab der Store **22 statt 162 Metadatenlesevorgänge** bei identischen Treffern. Das Ergebnis gilt für die einzelne Folgeanfrage. Neue Treffer oder gelöschte Dokumente können das Ergebnis zwischen Anfragen verändern.

**Review und Tests.** Die Prüfung umfasste einen Rechteentzug nach Seite 1, geänderte Metadaten eines schon gelesenen Dokuments, native Volltext- und SQL-Abfragen, abgelaufene Links, fremde Benutzer sowie den tatsächlichen Weiter-Link im mobilen Chromium bei 375 × 812.

**Abschlussprüfung.** Der ursprünglich rote SQL-Disjunktionstest hatte nur einen passenden Treffer und forderte deshalb versehentlich wieder die erste Seite an. Die korrigierte Fixture erzeugt einen echten Weiter-Link. Der kombinierte Lauf mit Such-, Store-, Kosten-, Browser- und Indexregressionen bestand alle 83 Tests. PR #1081 und #1082 sind inzwischen in main integriert.

### P2 · Kurznachrichten per Ton übertragen

| Phase | Arbeit und Ergebnis |
| --- | --- |
| Analyse | QR konnte Inhalte teilen. Es fehlten die akustische Übertragung zwischen zwei geöffneten Seiten sowie die Mikrofonfreigabe für den Empfang. |
| Planung | Vier GPT-6-Luna-Agenten übernahmen getrennt PCM-Modem, Fehlerkorrektur, Browsersteuerung und mobilen Integrationstest. Die Koordination integrierte Navigation, Oberfläche und Berechtigungen. Nach Erschöpfung der Agenten-Credits übernahm sie die abschließenden Korrekturen und Tests. |
| Implementierung | Zwei Frequenzbänder, drei Übertragungsmodi und CRC-16 für UTF-8-Nachrichten. Die Aufnahme beginnt auf Tastendruck und endet nach gültiger Nachricht, Abbruch oder Zeitablauf. Die eigene Entwurfsnachricht bleibt beim Empfang erhalten. |
| Tests | Node prüft Hamming-Einzelbitkorrektur, Erkennung zweier Fehler pro Codewort, Mehrheitsentscheidung, CRC, unpassende Einstellungen, UTF-8 und beide Richtungen zwischen 44,1 und 48 kHz. Mobiles Chromium sendet und empfängt eine synthetische Tonfolge über einen Web-Audio-Medienstream. Routentests prüfen die auf die Freigabeseite beschränkte Mikrofonberechtigung. |
| Soll-Ist-Vergleich | Dieselbe Nachricht lässt sich im simulierten Audiokanal mit wählbarer Fehlerkorrektur übertragen. Gerätepaar, Reichweite und Hochtonfilter beeinflussen den Empfang über Luft. Physische Geräte wurden nicht gemessen. |
| Review und Abschluss | Die Koordination begrenzte Nachrichten auf 128 UTF-8-Bytes, korrigierte die Rahmenlänge, prüfte Abtastratenwechsel und begrenzte Kopieraufwand und Ressourcenlebensdauer. Nächster Praxistest ist die Übertragung zwischen Notebook und Telefon in beiden Bändern. |
## 19. November 2026 · Suchfilter beim Verwalten von Lesezeichen

| Phase | Zuständigkeit und Nachweis |
|---|---|
| Analyse | Die POST-Routen für Anlegen, Ändern, Anheften und Löschen leiteten auf die unbeschränkte Liste zurück. Auf dem Handy musste man die Suche nach jedem Schritt wiederholen. PR #1086 und #1087 wurden nach erfolgreichen CI-Prüfungen integriert. Der offene Einkaufslistenentwurf #1085 betrifft andere Dateien. |
| Planung | Drei GPT-6-Luna-Agenten bearbeiten getrennte Bereiche. `filter_impl` übernimmt POST-Routen und Formulardaten, `filter_review` die unabhängigen Flask-Regressionen und `filter_mobile` den Touch-Ablauf. Der Integrator übernimmt Rücksetz-Link, Leerzustand und Gesamtprüfung. |
| Implementierung | Jede Aktion sendet den aktuellen Suchbegriff mit. Das Ziel bleibt die serverseitig erzeugte Lesezeichenroute. GET und POST verwenden dieselben Grenzen für Steuerzeichen und höchstens 200 UTF-8-Bytes. Bei ungültiger Eingabe im Lesezeichen bleibt ein gültiger Filter erhalten. Ohne Filter führen bestehende Clients weiterhin zur vollständigen Sammlung. |
| Zwischenstand | Auch ein korrekt bearbeitetes Lesezeichen kann aus dem Ergebnis verschwinden. Die Ansicht unterscheidet jetzt eine Suche ohne Treffer von einer leeren Sammlung. Über „Suche zurücksetzen“ lässt sich die Sammlung wieder öffnen. |
| Tests und Soll-Ist-Vergleich | Neun kombinierte Tests bestanden. Im mobilen Chromium bei 360 × 800 blieb ein zweites, unpassendes Lesezeichen während Bearbeiten, Anheften und Löschen verborgen. Nach dem Löschen erschien der gefilterte Leerzustand. Zurücksetzen zeigte den zweiten Eintrag wieder. Der Desktopvergleich lief bei 1280 × 900. |
| Review und Abschluss | Die Routenprüfung deckte ungültige URLs, fehlende und überlange Suchbegriffe, mehrbyteige Eingaben und URL-artigen Suchtext ab. Der Redirect blieb innerhalb von /bookmarks/. Der Integrator prüfte außerdem, dass ein aus der Suche heraus umbenannter Eintrag in der gesamten Sammlung erhalten bleibt. Die Prüfung mit physischer Handytastatur steht aus. |

## 19. November 2026 · Kids-Bereich 2–5 Jahre: drei Prioritätsspiele und Kids-Modus

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Drei unabhängige Perspektiven kartierten den Bestand: Routen `app/kids_learning.py` (`/kids/lernen`, `/spielpause`, `/qr`) und `app/kids_coloring.py` (`/kids/coloring` + Drawings-API), Templates `kids/learning.html`, `mini_games.html`, `coloring.html`, `share_qr.html`, JS `kids_learning.js` (971 Z.), `kids_mini_games.js` (301 Z.), `kids_language_audio.js`, `kids_coloring.js`, Vokabular `static/data/kids_language_vocabulary.json` (100 Einträge) und lokale Audio-Clips. Belegte Befunde: B1 Englisch-Fach tot (`englishWordCards` in `kids_learning.js` Z. 248 referenziert, nirgends definiert), B2 leeres Vokabular crasht Deutsch/Mixed, S1 Kleinkind erreicht in 2 Tipps Finanzen/Vault/Logout (volle Nav auf allen Kids-Seiten), S2 Cloud-Stimmen bevorzugt (voiceRank +12 vs. +8 lokal), keine Kids-Tests im Repo. |
| Planung | 2-aus-3-Auswahl belegte: Bestand erweitern statt Parallelsystem, Dashboard als Einstieg, schlanker Fortschritts-Blueprint später. Drei Branches mit disjunkten Scopes: `kids/matching-shapes` (Zuordnung 2–3 J. + Dashboard-Shell + Shared-Assets), `kids/numbers-quantities` (Mengen/Zahlen 4–5 J.), `kids/syllables` (Silben klatschen). Vorgabe: login_required, private/no-store-Header, offline-first ohne Fremd-Requests, Touch-Ziele ≥ 44 px, ohne Lesen bedienbar, kein Zeitdruck, DE+EN nach `g.language`-Muster. |
| Implementierung | PR #1094: Dashboard `/kids` (6 Symbolkarten) + `/kids/zuordnen` (4 Kategorien à 8 Items, Stufen 2–4 Auswahl/6–10 Runden). PR #1095: `/kids/numbers` (zählen, zeigen, mehr/weniger; Stufen bis 3/5/10, je 8 Aufgaben). PR #1096: `/kids/lernen/silben` (+7 Zeilen im Bestands-Blueprint; klatschen per Tipp-Button ohne Mikrofon, zählen 1–4, Reime; je 3 Aufgaben). Koordinator-Fix S2 vor Merge in allen drei Zweigen: On-Device-Stimmen zuerst (24 vs. 6), per Node-Probe verhaltensverifiziert. Glue-Branch: Dashboard-Karten für beide neuen Spiele, Kids-Modus (eigener Header ohne Global-Nav/Export-Toolbar/Befehlspalette auf allen 8 Kinderseiten) plus Erwachsenen-Gate (Rechenaufgabe, Session-Antwort). |
| Tests und Soll-Ist-Vergleich | Soll je Spiel: 5–15 Min., Zufallsaufgaben, Stufen, Sofort-Feedback, ohne Lesen. Ist gemessen: Matching 15/15, Numbers 14/14, Syllables 8/8 Python- + 7/7 Node-Tests; Kids-Modus 8/8 (Containment aller 10 Kids-URLs, Gate richtig/falsch/ohne Frage, Erwachsenenseite behält Nav); kombiniert 29/29 bzw. 23/23 nach Zusammenführung; `node --check`, DOM-Stub-Proben (10 Runden/32 Ansagen bzw. 3×8 Aufgaben), Policy-/Größen-/Compile-Gates grün. CI je PR vollständig grün (36/36 bzw. 35 + Rest) vor Merge; dazwischen nur Ubuntu-Spiegel-503 und ein nicht-blockierender Python-3.15-Budget-Timeout (5924 Tests liefen, alle 8 Kids-Tests darunter). |
| Review und Abschluss | Unabhängiges Review gab alle drei Zweige frei (keine externen Requests, kein Tracking, keine Timer, ermutigendes Feedback, keine Routenkollisionen). Offen als Follow-up: B1/B2-Fixes im Lernspiel, S2-Umsortierung im Alt-Code, Fortschritts-/Eltern-Backend, Offline-Arbeitsblätter/Bastelanleitungen sowie Praxisprüfung auf physischen Geräten (Touch, Tastatur, Lautsprecher/Mikrofon). |

## 19. November 2026 · Dateiverwaltung: Mehrfachauswahl mit Stapel-Tags und -Verschieben

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Ordnerbaum ohne Auswahl; Tag/Verschieben nur einzeln auf Detailseiten. Einzeloperationen (`set_tags`, Storage-`move`) wiederverwendbar, CSRF für fetch global vorhanden, kein Bulk-Endpunkt. |
| Implementierung | Checkboxen je Baumzeile (ohne Details-Toggle), Stapel-Leiste (Zähler, Tag-/Ordnereingaben, Fortschrittsbalken, Status), gechunkte Anfragen (25er) mit Teilergebnissen und Reload. Neue Routen `POST /documents/bulk-tags` und `/bulk-move` in eigenem Modul (Größen-Gate): max. 200 IDs, je Eintrag aktualisiert/fehlerhaft, vorhandene Store-Prüfungen. |
| Tests und Soll-Ist-Vergleich | Soll: mehrere Dateien gemeinsam taggen/verschieben mit Fortschritt und ohne Datenverlust. Ist gemessen: 4/4 Tests (Tags für 2, Verschieben mit ID-Erhalt + Teilfehler, 400/Anonym-Fälle, Marker), Browser-Test (Chromium, CI): 2 auswählen, taggen, Reload. Ruff, Gates, Compile grün. |
| Review und Abschluss | Offen als Follow-up: Kachelansicht, Drag-and-drop, Kontextmenüs, Umbenennen/Favoriten im Stapel (eigene Schritte). |

## 19. November 2026 · Wiedervorlage-Hub: Fristenansichten, Verlauf, Kommentare

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | VTODO-Bestand wiederverwendet: `TodoStore` (Status, Fälligkeit, Kategorien, `source_document_id`, Verlauf über `RevisionHistory`, Kommentare), Board mit Spalten/Aktionen, Dokument→Aufgabe-Endpunkt (reicht alle Formularfelder durch). Lücken: kein Anlegen mit Fälligkeit ab Dokument, keine Fristenansichten, kein sichtbarer Verlauf, keine Kommentar-UI. |
| Implementierung | Detailseite: Wiedervorlage-Formular (Titel, Fälligkeit, Notiz, Kategorie) auf vorhandenem Endpunkt. Board: `?view=`-Ansichten (Alle/Heute/Überfällig/Nächste 7 Tage/Warten=in-process/Erledigt) mit Zählern, ein Verlaufsscan gruppiert pro Aufgabe, Kommentarformular mit neuer `POST`-Route, Delegieren/Erledigen über vorhandene Routen. Keine neue Datenbank. |
| Tests und Soll-Ist-Vergleich | Soll: vertikaler Fall Anlegen→Ansicht→Kommentar→Verlauf→Delegieren mit Rechten. Ist gemessen: 4/4 Tests (Quellverknüpfung+Fälligkeit, alle 5 Ansichten filtern korrekt inkl. ungültigem Fallback, Kommentar+Verlaufseinträge sichtbar, Delegation gespeichert). Ruff, Gates, Compile grün. |
| Review und Abschluss | Offen als Follow-up: wiederkehrende Wiedervorlagen/Erinnerungen, Verknüpfungen zu Kontakten/Projekten/Terminen (Verknüpfungsauftrag), Board-Mobilfeinschliff. |

## 19. November 2026 · Schnellsuche als Arbeitsoberfläche: Live-Suche, Vorschau, Verlauf

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Bestand: Server-seitige Seite mit 5 Trefferarten ohne Live-Suche, Vorschau, Verlauf oder Relevanz (Gruppenreihenfolge). PR #1099 (Karten, Filter, Kopieren) geprüft, dessen A11y-Regression (`aria-live` fehlte) auf dem PR-Branch gefixt, verifiziert und gemergt. Berechtigungsaudit: Dokumente (Request-Akteur), Kontakte (Akteur), Rechnungen (`can_manage`) gefiltert; Objekte/Projekte instanzoffen konsistent zu Detailrouten. Kein Server-Verlauf vorhanden → Verlauf/Favoriten lokal im Browser (dokumentiert). |
| Implementierung | Neues Modul `documents_routes_quicksearch.py`: JSON-Live-API (Debounce 250 ms, Abbruch, min. 2 Zeichen), 4 neue Arten (Termine, Aufgaben, Chats, Links) mit je eigenem Berechtigungsmodell, Relevanz (exakt 100, Präfix 80, Teilstring 60, Detail 40), Vorschau-Endpunkt mit erneuter Rechteprüfung, 12 je Art, 50 gesamt. UI: Tastatur (Pfeile/Enter/Escape), Vorschau-Spalte, Verlauf/Favoriten, Aktionen Öffnen/Bezeichnung/Link/Vorschau, alles per `textContent` (XSS-sicher), No-JS-Fallback erhalten, mobil gestapelt. |
| Tests und Soll-Ist-Vergleich | Soll: < 500 ms bei großem Bestand, Isolation, Relevanz. Ist gemessen: Benchmark `tools/benchmark_quicksearch.py` 200 Dokumente → 22 ms Median, 2000 Dokumente + 1560 weitere Einträge → 129 ms Median, je 50 Treffer. 8/8 API-Tests (alle Arten, Relevanzfolge, Filter, Limits, Login, Fremdnutzer-Isolation, Vorschau 404), 4/4 Workspace-Tests, Browser-Tests (Chromium, CI): Live-Fluss ohne Reload, Tastatur, Vorschau, Verlauf, XSS-Freiheit. Gates grün. |
| Review und Abschluss | Offen als Follow-up: vollständiger Teilen-Flow und „Verknüpfen mit …“ (eigene Aufträge), server-seitiger Verlauf/Favoriten für Geräte-Roaming, Inhalts-Exzerpte in der Dokumentvorschau (Vorschau-Auftrag). |
## 19. November 2026 · Textvorschau: suchen, springen, kopieren, Extrakt-Status

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Bestand: `preview.kind` text (Dateitext, 300 Zeilen/12k Zeichen), PDF per iframe, Office/Bilder nur Vorschaubild. Extraktionsdaten (`extracted_text`, `text_extraction.status`) wurden für PDF/Office/Bilder nie angezeigt, obwohl vorhanden. Keine Suche, keine Kopieraktionen, kein Sprung zu Fundstellen. Extraktion läuft synchron in Scan/Refresh (`refresh_missing_text`), kein Einzel-Neustart, kein Worker. |
| Implementierung | Geteiltes Partial `_text_preview.html` (Suchfeld mit Zähler, Markierung, vor/zurück, Text/Auswahl kopieren, alles client-seitig ohne Requests, `textContent`-sicher); zusätzlich unter PDF-/Office-/Bildvorschau bei vorhandenem Extrakt; Extraktionsstatus aus bereits geladenen Metadaten (keine Zusatz-Queries); neuer `document_preview.js`. Keine neue Extraktionsarchitektur. |
| Tests und Soll-Ist-Vergleich | Soll: schnelle Vorschau mit Suche/Kopieren/Status ohne Serverlast. Ist gemessen: 4/4 Python-Tests (Marker, Extrakt-Status, Trennung Datei/Extrakt, Skript-Sicherheit), Browser-Test (Chromium, CI): 2 Treffer markiert, Zähler 1/2→2/2, Kopie in Zwischenablage, keine Konsolenfehler. Detailseite mit 500-Zeilen-Text: Median 141 ms (40 KiB); PDF mit Extrakt: Median 129 ms (42 KiB). `node --check`, Ruff, Gates grün. |
| Review und Abschluss | Offen als Follow-up: asynchroner Einzel-Neustart der Extraktion mit Fortschritt, Office-Volltext ohne Größenkürzung auslagern, Bild-OCR-Vorschau mobil prüfen. |
## 19. November 2026 · Dateimanager-Brücke: lokale Dateien per Desktop-Shell zeigen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Keine Reveal-Funktion vorhanden. Tauri lehnt Datei-/Shell-Zugriff per Capability ab → nur Electron. Electron startet Backend lokal auf 127.0.0.1 und stellt Bridge (`simpleOfficeDesktop`, IPC mit Absenderprüfung). Speicherabstraktion (Legacy/Shadow/V2/verschlüsselte Blobs, Overlays): nur echte lokale Dateien eignen sich. `localhost` allein beweist nichts → Nachweis ist die Bridge-Präsenz in der First-Party-Shell. |
| Implementierung | Server-Endpunkt `GET /documents/<id>/local-path` (Login, Katalogauflösung, Leserecht via Storage-`stat`, Containment, reguläre Datei) liefert absoluten Pfad oder `{available:false}`; startet nie Prozesse. Electron: `file:reveal`-Handler mit Absender- plus Pfadprüfung (`reveal.js`: absolut, existent, Datei, plattformgerecht), `shell.showItemInFolder` (Explorer/Finder/Dateimanager inkl. Dateiauswahl, ohne Subprozess). Button nur mit Bridge sichtbar (Detailseite); sonst Hinweis statt Aktion. |
| Tests und Soll-Ist-Vergleich | Soll: Reveal nur für lesbare lokale Dateien, kein Traversal/Injection, Linux/Windows/macOS. Ist gemessen: 5/5 Python-Tests (Pfad absolut+Datei, 404, Login, fehlende Datei→`not_local`, Marker), 6/6 Node-Plattformtests (posix/win32/darwin, relativ/fehlend/Verzeichnis/Fehlformen ohne fs-Zugriff), Browser-Tests (Chromium, CI): ohne Bridge versteckt, mit simulierter Bridge Pfadaufruf. `node --check`, Ruff, Gates grün. |
| Review und Abschluss | Offen als Follow-up: Reveal-Aktion in Schnellsuche/Dateilisten nach deren Merge, Praxisprüfung in Desktop-Builds je OS, Tauri bleibt ohne Shell-Zugriff (Design). |
