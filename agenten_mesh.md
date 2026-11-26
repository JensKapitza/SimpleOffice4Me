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

### P0 · Security-Triage #874 · 21. November 2026

| Phase | Arbeitsstand und Nachweis |
|---|---|
| Analyse | 317 offene Code-Scanning-Alerts auf `refs/heads/main` (Basis `3a374f94`): 143× `py/path-injection` (high), 64× `py/stack-trace-exposure`, 9× weak-hashing, je 1× XSS/ReDoS/clear-text-logging, Rest Supply-Chain/Lint. Dependabot deaktiviert (HTTP 403), Secret-Scanning per API nicht abfragbar — beide nicht als sauber gewertet. |
| Verifikation | Stichproben am Code gelesen: TURN-HMAC-SHA1 ist protokollbedingt (`simpleoffice_connection_relay.py:291`); XSS-Alert #449 zeigt auf driftete Zeile (neu zuordnen); Redirect-Senken teilvalidiert; Storage-Sink generisch (V2-Abgleich offen). |
| Ergebnis | Triage-Protokoll `docs/SECURITY_TRIAGE_2026-11-21.md`; Folge-Issues #1154 (path-injection/ReDoS/Storage), #1155 (XSS/Redirect/Logging), #1156 (Supply-Chain). Kein Dismiss, kein Merge (Koordinator mergt). |

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

