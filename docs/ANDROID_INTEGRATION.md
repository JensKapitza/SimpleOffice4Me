# Android-Integration

## Ziel

Die Android-App soll sich wie eine normale Android-Anwendung verhalten, ohne den bestehenden SimpleOffice-Sicherheits- und Dokumentenpfad zu umgehen. Die Integration ist deshalb absichtlich schmal: Android liefert Dateien, Ziele oder Speicherorte; die eigentliche Dokumentverarbeitung bleibt bei SimpleOffice.

## Unterstützte Android-Versionen

Die unterstützte APK hat `minSdk 24` und unterstützt damit Android 7.0 oder neuer.

- `arm64-v8a`: Python 3.15.
- `armeabi-v7a` wird derzeit nicht gebaut oder als unterstütztes APK-Profil angeboten.

Die native Integration verwendet nur APIs, die ab API 24 vorhanden sind. Safe Browsing bleibt weiterhin erst ab Android 8 aktiviert.

### Native Python-Erweiterungen

Vor dem Import der Anwendung macht `android_runtime` die eingebetteten
CPython-Symbole für native Erweiterungen global sichtbar und lädt
`cryptography.hazmat.bindings._rust` explizit vor. Der Android-Wheel-ELF
referenziert CPython-Datensymbole wie `PyLong_Type` ohne direkten
`DT_NEEDED`-Verweis auf `libpython`; deshalb reicht ein erfolgreicher APK-Build
als Kompatibilitätsnachweis nicht aus. Die Android-APK-CI sammelt deshalb die
ELF-Dynamik- und Symboltabellen des Wheels und der eingebetteten Python-Laufzeit.
Das liefert keinen Laufzeitnachweis: Die derzeitigen GitHub-gehosteten Linux-
und macOS-ARM-Runner konnten den ARM64-Emulator mangels Android-Emulatorpaket
beziehungsweise nutzbarer Nested-Virtualisierung nicht starten. Vor Freigabe
muss der App-Start einschließlich `_rust`-Import auf einem ARM64-Gerät oder
einem Emulator mit funktionierender Hardwarevirtualisierung nachgewiesen werden.

## Dateien mit Android teilen oder öffnen

Die Activity ist als Ziel für `ACTION_SEND` und `ACTION_SEND_MULTIPLE` registriert. Dadurch kann der Benutzer in Galerie, Dateien, PDF-Readern und anderen Apps **Teilen -> SimpleOffice4Me** wählen.

Zusätzlich akzeptiert die App `ACTION_VIEW` ausschließlich für `content://`-Dokumente. Damit erscheint SimpleOffice auch als **Öffnen mit**-Ziel für kompatible Dateimanager und Dokument-Apps. Fremde `file://`-Pfade werden nicht angenommen.

### Ablauf

1. Android übergibt einen oder mehrere `content://`-URIs.
2. `AndroidIntentRouter` behält höchstens 20 eindeutige URIs im Speicher.
3. Fremde `file://`-Pfade werden abgelehnt.
4. SimpleOffice öffnet die Dokumentansicht.
5. Ein sichtbarer Hinweis meldet die geteilten beziehungsweise geöffneten Dateien.
6. **Dateien übernehmen** füllt das vorhandene Mehrfach-Dateifeld der Dokumentenverwaltung.
7. Erst **Vollständig importieren** führt den bestehenden serverseitigen Import aus.

Damit bleiben Upload-Limit, Hash-Prüfung, Dokumenten-Metadaten, Audit-Historie, Rechte und alle bestehenden Importprüfungen erhalten. Die native Android-Schicht schreibt übergebene Dateien nicht direkt in den Dokumentordner.

## Downloads und Öffnen mit Android-Apps

Lokale Downloads aus dem eingebetteten WebView werden durch `AndroidDownloadHandler` übernommen.

### Speicherung

