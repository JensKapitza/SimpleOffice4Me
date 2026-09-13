# Android-Integration

## Ziel

Die Android-App soll sich wie eine normale Android-Anwendung verhalten, ohne den bestehenden SimpleOffice-Sicherheits- und Dokumentenpfad zu umgehen. Die Integration ist deshalb absichtlich schmal: Android liefert Dateien, Ziele oder Speicherorte; die eigentliche Dokumentverarbeitung bleibt bei SimpleOffice.

## Unterstützte Android-Versionen

Beide APK-Varianten haben `minSdk 24` und unterstützen damit Android 7.0 oder neuer.

- `arm64-v8a`: Python 3.13 für moderne 64-Bit-Geräte.
- `armeabi-v7a`: Python 3.11 für 32-Bit-Geräte und ältere Tablets.

Die native Integration verwendet nur APIs, die ab API 24 vorhanden sind. Safe Browsing bleibt weiterhin erst ab Android 8 aktiviert.

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

## WebView-Lifecycle

Beim Wechsel in den Hintergrund werden zusätzlich zum bestehenden Kamera-/NFC-Cleanup `WebView.onPause()` und `pauseTimers()` aufgerufen. Beim Zurückkehren werden `resumeTimers()` und `WebView.onResume()` ausgeführt.

Das reduziert unnötige WebView-Arbeit auf älteren Geräten und ändert nichts am lokalen Flask-Prozess oder an ausdrücklich gestarteten nativen Audio-Threads.

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

GitHub Actions baut anschließend beide APK-Architekturen und prüft Signatur und ABI wie bisher.

## Bewusste Grenzen und nächste Schritte

Der allgemeine Android-Dateipfad führt zunächst in die normale Dokumentenablage. Eine spätere Erweiterung kann explizite Share-Ziele für EÜR-Belege, Bilder oder Chat-Anhänge ergänzen. Solche Ziele sollten ebenfalls den jeweiligen bestehenden serverseitigen Importweg verwenden und Dateien nicht direkt in interne Ablagen kopieren.

Für Geräte ohne Google Play Services ist außerdem ein vollständig lokal gebündelter Barcode-Scanner als weiterer Ausbau sinnvoll; der derzeitige Google-Code-Scanner besitzt bereits einen manuellen Eingabe-Fallback, ist aber auf solchen ROMs nicht die optimale Lösung.