## 19. November 2026 · Issue #1111: Bestandsbuchungen mit Grund

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Einundzwanzigster Slice (nach #1115-Merge): Mengen waren nur bei Anlage setzbar. Bestand wiederverwendet: Dezimalvalidierung, Update mit Verlauf, Notizen, Routenregistrierung per Bottom-Import. |
| Implementierung | Neues Modul `inventory_stock_adjust_routes.py` mit `POST /inventory/<id>/stock` (Delta mit Komma, Pflichtgrund, kein Negativstand, JSON/Flash, Login, Notiz im Verlauf); Buchungsformular in der Bestandszeile; zukunftssichere Werterhaltung. |
| Tests und Soll-Ist-Vergleich | Soll: rauf/runter buchen, kein Negativ, Einzelstück ausgenommen, Validierung, Rechte. Ist gemessen: 4/4 Blackbox-HTTP-Tests (50→42→45,5, Negativ abgelehnt ohne Änderung, Einzelstück-400, anonym+Marker); Nachbar-Suites 42/42 inkl. Mengen-Suite; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi-Umlagern/Inventur, föderierte Rechte, Fotoerkennung, Offline-Sync. |
## 19. November 2026 · Issue #1111: Freigabeschalter je Objekt für die Föderation

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Neunzehnter Slice: Freundes-Bestandssuche war alles-oder-nichts pro Gegenstelle. Bestand wiederverwendet: Index-Builder mit Minimalfeldern, Erfassungsroute, Bibliotheks-Aktualisierungspfade, Detailseite. |
| Implementierung | `share_inventory`-Schalter je Objekt (Standard geteilt, Altdaten tolerant, Erhalt bei Updates); Ausschluss im Index-Builder; Opt-out-Kontrollkästchen im Erfassungsformular, Umschalt-Route mit Abzeichen auf der Detailseite. |
| Tests und Soll-Ist-Vergleich | Soll: Standard geteilt, Opt-out wirkt im Index, Umschalten, Validierung, Rechte. Ist gemessen: 4/4 Blackbox-HTTP-Tests (Standard+Opt-out-beim-Anlegen, Umschalten mit Indexprüfung, 400/404-Fälle, anonym+Marker); Nachbar-Suites 61/61 inkl. Föderationsindex; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Rechte pro Identität/Gruppe, Fotoerkennung, Offline-Sync, E-Mail-Erinnerungen. |
## 19. November 2026 · Issue #1111 (P1-Slice): Scanmodi Einlagern und Entnehmen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Achtzehnter Slice: Stapelscans ohne Dialoge fehlten. Bestand wiederverwendet: Code-Auflösung, Zuordnung mit Rollback und Spiegelung, Auditmuster. Umlagern/Inventur als Modi folgen nach #1112/#1117-Merge. |
| Implementierung | `LibraryStore.unassign` mit Ereignis; neues Modul `scan_dispatch_routes.py` mit `POST /library/scan` (Modus+Code+Ziel, JSON/Flash, Login, Audit): Einlagern ordnet zu, Entnehmen löst Zuordnung und Spiegelung (mit Wiederherstellung bei Spiegelfehler); Scan-Karte auf der Bibliotheksseite. |
| Tests und Soll-Ist-Vergleich | Soll: beide Modi wirken, Spiegel konsistent, Validierung, Rechte. Ist gemessen: 4/4 Blackbox-HTTP-Tests (Einlagern per Code, Entnehmen löst+leert Spiegel, 3 Validierungsfälle, anonym+Marker); Nachbar-Suites 24/24; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Modi Umlagern/Inventur, Zielort-Gedächtnis, föderierte Rechte, Fotoerkennung, Offline-Sync. |
## 21. November 2026 · PR #1171 ausgearbeitet: Identitäts-First-Seen + QR-TOCTOU + Laufzeitnachweise

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Codex-PR #1171 (nur statisch geprüft) im Review bestätigt und erweitert: gleiche Lochklasse in `trust.remember` (Fingerprint still überschrieben) und TOCTOU in `import_qr` (get+save) gefunden; kein legitimer Remote-Rotationsflow vorhanden (nur lokaler Token-Rotations-Button). |
| Implementierung | `remember_discovered_identity` (First-Seen-Bindung, Konflikt-Event `peer_identity_conflict`, keine Überschreibung) für Register/Rendezvous/Discovery; QR-Import nutzt `save_discovered_peer` + Konflikt-Warnung, nur explizite Vor-Ort-Zeremonie bindet neuen Fingerabdruck; TTS-Endpunkt aus Browser-Inventar (keine Seite). |
| Tests und Soll-Ist-Vergleich | Soll: weder Verbindung noch Identität per Discovery umbiegbar, Zeremonie funktioniert. Ist gemessen: 44/44 (Discovery-, Admin-UI-, Peer-Trust-Suites inkl. 4 neue Konflikt-/Zeremonie-Tests); Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: nichts. |

## 21. November 2026 · Federation: Peer-Diagnose-Buttons (Datei, Ton, QR, Druck-Test)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Nutzerforderung: neben „Verbindung testen“ auch Dateisenden, Ton, QR-Generierung und Druckaufruf (ohne zu drucken) prüfen. Bestand: Diagnose-Infra (`probe_peer`), Block-API (nur Pull), Audio-Engine (Rendern+Playback), Druck-Client mit Proof-Protokoll, Druckverlauf. Lücken: kein Echo-, Ton-, QR-Roundtrip- und kein Dry-Run-Test. |
| Implementierung | `POST /federation/v1/blocks/test` (1–4 KiB Echo als SHA-256, keine Speicherung); `POST /federation/v1/audio/test-tone` (Gong rendern + Wiedergabeversuch, beide Stufen ehrlich gemeldet, Event); `submit(dry_run)` + `?dry_run=1` (volle Auth/Proof-Validierung, Verlaufseintrag Status `test`/„Testdruck“, kein Spool); Sender `federation_peer_tests.py`; 4 Admin-Routen + Buttons neben „Verbindung testen“; Flash-Texte de/en. |
| Tests und Soll-Ist-Vergleich | Soll: jeder Test prüft echten Aufruf, Dry-Run spult nie, Fehler ehrlich. Ist gemessen: 12/12 Blackbox-Tests (Echo+Limit, Ton beide Stufen, Sender-Hash/Mismatch/Stufen/QR-Roundtrip/Deaktiviert, Dry-Run ohne Spool + Verlauf, Real-Submit spult, Admin-Routen); Nachbarn 45/45; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: Praxistest gegen echten Peer durch Eigentümer (Ton hörbar, Testdruck im Verlauf). |

## 21. November 2026 · Federation: Hello macht Scans beidseitig sichtbar

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Nutzerbefund: QR-Scan meldet „Peer erkannt“, aber nur einseitig. Root Cause: `import_qr` speicherte ausschließlich lokal; ein Rückmelde-Mechanismus existierte nicht. Krypto (Ed25519-Signaturen, Fingerprint-Bindung) strukturell geprüft und als Basis wiederverwendet; Nebenbefund: `peer_profile` war nicht idempotent (leeres Deskriptor-Dict warf). |
| Implementierung | Neues Modul `app/federation_hello.py` (signiertes Hello, Empfänger-Prüfung: Signatur, Fingerprint-Bindung, Zeitfenster, Identitätsdiebstahl-Ablehnung, Fingerprint-Rate-Limit); Endpunkt `POST /federation/v1/discovery/hello` (sichtbar-aber-unvertraut: deaktiviert, Trust NONE); QR-Import meldet Hallo best-effort mit Treffer-/Fehler-Flash; `normalize_protocol_descriptor({})` gefixt. |
| Tests und Soll-Ist-Vergleich | Soll: nach Scan beide Seiten sichtbar, Fälschung/Identitätsdiebstahl abgelehnt, kein Vertrauen durch Hello. Ist gemessen: 5/5 Zwei-Instanzen-Blackbox-Tests; Nachbarn (Admin-UI, Discovery, Kompatibilität) grün; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: Praxistest mit zwei echten Instanzen durch Eigentümer; Diagnose-Buttons (Datei, Ton, QR, Druck-Test) als Folge-PR. |

## 21. November 2026 · E-Rechnung Phase 2b: UBL-2.1-Generator (KoSIT-bestätigt)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Auftrag AP3: eigenständiger XRechnung-Export (UBL 2.1, XRechnung 3.0.2) aus dem eingefrorenen Snapshot. KoSIT-Validator lokal installiert (1.6.3, Config 2026-08-31) und als Entwicklungs-Orakel verwendet. |
| Implementierung | Neues Modul `app/einvoice_ubl.py`: CustomizationID aus Installer-Konstante, Parteien mit E-Adressen/Steuernummern/Kontakt (BG-6), Zahlungsmittel IBAN/BIC, Steueraufschlüsselung je Gruppe mit Kategorien/Befreiungsgründen, Rabatte als positionsbezogene AllowanceCharge (UNCL-95-Code, keine stillen Korrekturen), Summen-Revalidierung mit Decimal (Fehler statt Fix). Zusätzlich AP2-Lücke geschlossen: Verkäufer-Ansprechpartner in Settings + Snapshot (KoSIT BR-DE-2 Pflicht). |
| Tests und Soll-Ist-Vergleich | Soll: standardkonforme XML, Summenfehler laut, KoSIT-Erfolg. Ist gemessen: 5/5 (`test_einvoice_ubl.py`: Struktur, Summen-Manipulation abgelehnt, Pflichtfelder, XML-Escaping, **echte KoSIT-Prüfung ACCEPTABLE**, Skip ohne Validator); 62/62 Bestand + 9/9 Validator-Suite; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: Phase 3 (Freigabe-Gate + Archivpaket + Editor-Protokoll). PR #1174 bleibt Draft, kein Auto-Merge. |

## 21. November 2026 · E-Rechnung Phase 2a: Snapshot-Artefakt + AP2-Felder

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Auftrag AP1→2a: eingefrorener Snapshot + XRechnungs-Felder (BT-10, E-Adressen, Refs, Zeitraum, Kategorien, Kontenzuordnung, Rabatt). Bestand wiederverwendet: Finalisierungs-Locks, Nummernkreis, Decimal-Summen, Settings/Editor-Muster. |
| Implementierung | Neues Modul `app/einvoice_snapshot.py` (kanonischer Snapshot, SHA-256, Tolerant-Reader, Formatvalidierung ohne erfundene Defaults); Finalisierung schreibt Snapshot + Hash + Historie; Positionen mit Rabatt %/Steuerkategorie/Befreiungsgrund (Summen rabattbereinigt, Decimal); Belegfelder + Käufer-Steuernummer + E-Adressen; Verkäufer-E-Adresse in Settings; Editor- + Settings-Formulare + JS-Vorschau. Keine Migration nötig (tolerante Leser, alte Belege unverändert lesbar). |
| Tests und Soll-Ist-Vergleich | Soll: Snapshot-Beweis, Hash-Manipulation erkannt, Felder rundum, Bestand grün. Ist gemessen: 10/10 neu (`test_einvoice_snapshot.py`); 103/103 Rechnungs-Nachbarsuites; `node --check`, Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: Phase 2b (UBL-Generator), Phase 3 (Freigabe-Gate + Archivpaket + Editor-Protokoll). PR #1174 bleibt Draft, kein Auto-Merge. |

## 21. November 2026 · Sieve: Skripte mit Sonderzeichen werden gefunden + Diagnose

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Nutzerbefund: eigene Sieve-Skripte werden trotz Login nicht gefunden. Root Cause: `list_scripts` verwarf stillschweigend alle Servernamen außerhalb `[A-Za-z0-9_.-]` (Leerzeichen, Umlaute); `_quote`/`_safe_sieve_name` hätten sie danach ebenfalls abgelehnt. Zusätzlich keine Diagnose (stilles `continue`). |
| Implementierung | `list_scripts_detailed` (alle parsetauglichen Namen + `skipped`-Liste, `list_scripts` bleibt kompatibel); `_quote` für RFC-5804-Quoted-Strings (Leerzeichen/Unicode ok, nur `"\\/Steuerzeichen` abgelehnt); `sanitize_local_name` (sichere Namen identisch, Rest deterministisch gefaltet + Hash, kollisionsfrei); Sync speichert unter lokalem Schlüssel, meldet `renamed`/`skipped` (Flash + Audit-History); Aktivieren nutzt Server-Namen; UI zeigt Server-Namen + Mapping. |
| Tests und Soll-Ist-Vergleich | Soll: Namen mit Leerzeichen/Umlauten laden, aktivieren, Diagnose sichtbar. Ist gemessen: 9/9 Sieve-Tests (4 neu: Parsen+skipped, Sanitize-Stabilität/Kollision, Sync-Mapping, Aktivieren mit Server-Namen); Nachbarn 40/40; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: Praxistest gegen echten Dovecot/Anbieter mit Sonderzeichen-Skripten durch Eigentümer. |

## 20. November 2026 · Kids-Vorlesestimme: überall Piper statt Browser-Stimmen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Browser-SpeechSynthesis war geräteabhängig unverständlich (u. a. Maze falsche Stimme). Vollinventar: 4 Kids-JS (`kids_learning/matching/mini_games/language/shared`) + Stimmwahl-Template. Bestand: `audio_output_engine.render_tts` (lokale Piper-CLI, Disk-Cache), Medien-Settings mit `piper_model`. |
| Implementierung | Neuer Endpunkt `GET /kids/lernen/sprache` (Login, 1–500 Zeichen, de-DE/en-US, deutsches Standardmodell + optionales EN-Modell, 503-Code bei fehlender Stimme, immutable Browser-Cache); neuer Client `static/js/kids_piper_voice.js` (Queue, Abbruch, Statusmeldung, kein Synthesis-Fallback); alle 4 Kids-JS + 5 Templates umgestellt; Stimmwahl durch Hinweis ersetzt; `piper_model_en`-Setting + Doku. |
| Tests und Soll-Ist-Vergleich | Soll: alles über Piper, Maze mit Serverstimme, kein Synthesis-Rest, Fehler kontrolliert. Ist gemessen: 12/12 neue Blackbox-Tests (`test_kids_tts.py`: Audio, Caching, EN-Fallback, Validierung, 503, Login, Engine-Wiederverwendung, kein Synthesis, Maze-Stimme, Template-Verdrahtung) + 46/46 angepasste Kids-Suites + 2/2 Node-Audiotests; `node --check`, Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Supervisor/Dev-Struktur verwendet; Koordinator hat nach Agenten-Stillstand Review + Fertigstellung übernommen. Offen: Piper-Modell muss in Medien-Einstellungen hinterlegt sein (sonst 503-Hinweis); physischer Hörtest auf Endgerät. |

## 19. November 2026 · Issue #1111 (P1-Slice): Inventur-Abgleichmodus am Objekt

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Inventur-Slice aus #1111 (Schritt 10/12): gezählte Menge vs. Sollmenge hatte keinen Klärpfad. Auf `origin/main` plus `origin/feature/inventory-stock-adjust` (#1133, offene Delta-Buchungen `POST /inventory/<id>/stock`) aufgebaut, um eine Routenkollision zu vermeiden: eigene `adjust_stock`-Route entfernt, gezählter Abgleich als Modus der #1133-Route. Bestand wiederverwendet: `quantity`-Normalisierung, `_write`-Audit, `item_detail`-Route, Flash-i18n. |
| Implementierung | `ObjectStore.adjust_quantity` (Grund-Whitelist `zaehlfehler/schwund/zugang/korrektur`, Mengenvalidierung, `stock_adjustments`-Historie mit Vorwert/Zählwert/Grund/Notiz/Akteur/Zeit, Normalisierung in `_normalized_item`, Audit-Aktion `object_quantity_adjusted`) behalten; `inventory_stock_adjust_routes.adjust_object_stock` um gezählten Modus erweitert (Feld `counted` nicht-leer in Formular/JSON → `adjust_quantity` mit Grund-Whitelist, Notiz, Flash `inv.stock.adjusted` de/en; sonst Delta-Modus exakt unverändert); „Fehlmenge klären"-Karte postet `counted/reason/note` an `inventory.adjust_object_stock`, Historientabelle bleibt. Keine neue Datenbank. |
| Tests und Soll-Ist-Vergleich | Soll: Zählwert übernehmen (Formular+JSON), Vorwert+Grund auditiert, ungültige Gründe/Mengen abgelehnt, anonym blockiert, Historie nach Reload, leerer `counted`-Wert fällt auf Delta-Modus zurück. Ist gemessen: 6/6 Blackbox-HTTP-Tests (`test_object_stock_adjust_http.py`: 10→7 mit Audit+Historie, anonym 302 ohne Änderung, Grund/Menge/negativ abgelehnt ohne Änderung, JSON-Abgleich 10→6, Delta-Fallback 10→8 ohne Abgleichhistorie, 2 Einträge nach Reload); Nachbar-Suite `test_inventory_stock_adjust_http` 4/4 unverändert. |
| Review und Abschluss | Finaler Rebase nach #1133-Merge hiermit erledigt; Branch frisch ab Main (Redo nach #1141-Revert).dlösung). Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, System-/manuelle Metadatenwerte, CSV-Import, Objekt-Verleih, föderierte Rechte. |

## 20. November 2026 · Revert des #1134-Merges (fremde Branches eingeschleppt)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Der Merge-Agent für die Kette #1129–#1134 hat gegen seine Anweisung („nur Rebase auf origin/main“) im #1134-Branch fremde PR-Heads integriert („Integrate preceding reviewed PR heads“): #1135 (2 Commits), #759-Tests, #1136-Slices 1–3. Dadurch landete ungesehener Code in Main (56394d17), u. a. gegen die #1136-Vorgabe „keine automatischen Merges“. #1129/#1130/#1131/#1133 verifiziert sauber (nur eigene Commits + VNU-Fix). |
| Implementierung | Revert per `git revert -m 1 56394d17` (Vorwärts-Fix, kein History-Rewrite): 21 Dateien, Fremdcode vollständig entfernt, legitime #1117–#1133-Inhalte verifiziert intakt. Danach: #1134-Doku neu aufsetzen, Slice-Branches frisch vom neuen Main abzweigen (neue Hashes, saubere PR-Diffs). |
| Tests und Soll-Ist-Vergleich | Soll: Fremddateien weg, legitime Slices da, Suite grün. Ist gemessen: 6/6 Fremddateien entfernt, 4/4 Slice-Module + share-Flag vorhanden; Mail-/Inventur-/Bibliotheks-Nachbarsuites + Gates vor Push grün (siehe PR-CI). |
| Review und Abschluss | Lehre: Merge-Agents dürfen nur `git merge origin/main` ausführen, nie fremde Branches integrieren; Merges künftig per Diff gegen PR-Branch verifizieren. Offen: #1134-Doku neu, #1135/#1137 normal mergen, #1136-Slices bleiben zur Nutzer-Review offen. |

## 20. November 2026 · Issue #1136 (Slice 3): Lese-Timing und Gelesen+Nächste

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | #1136 fordert konfigurierbare Verweildauer bis Gelesen + Schnellaktion „Gelesen und nächste“. Ist: nur manuelles Umschalten per Formular+Redirect. Entwurf: Policy-Feld `seen_dwell` (Default 5 s, 0–120, 0=aus), `set_seen` mit validiertem `next_uid`-Redirect, `next_uid`-Berechnung aus Listenreihenfolge, JS-Timer nur bei (schreibbar + ungelesen + dwell>0). |
| Implementierung | `MailAccountPolicy.seen_dwell/set_seen_dwell` (+ Audit, Zeilen-Merge statt Überschreiben in `set_read_only`, damit Dwell erhalten bleibt); Route `POST /accounts/<id>/seen-dwell` (Header-Auswahl 0/3/5/10/30 s); `set_seen` mit `next_uid`-Validierung (UID-Regex, Fallback aktuelle Mail); Template-Marker (`data-mail-autoseen/dwell-seconds`, `seen-next`-Formular mit `n`-Kürzel); JS-Timer mit Einmal-Schutz. |
| Tests und Soll-Ist-Vergleich | Soll: Default/Clamp, Marker+Setting, Gelesen+Nächste mit genau 1 STORE, gefälschte Ziele abgelehnt, Readonly blockt STORE. Ist gemessen: 5/5 Blackbox-HTTP mit Fake-IMAP; vererbte Slice-1-Tests + Nachbarn 38/38; `node --check`, Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Gestapelt auf Slice 1 (erst nach dessen Review mergen); kein Auto-Merge (Issue-Vorgabe). Offen: Slices 4–7 (Tags, Überführen, Signaturen, Archiv-Härtung). |

## 20. November 2026 · Issue #1136 (Slice 1): Mail-UI-Shell mit Tastatur, Palette, mobilen Panes

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | #1136-Ist verifiziert: Mehrkonten-IMAP/SMTP, BODY.PEEK-Lesen, `require_writable`-Schreibschutz, Ordner/Liste/Lesebereich als 3-Spalten-Template, Archiv/Vorgänge vorhanden. Lücken: keine Tastaturbedienung, keine Befehlspalette, kein mobiles Pane-Verhalten, kein LRU-Cache, kein Lese-Timing, keine Tag-/Aufgaben-Integration. Plan mit 7 Slices in `docs/ISSUE_1136_PLAN.md`. |
| Implementierung | Slice 1 ohne Backend-Änderung: `static/js/mail_client.js` (j/k/Enter-Navigation, Kürzel e/u/r/c//, Strg+K-Palette aus gerenderten `data-mail-action`-Aktionen, mobile Pane-Umschaltung), `static/css/mail_client.css`, Template-Marker (`data-mail-pane/uid/action`, Palette, Hinweise). Nur bestehende Routen; ohne JS bleibt alles Link/Formular; Readonly liefert konstruktionsbedingt keine Schreibaktionen. |
| Tests und Soll-Ist-Vergleich | Soll: 3 Panes + Palette rendern, Zeilen/Key-Hooks, Preview-Aktionen, Readonly ohne Schreibaktionen, ohne Konto stabil. Ist gemessen: 5/5 Blackbox-HTTP-Tests mit Fake-IMAP (`test_mail_client_shell_http.py`); Nachbarn 40/40; `node --check`, Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Kein Auto-Merge (Issue-Vorgabe). Offen: Slices 2–7 (Cache, Timing, Tags, Überführen, Signaturen, Archiv-Härtung). |
## 21. November 2026 · Mail: Read-only-Schalter verlässt Reader nicht mehr

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Nutzerbefund: Umschalten Read-only↔Schreibzugriff verließ die Anzeige. Ursache: `set_readonly` leitete immer auf die Einstellungen um, obwohl der einzige Schalter im Reader steht. |
| Implementierung | Route wertet `return_to=reader` aus und kehrt mit Kontext (Ordner, UID, Suche, Modus) in den Reader zurück (fester Zielname, kein offener Redirect; leere Werte entfallen); Reader-Formular sendet den Kontext mit; Fallback ohne `return_to` bleibt Einstellungen. |
| Tests und Soll-Ist-Vergleich | Soll: Ansicht + Kontext bleiben, Umschaltung wirkt, Fallback stabil. Ist gemessen: 3/3 Blackbox-HTTP-Tests (`test_mail_readonly_stay_http.py`); Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen: nichts. |

## 21. November 2026 · Issue #759: restliche Checklistenpunkte belegt + Tests erweitert

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Issue-Checklisten war unvollständig abgehakt. Nachgeholt: CSRF (globaler `protect_browser_mutation`-Hook, per Test mit aktiviertem Schutz belegt), Pfad-Traversal (UUID-Validierung + Symlink-/Containment-Prüfung in `_report_directory`), Besitzrechte (Fremdzugriff 404 ohne Leak), Formate (nur PDF angeboten, kein CSV/Download-Zweig vorhanden), Leerauswahl (kontrollierter Leerbericht), Dateiname + Pfad-Leakfreiheit, 50-Befunde-Bericht. |
| Implementierung | Nur Tests erweitert (`tests/test_site_visit_report_http.py` 5→11): Fremdbenutzer-404, CSRF-403 ohne Token, Traversal-IDs, Leerauswahl, Dateiname/Leakfreiheit, Großbericht. Kein Produktcode geändert. |
| Tests und Soll-Ist-Vergleich | Soll: alle Issue-Punkte belegt. Ist gemessen: 11/11 Blackbox-Tests grün; Ruff, Policy-, Größen-, Compile-Gates grün. Nicht durchgeführt: dedizierter Browser-Test (nur CI-Smoke), Last-/Timeouttest unter echter Konkurrenz. |
| Review und Abschluss | Issue-Checkboxen nach Merge auf verifizierten Stand setzen + Nachweis kommentieren. |

## 20. November 2026 · Issue #759: Berichts-Erstellung/Download verifiziert + Regressionstests

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Issue #759 (20.10.2026): Berichte weder erstell- noch downloadbar. Inventarisiert: Ortstermin-Berichtspfade `GET /site-visits/<id>/report` (Vorschau), `POST /site-visits/<id>/report.pdf` (Erstellung+Download), `GET /site-visits/<id>/reports/<rid>.pdf` (gespeicherter Download) mit `build_visit_report` (reportlab), Versionierung, SHA-256, Audit. Reproduktion auf aktuellem Main: Fehler tritt NICHT mehr auf (Vorschau 200, Erstellung 200 mit %PDF-, Download 200 byteidentisch). Nebenbefund: veraltete installierte `app`-Kopie in site-packages des Test-Venvs (Templates fehlten dort) — kein Repo-Fehler. |
| Implementierung | Keine Produktänderung nötig; Absicherung durch neue Blackbox-Regressionssuite `tests/test_site_visit_report_http.py` (Vorschau, Erstellung+Versionierung, Byteidentität, kontrollierte Fehler ohne Teilspeicherung, Login-Pflicht). |
| Tests und Soll-Ist-Vergleich | Soll: Erstellen/Downloaden funktioniert, Fehler kontrolliert, Rechte geprüft. Ist gemessen: 5/5 Blackbox-HTTP-Tests grün; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Issue #759 nach Merge mit Nachweis schließen. Offen: site-packages-Kopie im Test-Venv bei Bedarf aktualisieren (Umgebung, kein Repo). |
## 20. November 2026 · Issue #1136 (Slice 2): Flüchtiger LRU-Nachrichtencache

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | #1136 fordert flüchtigen Cache (256 MiB, 200–300 konfigurierbar, LRU, Neustart-Leerung, Benutzerisolation, Flags separat, keine Server-Mutationen). Ist: jeder Aufruf lädt BODY.PEEK[] neu. Entwurf: prozess-lokales Singleton (Neustart-leer per Konstruktion), Schlüssel (Benutzer, Konto, Ordner, UID), Flags per leichtem `(FLAGS)`-Fetch frisch, Actor wird an den zwei `message`/`attachment`-Aufrufern durchgereicht. |
| Implementierung | Neues Modul `app/mail_message_cache.py` (Thread-Lock, Byte-Bilanz, LRU-Verdrängung, Clamp 200–300 MiB, `MAIL_CACHE_MAX_BYTES`-Konfiguration); `ImapWebClient` mit Cache-Injektion, `_raw_message(..., actor=)` (Treffer → nur FLAGS-Fetch), `message`/`attachment` mit `actor`-Parameter, Routen reichen `_actor()` durch. |
| Tests und Soll-Ist-Vergleich | Soll: Zweitöffnung ohne Body-Re-Download, Flags frisch, Isolation, Clamp, keine Mutation. Ist gemessen: 5/5 Unit-Tests (Clamp, Roundtrip+Stats, Isolation, LRU, Oversize/Invalidate) + 4/4 Blackbox-HTTP mit Fake-IMAP (1 Body-Fetch bei 2 Öffnungen, FLAGS-Refresh, 2 Fetches bei 2 Benutzern, Config-Clamp, 0 Mutationen); Nachbar-Suites OK; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Kein Auto-Merge (Issue-Vorgabe). Offen: Slices 3–7 (Timing, Tags, Überführen, Signaturen, Archiv-Härtung). |

## 19. November 2026 · Issue #1111: Reservierungen mit Zeiträumen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Siebzehnter Slice: Vormerkungen waren von physischer Entleihe nicht getrennt. Bestand wiederverwendet: Objektauflösung, Routenregistrierung per Bottom-Import, Detailseite, S3-Coverage-Register. |
| Implementierung | Neues Modul `inventory_reservations.py` (gesperrter Store: Zeitraum, Person, Notiz; Überschneidungsschutz, Storno) plus `inventory_reservation_routes.py` (Reservieren/Stornieren, JSON/Flash, Login); Reservierungskarte auf der Detailseite; Coverage-Eintrag. |
| Tests und Soll-Ist-Vergleich | Soll: Zyklus, keine Doppelbelegung, Validierung, Rechte. Ist gemessen: 4/4 Blackbox-HTTP-Tests (Zyklus+Storno, Überschneidung-400, 4 Validierungsfälle, anonym+Marker); Nachbar-Suites 39/39 inkl. Coverage-Guard; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Entleihe-gegen-Reservierung-Prüfung nach #1123-Merge, Scanmodi, föderierte Rechte, Fotoerkennung, Offline-Sync, E-Mail-Erinnerungen. |

## 19. November 2026 · Issue #1111 (P2-Slice): Garantiefristen und Kaufwert

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Sechzehnter Slice: Garantiedauer fehlte, Kaufwert war nur teilweise sichtbar. Bestand wiederverwendet: Feldvalidierung, Fehlerfeld-Zuordnung, Monatsarithmetik, Detailseite. |
| Implementierung | `warranty_months` (0–120, validiert) im Erfassungsformular mit Fehlerfeld; Ablaufdatum aus Kaufdatum+Monaten mit Aktiv/Abgelaufen-Kennzeichen auf der Detailseite; Kaufwert-Anzeige verifiziert. |
| Tests und Soll-Ist-Vergleich | Soll: Frist berechnen/kennzeichnen, ungültig ablehnen, Wert sichtbar. Ist gemessen: 5/5 Blackbox-HTTP-Tests (aktiv mit Datum, abgelaufen 2021-01-15, 2 Validierungs-400 mit Fehlerfeld, Wert+Händler, Formularmarker); Nachbar-Suites 43/43; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi pro Aktion, föderierte Rechte, Fotoerkennung, Offline-Sync, E-Mail-Erinnerungen, Rechnungsbeleg-Verknüpfung. |

## 19. November 2026 · Issue #1111 (P3-Slice): Mindestbestände mit Warnung

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Fünfzehnter Slice (nach #1115-Merge entblockt): Unterschreitungen fielen nicht auf. Bestand wiederverwendet: Mengenvalidierung, Erfassungsroute, Bibliotheks-Aktualisierungspfade, Routenregistrierung per Bottom-Import. |
| Implementierung | Optionales `min_stock` in `ObjectStore` (gleiche Validierung, tolerante Altdaten); `low_stock`-Abfrage; `GET /inventory/low-stock` (JSON, Login); Mindestbestand-Feld im Erfassungsformular, Warnabzeichen auf der Detailseite; Erhalt bei Bibliotheks-Updates. |
| Tests und Soll-Ist-Vergleich | Soll: Warnung unterhalb, Ruhe oberhalb/ohne, Validierung, Erhalt, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (Warnung+Liste, keine Warnung, 2 Validierungs-400, Zuordnung erhält, anonym+Marker); Nachbar-Suites 63/63 inkl. Mengen-Suite; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi pro Aktion, föderierte Rechte, Fotoerkennung, Offline-Sync, E-Mail-Erinnerungen, CSV-Mengen-Spalten. |

## 19. November 2026 · Issue #1111 (P2-Slice): Objektzustände und Lebenszyklus

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Vierzehnter Slice: physischer Zustand (neu/gebraucht/defekt/in Reparatur/ausgesondert) war getrennt von Verfügbarkeit nicht abbildbar. Bestand wiederverwendet: `ObjectStore`-Validierungsmuster, Erfassungsroute, Bibliotheks-Aktualisierungspfade. |
| Implementierung | Optionales `condition`-Feld in `ObjectStore` (tolerante Altdaten, keine Änderung am Status-Vokabular); Durchreichen im Erfassungsformular (Auswahlliste) und Anzeige auf der Detailseite; Erhalt bei Bibliotheks-Zuordnung/Identität. |
| Tests und Soll-Ist-Vergleich | Soll: Zustand anlegen/anzeigen/validieren, Zuordnung ohne Verlust. Ist gemessen: 5/5 Blackbox-HTTP-Tests (Anzeige, Standard leer, ungültig 400, Zuordnung erhält, Formularmarker); Nachbar-Suites 59/59; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi pro Aktion, föderierte Rechte, Fotoerkennung, Offline-Sync, Mindestbestände, Garantie/Belege/Wert. |

## 19. November 2026 · Issue #1111: Fällige Wartungsprüfungen im Überblick

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Dreizehnter Slice: Prüftermine existierten nur je Objekt und als VTODO, ohne objektübergreifende Fälligkeitsliste. Bestand wiederverwendet: Inspektions-Sidecar, Detailseite, Routenregistrierung per Bottom-Import. |
| Implementierung | `due_inspections` im Sidecar (Horizont, nur aktive, fälligst zuerst) plus neues Modul `inventory_inspection_routes.py` mit `GET /inventory/inspections/due` (JSON/HTML-Seite, Login); Verweis in der Prüfungen-Karte der Detailseite. |
| Tests und Soll-Ist-Vergleich | Soll: fällige zuerst, Horizont filtert, erledigte verschwinden, Rechte, Seite. Ist gemessen: 3/3 Blackbox-HTTP-Tests (Sortierung+Objektname, Horizont+Erledigen, Seite+Login+Verweis); Nachbar-Suites 51/51; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi pro Aktion, föderierte Rechte, Fotoerkennung, Offline-Sync, Mindestbestände, E-Mail-Erinnerungen. |

## 19. November 2026 · Issue #1111 (P2-Slice): Temporäre Orte mit Rückgabefristen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Zwölfter Slice: Werkbank/Fahrzeug/Reparatur waren von Dauerorten nicht unterscheidbar, Rückgaben ohne Frist. Bestand wiederverwendet: Standortanlage, Zuordnungsroute mit Rollback, Objektakte, Bibliotheksseite. |
| Implementierung | `temporary`-Kennzeichen je Standort plus optionales Rückgabedatum je Zuordnung (ISO, nicht vergangen); `temporary_holds` (fälligst zuerst, Überfällig-Kennzeichen); neues Modul `hold_routes.py` mit `GET /library/temporary-holds`; Checkbox/Abzeichen/Rückgabefeld/Auslagenliste in der UI. |
| Tests und Soll-Ist-Vergleich | Soll: Auslagen mit Frist sichtbar, Überfälligkeit erkennbar, Validierung, Rechte. Ist gemessen: 4/4 Blackbox-HTTP-Tests (Frist+3 Tage, Überfällig-Simulation, Vergangenheits-400, anonym+Marker); Nachbar-Suites 39/39 inkl. Coverage-Guard; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi pro Aktion, föderierte Rechte, Fotoerkennung, Offline-Sync, Mindestbestände, Reservierung/Genehmigung. |

## 19. November 2026 · Issue #1111: Objekt-Verleih mit Fristen und Verlauf

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Elfter Slice: physischer Objektverleih fehlte (`rentals.py` ist Nebenkostenabrechnung). Bestand wiederverwendet: Objektauflösung, Routenregistrierung per Bottom-Import, Detailseite, Login/CSRF-Muster. Reservierung/Genehmigung/E-Mail/Föderation bewusst als Follow-up abgetrennt. |
| Implementierung | Neues Modul `inventory_lending.py` (gesperrter Store: Entleiher, Fälligkeit, Notiz, Quelle; eine offene Leihe je Objekt) plus `inventory_lending_routes.py` (Entleihen/Rückbuchen/Liste offen+überfällig, JSON/Flash, Login); Verleih-Karte mit Verlauf auf der Detailseite. |
| Tests und Soll-Ist-Vergleich | Soll: Zyklus Entleihen→Rückgabe, keine Doppelleihe, Validierung, Rechte. Ist gemessen: 4/4 Blackbox-HTTP-Tests (Zyklus+Verlauf, Doppelleihe-400, 4 Validierungsfälle, anonym+Marker); Nachbar-Suites 41/41; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Reservierung/Genehmigung, E-Mail-Erinnerungen, föderierte Rechte, Selbstentnahme per QR, Scanmodi, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Issue #1111 (P2-Slice): Virtuelle Sammlungen ohne Umlagerung

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Zehnter Slice: thematische Gruppierung gab es nur als Freitext-Tags ohne Identität. Bestand wiederverwendet: Objektauflösung, Routenregistrierung per Bottom-Import, Inventarseiten, Login/CSRF-Muster. |
| Implementierung | Neues Modul `inventory_collections.py` (gesperrter JSON-Store: Name eindeutig, Mitglieder, Quelle) plus `inventory_collection_routes.py` (Liste/Anlegen/Hinzufügen/Entfernen, JSON/Flash, Login, Rücksprung validiert); Sammlungskarte auf der Inventarseite, Zugehörigkeitskarte auf der Detailseite. |
| Tests und Soll-Ist-Vergleich | Soll: mehrere Themen je Objekt, Lagerort unverändert, Validierung, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (2 Objekte gruppiert+gezählt, Lagerorte beider unverändert, Entfernen+Zweitversuch, 4 Validierungs-400, anonym+Marker); Nachbar-Suites 54/54; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Scanmodi pro Aktion, Objekt-Verleih, föderierte Rechte, Fotoerkennung, Offline-Sync, Mindestbestände. |

## 19. November 2026 · Issue #1111 (P1-Slice): QR-Etiketten für Standorte

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Neunter Slice: Standorte druckten nur Barcode, QR-Renderer existierte nur für Bücher. Bestand wiederverwendet: `render_barcode`/`render_qr_code`, Buch-Vorschaumuster, Standortauflösung per Code (Scan- und Umlagerungsflüsse verstehen Codes bereits). |
| Implementierung | Neues Modul `label_routes.py` mit `POST /locations/<id>/labels.preview` (Barcode/QR als PNG ohne Drucker); `print_location` um Stilwahl erweitert (Art protokolliert); Etikettenformular mit Stilauswahl und Vorschau-Button. QR kodiert den Standortcode. |
| Tests und Soll-Ist-Vergleich | Soll: beide Arten als PNG, Validierung, Rechte, Code auflösbar. Ist gemessen: 5/5 Blackbox-HTTP-Tests (PNG-Magie beider Arten, Stil-400/Standort-404, anonym 302, Formularmarker, Code-Auflösung); Nachbar-Suites 46/46; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: A4-Bögen/Thermoetiketten-Stapel, Scanmodi pro Aktion, Objekt-Verleih, föderierte Rechte, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Issue #1111 (P2-Slice): Objektbeziehungen für Zubehör und Sets

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Achter Slice: Zubehör (Netzteil→Laptop) und Set-Zugehörigkeit waren nicht abbildbar. Bestand wiederverwendet: `ObjectStore`-ID-Auflösung, Routenregistrierung per Bottom-Import, Detailseite, Verknüpfungsmuster aus PR #1110. |
| Implementierung | `relations`-Liste je Objekt (Art, Ziel, Notiz, Quelle; max. 50, idempotent, kein Selbstbezug); `add_relation`/`remove_relation`/`related` mit Rücklinks und defekttoleranter Zielauflösung; neues Modul `inventory_relation_routes.py` (Liste/Anlegen/Entfernen, JSON/Flash, Login); Beziehungs-Karte auf der Detailseite mit Anlege-Formular. |
| Tests und Soll-Ist-Vergleich | Soll: beide Richtungen sichtbar, Idempotenz, Validierung, Entfernen, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (Zubehör beidseitig+Notiz, Idempotenz, 3 Validierungs-400, Entfernen+404-Fall, anonym+Marker); Nachbar-Suites 45/45; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Suche/Picker für Ziele nach #1110-Muster, Scanmodi pro Aktion, Objekt-Verleih, föderierte Rechte, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Issue #1111 (P2-Slice): CSV-Massenimport mit Vorschau und Dublettenprüfung

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Siebter Slice: Bestände mussten einzeln erfasst werden. Bestand wiederverwendet: `ObjectStore`-Validierung, Dublettensuche, Routenregistrierung per Bottom-Import, Login/CSRF-Muster. Mengen/Einheit-Spalten bewusst ausgespart bis PR #1115 gemergt ist (überschneidungsfreie Slices). |
| Implementierung | Neues Modul `inventory_import.py` (Trennzeichen-Erkennung, feste Spalten mit Aliasen, max. 500 Zeilen/1 MB, Staging mit Einmal-Token/TTL/Akteurbindung) plus `inventory_import_routes.py` (`POST /inventory/import/preview` mit JSON/Vorschauseite, `POST /import/commit` mit Dubletten- und Fehlerbericht); Upload-Karte auf der Inventarseite. |
| Tests und Soll-Ist-Vergleich | Soll: Zuordnung, Vorschau, Dubletten (Bestand+Datei), Fehlerbericht, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (Abbildung+Zählung, 3 Problemarten, Anlegen+Token-Einmaligkeit, Datei-/Token-Fehler, anonym+Marker); Nachbar-Suites 41/41; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Mengen/Einheit-Spalten nach #1115-Merge, Scanmodi pro Aktion, Objekt-Verleih, föderierte Rechte, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Issue #1111 (P1-Slice 5): System- und manuelle Metadatenwerte trennen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Fünfter P1-Slice (sechster insgesamt): manuelle Korrekturen hätten Systemwerte aus Nachschlagen/OCR still überschrieben. Bestand wiederverwendet: `ObjectStore`-Validierungsmuster, Erfassungsroute, Detailseite, Routenregistrierung per Bottom-Import. |
| Implementierung | `overrides`-Abbildung je Objekt (Feld → Wert/Zeitpunkt/Benutzer, tolerante Altdaten); `set_override`/`clear_override` mit Feldnamen- und Längenprüfung, `effective_fields` (manuell gewinnt); neues Modul `inventory_override_routes.py` mit Setzen/Zurücksetzen (JSON/Flash, Login); Metadaten-Karte auf der Detailseite (manuell umrandet, Systemwert mit Quelle sichtbar). |
| Tests und Soll-Ist-Vergleich | Soll: beide Werte sichtbar, manuell bevorzugt, keine stillen Überschreibungen, Validierung, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (beide sichtbar+Marker, Zurücksetzen stellt System wieder her, 5 Validierungsfälle, anonym 302, Formularmarker); Nachbar-Suites 46/46; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, CSV-Import, Objekt-Verleih, föderierte Rechte, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Issue #1111 (P2-Slice): Inventur mit Differenzprotokoll

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Fünfter Slice: Soll/Ist-Abgleich fehlte (P2-Abnahmetest „Inventur erkennt fehlende, zusätzliche und falsch gelagerte Gegenstände“). Bestand wiederverwendet: Standortzuordnungen, Code-Auflösung, Ereignisablage, Auditmuster. |
| Implementierung | `LibraryStore.record_inventory` (Ereignis `inventory_counted`) plus neues Modul `inventory_routes.py` mit `POST /library/locations/inventory` (JSON/Formular, max. 500 Codes, Audit, Login): vorhanden/fehlend/falsch einsortiert (mit Soll-Pfad)/unbekannt, Duplikat-Scans entdoppelt; Zählformular auf der Bibliotheksseite, Ergebnis als Hinweis plus Ereignis. |
| Tests und Soll-Ist-Vergleich | Soll: Differenzen je Kategorie korrekt, Verlauf sichtbar, Validierung, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (1/1/1/1-Differenz mit Soll-Pfad, Ereignis auf Seite, leer/501/unbekannt, anonym 302, Formularmarker); Nachbar-Suites 24/24; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, System-/manuelle Metadatenwerte, CSV-Import, Objekt-Verleih, föderierte Rechte, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Kalender: Schnellerfassung per Tag-Klick und mobile Liste

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Server-seitig gerenderter Monatskalender (Tab-Arbeitsbereich, Termin-Modals mit Bearbeiten/Löschen/Freigaben, Serien/Zeitzonen/Erinnerungen/Konflikte vorhanden). Lücken: kein Anlegen per Tag-Klick (nur Formular), kein Heute-Sprung, 7-spaltiges Raster auf Handys unbedienbar (8,5rem-Zellen, 0,75rem-Termine). |
| Implementierung | Plus-Button je Tag (`data-calendar-quick-add` mit ISO-Datum) öffnet Buchungsreiter mit vorbelegten 09:00–10:00 und Titel-Fokus; Heute-Link springt in den aktuellen Monat; unter 576 px wird das Raster zur Liste (Wochentage/leere Tage versteckt, Termine umbrechend, 44 px Ziele). Keine Backend- oder CalDAV-Änderung. |
| Tests und Soll-Ist-Vergleich | Soll: 1 Klick zum vorbelegten Formular, mobil lesbar ohne Horizontal-Scroll. Ist gemessen: 4/4 Python-Tests (28–31 ISO-Tage, Heute-Link, Handler, Mobil-CSS), Browser-Tests (Chromium, CI): Klick auf 2026-11-19 → Formular mit 09:00/10:00, Mobil 360 px: Wochentage versteckt, Tage ≥ 80 % Breite, Plus-Ziele ≥ 44 px. Gates grün. |
| Review und Abschluss | Offen als Follow-up: Drag-and-drop, Größenänderung, Wochen-/Tagesraster, Terminverknüpfungen (eigene Aufträge), physische Geräteprüfung. |

## 19. November 2026 · Issue #1111: Standort-Etikettenbögen als PDF

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Zwanzigster Slice: Bögen gab es nur für Bücher. Bestand wiederverwendet: Etiketten-PDF-Renderer (Medien+A4-3×7), Buch-Bogenmuster, Druckerbereich der Seite. |
| Implementierung | Neues Modul `sheet_routes.py` mit `POST /library/locations/labels.pdf` (Auswahl max. 200, Barcode/QR, Medien/A4, Login, Download); Bogenformular im Druckerbereich; QR kodiert den auflösbaren Standortcode. |
| Tests und Soll-Ist-Vergleich | Soll: gültiges PDF beider Layouts, Auswahl validiert, Rechte, UI. Ist gemessen: 3/3 Blackbox-HTTP-Tests (PDF-Magie beider Layouts, 4 Validierungs-400, anonym+Marker); Nachbar-Suites 30/30; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up: Thermoetiketten-Stapel, föderierte Rechte, Fotoerkennung, Offline-Sync. |

## 19. November 2026 · Issue #1111 (P1-Slice): Lagerorte rekursiv umlagern

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Issue #1111 Schritt 1 inventarisiert: `ObjectStore` (UUID+Folgenummer, Typ/Identifier/Standort-Freitext, Tags/Felder/Dokumente/Notizen, Verlauf), `LibraryStore` (hierarchische Standorte mit Pfad/Code, Objektzuordnung, Ereignisse, Etikettendruck), Buch-Exemplare/Ausgaben (`catalog.py`, nur Bücher), Exporte (CSV/RIS/BibTeX/Calibre), Amazon/eBay-Lookup (`inventory_marketplace.py`), Fotoerfassung mit Stapel (`inventory_capture_routes.py`), Federation-Katalog. Lücken: kein Umlagern ganzer Kisten, keine rekursiven Inhalte, gespiegelte Pfade würden veralten, kein Objekt-Verleih (`rentals.py` ist Nebenkostenabrechnung, kein Leihsystem). |
| Implementierung | `LibraryStore.move_location` (Umhängen mit Zyklenschutz, Ereignis `location_moved`) plus `children`/`descendants`/`subtree_contents`; neues Modul `move_routes.py` mit `POST /library/locations/<id>/move` (Ziel per ID oder Barcode-Code für QR-Scans, JSON oder Formular, Audit, Login-Pflicht); danach Spiegelauffrischung über vorhandenes `assign_object_to_location` und Regal-Objekte; Umlagern-Spalte auf der Bibliotheksseite. Keine neue Datenbank, keine Parallelarchitektur. |
| Tests und Soll-Ist-Vergleich | Soll: ganze Kiste per Zielcode umlagern, alle Pfade rekursiv korrekt, Zyklen abgelehnt, Rechte geprüft. Ist gemessen: 5/5 Blackbox-HTTP-Tests (`test_library_location_move_http.py`: Kiste→anderer Raum mit Unterbox+2 Büchern, Zyklus-400 ohne Änderung, unbekanntes Ziel 400/unbekannter Standort 404/anonym 302, oberste Ebene+Selbst-400, Formularmarker); Nachbar-Suites 32/32; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere P1/P2-Slices aus #1111): Standortvorlagen, Scanmodi pro Aktion, Exemplare für alle Objekttypen, Mengen/Seriennummern, System-/manuelle Metadatenwerte, CSV-Import, Inventur/Differenzen, Objekt-Verleih, föderierte Rechte. |

## 19. November 2026 · Issue #1111 (P1-Slice 4): Exemplare für alle Objekttypen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Vierter P1-Slice: physische Exemplare (eigene ID, eigener Standort) gab es nur für Bücher (`Nur Bücher`-Tor, Test-pin bleibt). Bestand wiederverwendet: Werk/Ausgabe/Exemplar-Identitäten, Zuordnungsroute, Detailseite, Auditmuster. |
| Implementierung | `catalog.py`-Kern mit `require_book`-Schalter refactoriert (Buchverhalten und -meldungen unverändert), generische `object_copies`/`create_object_copy`/`object_copy_inventory` daneben; neues Modul `copy_routes.py` mit `GET/POST /library/objects/<id>/copies` (JSON/Flash, Audit, Login); Exemplar-Karte mit Liste und Anlege-Button auf der Inventar-Detailseite. Seriennummern werden nicht vererbt. |
| Tests und Soll-Ist-Vergleich | Soll: zwei PCs als Exemplare an zwei Orten, keine Serien-Dubletten, Validierung, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (2 PCs + Standorte + Liste, Serien-Nichtvererbung, 404/400-Fälle, anonym 302, Detailmarker); Nachbar-Suites 67/67 inkl. Buch-Gate-Test; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, System-/manuelle Metadatenwerte, CSV-Import, Inventur/Differenzen, Objekt-Verleih, föderierte Rechte. |

## 19. November 2026 · Issue #1111 (P1-Slice 3): Mengen und Einzelstücke unterscheiden

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Dritter P1-Slice: Stapelartikel (50 Schrauben) und Einzelstücke (Seriennummer) waren im Modell nicht unterscheidbar; Seriennummern existierten nur als Freitextfeld, Mengen gar nicht. Bestand wiederverwendet: `ObjectStore`-Validierungsmuster, Erfassungsroute, Bibliotheks-Aktualisierungspfade. |
| Implementierung | Optionale `quantity` (≥ 0, max. 3 Dezimalen, Komma zulässig) + `unit` (≤ 24 Zeichen) in `ObjectStore` (leer = Einzelstück, alte Datensätze tolerant); Durchreichen im Erfassungsformular (Menge/Einheit mit Vorschlagsliste) und Bestandsanzeige auf der Detailseite; Mengen bleiben bei Bibliotheks-Zuordnung/Identität erhalten, neue Exemplare starten als Einzelstück. |
| Tests und Soll-Ist-Vergleich | Soll: Stapel vs. Stück anlegen/anzeigen/validieren, Zuordnung/Kopie ohne Mengenverlust. Ist gemessen: 4/4 Blackbox-HTTP-Tests (50 Stück, Einzelstück+Seriennummer, 4 ungültige Eingaben 400, Zuordnung+Kopie erhalten Quelle); Nachbar-Suites 59/59; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, Exemplare für alle Objekttypen, System-/manuelle Metadatenwerte, CSV-Import, Inventur/Differenzen, Objekt-Verleih, föderierte Rechte. |

## 19. November 2026 · Issue #1111 (P1-Slice 2): Lagerort-Vorlagen für verschachtelte Strukturen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Zweiter P1-Slice nach der Umlagerung: Regale, Schränke, Kartons und Kommoden mussten Fach für Fach einzeln angelegt werden. Bestand wiederverwendet: `create_location` (Codes, Ereignisse, Regal-Objekte), Routenmuster aus Scan-/Move-Modulen, Bibliotheksseite. |
| Implementierung | Neues Modul `location_templates.py` (4 Vorlagen: Regal, Schrank, Umzugskarton, Kommode; Zählung 1–max, Standard je Vorlage) plus `template_routes.py` mit `POST /library/locations/from-template` (JSON/Formular, Audit, Login) und `GET /location-templates`; Vorlagen-Formular auf der Bibliotheksseite; Vorlagen unter beliebigem Standort instanziierbar (beliebig tiefe Schachtelung durch Komposition). |
| Tests und Soll-Ist-Vergleich | Soll: ganze Struktur in einer Aktion, Pfade rekursiv korrekt, Validierung, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (Regal 3 Fächer mit Pfad, Kommode-Standard unter Kiste Tiefe 4, 4 Validierungs-400, anonym 302, Katalog+Formularmarker); Nachbar-Suites 23/23; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, Exemplare für alle Objekttypen, Mengen/Seriennummern, System-/manuelle Metadatenwerte, CSV-Import, Inventur/Differenzen, Objekt-Verleih, föderierte Rechte. |

## 19. November 2026 · Maze-Tippmodus: Solve-Verhalten verifiziert, Vollbild-Fit hergestellt

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Live-Reproduktion per Headless-Chromium mit echter Tastatur: letzter Buchstabe löst exakt (Solve im selben Tastenschlag wie Aufdecken), Falschbuchstabe ohne Fortschritt, Pfeile ignoriert, keine JS-Fehler. Echter Befund: Panel 944 px auf 360×800 — kein Vollbild-Fit (Header 217 + Titel 165 + Panel). |
| Implementierung | Nur CSS + 1 JS-Token (999-Zeilen-Gate): `is-typing`-Klasse am Panel, Kompaktregeln ≤576 px (Buchstaben 44 px/5 Spalten, Karte 38vmin, Optionen 2×2, Task 1,5rem), Kids-Nav und Seittitel während der Maze-Runde per `:has()` ausgeblendet (Zurück-Button bleibt, Nav kehrt automatisch zurück), Statuszeile einzeilig. Desktop unverändert. |
| Tests und Soll-Ist-Vergleich | Soll: Solve exakt beim letzten Buchstaben, alles ohne Scrollen sichtbar. Ist gemessen: Solve-exakt per Tastatur in 5/5 Live-Läufen; Panel unten 748–790 px (Worst-Case 5-Buchstaben-Wort 749), vorher 955–1026. Neu: Browser-Test Tastatur-Solve + Fit ab Seitenanfang + Nav-Rückkehr, statischer Pin für Klasse/CSS. 7/7 Python-, Node-Suite grün, `node --check`, Gates grün. |
| Review und Abschluss | Offen als Follow-up: Weiter/Zurück-Buttons liegen 160 px unterhalb (kleiner Scroll, Fokus springt automatisch); `:has()` braucht moderne Browser, sonst wieder Scrollen; physische Geräteprüfung. |

## 19. November 2026 · Verknüpfungen: Aufgaben und Dokumente beidseitig verbinden

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Modelle vorhanden: Aufgaben mit `document_ids`/`contact_id`/`project_id`, Dokument-Relationen mit Zielauflösung, Aufgaben-Rücklinks in Detaildaten (ungenutzt im Template). Lücken: kein Anheften per Suche, keine Rücklinks auf Detailseiten, Picker fehlt. Keine zweite Datenbank nötig. |
| Implementierung | Neue Route `POST /tasks/<id>/documents` (Sichtbarkeitsprüfung beider Seiten, idempotent, max. 50, Verlauf via Update); Picker je Aufgabenkarte (Live-Suche über Quick-Search-API, POST, Reload); Rücklink-Karte auf Dokumentdetailseite (Titel, Status, Fälligkeit); defekte Referenzen zeigen vorhandenen Hinweis statt Link. |
| Tests und Soll-Ist-Vergleich | Soll: vollständiger Fall Anheften→beide Richtungen→Defekt→Rechte. Ist gemessen: 4/4 Tests (Rücklink erscheint, Idempotenz, unbekannte ID + anonym abgewiesen, Defekt-Hinweis, Picker-Marker). Ruff, Gates, Compile grün. |
| Review und Abschluss | Offen als Follow-up: Picker für Kontakte/Projekte/Termine/Chats nach gleichem Muster, Verknüpfungen entfernen-UI, Verknüpfungsübersicht pro Objekt. |

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
## 19. November 2026 · Mobile Arbeitsoberfläche: Schnellzugriff und Aktionsbutton

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Mobil kostete jedes Hauptziel 3 Taps (Hamburger, Dropdown, Link): Suche (Hamburger, Formular, Absenden), Kalender (Hamburger, Kommunikation, Kalender), Dateien (Hamburger, Dokumente, Dokumente), Aufgaben (Hamburger, Arbeit, Tasks), Chats (Hamburger, Kommunikation, Chat). Wiedervorlage existiert nur als Prüfungsregel mit Aufgabenerzeugung (Inventar/Bibliothek), kein zentrales Hub. Fundament vorhanden und wiederverwendet: `mobile-viewport.css`/`mobile_viewport.js` (Safe-Area, Tastatur-Status), alle expliziten Touch-Ziele ≥ 44 px. |
| Implementierung | Neue `_mobile_workspace.html` (eingebunden für angemeldete Nutzer): Bottom-Bar mit 5 Direktlinks (64 px Ziele, Active-Markierung) nur unter 992 px, Aktionsbutton (56 px) mit kontextabhängigem Menü (Kalender/Aufgaben/Dateien/Chats: Termin, Wiedervorlage→Anlegeformular, Aufgabe, Erfassen, Text kopieren, Teilen), Web-Share-API mit Zwischenablage-Fallback, Textkopie aus Auswahl oder Titel+URL mit Rückmeldung, Button versteckt bei offener Tastatur, Body-Polster gegen Überdeckung. Desktop unverändert. Feature-Flags wie Hauptnavigation, kein Menü im Kids-Modus. |
| Tests und Soll-Ist-Vergleich | Soll: 1 Tap zu jedem Hauptziel, 2 Taps zu Kontextaktionen, kein Desktop-Bruch. Ist gemessen: 5/5 Python-Tests (Marker, Links, Aktionen inkl. Wiedervorlage/Kopieren, Kids-Ausschluss, CSS-/JS-Regeln), Browser-Tests (Chromium, CI): 390×844 Ein-Tap-Navigation mit Active-Markierung und ≥ 44 px Zielen, Aktionsmenü mit Wiedervorlage-Fokus unter /tasks/, Kopie in Zwischenablage verifiziert, Escape schließt, Desktop 1280 versteckt Bar und Button. `node --check`, Policy- und Größen-Gates grün. |
| Review und Abschluss | Offen: Wiedervorlage-Hub als eigenes Aktionsziel (eigener Auftrag), physische Geräteprüfung (Safe-Area, Tastatur, PWA), Desktop-Funktionen unangetastet verifiziert. |

## 19. November 2026 · Dateimanager-Brücke: lokale Dateien per Desktop-Shell zeigen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Keine Reveal-Funktion vorhanden. Tauri lehnt Datei-/Shell-Zugriff per Capability ab → nur Electron. Electron startet Backend lokal auf 127.0.0.1 und stellt Bridge (`simpleOfficeDesktop`, IPC mit Absenderprüfung). Speicherabstraktion (Legacy/Shadow/V2/verschlüsselte Blobs, Overlays): nur echte lokale Dateien eignen sich. `localhost` allein beweist nichts → Nachweis ist die Bridge-Präsenz in der First-Party-Shell. |
| Implementierung | Server-Endpunkt `GET /documents/<id>/local-path` (Login, Katalogauflösung, Leserecht via Storage-`stat`, Containment, reguläre Datei) liefert absoluten Pfad oder `{available:false}`; startet nie Prozesse. Electron: `file:reveal`-Handler mit Absender- plus Pfadprüfung (`reveal.js`: absolut, existent, Datei, plattformgerecht), `shell.showItemInFolder` (Explorer/Finder/Dateimanager inkl. Dateiauswahl, ohne Subprozess). Button nur mit Bridge sichtbar (Detailseite); sonst Hinweis statt Aktion. |
| Tests und Soll-Ist-Vergleich | Soll: Reveal nur für lesbare lokale Dateien, kein Traversal/Injection, Linux/Windows/macOS. Ist gemessen: 5/5 Python-Tests (Pfad absolut+Datei, 404, Login, fehlende Datei→`not_local`, Marker), 6/6 Node-Plattformtests (posix/win32/darwin, relativ/fehlend/Verzeichnis/Fehlformen ohne fs-Zugriff), Browser-Tests (Chromium, CI): ohne Bridge versteckt, mit simulierter Bridge Pfadaufruf. `node --check`, Ruff, Gates grün. |
| Review und Abschluss | Offen als Follow-up: Reveal-Aktion in Schnellsuche/Dateilisten nach deren Merge, Praxisprüfung in Desktop-Builds je OS, Tauri bleibt ohne Shell-Zugriff (Design). |

## 21. November 2026 · Issue #679: Federation-Testnetz-Slice (fed-testnet)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Bestand gegen #679 geprüft, kein zweites Peer-/Trust-System gebaut: Peer-Discovery (LAN/direkt/Land/E-Mail, QR-Scan + manuelles Einfügen, manuelle IP-/URL-Eingabe via `/discover/direct`), Trust/Autorisierung (gerichteter Trust-Store, deny-by-default Capabilities), Discovery-Routen (well-known, V3-Capabilities, Pairing claim/status, Verzeichnis) und `compose.federation-test.yaml` (peer-a/peer-b/relay, internes Netz, getrennte Volumes/Identitäten, Seed-/Smoke-Werkzeuge, Docker-CI-Job) existieren bereits. Lücken: keine Compose-Validierung als Test, kein Discovery-Routen-Blackbox über zwei isolierte Instanzen, keine dokumentierten Discovery-Szenarien für das isolierte Netz. UI-Darstellung (Fähigkeiten, Erreichbarkeit, Trust-Status) und manuelle URL-/IP-Eingabe sind vorhanden, keine Änderung nötig. |
| Planung | Slice: (1) Compose-Validierungstest ohne Docker/Netz, (2) Discovery-Blackbox über zwei getrennte Datenstände (Hinweis ≠ Trust, keine Umetikettierung konfigurierter Peers, Instanztrennung), (3) README-Abschnitt mit Discovery-Szenarien (direkter Weg statt Broadcast-Scan, Private-Ziele-Opt-in, QR/Pairing-Grenzen). Verbote eingehalten: kein TLS-Aufbrechen, kein heimliches Relay (expliziter Opt-in bleibt), kein Pairing ohne beidseitige Bestätigung (nur Doku + Tests, kein Protokollumbau). |
| Implementierung | Neu: `tests/test_federation_testnet_compose.py` (7 Tests: exakt 3 Dienste, `internal: true`, keine Ports/Host-Modi, getrennte Volumes/Identitäten, unterschiedliche Relay-Token, gepinnte Test-only-Werte ohne Secret-Passthrough, Image-Healthcheck, Doku-Marker) und `tests/test_federation_testnet_discovery.py` (4 Tests: well-known je Instanzadresse mit `no-store`, Discovery ohne Trust/Aktivierung/Datenrecht, Wiederentdeckung erhält Betreiber-Entscheidung, Instanztrennung). Geändert: `deploy/docker/README.md` (neuer Abschnitt „Discovery scenarios in the isolated test network“). Kein Produktionscode geändert. |
| Tests | Neu 11/11 bestanden; Nachbarn `test_federation_peer_discovery`, `test_federation_discovery_http`, `test_deployment_assets`, `test_federation_testnet_seed`, `test_v3_federation_two_peer_http`: kombiniert 63/63 OK. Gates: `ruff check`, `compileall`, `tools/check_project_policy.py`, `check_file_size.py` (Limit 1000), `check_function_size.py` (Limit 300) grün. Docker-Live-Lauf nicht lokal möglich (kein Docker-Host); Abdeckung über den bestehenden Ubuntu-Docker-CI-Job. |
| Soll-Ist-Vergleich | Soll: reproduzierbarer Mehrknotenaufbau + Discovery-Szenarien + kleine Lücken schließen, ohne Doppelbau. Ist: Compose-Netz war vorhanden und ist nun per Test fixiert; Discovery-Hinweis-Charakter und Instanztrennung sind per Blackbox belegt; Discovery-Szenarien sind dokumentiert. Offen: realer LAN-/Extern-Peer-Nachweis, Langzeit-/Lastbetrieb und Praxislauf auf Geräten (bleibt in #679, kein Merge in diesem Auftrag). |
| Review | Unabhängige Gegenprüfung: keine neuen Trust-/Pairing-Pfade, keine Secrets in Tests oder Compose (nur `test-only`-/`relay-`-Marker), SSRF-/DNS-Rebinding-Schutz unverändert (gepinnter Fetcher, strenger Hostnamenpfad, Opt-in für private Ziele), keine bestehenden Tests abgeschwächt. |
| Abschluss | Branch `feature/federation-testnet`, PR zur Koordinaten-Review geöffnet, **nicht gemergt**. Folgeauftrag: realer Mehrknoten-/LAN-Nachweis und optionaler autorisierter Relay als getrennte Abnahme. |

## 21. November 2026 · FinTS Slice 1 (read-only) · fints-ro · Issue #738

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Ausgangsbasis `origin/main`: Slice-1-Bestand verifiziert — `app/finance_fints.py` (Discovery + Bankfähigkeiten/TAN-Verfahren), `app/finance_fints_balances.py` (lesender Saldo + verfügbarer Saldo), `app/finance_web.py` (Konten-Discovery mit Fähigkeitsanzeige, Saldo-Report, TAN-Challenge-Flow), Templates (`bank_discovery`, `balance_report`, `balance_challenge`), verschlüsselte owner-gebundene Single-Use-Challenge-Ablage. Keine SEPA-/Transfer-Routen oder -APIs im Finance-Bereich. Lücke: kein Mock-Bank-Nachweis für Timeout, Berechtigungen und Geheimnisfreiheit in Logs. |
| Planung | Ausschließlich im Worktree `/tmp/wt-fints` (Branch `feature/fints-balances`) arbeiten, keine neue Banking-Architektur, keine Produktionscode-Änderung: eine Regressionssuite mit Mock-Bank (ohne `fints`-Lib) für Salden, Fähigkeiten, Timeout, Fehler, Berechtigungen, Geheimnisfreiheit. PIN/TAN nirgends in DB/Logs/Antworten/Sessions; Transaktionen/SEPA bleiben unberührt bzw. deaktiviert. |
| Implementierung | Nur `tests/test_finance_fints_slice1.py` neu (320 Zeilen, 10 Tests). Zwischenstand: 9/10 grün; Re-Login im Berechtigungstest scheiterte zunächst am CSRF-Schutz (403 → als Owner 200 statt 404). Root Cause: Test-Helper ohne CSRF-Header; Fix im Helper, kein Produktionscode. |
| Tests | Neu 10/10 grün. Nachbarn 56 Tests OK (1 Skip: optionales Banking-Extra fehlt): `test_finance_fints{,_balances,_balance_sca,_balance_http,_capability_status,_retry,_sync_http,_challenges,_transactions_sca}`. Gates grün: ruff, compileall, policy, file-size, function-size. |
| Soll-Ist-Vergleich | Soll: lesende Salden + verfügbare Salden per Mock-Bank — Ist: nachgewiesen. Soll: Fähigkeiten/TAN-Verfahren ermitteln, Fehlendes deaktiviert — Ist: `GET_BALANCE=False` blockiert Abruf per `FinTSFeatureUnavailable` ohne Abrufversuch, TAN 942 gelistet. Soll: Timeout/Fehler/Berechtigungen/Geheimnisfreiheit — Ist: 3 begrenzte Versuche, generische Fehler, Fremdnutzer-404 ohne Fetch, kein PIN/TAN in Seite/Logs/Flashes/DB (adversarialer Fehlertext geprüft). Soll: SEPA deaktiviert — Ist: keine Finance-SEPA-/Transfer-Routen, keine Adapter-APIs. |
| Review | Unabhängige Prüfung steht aus: Koordinator reviewt Banking separat. Hinweis ohne Änderung: `finally: pin = ""` löst nur die lokale Referenz (kein Speicher-Wipe); Geheimnisfreiheit beruht auf Nicht-Persistenz + `no-store` + typisierten Logs — per Tests nachgewiesen. |
| Abschluss | Commit `557bf8ab`, PR #1152 (`feature/fints-balances` → `main`), NICHT gemergt. Offen: Koordinator-Review, manueller Praxistest am Endgerät durch Eigentümer nach Merge, echte Bank-Anbindung nur mit bewusster Freigabe. |

## 21. November 2026 · Issue #1111 (P2-Slice): CSV-Import mit Mengen-/Einheit-Spalten

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | CSV-Import kannte Menge/Einheit nicht; Stapelartikel mussten einzeln nachgepflegt werden. Bestand wiederverwendet: `ImportStageStore`-Staging, `ObjectStore`-Validierung (`_quantity`/`_unit`, Einheit max. 24 Zeichen), keine neue DB. |
| Implementierung | `COLUMNS`-Mapping um `quantity` (menge/anzahl/bestand/qty/count/stueckzahl/stückzahl) und `unit` (einheit/masseinheit) erweitert; `validate_row` normalisiert und meldet ungültige Mengen/zu lange Einheiten auf Deutsch; JSON- und HTML-Vorschau zeigen Menge/Einheit, Commit legt sie per `ObjectStore.create` an. |
| Tests und Soll-Ist-Vergleich | Soll: Alias-Abbildung, Vorschau, Validierung, Anlage mit Menge/Einheit, Rechte. Ist gemessen: 2 neue Blackbox-HTTP-Tests (Alias-Mapping + Commit mit Mengenanlage; ungültige Menge/zu lange Einheit), 7/7 CSV-Suite, 366/366 `test_inventory*.py`; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Leih-/Reservierungs-Überschneidungscheck (eigener Slice), Scanmodi pro Aktion, Fotoerkennung, Offline-Sync, föderierte Rechte. |

## 21. November 2026 · Issue #1111 (Slice): Leih-/Reservierungs-Überschneidungscheck

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Checkout und Reservierung prüften nur ihre eigene Store-Hälfte (Leihe blockt Leihe, Reservierung blockt Reservierung); eine Leihe konnte in eine aktive Reservierung hineinlaufen und umgekehrt. Bestand wiederverwendet: `LendingStore`/`ReservationStore` mit eigenen Locks/Dateien, keine neue DB, keine Auto-Stornierung. |
| Implementierung | `LendingStore.blocking_loan` und `ReservationStore.conflicts` als reine Prüfungen; Checkout lehnt bei überlappender aktiver Reservierung ab, Reservierung bei überlappender offener Leihe — je mit klarer deutscher Meldung (Zeitraum + Person). Ungültige Daten liefern weiterhin die bestehenden Validierungsfehler. |
| Tests und Soll-Ist-Vergleich | Soll: beide Blockrichtungen, Freigabe außerhalb des Fensters, stornierte Reservierung/Rückgabe geben frei, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (beide Richtungen, Fenster-daneben erlaubt, Storno/Rückgabe entsperrt); Nachbar-Suites 13/13 (lending/reservations/neu), 369/369 `test_inventory*.py`; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, Fotoerkennung, Offline-Sync, föderierte Rechte. |

## 23. November 2026 · Übersetzungslücken (DE/EN), Branch `fix/i18n-dynamic-ui-coverage`

| Phase | Arbeitsstand und Nachweis |
|---|---|
| Analyse | Ausgangsbasis `origin/main@628369d4`. Drei unabhängige Perspektiven prüften JavaScript-UI, Server/Templates und Tests. Befund: 987 Katalogschlüssel haben bereits DE/EN-Parität; `i18n.js` übersetzte nur den vorhandenen DOM beim einmaligen Laden. Dynamische Widgets sowie ganze Vorlagen und Geräte-/Statusmeldungen umgingen den Katalog. Nutzertexte und gespeicherte Werte müssen unangetastet bleiben. |
| Planung | Gemeinsame Runtime-Funktion `SimpleOfficeI18n.text(de,en)` für ausdrücklich paarige dynamische UI-Texte; keine pauschale Übersetzung von Nutzerdaten, IDs, Statuscodes oder HTML. Die Funktion muss vor synchron geladenen Page-Scripts bereitstehen. Projektcockpit, Kalender-Schnellfunktionen, Ortstermin-Gerätehinweise, Android-Offline-Fallbacks, Screen-Supportstatus, Kontakt-Firmen-Autovervollständigung sowie drei klar deutschsprachige Templates abdecken. Folgeprüfung: Bookmark-Seite, Audio-Streamer-API und auditierte Repository-Arbeitszentrale übernehmen. Literale `tr(...)`-/`translate(...)`-Schlüssel dauerhaft auf Katalogvollständigkeit prüfen. |
| Implementierung | Geändert: `templates/layout.html`, `static/js/i18n.js`, `app.js`, `calendar_quick_wins.js`, `site_visits.js`, `v3_android_offline.js`, `screen_support.js`, `company-contact-autocomplete.js`, `business-template-editor.js`, `repository_workspace.js`, `shopping.js`, `library_scan_session.js`, `federation_qr_scan.js`, `federation_browser_diagnostics.js`, `mobile_viewport.js`; `templates/repository_workspace.html`, `templates/bookmarks/index.html`, `templates/bookmarks/import_preview.html`, `app/browser_bookmarks.py`, `app/audio_streamer_admin.py`, `app/library/scan_routes.py`; neue DE/EN-Schlüssel in `app/translations.py`; Vorlagenübersicht für visuelle Geschäftsvorlagen, Datenlogger-Einstellungen und generische V3-Detailansicht auf Katalogeinträge umgestellt. Bibliotheks-Scan-API-Fehler richten sich nun nach der Sitzungssprache. Im Projektcockpit kommt der Filterstatus nun aus dem kanonischen `data-task-status` statt dem übersetzbaren Badge-Text. Dynamische Namen und Inhalte werden nicht verändert. Neue Suiten: `tests/test_i18n_coverage.py`, `tests/translation_dynamic_modules.test.cjs` plus Lesezeichen-/Audio-, Bibliotheks-Scan-API- und Arbeitszentralen-Übersetzungsregressionen. |
| Tests | 19 neue Übersetzungsprüfungen bestanden: 10 Abdeckungs-/Chromiumtests, 2 Bookmark-Routen-/Renderprüfungen, 1 zweisprachige Audio-Katalogprüfung, 1 DE/EN-Arbeitszentrale-Renderprüfung, 3 zusätzliche englische UI-/Diagnostikprüfungen und 2 Bibliotheks-Scan-API-Sprachprüfungen. Bookmark-Importvorschau wurde auf Deutsch und Englisch über den echten Upload- und Preview-Pfad geprüft. Repository-Arbeitszentrale: Auth-/Routen-Suite 32/32; Node-Frontend-Regressionen 15/15, darunter englische dynamische Status-, Anmelde- und Markdown-Exporte. Browserdiagnostik: 9/9 Node-Tests; Mobile-, Bibliotheks-, Einkaufs- und Kamera-Nachbarsuiten: 31/31. Voller ausgewählter Validierungslauf nach allen Änderungen: 107/107 Python- und 25/25 Node-Tests. Testhost protokollierte Playwright-`asyncio`-Cleanup-Warnungen ohne Fehlschlag. Vorherige sechs i18n-Module, Datenlogger und Geschäftsvorlagen: 171/171 bestanden. `node --check` für geänderte JS-Dateien, Python-Compile, Projektregeln und Funktionsgrößenprüfung bestanden. Dateigrößenprüfung bleibt wegen des bestehenden Main-Befunds `app/mail_reader_routes.py` (1008 statt maximal 1000 Zeilen) rot; die Datei wurde hier nicht verändert. |
| Soll-Ist-Vergleich | Soll: neue dynamische Bedienoberflächen, die drei Vorlagen, Bookmark-Seiten, Audio-API-Meldungen, Repository-Arbeitszentrale sowie Einkauf, Bibliotheks-Scan, Federation-QR/Diagnostik und mobile Zurück-Schaltfläche folgen DE/EN gemäß Sitzungssprache. Ist: für diese Pfade per Browser-, Render-, Upload-, Node-, API- und Katalogprüfungen belegt. Noch offen: weitere bisher nicht vertieft geprüfte Module aus dem 4.760-Dateien-Inventar. Die Übersetzung der gesamten Codebasis ist daher noch nicht vollständig bestätigt. |
| Review | Integrator prüfte den finalen Diff sowie Textausgaben: keine HTML-Interpolation, keine Übersetzung gespeicherter oder vom Benutzer stammender Bezeichnungen; DOM-Schreibvorgänge bleiben `textContent`/Attributwerte. Statusverarbeitung in Projektaufgaben nutzt den kanonischen Statuswert. Die Templates für Geschäftsvorlagen, Datenlogger, Bookmark-Verwaltung und Repository-Arbeitszentrale besitzen DE/EN-Anzeigen. Noch offen: V3-Detailansicht mit einer konkreten Fixture direkt per Route rendern und verbleibende Bereiche systematisch nach Risiko/Nutzerwirkung portieren. |
| Abschluss | Technischer Batch zur Review vorbereitet; nicht gemergt. Keine Aussage, dass alle sichtbaren Texte der Codebasis bereits zweisprachig sind. Vor Merge: finale PR-Diffprüfung und CI; PR-Beschreibung erhält die gewünschte Vorher/Nachher-Skizze. |
### Mail · Lesbare Nachrichtenliste

- **Ist und Soll.** Im Titelblock eingebettete Stile und Skripte wurden als Titeltext interpretiert. Alle Felder drängten sich in eine schmale Tabelle. Betreff und Absender sollen eine lesbare Zeile erhalten.
- **Umsetzung.** Stylesheet im Dokumentkopf, Skript nach dem Markup. Absender und Datum stehen über dem Betreff, zusätzliche Metadaten darunter. Die Ansicht merkt sich die Feldauswahl je Konto. Auf kleinen Displays lässt sich die Ordnerliste ausklappen.
- **Prüfung.** Authentifizierter Mailreader mit synthetischem IMAP-Konto in Chromium bei 390, 1024 und 1900 Pixeln: Betreff sichtbar und anklickbar, Empfänger einblendbar, Standardansicht wiederherstellbar. Die Standardzeile maß rund 70 Pixel Höhe. 24 Mailreader- und Vorlagentests bestanden.
- **Sicherheitsreview.** Anzeigeänderung an vorhandenen Jinja-Feldern und Links. Das Datum wird über textContent formatiert. IMAP-Aufrufe, Rechte und Mailinhalte wurden für diese Änderung nicht erweitert.
## 21. November 2026 · Issue #1111 (P2-Slice): CSV-Import mit Mengen-/Einheit-Spalten

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | CSV-Import kannte Menge/Einheit nicht; Stapelartikel mussten einzeln nachgepflegt werden. Bestand wiederverwendet: `ImportStageStore`-Staging, `ObjectStore`-Validierung (`_quantity`/`_unit`, Einheit max. 24 Zeichen), keine neue DB. |
| Implementierung | `COLUMNS`-Mapping um `quantity` (menge/anzahl/bestand/qty/count/stueckzahl/stückzahl) und `unit` (einheit/masseinheit) erweitert; `validate_row` normalisiert und meldet ungültige Mengen/zu lange Einheiten auf Deutsch; JSON- und HTML-Vorschau zeigen Menge/Einheit, Commit legt sie per `ObjectStore.create` an. |
| Tests und Soll-Ist-Vergleich | Soll: Alias-Abbildung, Vorschau, Validierung, Anlage mit Menge/Einheit, Rechte. Ist gemessen: 2 neue Blackbox-HTTP-Tests (Alias-Mapping + Commit mit Mengenanlage; ungültige Menge/zu lange Einheit), 7/7 CSV-Suite, 366/366 `test_inventory*.py`; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Leih-/Reservierungs-Überschneidungscheck (eigener Slice), Scanmodi pro Aktion, Fotoerkennung, Offline-Sync, föderierte Rechte. |

## 21. November 2026 · Issue #1111 (Slice): Leih-/Reservierungs-Überschneidungscheck

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Checkout und Reservierung prüften nur ihre eigene Store-Hälfte (Leihe blockt Leihe, Reservierung blockt Reservierung); eine Leihe konnte in eine aktive Reservierung hineinlaufen und umgekehrt. Bestand wiederverwendet: `LendingStore`/`ReservationStore` mit eigenen Locks/Dateien, keine neue DB, keine Auto-Stornierung. |
| Implementierung | `LendingStore.blocking_loan` und `ReservationStore.conflicts` als reine Prüfungen; Checkout lehnt bei überlappender aktiver Reservierung ab, Reservierung bei überlappender offener Leihe — je mit klarer deutscher Meldung (Zeitraum + Person). Ungültige Daten liefern weiterhin die bestehenden Validierungsfehler. |
| Tests und Soll-Ist-Vergleich | Soll: beide Blockrichtungen, Freigabe außerhalb des Fensters, stornierte Reservierung/Rückgabe geben frei, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (beide Richtungen, Fenster-daneben erlaubt, Storno/Rückgabe entsperrt); Nachbar-Suites 13/13 (lending/reservations/neu), 369/369 `test_inventory*.py`; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, Fotoerkennung, Offline-Sync, föderierte Rechte. |

## 21. November 2026 · Issue #1136 Slice 4: IMAP Tags/Keywords (PERMANENTFLAGS-Sync, Reader-Badges)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Bestand: `ImapWebClient` kannte nur Seen/Flagged/Answered, keine Keywords; SELECT-Antwort wurde auf Count reduziert, PERMANENTFLAGS nie ausgewertet; Reader zeigte keine Kennzeichnungen. Vorgabe: Sync nur nach IMAP-Capability, `require_writable` beachten, Readonly zeigt nur. |
| Implementierung | `app/mail_webclient.py`: `normalize_keyword` (IMAP-Atom, max 50, System-Flags abgelehnt), `keywords_of` (Nicht-`\`-Flags), `_select_detailed` + `_permanent_flags` (SELECT parsen inkl. `connection.response("PERMANENTFLAGS")`), `_raw_message` liefert zusätzlich PERMANENTFLAGS, `message()`/`messages()` mit `keywords` + `keyword_support`, `set_keywords` (require_writable, `\*`-Pflicht, STORE +FLAGS/-FLAGS.SILENT, Audit `imap_message_keywords_changed`). Neues Modul `app/mail_keyword_routes.py` (eigener Blueprint, gleiche URL-Basis; `mail_reader_routes.py` stand bei 982/1000 Zeilen). Template: Badges in Liste und Vorschau, Schreib-Formular nur bei `not read_only` + Capability, sonst Hinweistext. Doku: Abschnitt in `docs/MAIL_ACCOUNT_SETUP.md`. Bestehender Mock in `tests/test_mail_webclient.py` auf neues 3-Tupel angepasst (bewusste Vertragserweiterung). |
| Tests und Soll-Ist-Vergleich | Soll: Capability-gesteuerter Sync, Readonly ohne STORE, Badges sichtbar, Gates grün. Ist gemessen: 5/5 Blackbox-HTTP-Tests mit Fake-IMAP (`tests/test_mail_keywords_http.py`: Sync+Badges+Formular schreibend, Readonly ohne STORE/Formular, unbekannte/fehlende Capability und ungültiges Keyword ohne STORE), 89/89 Mail-Nachbarsuites, Ruff/Compileall/Policy/Datei-/Funktionsgrößen grün. |
| Review und Abschluss | Slice gemergt in [PR #1160](https://github.com/JensKapitza/SimpleOffice4Me/pull/1160) am 22.11.2026. Für diesen Slice keine offenen Punkte; der übergeordnete Ausbau #1136 bleibt für nicht erledigte Produktwünsche offen. |

## 22. November 2026 · PR-Dokumentationsabgleich und Issue #1155

| Phase | Arbeitsstand und Nachweis |
|---|---|
| Analyse | `gh pr list --state open` ergab 0 offene PRs. Der Abgleich der 35 zuletzt gemergten PRs mit diesem Mesh und `docs/WORKLOG_2026-10-26.md` fand viele Commits unter Feature-/Arbeitsbranch-Namen, aber keine PR-ID-Verknüpfung. Bereits über PR-ID belegt waren in diesem Ausschnitt #1171 und #1152. Unverknüpfte gemergte PRs: [#1174](https://github.com/JensKapitza/SimpleOffice4Me/pull/1174), [#1173](https://github.com/JensKapitza/SimpleOffice4Me/pull/1173), [#1172](https://github.com/JensKapitza/SimpleOffice4Me/pull/1172), [#1170](https://github.com/JensKapitza/SimpleOffice4Me/pull/1170), [#1169](https://github.com/JensKapitza/SimpleOffice4Me/pull/1169), [#1168](https://github.com/JensKapitza/SimpleOffice4Me/pull/1168), [#1167](https://github.com/JensKapitza/SimpleOffice4Me/pull/1167), [#1166](https://github.com/JensKapitza/SimpleOffice4Me/pull/1166), [#1165](https://github.com/JensKapitza/SimpleOffice4Me/pull/1165), [#1164](https://github.com/JensKapitza/SimpleOffice4Me/pull/1164), [#1163](https://github.com/JensKapitza/SimpleOffice4Me/pull/1163), [#1162](https://github.com/JensKapitza/SimpleOffice4Me/pull/1162), [#1161](https://github.com/JensKapitza/SimpleOffice4Me/pull/1161), [#1160](https://github.com/JensKapitza/SimpleOffice4Me/pull/1160), [#1159](https://github.com/JensKapitza/SimpleOffice4Me/pull/1159), [#1158](https://github.com/JensKapitza/SimpleOffice4Me/pull/1158), [#1157](https://github.com/JensKapitza/SimpleOffice4Me/pull/1157), [#1153](https://github.com/JensKapitza/SimpleOffice4Me/pull/1153), [#1151](https://github.com/JensKapitza/SimpleOffice4Me/pull/1151), [#1150](https://github.com/JensKapitza/SimpleOffice4Me/pull/1150), [#1149](https://github.com/JensKapitza/SimpleOffice4Me/pull/1149), [#1148](https://github.com/JensKapitza/SimpleOffice4Me/pull/1148), [#1147](https://github.com/JensKapitza/SimpleOffice4Me/pull/1147) und [#1146](https://github.com/JensKapitza/SimpleOffice4Me/pull/1146). Die IDs sind damit mit dem GitHub-PR-Archiv verknüpft; vorhandene fachliche Arbeitsnachweise bleiben maßgeblich. |
| Offene Issues | Abruf vom 22.11.2026: #1154 Path-Injection/Storage/ReDoS; #1155 Redirect/XSS/Logging; #1156 Supply-Chain/Policy; #1136 Mail-Client; #1111 Inventar/Bibliothek; #874 Security-Alert-Tracking; #738 FinTS; #679 Federation; #474 Standards/Runtime-Kompatibilität. Die Issue-Liste belegt nicht, dass diese Themen vollständig unerledigt sind; mehrere haben gemergte Teilslices und benötigen Aktualisierung/Abnahme. |
| #1155 Analyse | Gewählter Slice: Rechnungsfehler aus der ZUGFeRD-Erzeugung und Finalisierung. Bisher wurden `str(exc)`-Texte bis 500 Zeichen in Rechnungsmetadaten/History persistiert; Finalisierungslogs enthielten Rechnungs-IDs. Diese Werte können lokale Pfade, Dokumentinformationen oder Integrationsdetails preisgeben. Andere #1155-Befunde bleiben offen: Redirect-Prüfung der Inventory-/SIP-Senken, aktuelle Zuordnung des WebDAV-XSS-Alarms und repräsentative Stack-Trace-Stichprobe. |
| #1155 Implementierung | `app/business_documents.py`: Timing-/Abschlusslogs ohne Rechnungs-ID; Fehlerlogs enthalten nur Arbeitsschritt und Ausnahmetyp; gespeicherte Finalisierungs- und ZUGFeRD-Fehlertexte sind generisch, History hält nur den Fehlertyp. `app/business_document_generation.py`: Ghostscript-, veraPDF- und XML-Validator-Ausgaben werden nur im Arbeitsspeicher zur Statusentscheidung ausgewertet; Logs und persistierte Diagnosefelder enthalten keine stdout-/stderr-Texte. `app/v3_finance.py`: Lifecycle-Fehler speichert nur den Ausnahmetyp. Produktionsänderung auf Branch `fix/1155-invoice-error-logging-sanitization`. |
| #1155 Tests | `tests/test_business_document_error_redaction.py` prüft, dass simulierte Pfad-/Token-Fehler weder in Rechnungs-JSON/History noch in Logs oder Validator-Ergebnissen erscheinen und dass der PDF-only-Zustand bei fehlgeschlagener ZUGFeRD-Erzeugung erhalten bleibt; `tests/test_v3_finance.py` prüft den Lifecycle-Fehlertyp. Ausgeführt: `pytest -q tests/test_business_document_error_redaction.py tests/test_business_documents.py tests/test_v3_finance.py` — 80 passed, 16 subtests passed; Ruff, Compileall, Funktionsgrößen und `git diff --check` grün. Repo-Dateigrößenregel meldet unverändert `app/mail_reader_routes.py` mit 1008 Zeilen; alle geänderten Dateien bleiben unter 1000 Zeilen. |
| Soll-Ist, offene Prüfung | Für den Rechnungsfehler-Slice erfüllt: Diagnosedaten bleiben als Arbeitsschritt/Fehlertyp erhalten; Rohtexte/IDs werden nicht mehr in Logs oder gespeicherte Fehlerfelder kopiert; gezielte Regressionen grün. #1155 ist insgesamt nicht abgeschlossen: `gh api` für aktuelle Code-Scanning-Alerts antwortet mit HTTP 403; XSS-/Redirect-/Stack-Trace-Funde sind daher nicht abschließend triagiert. Keine Dismiss-/Issue-Schließung. |
| Review | Drei unabhängige Perspektiven fanden keinen blockierenden Architektur- oder Sicherheitsfehler. Das Security-Review entdeckte zusätzlich Validator-stdout/stderr als gespeicherten/logged Datenpfad; der wurde in derselben Änderung entfernt und regressionsgeprüft. Als nicht blockierende Restlücke ist ein HTTP-Routentest für die Flash-Meldung noch offen; die generische Exception und Persistenz sind direkt getestet. |
| PR | Änderungen auf `fix/1155-invoice-error-logging-sanitization` sind in [PR #1175](https://github.com/JensKapitza/SimpleOffice4Me/pull/1175) geöffnet. #1155 bleibt für Redirect-, WebDAV-XSS- und Stack-Trace-Verifikation offen. Untracked `database/documents/.simpleoffice-meta/licensing.sqlite3` ist lokale Nutzerdatenbank und bleibt unberührt. |
## 21. November 2026 · Issue #679: Federation-Testnetz-Slice (fed-testnet)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Bestand gegen #679 geprüft, kein zweites Peer-/Trust-System gebaut: Peer-Discovery (LAN/direkt/Land/E-Mail, QR-Scan + manuelles Einfügen, manuelle IP-/URL-Eingabe via `/discover/direct`), Trust/Autorisierung (gerichteter Trust-Store, deny-by-default Capabilities), Discovery-Routen (well-known, V3-Capabilities, Pairing claim/status, Verzeichnis) und `compose.federation-test.yaml` (peer-a/peer-b/relay, internes Netz, getrennte Volumes/Identitäten, Seed-/Smoke-Werkzeuge, Docker-CI-Job) existieren bereits. Lücken: keine Compose-Validierung als Test, kein Discovery-Routen-Blackbox über zwei isolierte Instanzen, keine dokumentierten Discovery-Szenarien für das isolierte Netz. UI-Darstellung (Fähigkeiten, Erreichbarkeit, Trust-Status) und manuelle URL-/IP-Eingabe sind vorhanden, keine Änderung nötig. |
| Planung | Slice: (1) Compose-Validierungstest ohne Docker/Netz, (2) Discovery-Blackbox über zwei getrennte Datenstände (Hinweis ≠ Trust, keine Umetikettierung konfigurierter Peers, Instanztrennung), (3) README-Abschnitt mit Discovery-Szenarien (direkter Weg statt Broadcast-Scan, Private-Ziele-Opt-in, QR/Pairing-Grenzen). Verbote eingehalten: kein TLS-Aufbrechen, kein heimliches Relay (expliziter Opt-in bleibt), kein Pairing ohne beidseitige Bestätigung (nur Doku + Tests, kein Protokollumbau). |
| Implementierung | Neu: `tests/test_federation_testnet_compose.py` (7 Tests: exakt 3 Dienste, `internal: true`, keine Ports/Host-Modi, getrennte Volumes/Identitäten, unterschiedliche Relay-Token, gepinnte Test-only-Werte ohne Secret-Passthrough, Image-Healthcheck, Doku-Marker) und `tests/test_federation_testnet_discovery.py` (4 Tests: well-known je Instanzadresse mit `no-store`, Discovery ohne Trust/Aktivierung/Datenrecht, Wiederentdeckung erhält Betreiber-Entscheidung, Instanztrennung). Geändert: `deploy/docker/README.md` (neuer Abschnitt „Discovery scenarios in the isolated test network“). Kein Produktionscode geändert. |
| Tests | Neu 11/11 bestanden; Nachbarn `test_federation_peer_discovery`, `test_federation_discovery_http`, `test_deployment_assets`, `test_federation_testnet_seed`, `test_v3_federation_two_peer_http`: kombiniert 63/63 OK. Gates: `ruff check`, `compileall`, `tools/check_project_policy.py`, `check_file_size.py` (Limit 1000), `check_function_size.py` (Limit 300) grün. Docker-Live-Lauf nicht lokal möglich (kein Docker-Host); Abdeckung über den bestehenden Ubuntu-Docker-CI-Job. |
| Soll-Ist-Vergleich | Soll: reproduzierbarer Mehrknotenaufbau + Discovery-Szenarien + kleine Lücken schließen, ohne Doppelbau. Ist: Compose-Netz war vorhanden und ist nun per Test fixiert; Discovery-Hinweis-Charakter und Instanztrennung sind per Blackbox belegt; Discovery-Szenarien sind dokumentiert. Offen: realer LAN-/Extern-Peer-Nachweis, Langzeit-/Lastbetrieb und Praxislauf auf Geräten (bleibt in #679, kein Merge in diesem Auftrag). |
| Review | Unabhängige Gegenprüfung: keine neuen Trust-/Pairing-Pfade, keine Secrets in Tests oder Compose (nur `test-only`-/`relay-`-Marker), SSRF-/DNS-Rebinding-Schutz unverändert (gepinnter Fetcher, strenger Hostnamenpfad, Opt-in für private Ziele), keine bestehenden Tests abgeschwächt. |
| Abschluss | Branch `feature/federation-testnet`, PR zur Koordinaten-Review geöffnet, **nicht gemergt**. Folgeauftrag: realer Mehrknoten-/LAN-Nachweis und optionaler autorisierter Relay als getrennte Abnahme. |

## 21. November 2026 · FinTS Slice 1 (read-only) · fints-ro · Issue #738

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Ausgangsbasis `origin/main`: Slice-1-Bestand verifiziert — `app/finance_fints.py` (Discovery + Bankfähigkeiten/TAN-Verfahren), `app/finance_fints_balances.py` (lesender Saldo + verfügbarer Saldo), `app/finance_web.py` (Konten-Discovery mit Fähigkeitsanzeige, Saldo-Report, TAN-Challenge-Flow), Templates (`bank_discovery`, `balance_report`, `balance_challenge`), verschlüsselte owner-gebundene Single-Use-Challenge-Ablage. Keine SEPA-/Transfer-Routen oder -APIs im Finance-Bereich. Lücke: kein Mock-Bank-Nachweis für Timeout, Berechtigungen und Geheimnisfreiheit in Logs. |
| Planung | Ausschließlich im Worktree `/tmp/wt-fints` (Branch `feature/fints-balances`) arbeiten, keine neue Banking-Architektur, keine Produktionscode-Änderung: eine Regressionssuite mit Mock-Bank (ohne `fints`-Lib) für Salden, Fähigkeiten, Timeout, Fehler, Berechtigungen, Geheimnisfreiheit. PIN/TAN nirgends in DB/Logs/Antworten/Sessions; Transaktionen/SEPA bleiben unberührt bzw. deaktiviert. |
| Implementierung | Nur `tests/test_finance_fints_slice1.py` neu (320 Zeilen, 10 Tests). Zwischenstand: 9/10 grün; Re-Login im Berechtigungstest scheiterte zunächst am CSRF-Schutz (403 → als Owner 200 statt 404). Root Cause: Test-Helper ohne CSRF-Header; Fix im Helper, kein Produktionscode. |
| Tests | Neu 10/10 grün. Nachbarn 56 Tests OK (1 Skip: optionales Banking-Extra fehlt): `test_finance_fints{,_balances,_balance_sca,_balance_http,_capability_status,_retry,_sync_http,_challenges,_transactions_sca}`. Gates grün: ruff, compileall, policy, file-size, function-size. |
| Soll-Ist-Vergleich | Soll: lesende Salden + verfügbare Salden per Mock-Bank — Ist: nachgewiesen. Soll: Fähigkeiten/TAN-Verfahren ermitteln, Fehlendes deaktiviert — Ist: `GET_BALANCE=False` blockiert Abruf per `FinTSFeatureUnavailable` ohne Abrufversuch, TAN 942 gelistet. Soll: Timeout/Fehler/Berechtigungen/Geheimnisfreiheit — Ist: 3 begrenzte Versuche, generische Fehler, Fremdnutzer-404 ohne Fetch, kein PIN/TAN in Seite/Logs/Flashes/DB (adversarialer Fehlertext geprüft). Soll: SEPA deaktiviert — Ist: keine Finance-SEPA-/Transfer-Routen, keine Adapter-APIs. |
| Review | Unabhängige Prüfung steht aus: Koordinator reviewt Banking separat. Hinweis ohne Änderung: `finally: pin = ""` löst nur die lokale Referenz (kein Speicher-Wipe); Geheimnisfreiheit beruht auf Nicht-Persistenz + `no-store` + typisierten Logs — per Tests nachgewiesen. |
| Abschluss | Commit `557bf8ab`, PR #1152 (`feature/fints-balances` → `main`), NICHT gemergt. Offen: Koordinator-Review, manueller Praxistest am Endgerät durch Eigentümer nach Merge, echte Bank-Anbindung nur mit bewusster Freigabe. |

## 21. November 2026 · Issue #1111 (P2-Slice): CSV-Import mit Mengen-/Einheit-Spalten

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | CSV-Import kannte Menge/Einheit nicht; Stapelartikel mussten einzeln nachgepflegt werden. Bestand wiederverwendet: `ImportStageStore`-Staging, `ObjectStore`-Validierung (`_quantity`/`_unit`, Einheit max. 24 Zeichen), keine neue DB. |
| Implementierung | `COLUMNS`-Mapping um `quantity` (menge/anzahl/bestand/qty/count/stueckzahl/stückzahl) und `unit` (einheit/masseinheit) erweitert; `validate_row` normalisiert und meldet ungültige Mengen/zu lange Einheiten auf Deutsch; JSON- und HTML-Vorschau zeigen Menge/Einheit, Commit legt sie per `ObjectStore.create` an. |
| Tests und Soll-Ist-Vergleich | Soll: Alias-Abbildung, Vorschau, Validierung, Anlage mit Menge/Einheit, Rechte. Ist gemessen: 2 neue Blackbox-HTTP-Tests (Alias-Mapping + Commit mit Mengenanlage; ungültige Menge/zu lange Einheit), 7/7 CSV-Suite, 366/366 `test_inventory*.py`; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Leih-/Reservierungs-Überschneidungscheck (eigener Slice), Scanmodi pro Aktion, Fotoerkennung, Offline-Sync, föderierte Rechte. |

## 21. November 2026 · Issue #1111 (Slice): Leih-/Reservierungs-Überschneidungscheck

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Checkout und Reservierung prüften nur ihre eigene Store-Hälfte (Leihe blockt Leihe, Reservierung blockt Reservierung); eine Leihe konnte in eine aktive Reservierung hineinlaufen und umgekehrt. Bestand wiederverwendet: `LendingStore`/`ReservationStore` mit eigenen Locks/Dateien, keine neue DB, keine Auto-Stornierung. |
| Implementierung | `LendingStore.blocking_loan` und `ReservationStore.conflicts` als reine Prüfungen; Checkout lehnt bei überlappender aktiver Reservierung ab, Reservierung bei überlappender offener Leihe — je mit klarer deutscher Meldung (Zeitraum + Person). Ungültige Daten liefern weiterhin die bestehenden Validierungsfehler. |
| Tests und Soll-Ist-Vergleich | Soll: beide Blockrichtungen, Freigabe außerhalb des Fensters, stornierte Reservierung/Rückgabe geben frei, Rechte. Ist gemessen: 5/5 Blackbox-HTTP-Tests (beide Richtungen, Fenster-daneben erlaubt, Storno/Rückgabe entsperrt); Nachbar-Suites 13/13 (lending/reservations/neu), 369/369 `test_inventory*.py`; Ruff, Policy-, Größen-, Compile-Gates grün. |
| Review und Abschluss | Offen als Follow-up (weitere #1111-Slices): Scanmodi pro Aktion, Fotoerkennung, Offline-Sync, föderierte Rechte. |

## 22. November 2026 · E-Rechnung Phase 3: Freigabe-Gate + Abgleich + Archivpaket + Editor (AP4/AP5/AP6)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Auftrag AP4–AP6: Freigabe als bewusster Akt nach Finalisierung (Gate mit KoSIT-Pflicht, kein falscher Erfolg), PDF↔Snapshot-Abgleich aus dem eingebetteten CII, Archivnachweise (SHA-256), feldbezogene Fehler im Editor. Vorgefunden: `einvoice_release.py`-Entwurf mit totem Abgleich (falsche XPath `ram:Summation`, nie verdrahtet), UBL aus Live-Row statt Frozen-Snapshot, keine Pflichtfeld-, Idempotenz- oder PDF-Nachweise. |
| Implementierung | `app/einvoice_release.py` repariert/vervollständigt: Abgleich gegen echte CII-Pfade (Nummer, Datum, Währung, Summen, Steuergruppen, Positionen), UBL strikt aus `read_snapshot`, Pflichtfelder BT-10/BT-27/Käufername feldbezogen (T04), KoSIT-Gate (T17: explizite Blockade), Idempotenz per Snapshot+XML-SHA (T11), Protokoll mit PDF-/XML-/Snapshot-Hashes + Validatorversionen (AP5), Historie + Audit (nur technische Minimaldaten). Neu: `app/einvoice_release_routes.py` (Vorabprüfung, Freigabe, Protokoll, XML-Download mit Hash-Check T13), Templates `einvoice_precheck.html`/`einvoice_protocol.html`, AP6-Sektion in `invoice_detail.html`. T14 via `_managed_invoice` (404-Existenzschutz). Größe: `_write_einvoice_snapshot` → `freeze_snapshot` nach `einvoice_snapshot.py` ausgelagert (996/1000). |
| Tests und Soll-Ist-Vergleich | Soll: T04/T11/T13/T14/T17 + Abgleich + AP6-Routen. Ist gemessen: 21/21 in `tests/test_einvoice_release.py` (Abgleich gegen echten `_cii_xml`-Output: Match ok, Manipulation feldgenau erkannt; Full-Release mit KoSIT 1.6.3 ACCEPTABLE; Replay ohne Neuschrieb; Routen: fremder Benutzer auf allen 4 Routen abgewiesen, manipuliertes XML blockiert, Protokollseite rendert). Nachbarn: 15/15 Snapshot+UBL, 74/74 Business-Dokumente. Gates: Policy ok, Dateigröße ok, Funktionen ok, compileall ok, ruff ok. |
| Review und Abschluss | Offen: Phase 4 (voller T01–T20-Testplan + Negativtests), Phase 5 (Abnahme). Code-PR #1176 (Draft, Rebase auf Main), kein Auto-Merge. |

## 22. November 2026 · E-Rechnung Phase 4: T01–T20-Testplan + Negativtests

| Analyse | Testplan-Abgleich: T04/T11/T13/T14/T15/T17 bereits aus Phase 3 abgedeckt. Lücken: T01/T02/T03/T05/T06/T09/T10/T12/T16/T18/T19/T20 + Negativtests. Sondenbefund T05: KoSIT akzeptiert Müll-USt-IdNr. („INVALID!!“ → acceptable), daher eigene Format-Plausibilisierung im Gate nötig (AP4.1). |
| Implementierung | `validate_einvoice_fields`: USt-IdNr.-Format (Ländercode + 2–12 Zeichen, Leerzeichen-tolerant) und Steuernummer-Länge; greift in Vorabprüfung + Freigabe-Checkliste. `tests/test_einvoice_phase4.py` (19 Tests): T03 (20 parallele Finalisierungen, Barriere, echter Nummernkreis), T10 (Fehlerpfad), T19 (Timeout-Wiederholung, identische Nummer belegt), T01/T02/T06 (formateübergreifend), T09 (Cent-Abweichung laut, Rabatt exakt), T12 (Gutschrift eigene Nummer + Originalreferenz), T16 (Hybrid-Validierung ehrlich ohne veraPDF), T18 (Alt-Rechnung rendert), T20 (Zahlungen → ein offener Posten), Negative (malformed, Billion-Laughs, >32 MiB ohne Validatoraufruf, Timeout per Mock). |
| Tests und Soll-Ist-Vergleich | Soll: T01–T20 + Negative. Ist gemessen: 19/19 Phase 4, 58/58 E-Rechnung gesamt, 112/112 Business, 9/9 Validator. T07/T08 über Phase-2b-Referenzrechnung (3 Steuersätze inkl. G + Grund, KoSIT-ACCEPTABLE) abgedeckt. Gates: Funktionen ok, compileall ok, ruff ok. |
| Review und Abschluss | Offen: Phase 5 (Abnahme, PR #1176 bereitstellen). Kein Merge ohne Freigabe. |

## 23. November 2026 · E-Rechnung Phase 5: Abnahme (AP1–AP6, PR #1176 Draft)

| Analyse | Abnahmekriterien §7 gegen Bestand geprüft: Pflichtfunktionen AP1–AP6 implementiert; Nummern-/Betragsidentität (T02/T06); XML KoSIT-ACCEPTABLE + PDF strukturell validiert (T01/T15/T16); keine Doppelbuchung (T11/T20); kritische Tests lokal grün; Bestandsfunktionen unverändert (112 Business, 41 Rechnung); Doku vollständig (Gap-Analyse, Validatorbasis, Bedienung, Protokolle). |
| Implementierung | Bedienungs-/Fehlerbehebungs-Doku `docs/E_RECHNUNG_BEDIENUNG.md` (Liefergegenstand 6). CI-Befund eigener Test im Scanner-Image (`precheck_draft` ohne `note` ohne Validator) per Root-Fix behoben + Regressionstest (fällt ohne Fix, besteht mit Fix). CRA-Check grün, keine neuen Abhängigkeiten. |
| Tests und Soll-Ist-Vergleich | Soll: T01–T20 + Negative + grüne CI. Ist gemessen: 58/58 E-Rechnung, 112/112 Business, 41/41 Rechnung, 9/9 Validator lokal; Scanner-Image volle Suite grün (6649 passed). CI-Restrot: 3 Jobs scheitern ausschließlich am Dateigrößen-Gate `app/mail_reader_routes.py` (1008, pre-existing auf Main, fremder Workstream) – auf dem Branch nicht behebbar ohne Scope-Verletzung. |
| Review und Abschluss | PR #1176 bleibt Draft bis Main den Mail-Gate behebt; kein Merge ohne Eigentümer-Freigabe. Bekannte Einschränkungen: veraPDF fehlt lokal (PDF/A nur strukturell + ehrlich berichtet), Endgeräte-Praxistest durch Eigentümer offen. |
## 23. November 2026 · CI-Gate: mail_reader_routes-Split (1008 → 621 Zeilen)

| Analyse | Main-CI rot: `app/mail_reader_routes.py` (1008 Zeilen) verletzt das 1000-Zeilen-Gate und blockiert jeden PR. 17 Case-Routen (~385 Zeilen) als kohärente Auslagerung identifiziert; Muster `mail_keyword_routes` (eigener Blueprint, gleicher URL-Prefix) wiederverwendet. |
| Implementierung | Neu `app/mail_reader_case_routes.py` (Blueprint `mail_reader_cases`, Code wortgleich verschoben). Parität: Endpunkt-Checks + `case_context` in `document_store.py`, Feature-Gate in `auth.py`, Metering in `license_metering.py` erweitert; Template-Referenzen (17 Views) umgeschrieben; URLs unverändert. |
| Tests und Soll-Ist-Vergleich | Soll: Gate grün, kein Verhalten geändert. Ist gemessen: Dateigrößen-Gate grün; 313 Mail-Tests grün nach Fix (2 Datenschutz-Tests brachen durch Mock-Namespace-Kopplung – Patch-Ziele auf neues Modul umgestellt, bewusste Spezifikationsänderung); 34 Nachbartests grün; Policy/Funktionen/compileall/ruff grün. |
| Review und Abschluss | PR gestellt, kein Merge ohne Freigabe. Hinweis: `mail_keywords`/`mail_handover` stehen nicht im Feature-Gate-Mapping (vorgefunden, bewusst nicht geändert). |

## 23. November 2026 · LAN-Proxy: globaler Bind + sudo-start.sh

| Analyse | Proxy-Start lehnte `0.0.0.0` ab (`_host` + Runtime-Guard, nur Docker-Modus erlaubt). Main-Server akzeptierte Wildcard bereits. Root-Rechte nur für Ports <1024 nötig (Proxy-Minimum bleibt 1024); Firewall-Öffnung fehlte. |
| Implementierung | Opt-in `SIMPLEOFFICE_LAN_PROXY_GLOBAL=1` (Default aus, Warn-Log bei globalem Bind); `SIMPLEOFFICE_LAN_PROXY_BIND_HOST/PORT`-Overrides nur im Startpfad; Docker-Meldungen neutralisiert; `start.sh --global/--proxy-bind/--proxy-port` + Root-Hinweis; neu `sudo-start.sh` (sudo-vs-su-Erkennung, Least-Privilege, `--open-firewall` für ufw/firewalld, `--print-command`-Dry-Run, `%q`-sicheres Quoting). |
| Tests und Soll-Ist-Vergleich | Soll: global starten, Root-Ports, Firewall. Ist gemessen: 9/9 Bind-Tests, 21/21 Shell-Tests, lan_proxy-Suite nur mit den 8 Main-Baseline-Fehlern (Sandbox, belegt). Gates: Policy/Funktionen/compileall/ruff/shellcheck-Stil grün; Dateigröße nur via fremden Mail-Split (#1179) rot. |
| Review und Abschluss | PR gestellt, kein Merge ohne Freigabe. Reihenfolge: erst #1179 (Gate), dann dieser PR. |

## 23. November 2026 · LAN-Proxy: Parameter statt Magic-Vars + Hilfe-Doku

| Analyse | Nutzer-Vorgabe: keine Magic-Vars – alles per Parameter, `--help` als Anleitung. Lücke: `PUBLIC_HOST/PORT` nur per Env setzbar; Hilfe ohne Global-Anleitung. Nebenbei vorgefundener, fremd begonnener Rebase auf Main+#1179 abgeschlossen (nur Doku-Konflikte, bereits vereinigt). |
| Implementierung | `start.sh --proxy-public-host/--proxy-public-port` (validiert); Hilfe mit Global-Abschnitt, Beispielen, sudo-Verweis; Fehlermeldungen nennen Parameter zuerst (`--global`, `--proxy-public-host`), Env zweit; sudo-Hilfe mit Weiterleitungs-Hinweis. |
| Tests und Soll-Ist-Vergleich | Soll: kein Env-Wissen nötig. Ist gemessen: 31/31 (Bind/Shell/Start), lan_proxy nur mit den 8 Sandbox-Baseline-Fehlern; ruff/compileall/shell-Syntax grün. |
| Review und Abschluss | Auf PR #1181 gepusht (Rebase-Branch), kein Merge ohne Freigabe. |

## 23. November 2026 · Security: Feature-Gate für Split-Mail-Blueprints

| Analyse | Die ausgelagerten Blueprints `mail_keywords`/`mail_handover` fehlten in `FEATURE_BLUEPRINTS` und im Metering-Mapping: `enforce_feature_permission` ließ sie fail-open passieren (kein 403 ohne Mail-Feature möglich). |
| Implementierung | Beide Blueprints auf Feature `mail` gemappt (Gate + Metering); sonst keine Verhaltensänderung. |
| Tests und Soll-Ist-Vergleich | Soll: Nutzer ohne Mail-Feature 403, mit Feature Durchlass. Ist gemessen: 3/3 neu (ohne Fix 4 Fehler, mit Fix grün); 54/54 Nachbarn (Keywords/Handover/Case/Admin/Lizenz) grün; alle Gates grün. |
| Review und Abschluss | PR gestellt, kein Merge ohne Freigabe. |

## 23. November 2026 · Federation: Peer-Discovery per Ton

| Analyse | Wunsch: schnelles Erkennen via Ton – eigene IPs ansagen, hörender Client kann einladen. Bestand: AcousticCodec (max. 128 Bytes, Ultraschall/hörbar, FEC), QR-Connect-Ablauf, Direkt-Discovery. Voll-Payload passt nicht in 128 Bytes → Ton trägt nur die Endpunkt-URL, Identität kommt per Netzabruf (gleicher TOFU-Ablauf). |
| Implementierung | Neu `static/js/federation_sound.js` (Ansage pro Adresse, Lauschen ≤90 s, lokale Strict-Prüfung als Spiegel der Serverregel, kein Auto-Abruf, kein Beacon). Template: Ansage-Buttons in der QR-Schleife, Hör-Block im Direkt-Formular, Codec-Skripte eingebunden. Kein neuer Server-Endpunkt nötig. |
| Tests und Soll-Ist-Vergleich | Soll: ansagen, hören, übernehmen; nichts Heimliches. Ist gemessen: 6/6 Node-Tests (Validierungsmatrix, Ansage-Ablauf, Lauschen gültig/ungültig, Stopp; Fehlschlag erkannt); 31/31 Protokoll+Admin-UI; `node --check` grün. |
| Review und Abschluss | PR gestellt, kein Merge ohne Freigabe. Danach: Pairing-Code-Austausch und Chat wie bisher (Raum mit remote_peer_id). |
## 23. November 2026 · Federation: Token-Transfer per QR-Code

| Analyse | Wunsch: rotiertes Token per Ton/QR übertragen statt abtippen. Bewertung: QR zeigt denselben bereits angezeigten Wert (keine neue Offenlegung); Ton für ein Secret abgelehnt (Broadcast über Hörweite/Wände, aufzeichenbar – größer als Bildschirm-Sichtbarkeit). |
| Implementierung | Rotate-Seite rendert zusätzlich eingeklappten QR-SVG (serverseitig, kein neuer Endpunkt, Token nie in URLs). Peer-Formular: Kamera-Scan-Button füllt Peer-Token nach Strict-Prüfung (`^[A-Za-z0-9_-]{32,128}$`); Muster von federation_qr_scan.js (BarcodeDetector/jsQR, Generationen-Guard). |
| Tests und Soll-Ist-Vergleich | Soll: anzeigen, scannen, übernehmen; Müll abweisen. Ist gemessen: 3/3 Node (Matrix, Übernahme-Ablauf, Ungültig-Fall), 12/12 Token/Identity (inkl. QR-SVG-Assert), 30/30 Admin-UI; `node --check`, ruff, compileall grün. |
## 23. November 2026 · Issue #738 (FinTS): Verifikation + Doku, bereit zum Schließen

| Analyse | Alle 9 Checkboxen gegen Code/Tests geprüft: TAN/SCA, Salden, Fähigkeiten, Sync/Retry, Abgleich-Vorschläge, Mock-Bank-Tests, Read-only/SEPA-frei implementiert und grün (144 Tests). Lücken: Doku fehlte; Depot optional/unsupported. |
| Implementierung | Neu `docs/FINTS_BANKING.md` (Banken, Limits, Sicherheitsmodell, Testhinweis). Keine Codeänderung nötig. |
| Tests und Soll-Ist-Vergleich | Soll: #738-Kriterien. Ist: 144/144 FinTS-Tests grün; Geheimnis-Blocklisten und Einmal-Challenges im Code verankert; Doku aus Code zitiert und belegt. |
| Review und Abschluss | PR mit `Closes #738` gestellt; Checkboxen + Nachweis im Issue dokumentiert. |

## 24. November 2026 · Merge-Runde: 12 PRs gemergt, #1154-Triage mit Subagenten

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Alle-PR-Prüfung: 12 Merges (#1182/#1183/#1186/#1187/#1188/#1189/#1190/#1191/#1196/#1197/#1202/#1211), alle fremden Diffs reviewt ohne Befunde, nur Doku-Konflikte vereint. #1176/#1178 vom Eigentümer gemergt. Nächstes Issue #1154: CodeQL-API zugänglich, noch 48 statt 143 py/path-injection-Alerts (Stand Main). |
| Implementierung | 4 Triage-Subagenten (read-only) auf Cluster WebDAV/Screen/Store/Business; je Alert Datenfluss→Safe-Helper oder Gap. |
| Tests und Soll-Ist-Vergleich | Soll: #1154-Akzeptanz (je Alert Fix+Test oder begründete Ausnahme). Ist: Triage läuft, Synthese + Fixes + PRs folgen. |
| Review und Abschluss | Offen: Synthese, Fix-PRs, Triage-Protokoll, Issue-Update. |

## 24. November 2026 · Issue #1154: 48 path-injection-Alerts triagiert, 1 GAP gefixt

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | CodeQL-API: noch 48 statt 143 Alerts. 4 read-only Subagenten je Cluster (WebDAV/Screen/Store/Business), je Alert Datenfluss→Safe-Helper oder Gap. Integrator hat den einzigen GAP im Code verifiziert. |
| Implementierung | Fix-PR: `resolve_for_write_under` in `_store_generated_pdf` + 3 Regressionstests. Doku-PR: Dispositions-Protokoll (1 GAP gefixt, 47 COVERED mit Helfer/Restrisiko/Testlücken). |
| Tests und Soll-Ist-Vergleich | Soll: #1154-Akzeptanz (je Alert Fix+Test oder begründete Ausnahme). Ist: Fix-Tests 3/3 (rot ohne/grün mit), 62/62 Business-Nachbarn, ruff/compileall grün; CI beider PRs abgewartet. |
| Review und Abschluss | Fix mergen, dann Doku-PR mit `Closes #1154` mergen, Issue-Nachweis kommentieren. |

## 24. November 2026 · #1154 Runde 2: Trunkierungsfehler korrigiert, 94 Rest-Alerts

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Erste Abfrage war per `head` trunkiert (160 statt 324 Alerts gezeigt): tatsächlich 142 py/path-injection. #1154 nach voreiligem Auto-Close (per #1218) sofort wieder geöffnet + Korrektur kommentiert. 5 Cluster-Agenten (read-only) für die 94 Rest-Flows gestartet. |
| Implementierung | Ausstehend: Synthese, Fix-PRs bei GAPs, Protokoll-Ergänzung. |
| Tests und Soll-Ist-Vergleich | Ausstehend. |
| Review und Abschluss | Offen. |

## 24. November 2026 · #1154 Runde 2: Rest-Flows triagiert, Archiv-Admin-Gate gefixt

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | 5 read-only Subagenten für 94 Rest-Flüsse; Integrator hat beide GAPs (externe Archive ohne Admin-Gate) im Code verifiziert, Mount-Allowlist als ungeeignet verworfen (`/` ist Mount), Windows-Hinweis widerlegt (plattformsemantisch konsistent). |
| Implementierung | Admin-Gate (403) auf Register-/Offload-Routen + 3 Regressionstests; Protokoll §6. |
| Tests und Soll-Ist-Vergleich | Soll: alle 142 dispositioniert. Ist: 3/3 neu (ohne Fix Schreiben nach /tmp belegt), ruff/compileall grün; 142/142 abgedeckt (4 GAP-Instanzen gefixt, Rest dokumentiert). |
| Review und Abschluss | PR gestellt; nach Merge #1154 mit Nachweis schließen. |
# Peer-Verbindung auf dem Handy vereinfachen · 24. November 2026

## Paket F-Connect-01 · Token- und QR-Einrichtung

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Screenshots zeigen zwei getrennte Hürden: Live-QR-Scan kann auf einer per HTTP über LAN geöffneten Seite keine Kamera bekommen; ein Peer-Test endet mit 401. Der Request-Pfad verwendet ein peer-spezifisches Token für die Peer-Identität und zusätzlich den Federation-Server-Bearer. Die UI erklärte den zweiten Zugang bisher nicht. Sicherheitsgrenze: Authentifizierung nicht abschwächen; keine Tokens im Klartext protokollieren oder vorbefüllen. |
| Soll | Mobil verständliche Einrichtung mit klar getrennten Zugangsdaten, direktem Sprung zur Server-Token-Konfiguration, handlungsorientierter 401-Meldung und QR-Foto-Fallback ohne Live-Kamerafreigabe. |
| Implementierung | `templates/admin/federation.html` und `templates/admin/federation_peer_discovery.html`: kompakte Schrittfolge in der Peer-Einrichtung sowie QR-/Ton-Schnellstart, Sender-/Empfängerrollen, Rückrichtung und Links zu Import und Token-Konfiguration; QR-Bild-Auswahl. `static/js/federation_token_scan.js`: Bild lokal dekodieren, Live-Kamera-Fehler mit HTTPS-Hinweis und Foto-Fallback erläutern. `app/federation_admin.py`/`app/flash_translations.py`: HTTP 401 verständlich erklären. Keine Authentifizierungs- oder Berechtigungsänderung. |
| Zwischenschritte | Bearbeitungszweig `fix/federation-peer-connect-easier`; Code liegt in isoliertem Worktree. Kamera- und QR-Foto-UI wird mit Node-Harness regressionsgeprüft. |
| Tests | Soll: QR-Tokenübernahme, ungültiges Bild/Token, insecure-origin-Fallback, Formular DE/EN, 401-Erklärung, bestehende Bearer- und Bann-Authentifizierung. Ausgeführt: Node QR-Scan-Suite 5/5 bestanden; Federation-Admin-UI 34/34, neue Peer-Verbindungs-Integration 3/3, Federation-HTTP-/Moderationstests einschließlich Node-Brücke 22/22 und Node QR-Scan-Suite 5/5 bestanden. JS-Syntax, Policy, Größen- und Compile-Gates sowie `git diff --check` bestanden. |
| Soll-Ist / Review / Abschluss | Codeänderungen decken die sichtbaren Setup-Hürden ab. Foto-Decodierung bleibt lokal im Browser; sie gibt keine Bilddaten an den Server. Soll-Ist: Die Verbindungswege QR und Ton sowie die getrennten Sicherheits-/Token-Schritte sind direkt auf der Einstiegsseite erläutert; automatisierte Integrationstests und Diff-Review bestanden. Automatisierte Browsergeräteprüfung und Android-Praxistest nicht erfolgt. |

## Paket F-Connect-02 · Ein-Scan-Kopplung und automatisches Peer-Token

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Nutzerfluss präzisiert: A zeigt einen Einladungscode, B scannt ihn einmal und sendet seine signierte Identität an A zurück. Manuelles Token-Eintippen und ein zweiter QR-Scan entfallen. Der bestehende globale Federation-Server-Bearer bleibt vom Peer-Token getrennt. |
| Vorher-/Nachher-Skizze für den PR | **Vorher:** A zeigt Profil-QR → B scannt → A kennt B nur lokal → Token muss auf beiden Geräten manuell gefunden/eingetragen werden → Test kann mit 401 scheitern. **Nachher:** A tippt „Ein-Scan-Einrichtung erstellen“ → QR mit 10-Minuten-Einladung erscheint → B scannt und sendet signierte Identität + HMAC-Beweis an A → beide leiten dasselbe einmalige Peer-Token lokal ab → Peer-Diagnosen funktionieren, ohne das Token über HTTP zu senden. Ein normaler Profil-QR bleibt weiter verfügbar. |
| Sicherheitsgrenzen | Einladungs-Secret wird verschlüsselt gespeichert, ist auf eine Peer-Identität gebunden und läuft nach 10 Minuten ab. Derselbe Peer kann bei verlorener Antwort idempotent wiederholen. Das Secret steht nur im no-store QR-Bild und wird nicht im Request übertragen; B sendet einen HMAC-Beweis und Ed25519-signierte Identität. Das Peer-Token wird mit HMAC und Domain-Trennung aus Einladung + beiden Peer-IDs abgeleitet. Ein absichtlicher neuer Ein-Scan-QR aktualisiert das Peer-Token; gespeicherte Peer-Adresse, Richtlinien und Aktivierung bleiben erhalten, sofern die Identität gleich ist. Neu gekoppelte Peers sind für Diagnosen aktiv; Trust und Datenrechte werden nicht erteilt. Peer-HMAC wird ausschließlich für den kleinen Datei-Echo-Test und den Ton-Diagnosetest akzeptiert; Dokumentendpunkte behalten ihre vorhandene Server-Bearer-/Berechtigungsprüfung. |
| Implementierung | `federation_connection_invite.py` speichert/beansprucht Kurzzeit-Einladungen und leitet Tokens ab; QR-Codec unterstützt `sofp://pair/`; neue Admin-Route rendert den Ein-Scan-QR; QR-Import sendet den signierten Rückruf und speichert das Peer-Token. Verbindungsdiagnosen senden Peer-HMAC und können für genau ihre Test-Endpunkte Peer-Authentifizierung verwenden. Kamera-/Foto-Scanner akzeptiert beide QR-Formate. |
| Tests | Initial 42 Federation-/Admin-/Peer-Auth-Tests; nach CI-Korrekturen 76 fokussierte Python-Tests bestanden: One-scan-Roundtrip, QR-Decodierung, Ablauf/Einmaligkeit, identische Tokens ohne Token-Übertragung, Signatur/Rückruf, UI-Erzeugung/Import, authentifizierte Diagnosen, Admin-UI, Template-Shell/Accessibility und S3-Persistenzabdeckung. `ruff`, Compile, Datei-/Funktionsgrößen und `git diff --check` bestanden. |
| Soll-Ist / Review / Abschluss | Soll: nur ein QR-Scan, kein manuell gewähltes/geteiltes Peer-Token und keine Lockerung von Dokumentrechten. Ist: Einladung wird einseitig auf A erstellt; B meldet sich bei A; beide speichern dasselbe Peer-Token; beide bleiben ohne Trust/Datenrechte. Die bestehenden globalen Bearer-Zugangsdaten werden nicht überschrieben. HTTP- und Kamera-Praxistest mit zwei physischen Geräten nicht erfolgt; PR bleibt bis CI grün zur Review offen. |

## 25. November 2026 · Merge-Runde 3: #1154 geschlossen, Flake-Fix, 3 Merges

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | #1220-Scanner-Fail als Test-Flake entlarvt (CSRF-Zufall enthielt „cdn", kein PR-Fehler); #1217/#1221/#1219/#1222 bereits gemergt vorgefunden. |
| Implementierung | Flake-Test gehärtet (PR #1228, gemergt); #1219/#1222/#1224-Reviews sauber; #1219+#1222 gemergt; #1224 rebased+gepusht; #1154 mit Nachweis geschlossen; #1220-Branch auf Main gebracht. |
| Tests und Soll-Ist-Vergleich | Soll: Runde abschließen. Ist: 15/15 Kids-Tests, Robustheits-Probe (echte CDN-URL schlägt weiter fehl), ruff grün; Rest (#1220/#1224-Merge, #1156-Schluss) per Cron. |
| Review und Abschluss | Offen: #1220/#1224-Merges + #1156-Schluss nach Grün. |
## 24. November 2026 · Issue #1156: Abschluss bis auf Merges, #1225 abgespalten

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | 3 Verifikations-Subagenten: Pins (151 SHA-ok, 4 Lücken), Coverage (Dependabot/Secrets wieder abfragbar, je 1/0 offen), Rust (cookie/url auf Main gefixt). glib GHSA als Major-Upgrade erkannt (GTK-Stack). |
| Implementierung | PR #1224 (4 Pins), Folge-Issue #1225 (glib/Tauri), #1156-Statuskommentar. |
| Tests und Soll-Ist-Vergleich | Soll: #1156-Akzeptanz. Ist: alle Nachweise erbracht (Fix/Ausnahme je Fund); Merges + Schluss ausstehend. |
| Review und Abschluss | Offen: #1217/#1221/#1224 bei Grün mergen, #1154/#1156 schließen. |

## 25. November 2026 · Merge-Runde 4: 13 Merges, Browser-Root-Fix, #1156 geschlossen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | 6 Browser-Rots (#1232/#1233/#1250/#1251/#1252/#1256) per Subagent auf 1 Main-Fehler zurückgeführt: `templates/documents/setup.html` trug Inline-`<style>` im `<main>` (seit #1184, Nu-Checker-Fehler). #1251 hatte zusätzlich echten Python-Gate-Fail. 9 grüne PRs per 2 Review-Subagenten geprüft. |
| Implementierung | Fix als #1272 gestellt (Style in `styles`-Block, Regressionstest); Peer löste parallel sauberer via externer `document-setup.css` → #1272 obsolet-geschlossen, Main-Fehler behoben. Gemergt: #1232/#1220/#1252/#1254 + #1261–#1268/#1270 (13). #1224-Doku-Konflikt vereint/gepusht (Peer mergte nach Grün). #1156 mit Nachweis kommentiert+geschlossen. |
| Tests und Soll-Ist-Vergleich | Soll: Runde 4 + #1156-Schluss. Ist: Setup-Render 200 (Style im Head, kein `<style>` im Body, Klasse erhalten), 11/11 Setup-Tests; 9/9 Reviews MERGE-READY; lokale #1251-Vollsuite (47F+7E) als Env-Rauschen erkannt → CI-Rerun, Peer fixte Branch + mergte. Peer mergte parallel #1230/#1233/#1244/#1256/#1260/#1224/#1231/#1238/#1269/#1271/#1294. |
| Review und Abschluss | Offen: Welle #1284+ (Reviews laufen), rote Peer-PRs #1299 (OSSAR/macOS), #1300 (Python-Gate+CodeQL), #1301 (CodeQL+OSSAR) — fremde Fehler, nur benannt. |

## 25. November 2026 · Merge-Runde 5: 9 Merges (#1284–#1292/#1294)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | 9 grüne PRs per 2 Review-Subagenten geprüft (volle Diffs, Call-Sites, CI+Mergeable). Peer mergte parallel mit. |
| Implementierung | Selbst gemergt: #1284/#1285/#1287/#1288/#1289. Peer mergte: #1290/#1291/#1292/#1294 (Reviews retrospektiv MERGE-READY bestätigt, keine HOLDs). |
| Tests und Soll-Ist-Vergleich | Soll: Welle 5 komplett. Ist: 9/9 Reviews MERGE-READY ohne Blocker; je-PR CI-Grün + Merge-State verifiziert; #1302-Doku gemergt. |
| Review und Abschluss | Offen: #1307/#1308/#1309 (CI läuft), rot+fremd #1299 (OSSAR/macOS), #1300 (Python-Gate+CodeQL+Scanner), #1301 (CodeQL+OSSAR), #1303 (nur CodeQL 2s, Infra-verdächtig) — nur benannt. |

## 25. November 2026 · Merge-Runde 6: 4 Merges (#1307/#1308/#1309/#1310)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | #1307/#1308 (Kids Webcam/Reaktion) + #1309 (Datenlogger) per Review-Subagenten geprüft; #1310 (eigene Doku) selbst reviewt. Peer mergte #1299/#1300/#1301 (nur non-blocking 3.15 rot). |
| Implementierung | Gemergt: #1307 (Fingerzauber), #1308 (Reaktionsparcours+ESP32), #1309 (Datenlogger-Berichte), #1310 (Runde-5-Doku). #1310-Worklog-Konflikt vereint. |
| Tests und Soll-Ist-Vergleich | Soll: Welle 6 komplett. Ist: #1307 5/5 Node + 3/3 Python lokal verifiziert; #1308 3/3 Flask + 9/9 Node + Firmware-Compile; #1309 Secrets/Validierung/AuthZ geprüft; je-PR CI 0 fail (nur non-blocking 3.15 teils pendent, Protection gab frei). |
| Review und Abschluss | Offen: #1303 (nur CodeQL rot, fremd), neu #1311/#1312/#1313/#1315 (nächste Welle). |

## 26. November 2026 · Merge-Runde 7: 6 Merges, 2 Issue-Status, 2 Code-Konflikte (fremd)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | 9 grüne PRs per 2 Review-Subagenten geprüft (8 MERGE-READY); #1316 (eigene Doku) selbst reviewt; 8 Issues per Subagent gegen Main auf Erfüllung geprüft (keines voll schließbar). |
| Implementierung | Gemergt: #1311/#1312/#1313/#1315/#1316/#1322. #1317-Worklog-Konflikt vereint/gepusht (CI läuft). #1323 (`app/kids_learning.py`) + #1324 (`templates/documents/dashboard.html`) Code-Konflikte in fremden Branches → gestoppt/gemeldet. Status mit Nachweis kommentiert: #1296 (cf91acb6), #1319 (d0e0f012); Rest je Eigentümer-Gerätetest. |
| Tests und Soll-Ist-Vergleich | Soll: Welle 7 + 1 Issue. Ist: 8/8 Reviews MERGE-READY ohne Blocker; je-PR CI-Grün + Merge-State verifiziert; Issue-Dateien per `git cat-file` auf Main belegt. #1318 rot (staged-hand-browser, fremd), #1303 weiter CodeQL-rot (fremd). |
| Review und Abschluss | Offen: #1317 (CI), #1323/#1324 (fremde Code-Konflikte), #1303/#1318 (fremd rot), Rest-Issues brauchen Geräte-Praxistests. |

## 26. November 2026 · Merge-Runde 8: Code-Queue abgeschlossen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Scanner-Bestandsprüfung #1328, Android-Barcodescan #1330 und optionale Reader-/Mail-Stimmdateien #1331 auf Eigentümergrenzen, explizite Speicherung, native Brücke, Engine-Abhängigkeit und Fehlerpfade geprüft. |
| Implementierung | Gemergt: #1323/#1324/#1325/#1326/#1327/#1328 sowie #1330/#1331. #1330 wurde während der Prüfung parallel gemergt; #1331 danach aus dem Draft freigegeben und gemergt. |
| Tests und Soll-Ist-Vergleich | #1328: GitHub-CI grün, lokal 4 Python-Tests bestanden/1 übersprungen und 1 Node-Test bestanden. #1330: CI grün einschließlich Android-APK, Python und authentifiziertem Chromium-Scannertest; nur experimenteller Python-3.15-Lauf abgebrochen. #1331: 97 lokale Python-Tests bestanden/3 übersprungen und 9 Node-Tests bestanden; CI grün außer abgebrochenem, nicht blockierendem Python-3.15-Lauf. |
| Review und Abschluss | Keine offenen Code-PRs nach #1331. #1332 ist der verbleibende Dokumentations-PR für diese Runde. Nicht verifiziert: physischer Android-Scan, externer Modell-Download, echte Piper-Wiedergabe und standalone APK-Audio. |

## 26. November 2026 · Issue-Runde 9: #1320/#1321 abgeschlossen, #1314 bleibt offen

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | #1320/#1321 gegen Main und ihre Abschlussnachweise geprüft. #1314 bleibt ein Teilstand: Blickpfad, echte Inferenz und physischer Kameratest fehlen. #1333 im Review: der Dirty-Guard wurde im Client nicht gesetzt. |
| Implementierung | #1320 (Logikwerkstatt, #1323, Merge `3cf4256d`) und #1321 (Vorlesen/Downloads, #1324, Merge `cd46bc19`) mit Dateinachweis kommentiert und geschlossen. #1333-Guard ergänzt und PR mit `9f82e920` gemergt. #1331 aus Draft freigegeben und mit `7ed77c14` gemergt. |
| Tests und Soll-Ist-Vergleich | #1333: 10 Node-Tests lokal bestanden; alle 41 GitHub-Checks abgeschlossen ohne Fehlschlag, darunter Python-Anwendung, CodeQL und authentifizierter Chromium-Scannerlauf. #1331: 97 lokale Python-Tests bestanden/3 übersprungen, 9 Node-Tests; CI ohne Fehler, der experimentelle 3.15-Lauf nicht blockierend abgebrochen. |
| Review und Abschluss | #1320/#1321 geschlossen. #1314 bleibt offen, bis Blickpfad, echte Modellinferenz und Gerätetest belegt sind. #1333 ist gemergt; #1334 ist der nachfolgende Protokoll-PR. |

## 26. November 2026 · Issue-Runde 10: #874-Triage (338 Alerts) + Fix-PR #1336

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Subagent-Triage aller 338 Code-Scanning- + 1 Dependabot-Alerts gegen Main: 152 path-injection FP (eigene Sanitizer), 67 stack-trace FP (kuratierte Strings), Rest FP/Prozess/Hygiene. Belegstellen je Cluster geprüft ( containment in `safe_paths`/`webdav_part_2`, allowlists, HMAC-vs-Passwort, vendored libs). |
| Implementierung | #1335 Quellenvergleich gemergt (`15b62527`). #1336 behebt #384 über eine normale In-Memory-Antwort mit konstantem Downloadnamen (ohne `send_file`) und #1043 durch Sperren von `http://` in `safePreviewUrl`; Merge `fb8f523f`. glib bleibt #1225. |
| Tests und Soll-Ist-Vergleich | #1335: 35 authentifizierte Flask-Integrationstests lokal bestanden; GitHub Python-3.14-Regression, mobiler Chromium-Scannerlauf, Kontakt-/Erfassungs-Browserlauf, Security, statische Prüfung und Scanner/Reader-Docker-Prüfung bestanden. #1336: 13 CalDAV-Tests, 2 Node-Allowlist-Tests und Ruff lokal bestanden. CodeQL, PR-Regression, Security und statische CI erfolgreich. Beim Merge liefen noch die breite Python-Anwendungssuite, der Scanner-Image-Test und Python 3.15; es gab keine fehlgeschlagenen Checks. |
| Review und Abschluss | #1335 und #1336 sind gemergt. FP-Dismissals bleiben Eigentümer-Entscheidung. |

## 26. November 2026 · Issue-Runde 11: #1305-Lücken (WPM + Cue) als PR #1341

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Subagent-Gap-Analyse #1305 gegen Main: 8 Kriterien erfüllt, 5 Lücken (WPM, QR, Ordered-in-Rooms, Cue-Motion, Hardware-Nachweis). #1339 retrospektiv MERGE-READY (Peer mergte). |
| Implementierung | PR #1341: `keystrokesPerMinute` (Spiel-Logik + Export), Tempo in lokaler Wertung, Server-`rate` in Raum-Scores + Anzeige, CSS-Cue-Puls mit Reduced-Motion-Neutralisierung. #1305 mit Lückenstand kommentiert. |
| Tests und Soll-Ist-Vergleich | Soll: weiteres Issue + PRs. Ist: WPM-Fälle + Harness-Fix (Test 8 brach durch neuen Export, Harness ergänzt), `rate`-Assertions im Raum-Duell; lokal 10/10 + 3/3 Node, 9/9 + 1/1 Python, ruff/node-check grün. |
| Review und Abschluss | Offen: #1341-CI → mergen; #1305-Rest (QR, Ordered-Rooms, Hardware-Praxistest). |
## 26. November 2026 · PR-Merge-Kampagne der Assistenz-Session (42 eigene Merges, 23.–26.11.)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Neun Merge-Runden über 42 PRs (#1175–#1333): je PR Diff-Review (Aufrufpfade, Kompatibilität, Vertrauensgrenzen), Frische- und CI-Prüfung vor jedem Merge; Freigabe pro Paket beim Eigentümer eingeholt. |
| Implementierung | Eigene Fixes auf fremden Branches: `SUDO_START_FORCE`-Vorrang als Root (#1181), i18n-Fixture/Template-Assertions (#1180), Browser-/Ruff-Hash-Locks per Doku-Verfahren (#1177), P2P-Smoke + Discovery-Tests (#1185), Smoke-Text (#1268), Template-`default`-Guard (#1273), semantischer Read-Aloud-Merge (#1264), #1303-CodeQL-Analyse (SARIF-belegt, eigener Umbau nach Testbruch revertiert). |
| Tests und Soll-Ist-Vergleich | Soll: jeder Merge erst nach grüner Suite. Ist gemessen: alle Merges mit 0 Fehlern außer zwei dokumentierten, freigegebenen Ausnahmen (externes Tauri-`cargo audit` bei #1177; CodeQL-False-Positive bei #1303). Lokale Gates (Policy/Größen/Compile) vor jedem Push grün; Fake-Root-Simulation und Lock-Download-Checks als unabhängige Nachweise. |
| Review und Abschluss | Alle Merge-SHAs im Worklog belegt. Offene Fremd-PR-Reste (#1272 redundant, #1314/#1305-Rest) und FP-Dismissals bleiben Eigentümer-/Autor-Entscheidung. |

## 26. November 2026 · Issue #1225: glib-GHSA-Evaluierung (Upstream-blockiert)

| Phase | Arbeit und Nachweis |
|---|---|
| Analyse | Subagent-Recherche (read-only): glib 0.18.5 via Tauri 2.12.3 → `tauri-runtime-wry` → `wry ^0.57` + `webkit2gtk =2.0` + `gtk ^0.18` (`Cargo.lock:1060`, `3039`, `4155`, `3796`). Neuestes `tauri-runtime-wry` 2.12.5 (crates.io-verifiziert) pinnt weiter wry 0.57/webkit2gtk 2.0; auch `3.0.0-alpha.6`. Repo nutzt nur Tauri-Core ohne Plugins. |
| Implementierung | Kein Patch möglich: kein Release mit glib ≥ 0.20 existiert; `[patch]`-Vendoring wegen ABI-Risiko verworfen. Befund mit Entblockungs-Kriterium als Issue-Kommentar dokumentiert. |
| Tests und Soll-Ist-Vergleich | Soll: Upgrade-Pfad. Ist: zweifach belegter Negativbefund (Subagent + crates.io-API-Dependencies); kein Code geändert, keine Tests nötig; Issue bleibt offen als Watch-Item. |
| Review und Abschluss | Medium/Crash-only/Linux-only ohne CVE; Neubewertung bei erstem Runtime-wry mit `wry ^0.58`. |
## 26. November 2026 · Setup: lokale Piper- und RapidOCR-Wheels

| Phase | Aufgabe und Ergebnis |
| --- | --- |
| Analyse | Die OCR-Pakete stehen in `pyproject.toml` unter dem optionalen Extra `ocr`; die reguläre Startinstallation zieht dieses Extra nicht mit ein. Der Startskript-Status konnte die optionalen Engines überspringen, wenn die übrige `.venv` bereits vollständig war. Piper fehlte ebenfalls standardmäßig. |
| Planung | Auf unterstütztem Linux die vorhandenen Projektversionen von RapidOCR/ONNX Runtime und Piper in die Projekt-`.venv` aufnehmen. Wheels erzwingen, damit kein nativer Quellbuild die Installation blockiert. Termux überspringt diese optionalen Engines; Tesseract und der Start bleiben verfügbar. Stimmenmodelle werden nie automatisch geladen. `run`, `--check-system` und Opt-out-Variablen bleiben nicht installierend. |
| Implementierung | `start.sh` prüft optionale Engines auch beim Schnellstart. Fehlende Pakete werden mit festen Projektversionen installiert; Piper wird gezielt in `$VENV/bin` geprüft und dessen Verzeichnis liegt im Laufzeit-PATH. Lesbare Warnungen und Opt-outs sind ergänzt. Admin- und Setup-Hinweise dokumentieren Engine-/Stimmen-Trennung. |
| Tests | `bash -n start.sh`; `python -m unittest -q tests.test_start_script tests.test_object_vision_packaging tests.test_piper_settings`: 26 Tests bestanden. `git diff --check` bestanden. Installation echter nativer Wheels in der System-`.venv` wurde nicht ausgeführt. |
| Soll-Ist-Vergleich | Soll: beim regulären Linux-Setup sind Piper, RapidOCR und ONNX Runtime in der Projektumgebung verfügbar. Ist: Installer und Bereitschaftsprüfung implementiert. Auf dem aktuellen Host wurde die reale Wheelinstallation nicht validiert; Termux bleibt bewusst ausgenommen. |
| Review und Abschluss | Kein automatischer Import von Stimmen/Modellen. Ausstehend sind PR-CI und eine einmalige echte Linux-Wheelinstallation samt Inferenz-Smoke-Test. |