- nur URLs von `http://127.0.0.1:8765` beziehungsweise `localhost:8765` werden nativ heruntergeladen;
- die aktuelle WebView-Session wird über das vorhandene Cookie an den lokalen Flask-Endpunkt weitergegeben;
- Redirects werden nicht automatisch verfolgt;
- der Benutzer wählt den Zielort mit `ACTION_CREATE_DOCUMENT`;
- es wird keine breite externe Speicherberechtigung benötigt;
- bei einem fehlgeschlagenen Transfer wird das angelegte leere Zieldokument nach Möglichkeit wieder entfernt.

Nach erfolgreichem Speichern kann der Benutzer **Öffnen** wählen. Android erhält dafür nur einen temporären Lesezugriff auf den ausgewählten `content://`-URI. Nichtlokale Download-Links werden nur für `http` oder `https` an Android weitergereicht; andere Download-Schemata werden blockiert.

## Lokal freigegebene Ordner

Die Seite **Einstellungen → Lokale Ordner** kann unter Android einen Ordner über
`ACTION_OPEN_DOCUMENT_TREE` auswählen. Die App speichert die `content://`-URI
und die persistierbare SAF-Berechtigung ausschließlich in privaten Android-
`SharedPreferences`; die JavaScript-Bridge gibt nur eine zufällige lokale ID,
den Anzeigenamen, die Beschreibung und den Berechtigungsstatus zurück. Die URI
wird nicht an Flask, WebDAV oder einen anderen Server gesendet. Die Registry
wird außerdem von Cloud-Backup und Geräteübertragung ausgeschlossen, weil eine
SAF-Berechtigung gerätegebunden ist.

- Die Ordnerauswahl verlangt die ausdrückliche Bestätigung im Systemdialog und
  nimmt keine klassischen Dateisystempfade oder breite Speicherberechtigungen an.
- Der Anzeigename und die Beschreibung sind lokale Metadaten und können geändert
  werden, ohne die ausgewählte URI zu ändern.
- **Verbindung prüfen** fragt die gespeicherte Dokumentanbieter-Berechtigung ab;
  nach Widerruf wird die Freigabe als nicht verfügbar markiert und kann erneut
  ausgewählt werden.
- **Freigabe entfernen** entfernt nur den gespeicherten Verweis und gibt die
  persistierbare Berechtigung frei. Dateien des Ordners werden nicht verändert.
- Das ist ein gerätelokaler Zugriff, kein serverseitiger Mount oder Speicherort.
  Wenn der Benutzer Dateien auswählt und ausdrücklich importiert, werden nur die
  Dateiinhalte als Kopien über den bestehenden Upload-/Importpfad übernommen;
  der Ordner-URI bleibt in der nativen Android-Schicht.

### Ordner rekursiv importieren

Auf der Dokumentseite öffnet **Ordner importieren** unter Android den
Systemdialog `ACTION_OPEN_DOCUMENT_TREE`. Der native Importer liest die
Unterordner über `DocumentsContract`, prüft Dateinamen, Tiefe, Pfadlänge und
gemeldete Dateigrößen und streamt die ausgewählten Dateien in Teilpaketen von
höchstens 100 Dateien an den CSRF- und Session-gebundenen lokalen Flask-Endpunkt.
Es gibt keine feste Obergrenze für die Dateianzahl eines Importvorgangs. Der
Server validiert weiterhin jeden relativen Pfad und führt den normalen
hashbasierten Import einschließlich Inbox-/Auditpfad aus. Die SAF-URI wird
nicht an Flask übertragen. Ein kleiner Abbruchknopf stoppt zwischen vollständig
gesendeten Teilpaketen; ein unsicher bestätigtes Paket wird mit derselben
Importkennung erneut gesendet und bereits gespeicherte identische Dateien werden
anhand ihres SHA-256 idempotent erkannt. Abweichender Inhalt wird nie
überschrieben. Vor der Bestätigung zeigt die APK Dateianzahl, Gesamtgröße und
das Ziel eines neuen eindeutigen Importordners.
Für die Herkunfts-Smart-View bildet die APK lokal mit einem zufälligen,
installationsgebundenen Salt einen SHA-256-Fingerabdruck der SAF-Tree-URI und
sendet nur diesen opaken Wert als Quellkennung. Der Salt liegt in privaten
Android-`SharedPreferences`, die von Cloud-Backup und Geräteübertragung
ausgeschlossen sind. Wiederholte Importe derselben stabilen Provider-URI auf
dieser Installation bleiben so zugeordnet, ohne URI, Provider-Zugangsdaten oder
Berechtigungs-Token an den Server weiterzugeben. Nach App-Datenlöschung,
Neuinstallation oder bei wechselnden Provider-Tree-URIs entsteht bewusst eine
neue Herkunftskennung.

Browser mit `webkitdirectory` verwenden denselben serverseitigen Importpfad und
senden höchstens 100 Dateien pro Anfrage. Eine zufällige Importkennung bleibt
pro Tab in `sessionStorage`; nach Neuladen und erneuter Ordnerauswahl kann ein
unterbrochener Vorgang damit anhand der Pfade und SHA-256-Werte sicher
fortgesetzt werden. Die Dateianzahl eines Importvorgangs ist nicht begrenzt.

- höchstens 20 SAF-Wurzeln und maximal 64 Verzeichnisebenen; keine feste
  Dateianzahlgrenze;
- pro Anfrage höchstens 100 Dateien; relative Pfad- und Quell-ID-Listen sowie
  gesamte Anfragegröße sind pro Teilpaket begrenzt;
- Dateiinhalte werden mit begrenztem Puffer übertragen und die gesamte Anfrage
  bleibt an das konfigurierte Upload-Limit gebunden;
- Abbruch stoppt zwischen Teilpaketen. Bereits vollständig importierte Dateien
  bleiben erhalten und werden nicht still gelöscht;
- ein Anbieter, der keinen dauerhaft widerrufbaren SAF-Lesezugriff gewährt,
  wird vor dem Einreihen abgelehnt;
- Importkennung, SAF-Wurzeln, erworbene Rechte und ein Hash des CSRF-Tokens
  werden privat gespeichert. Nach App-Neustart wird die Warteschlange nur bei
  gültigen Rechten und derselben Sitzung wiederhergestellt; bereits übertragene
  Dateien werden nach erneutem Bestätigen idempotent übersprungen;
- temporäre Importberechtigungen werden nach Abschluss, Abbruch oder
  Sitzungswechsel freigegeben; nicht gespeicherte verwaiste Rechte werden beim Start bereinigt;
  diese Marker werden nicht in Cloud-Backup oder Geräteübertragung aufgenommen;
- bereits in **Einstellungen → Lokale Ordner** gespeicherte Berechtigungen werden
  wiederverwendet und nach dem Import nicht durch die Importwarteschlange widerrufen;
- leere Verzeichnisse werden nicht importiert; zwei ausgewählte Wurzeln mit
  gleichem Namen werden abgelehnt, damit sich Zielpfade nicht überschreiben;
  stattdessen kann der gemeinsame übergeordnete Ordner gewählt werden;
- liefert ein Dokumentanbieter keine Dateigröße, wird der Batch vor dem Upload
  abgebrochen, statt eine unbeschränkte Anfrage zu starten.

### Geräteabnahme für den rekursiven SAF-Import (PR #857 / Issue #801)

Diese Prüfung muss auf einem echten Android-Gerät oder einem Emulator mit
funktionsfähigem SAF-Dokumentanbieter durchgeführt und im PR mit Gerät,
Android-Version, Anbieter, APK-Commit und Ergebnis dokumentiert werden.
Ein erfolgreicher APK-Build oder Java-Quelltexttest ersetzt sie nicht.

1. **Mehrere Wurzeln:** Zwei unterschiedliche Ordner mit verschachtelten
   Unterordnern und gleichnamigen Dateien auswählen, Vorschau kontrollieren,
   Import bestätigen und alle Dateien am korrekten relativen Ziel öffnen.
2. **Abbruch:** Einen großen Import während der Übertragung abbrechen;
   anschließend kontrollieren, dass keine unvollständige Datei als erfolgreicher
   Dokumentimport erscheint und ein neuer Import möglich ist.
3. **Berechtigungswiderruf:** Nach der Auswahl die SAF-Freigabe in den
   Android-Einstellungen entziehen; erneuten Import versuchen und eine
   verständliche Fehlermeldung statt Absturz oder Hängenbleiben prüfen.
4. **Neustart und Wiederaufnahme:** Einen mehrteiligen Import während der
   Übertragung beenden und neu starten. Der Auftrag muss sichtbar bleiben;
   nach erneuter Bestätigung werden bereits gespeicherte Dateien übersprungen,
   fehlende Dateien ergänzt und keine Datei überschrieben. Dauerhaft
   registrierte lokale Ordner dürfen ihre Berechtigung nicht verlieren.
5. **Große Dateien und viele Dateien:** Eine Datei nahe der konfigurierten
   Uploadgrenze sowie Ordner mit 1.000, 1.001 und deutlich mehr Dateien testen.
   Erwartet werden begrenzte Teilpakete und kein Abbruch allein wegen der
   Dateianzahl.
6. **Dokumentanbieter:** Mindestens den Android-Systemdateianbieter und,
   sofern vorhanden, einen weiteren SAF-Anbieter prüfen. Anbieter ohne
   persistierbare Leseberechtigung müssen kontrolliert abgelehnt werden.
7. **Sitzungswechsel:** Während eine Auswahl offen ist, abmelden oder die
   Sitzung wechseln; kein Upload darf unter einer anderen Sitzung erfolgen.

**Abnahmebedingung:** Jeder Fall erhält ein protokolliertes PASS/FAIL mit
reproduzierbaren Schritten. Bei FAIL bleibt PR #857 Draft und der Fehler wird
vor dem Merge behoben. Ohne Geräteergebnis bleibt die Android-Abnahme offen.

## Deep Links

Für gezielte Navigation gibt es ausschließlich das Schema `simpleoffice4me://open`.

Erlaubte Ziele:

```text
simpleoffice4me://open/documents
simpleoffice4me://open/documents/DOKUMENT-ID
simpleoffice4me://open/inventory
simpleoffice4me://open/images
simpleoffice4me://open/calendar
simpleoffice4me://open/contacts
simpleoffice4me://open/drive
```

Die Zuordnung zu internen Pfaden steht als feste Allowlist in `AndroidIntentRouter`. Beliebige Hosts, Schemas oder interne URL-Parameter werden nicht als WebView-Ziel übernommen. Dokument-IDs müssen dem bereits im Dokumentensystem verwendeten sicheren ID-Muster entsprechen.

Wenn ein Benutzer noch nicht angemeldet ist, bleibt das Ziel während des ersten Navigationsversuchs vorgemerkt. Ein zwischenzeitlicher `/auth/login`-Redirect verwirft das Ziel nicht; nach erfolgreichem Login wird die erlaubte Zielseite erneut aufgerufen.

## Google Drive über native Android-Autorisierung

Die APK nutzt für Google Drive nicht den Web-OAuth-Callback des eingebetteten Flask-Servers. Mobile Loopback-Callbacks und ein Wechsel zwischen WebView- und Browser-Cookies sind dafür nicht zuverlässig genug. Stattdessen verwendet die APK Google Identity Services über `AuthorizationClient` aus `play-services-auth`.

Der Ablauf ist:

1. Der angemeldete Benutzer wählt **Google verbinden** oder **Jetzt synchronisieren**.
2. Das WebView fängt nur diese beiden Drive-Formulare ab und übergibt Aktion und aktuellen CSRF-Token an die geschützte lokale JavaScript-Bridge.
3. `AndroidGoogleAuthorization` fordert ausschließlich `https://www.googleapis.com/auth/drive.file` über `AuthorizationRequest` an.
4. Google Play Services zeigt bei Bedarf die native Einwilligung an und liefert anschließend ein Access-Token an Java zurück.
5. Das Access-Token wird **nicht** an JavaScript, einen Deep Link oder eine externe App übergeben.
6. Java sendet Token, gewährte Scopes und Aktion direkt an `http://127.0.0.1:8765/settings/google-drive/android-token`.
7. Der localhost-POST enthält die bestehende WebView-Session als Cookie und den aktuellen CSRF-Token im Header.
8. Flask akzeptiert den Handoff nur von Loopback, nur mit dem APK-User-Agent, nur für `connect`/`sync`, nur mit `drive.file` und nur für einen bereits angemeldeten Benutzer mit `documents`- und `sync`-Rechten.
9. Das Token wird mit der bestehenden Secret-Key-Infrastruktur verschlüsselt gespeichert. Bei `sync` läuft anschließend sofort der normale Drive-Sync.
10. Das WebView lädt danach die Drive-Seite mit einem festen lokalen Status wie `connected`, `synced`, `cancelled` oder `error` neu.

Der normale Desktop-/Browser-OAuth-Ablauf bleibt unverändert und kann weiterhin einen Refresh-Token für serverseitige Langzeitnutzung erhalten. Die lokale APK verwendet dagegen die native Online-Autorisierung vor manuellen Drive-Aktionen. Für unbeaufsichtigten Hintergrund-Sync ohne Benutzerinteraktion wäre ein öffentlich erreichbarer HTTPS-Server-OAuth-Flow notwendig; die Android-App simuliert das nicht mit einem unsicheren Loopback- oder Custom-Scheme-Tokenfluss.

### Voraussetzung in Google Cloud

Für die APK muss ein Android-OAuth-Client zur Paketkennung `de.simpleoffice4me.android` und zum Signatur-Zertifikat der verwendeten APK eingerichtet sein. Für Test-/Debug-Builds und produktiv signierte Builds sind entsprechend die richtigen SHA-Fingerprints zu hinterlegen. Auf dem Gerät müssen Google Play Services für die native Drive-Autorisierung verfügbar sein.

## Kontrollierte Offline-Arbeitsdaten

Die optionale Capability `v3.android_offline` verbindet den bestehenden
`TodoStore` mit dem app-privaten `AndroidOfflineWorksetStore`. Es wird kein
zweites Aufgabenmodell angelegt.

- Die Oberfläche **Mehr -> Android Offline** lässt nur ausdrücklich ausgewählte
  Aufgaben in den lokalen Workset `tasks` übernehmen.
- Der Browser erhält aus der API nur die für die Offline-Aufgabe benötigte
  Projektion und einen starken ETag; Secrets und Authentisierungsmaterial gehören
  nicht zur Projektion.
- Offline schreibbar ist ausschließlich `task_status`. Die Mutation bleibt bis
  zur Synchronisation in einer begrenzten nativen Outbox.
- Der Server prüft bei jeder Mutation die bestehenden Aufgabenrechte und den
  Basis-ETag. Abweichungen werden als sichtbarer Konflikt behandelt.
- Eine Operation-ID ist pro Benutzer idempotent. Das Receipt wird im selben
  atomaren `todo.json`-Commit wie die Statusänderung gespeichert.
- Native Cache-Payloads erhalten zusätzlich einen SHA-256-Inhaltshash. Beim Lesen
  und nach einem unterbrochenen Dateiaustausch wird der Inhalt gegen die zum
  Index gehörende Prüfsumme validiert; ein unpassender Payload wird nicht als
  gültiger Offline-Stand ausgeliefert.
- `NavigationActivity` meldet einen wiederhergestellten Netzwerkzugang an die
  WebView; die Offline-Oberfläche stößt dann den Outbox-Sync an.
- `window.SimpleOfficeOffline` wird nur erzeugt, wenn die serverseitige
  Capability aktiv ist **und** die Seite eine authentifizierte SimpleOffice-
  Benutzer-ID trägt. Beim Logout wird die native Owner-Bindung aufgehoben.
- Der native Store bindet seinen Owner an Android-Identität **und** SimpleOffice-
  Benutzer-ID. Ein Wechsel einer dieser beiden Identitäten löscht den bisherigen
  Offline-Bestand vor dem ersten Lesezugriff.
- Beim Android-Kontowechsel werden Offline-Store, Outbox und lokale
  Owner-Metadaten vollständig entfernt und der Store ungebunden, bevor
  Android-Identität und WebView-Cookies gelöscht werden. Schlägt die physische
  Löschung fehl, wird der Kontowechsel abgebrochen.

## WebView-Lifecycle

Beim Wechsel in den Hintergrund werden zusätzlich zum bestehenden Kamera-/NFC-Cleanup `WebView.onPause()` und `pauseTimers()` aufgerufen. Beim Zurückkehren werden `resumeTimers()` und `WebView.onResume()` ausgeführt.

Das reduziert unnötige WebView-Arbeit auf älteren Geräten und ändert nichts am lokalen Flask-Prozess oder an ausdrücklich gestarteten nativen Audio-Threads.

## Bildschirmfreigabe und System-Cast

Die Bildschirm-Seite öffnet über die lokale, tokengebundene JavaScript-Bridge die Android-Anzeigeauswahl (`ACTION_CAST_SETTINGS`), sofern das Gerät dafür eine Systemkomponente anbietet. Fehlt diese, bleibt SimpleOffice Screen Share verfügbar.

Für den Fallback fordert Android die MediaProjection-Zustimmung über den Systemdialog an. Nach Zustimmung läuft die Aufnahme in `ScreenCaptureService` als `mediaProjection`-Foreground-Service mit dauerhafter Benachrichtigung und Stop-Aktion. Begrenzte JPEG-Frames werden ausschließlich an die lokale WebView übergeben, dort in eine Canvas-Videospur umgesetzt und über den normalen SimpleOffice-WebRTC-Pfad versendet. Abbruch, System-Stopp und der Stop-Button beenden Projektion, Virtual Display und MediaStream-Spur.

## Lokales Backend und Performance

Das eingebettete Flask-Backend läuft weiterhin ausschließlich auf `127.0.0.1:8765`. Der bisherige einzelne WSGI-Request-Thread wurde durch einen kleinen begrenzten Pool ersetzt:

- maximal 6 gleichzeitig arbeitende HTTP-Worker;
- maximal 12 angenommene beziehungsweise wartende Requests;
- danach erzeugt der Server keine weiteren Request-Threads, sondern wartet auf freie Kapazität;
- Worker und Warteschlange bleiben unabhängig von der Anzahl der WebView-Ressourcen begrenzt.

Damit können HTML, CSS, JavaScript, Bilder und lokale API-Aufrufe parallel abgearbeitet werden, ohne auf älteren Tablets einen unbeschränkten Thread-pro-Request-Server zu betreiben. Die Anwendung setzt zusätzlich `SIMPLEOFFICE_ANDROID=1`, damit künftige plattformspezifische Entscheidungen explizit erfolgen können statt über User-Agent-Erkennung im Python-Code.

## Bildschirmtastatur und Android-Systemleisten

Die allgemeine Weboberfläche nutzt zusätzlich `window.visualViewport`, sofern die WebView diese API bereitstellt. `static/js/mobile_viewport.js` berechnet beim Bearbeiten eines Feldes den tatsächlich von der Bildschirmtastatur verdeckten Bereich und stellt ihn als CSS-Variable `--so-keyboard-inset` bereit.

Dadurch gelten auf schmalen Bildschirmen global:

- zusätzlicher Scroll-Abstand unter fokussierten Eingabefeldern;
- automatisches Nachscrollen, wenn das aktive Feld unter der Bildschirmtastatur liegt;
- `sticky-bottom`-Aktionsleisten werden oberhalb der Tastatur positioniert;
- Scroll-Modals und Bottom-Offcanvas berücksichtigen die sichtbare Viewport-Höhe;
- `env(safe-area-inset-bottom)` bleibt zusätzlich erhalten.

Die Erkennung hängt nicht von einem Android-User-Agent ab und funktioniert damit auch in mobilen Browsern. Bereits vorhandene seitenbezogene Inline-Positionierung, etwa in der Inventarerfassung, hat weiterhin Vorrang.

## APK-Version und Architektur

Der Gradle-Build erzeugt für jede APK ein lokales `static/android-build.json`. In der nativen App ergänzt die Navigation damit die Web-App-Version um zum Beispiel:

```text
APK 1.0.6 · ARM32
```

beziehungsweise `ARM64`. Das erleichtert insbesondere bei älteren Geräten die Kontrolle, ob die richtige APK-Architektur installiert ist. Normale Browser laden diese Android-Metadaten nicht.

## Sicherheitsgrenzen

- Die JavaScript-Bridge bleibt nur auf der lokalen SimpleOffice-Origin aktiv.
- MediaProjection startet nie ohne den Android-Systemdialog und bleibt durch eine Foreground-Benachrichtigung sichtbar.
- Externe HTTP(S)-, Telefon- und Mail-Links werden weiter an Android übergeben.
- Geteilte oder über **Öffnen mit** übergebene Dateien werden nicht automatisch gespeichert.
- Maximal 20 Dateien werden pro noch nicht bestätigtem Share-Paket vorgemerkt.
- Cross-App-Dateien werden nur als `content://` akzeptiert.
- Native Downloads akzeptieren nur den fest gebundenen lokalen Flask-Endpunkt.
- Download-Redirects werden nicht automatisch verfolgt.
- Nichtlokale Downloads dürfen ausschließlich `http` oder `https` verwenden.
- Es gibt keine zusätzliche Speicher-Vollberechtigung.
- Die Deep-Link-Navigation ist eine feste Allowlist und keine allgemeine URL-Weiterleitung.
- Google-Drive-Access-Tokens erscheinen weder in JavaScript noch in Android-Intents oder Custom-Schemes.
- Der native Drive-Token-Handoff ist an Loopback, die bestehende WebView-Session, CSRF, APK-User-Agent, erlaubte Aktion und `drive.file` gebunden.

## Bedienung auf einem Galaxy Tab 3 mit Android 7

Für ein 32-Bit-System wird die `armeabi-v7a`-APK verwendet. Teilen, **Öffnen mit** und Speichern nutzen Androids Standard-Intents und benötigen keine neueren Storage-APIs. Die globale Viewport-Anpassung reduziert zusätzlich das Risiko, dass Eingabefelder oder Aktionsleisten hinter der Bildschirmtastatur beziehungsweise Systemnavigation verschwinden. Der begrenzte HTTP-Pool verbessert die lokale Seitenauslieferung, ohne das Gerät mit beliebig vielen Threads zu belasten.

Wenn eine Funktion wie Google Play Services, der Google Code Scanner oder ein bestimmter Audio-Codec auf dem Gerät nicht verfügbar ist, bleibt die restliche Anwendung weiterhin nutzbar. Drive-Autorisierung benötigt Google Play Services; ein vollständig Google-freies ROM kann die übrige SimpleOffice-App trotzdem verwenden.

## Tests und CI

### Black-Box-Verträge der Offline-HTTP-API

`tests/test_android_offline_api_black_box.py` prüft die bestehenden Verträge
über den Flask-HTTP-Testclient. Konten, Logins und Aufgaben entstehen über
`/auth/register`, `/auth/login` und `/tasks/`; direkte Datenbank-/Store-Eingaben
und Session-Manipulationen werden dafür nicht verwendet. Lediglich isolierte
Installationspfade, Capability-Konfiguration und die reguläre Schema-
Initialisierung gehören zur Testumgebung. CSRF bleibt eingeschaltet.

| Öffentlicher API-Aufruf / Eingabe | Unabhängig festgelegter Vertrag |
|---|---|
| `GET /api/v3/android-offline/tasks/<id>` nach Aufgabenerstellung | Titel, Beschreibung, Datum und Status entsprechen den eingegebenen Fachwerten; starker ETag, `no-store`, keine Credential-Felder |
| `POST /api/v3/android-offline/sync`, `task_status=completed`, aktueller Basis-ETag | Ergebnis `synced`; folgende GET-Anfrage zeigt `completed`, Titel/Beschreibung/Fälligkeit bleiben erhalten |
| Dieselbe Operation erneut nach einer späteren erfolgreichen Änderung, aus neuer Sitzung | Idempotenter Replay; kein Zurücksetzen des neueren Status und keine weitere Versionsänderung |
| Neue Operation mit veraltetem Basis-ETag | Ergebnis `conflict`; kein Last-Write-Wins und keine Versionsänderung |
| Andere Mutation, zusätzliche fachliche Payload-Felder oder unbekannter Status | Ergebnis `rejected`; ursprüngliche Fachwerte und Version bleiben erhalten |
| Zugriff eines anderen Benutzers auf eine private Aufgabe | Keine Auflistung; bekannte fremde ID berechtigt nicht zur Mutation |
| Cookie-authentifizierter Sync ohne CSRF-Token | HTTP 403; keine Status-/Versionsänderung |
| Zwei Benutzer mit derselben Operation-ID für jeweils eigene Aufgaben | Zwei unabhängige Änderungen; keine benutzerübergreifende Wiederverwendung eines Receipts |

IDs und ETags werden als undurchsichtige Eingabewerte aus öffentlichen
Antworten übernommen. Der Test berechnet keine Hashes, rekonstruiert keine
Versionen und verwendet keine Produktivhelfer für erwartete Ergebnisse.
Die erwarteten Fachwerte stehen als feste Literale im Test. Authentisierung,
Mutation und Kontrolle laufen durch die tatsächlichen HTTP-Routen; es wird
kein Erfolgsresultat gemockt. Netzwerktransport, APK und Geräteverhalten sind
damit weiterhin keine abgenommenen Testgegenstände.

Ausführen:

```bash
python -m unittest discover -s tests -p test_android_offline_api_black_box.py -v
```

Die Regressionstests prüfen insbesondere:

- `ACTION_SEND`, `ACTION_SEND_MULTIPLE` und `ACTION_VIEW content://` im Manifest;
- `singleTop` für neue Intents bei bereits geöffneter App;
- feste Deep-Link-Allowlist und Login-Redirect-Erhalt;
- Ablehnung fremder `file://`-Shares;
- Begrenzung auf 20 geteilte Dateien;
- Storage Access Framework für Downloads;
- lokale Origin-Prüfung, deaktivierte Redirects und externe Schema-Allowlist;
- WebView-Pause/Resume;
- `visualViewport`, Tastatur-Inset und mobile Sticky-Actions;
- native Google-`AuthorizationClient`-Verwendung, `drive.file`, CSRF/Session-gebundenen localhost-Handoff und das Fehlen eines Token-Deep-Links;
- begrenzten lokalen HTTP-Worker-Pool;
- weiterhin getrennte ARM64- und ARM32-Buildprofile.
- MediaProjection-Service, Foreground-Service-Typ und tokengebundene Screen-Bridge.

GitHub Actions baut anschließend beide APK-Architekturen und prüft Signatur und ABI wie bisher.

## Bewusste Grenzen und nächste Schritte

Der allgemeine Android-Dateipfad führt zunächst in die normale Dokumentenablage. Eine spätere Erweiterung kann explizite Share-Ziele für EÜR-Belege, Bilder oder Chat-Anhänge ergänzen. Solche Ziele sollten ebenfalls den jeweiligen bestehenden serverseitigen Importweg verwenden und Dateien nicht direkt in interne Ablagen kopieren.

Für Geräte ohne Google Play Services ist außerdem ein vollständig lokal gebündelter Barcode-Scanner als weiterer Ausbau sinnvoll; der derzeitige Google-Code-Scanner besitzt bereits einen manuellen Eingabe-Fallback, ist aber auf solchen ROMs nicht die optimale Lösung.
