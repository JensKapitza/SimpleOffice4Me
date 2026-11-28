# Android-SMS-Archiv: Ablage und Sicherung

`app.sms_archive_store.SmsArchive` ist ein serverseitiger Ablage- und
Wiederherstellungskern. Er liest, sendet und löscht keine SMS auf Geräten.
Unter **Kommunikation → SMS-Archiv** können angemeldete Konten mit dem
Mail-/Kommunikationsrecht vorhandene archivierte SMS prüfen, nach Nummer oder
Text suchen und ausgewählte Nachrichten als externe Sicherung herunterladen.

Die Archivsuche kombiniert Nummer oder Nachrichtentext mit **Empfangen**
oder **Gesendet** sowie einem optionalen Zeitraum. Suche und Anzeige
verwenden UTC; Anfangs- und Endtag sind eingeschlossen. Beim Blättern
bleiben die Filter erhalten. **Filter zurücksetzen** zeigt wieder das
gesamte eigene Archiv. Die Sicherung oder der Mailentwurf übernimmt
weiterhin nur ausdrücklich markierte Nachrichten, nicht alle Treffer.

**English:** Combine archive text/number search with incoming/outgoing
and optional inclusive UTC dates. Filters persist across result pages and
stay out of URLs. Backups and drafts still include only selected messages.


1. Markiere die gewünschten Nachrichten und wähle **Auswahl sichern**.
2. Bewahre die Datei verschlüsselt und außerhalb der App auf.
3. Wähle unter **Sicherung wiederherstellen** eine Sicherung desselben
   SimpleOffice-Kontos aus und bestätige den Import. Bereits vorhandene
   Nachrichten bleiben unverändert. Eine abweichend wiederverwendete ID
   bricht den Import der gesamten Auswahl ab.

### Eine SMS ohne Android-SMS-Berechtigung übernehmen

Kopiere in der vorhandenen Nachrichten-App den Text einer ausgewählten
Nachricht. Unter **Kommunikation → SMS-Archiv → Kopierte SMS hinzufügen**
fügst du den Text ein und ergänzt Rufnummer oder Absenderkennung, Richtung
und ursprünglichen Zeitpunkt. Die Zeitzone steht sichtbar im Formular.
Uneindeutige Ortszeiten bei Zeitumstellungen erfordern eine eindeutige
UTC-Eingabe. Erst die Bestätigung archiviert; die Nachricht auf dem Handy
bleibt unverändert. Ein Eintrag mit exakt gleichem Text, Absender, Richtung
und Zeitpunkt wird nicht doppelt angelegt. Die Liste kennzeichnet den
Eintrag als manuell und die Sicherung umfasst ihn wie andere SMS.

Wenn die Android-Nachrichten-App **Teilen** für eine einzelne SMS anbietet,
wähle **SimpleOffice4Me**. Die APK öffnet das eigene lokale SMS-Archiv und
bietet **Ins Formular übernehmen** an. Es wird ausschließlich der geteilte
Klartext übernommen, niemals automatisch gespeichert oder über eine URL
transportiert. Ein bereits ausgefüllter Text bleibt unverändert. Nummer,
Richtung und ursprüngliche Zeit kommen in der üblichen Android-Textfreigabe
nicht zuverlässig mit; prüfe und ergänze sie vor dem Speichern. Wenn die
Nachrichten-App kein Teilen anbietet, funktioniert Kopieren und Einfügen
weiterhin. Die APK fordert dazu weder SMS- noch Speicherzugriff an und liest
keine Anhänge, den Gesprächsverlauf oder die Zwischenablage.

Die Standard-APK und das manuelle Teilen haben keinen Zugriff auf die
SMS-Datenbank des Handys. Beliebige Android-Backups werden nicht unterstützt.
Die Zwischenablage wird nie
von SimpleOffice automatisch gelesen. Suchbegriffe werden
per Formular gesendet statt in URL oder Serverprotokoll geschrieben.
Sitzung, Funktionsrecht und CSRF werden nach dem üblichen Verfahren geprüft.
Die Auswahl und die Sicherungsdatei können den Archiveigentümer nicht setzen.
Der Browser erhält für Seiten und Backups `private, no-store`. Die SQLite-
Ablage bleibt lokal; sie ist weder ein DMS-Dokument noch ein Objekt für die
allgemeine S3-Schnittstelle. Der bewusste Download aus dem eigenen Konto ist
der vorgesehene Exportweg. Lege diese Datei nicht in einem freigegebenen Ordner ab.

Auf Android öffnet **Auswahl sichern** die Android-Dateiverwaltung. Wähle dort
ein unabhängiges Speicherziel. Falls die Auswahl des Speicherorts länger als
fünf Minuten dauert oder du dich abmeldest, beginne den Export erneut. Der
Downloadlink enthält nur eine Zufallskennung und funktioniert nur in der
aktuellen angemeldeten Sitzung. Er enthält weder SMS-Text noch Eintragsnummern.

- Der Kern speichert im konfigurierten Dokumentstamm unter
  `.simpleoffice-meta/sms-archive.sqlite3`. Für ein von der APK-Deinstallation
  unabhängiges Archiv muss dieser Stamm auf einem Server oder sonst außerhalb
  des privaten APK-Speichers liegen. Die Existenz einer SQLite-Datei im
  APK-Speicher allein belegt kein unabhängiges Backup.
- Bei manueller Auswahl oder Sicherung werden bewusst ausgewählte SMS
  (Eingang/Ausgang) verarbeitet, maximal 50 je Transaktion. Der Schlüssel enthält den Eigentümer, eine stabile zufällige
  Gerätezuordnung und die SMS-Provider-ID. Dieselbe Zeile derselben Zuordnung
  ist idempotent. Eine später neu belegte Provider-ID mit abweichendem Inhalt
  erscheint als Konflikt und überschreibt nichts. Nach einer Neuinstallation
  muss die berechtigte Gerätezuordnung wiedererkannt oder bewusst neu vergeben
  werden; geräteübergreifend gleiche Nachrichten werden nicht geraten.
- Suche ist auf den Eigentümer begrenzt. Vor Anzeige als archiviert und vor
  Export wird der gespeicherte Inhalt geprüft. Export und Wiederherstellung
  arbeiten pro ausgewähltem Paket; ein Prüfsummenfehler oder ein fremder
  Eigentümer verhindert die Wiederherstellung. Wiederherstellung in eine
  frisch angelegte Ablage wurde getestet. SHA-256 erkennt Korruption, ersetzt
  aber weder Authentifizierung noch Verschlüsselung. Backups enthalten
  private SMS im Klartext und gehören in einen zugriffsgeschützten,
  verschlüsselten externen Speicher.
- **Mailentwurf vorbereiten:** Markiere archivierte SMS und öffne die lokale
  Vorschau. Sie enthält nur die Auswahl, inklusive Originalzeit in UTC,
  Richtung und Rufnummer. Es wird noch nichts versendet oder als neuer
  Entwurf auf einem Mailserver gespeichert. Wähle ein beschreibbares eigenes
  Versandkonto, gib den Empfänger bewusst ein und prüfe den editierbaren Text.
  Erst **Mail senden** nutzt den normalen SimpleOffice-Mailversand. Bei mehr
  als 64 KiB wähle einen kleineren Teil. Der Originaleintrag bleibt unverändert.
  Eine Mailkopie ersetzt keine überprüfbar wiederherstellbare Sicherung.
- Geräte-Löschung ist nicht enthalten. Eine spätere separate, ausdrücklich
  bestätigte Aktion darf nur nach unabhängig überprüfter Wiederherstellbarkeit
  und einem erneuten Abgleich der unveränderten Provider-Zeile angeboten
  werden. Ohne Android-Standard-SMS-Rolle bleibt nur eine vom Benutzer
  selbst ausgeführte Lösung in seiner SMS-App. SimpleOffice darf die Rolle
  nicht stillschweigend ändern.

## Android und Distribution

Der Android-SMS-Provider kann anderen Apps bei berechtigtem lesenden Zugriff
SMS liefern. Schreib- und Löschrechte sind laut
[Android Telephony API](https://developer.android.com/reference/android/provider/Telephony)
auf die Standard-SMS-App beschränkt. Zusätzlich sind SMS-/Anruflisten-
Berechtigungen in [Google Play](https://support.google.com/googleplay/android-developer/answer/10208820)
auf zulässige Standard-Handler oder genehmigte Ausnahmen begrenzt. Eine
Ausnahme für Backup/Restore verlangt einen Nachweis der Rücksicherung in den
ursprünglichen Standard-Handler, nicht nur einen Export aus SimpleOffice.
Dieser Kern fordert weder SMS-Berechtigungen noch eine Standardrolle an.

Test mit ausschließlich synthetischen SMS:

```sh
python -m unittest -v tests.test_sms_archive_store tests.test_sms_archive_routes
```


APK-Prüfung aus `android/apk` mit konfiguriertem Android-SDK und Gradle:

```sh
gradle --no-daemon testDebugUnitTest assembleDebug
```

Auf einem Testgerät eine synthetische SMS als Klartext teilen, die Übernahme
einmal abbrechen, dann erneut teilen und übernehmen. Vor Bestätigung der
Pflichtfelder darf die Suche keinen neuen Eintrag zeigen. Danach suchen und
die Nachricht in eine externe Sicherung aufnehmen. Zwei Freigaben,
Nicht-Klartext und einen vorhandenen Textentwurf als Negativfälle prüfen.

### Wiederherstellung nach einer Neuinstallation prüfen

Die Android-CI startet zwei frische Wegwerf-Emulatoren mit demselben
ARM64-Debug-APK. Der erste exportiert eine von zwei synthetischen Meldungen
nach Abbruch und erneuter Auswahl über den Systemdialog. Nur diese Datei
wird übertragen, keine App-Daten. Der zweite startet mit einem leeren
Archiv, wählt die Datei im Android-Dateidialog und prüft, dass der erste
Import genau eine und die Wiederholung keine neue Nachricht anlegt.

Lokal ist mit einem frisch eingerichteten kompatiblen Emulator der Exporttest
reproduzierbar. Vom Repository-Stamm starten und die Emulator-ID einsetzen:

```sh
python -m pip install --require-hashes -r requirements-ci-tools.txt -r requirements-ci-browser.txt
bash tools/run_android_sms_download_acceptance.sh android/apk/app/build/outputs/apk/debug/app-debug.apk emulator-5560
```

Der vollständige Lauf mit zwei frischen Emulatoren ist Teil des Android-CI-Jobs.
Ein grüner Exportmarker allein bestätigt noch nicht die Wiederherstellung.

## Direkter Geräteimport in einer ausdrücklich freigegebenen APK

In einer entsprechend gebauten APK findest du den Import unter **Kommunikation → SMS-Archiv → SMS vom Handy importieren**. Optional kannst du einen UTC-Zeitraum eingrenzen. Tippe auf **SMS-Vorschau** und bestätige den nativen Dialog. Beim ersten Mal fragt Android ggf. zusätzlich nach Lesezugriff. Die Vorschau zeigt die Anzahl neuer und bereits archivierter Nachrichten für den gewählten Zeitraum. Sie speichert nichts. Erst **Geprüfte SMS archivieren** und die separate native Bestätigung übernehmen die geprüfte Auswahl in das private lokale Archiv des angemeldeten Kontos. Veränderte, fehlende oder neu hinzugekommene Provider-Zeilen sowie geänderte Archivzahlen erfordern eine neue Vorschau. Die Vorschau ist fünf Minuten gültig. Bei einem erneuten Import werden identische Provider-Zeilen nicht dupliziert. RCS, MMS, Entwürfe und Nachrichten anderer Android-Profile gehören nicht zum Import. Die Nachrichten-App bleibt unverändert; dieser Einstieg versendet oder löscht nichts.

Für einen Installationskanal, der die Android-Freigabe unterstützt, lässt sich die **optionale APK** mit `SIMPLEOFFICE_ANDROID_DEVICE_SMS=1` bauen (aus dem bestehenden Android-Build-Verzeichnis mit dem üblichen Wheel- und Signatur-Setup: `SIMPLEOFFICE_ANDROID_DEVICE_SMS=1 gradle --no-daemon :app:assembleRelease`). Ohne die Option enthält die APK **kein** `READ_SMS`. Unter Android 10+ ist [READ_SMS als „hard restricted“ eingestuft](https://developer.android.com/reference/android/Manifest.permission#READ_SMS): Der Installationskanal muss die Berechtigung zulassen, und der Nutzer muss sie erteilen. Ein Download oder Sideload allein garantiert das nicht. Diese Variante ist nicht für Google Play vorgesehen: Die [Play-Regeln zu SMS-Berechtigungen](https://support.google.com/googleplay/android-developer/answer/10208820) verlangen Eignung und Freigabe. Das private Archiv ersetzt keine Wiederherstellung in den ursprünglichen Android-Nachrichtenanbieter. SimpleOffice wird dabei nicht zur Standard-SMS-App. Fällt der Direktimport weg, bleiben manuelles Teilen und die Archiv-Sicherung verfügbar.

Für signierte Testverteilung erzeugt ein manueller Start des Workflows
**Android APK release (signed)** auf `main` das getrennt benannte Artefakt
`-SMS-IMPORT.apk`. Der Workflow nimmt keine frei wählbaren Eingaben an.
Version-Tag-Releases bleiben Standard-APKs ohne SMS-Leserecht.
Die APK verwendet dieselbe App-ID und Signatur. Erfordert der Installationskanal
eine Neuinstallation, sichere und prüfe das Archiv zuerst außerhalb der App.
Nicht deinstallieren, um eine Berechtigung zu erzwingen.

Pro Vorgang sind höchstens 1.000 SMS und 2 MiB Nachrichtentext einschließlich Absender erlaubt. Bei Überschreitung schlägt der gesamte Vorgang ohne Teilimport fehl: Wähle einen kleineren Datumsbereich. Ein Konflikt mit einem bereits archivierten Datensatz wird ebenfalls vollständig zurückgerollt. Eine lokale zufällige Installationskennung trennt Android-Provider-IDs von manuellen Einträgen und von anderen Installationen. Eine Neuinstallation verwirft diese lokale Identität, sodass ein importiertes Archiv aus einer früheren Installation nicht anhand der Provider-ID dedupliziert werden kann. Bewahre Sicherungen wie bisher außerhalb der App an einem geschützten, verschlüsselten Ort auf.

Sicherheitsgrenzen: Nur die exakt lokale, eingebettete Archivansicht bekommt einen Main-Frame-Kanal. Die native Freigabe stellt eine einmalige, nicht für JavaScript sichtbare Leseberechtigung für den aktuellen Vorgang aus. Der feste Loopback-Aufruf übergibt nur Zeitraum, Aktion, opaque Vorschau-ID, Anmeldecookie, CSRF-Token und Vorgangsberechtigung. Für die Vorschau verbleiben nur Prüfsumme, Anzahl und Sitzungsgeltungsbereich vorübergehend im Prozessspeicher. Der HTTP-Handler prüft das Konto und liest den Android-Provider danach im selben Prozess über Chaquopy; SMS-Inhalte und Absender werden weder im Bridge-Aufruf noch in dessen HTTP-Anfrage oder -Antwort übertragen. Die normale authentifizierte Archivansicht bleibt zum Anzeigen und Durchsuchen zuständig. Fehler enthalten keine Provider-Texte. Navigation oder Hintergrundwechsel widerrufen die Freigabe für noch nicht abgeschlossene Lesevorgänge. Eine bereits atomar begonnene Archivschreiboperation bleibt vollständig oder wird zurückgerollt.

Prüfung: `python -m unittest -q tests.test_android_sms_device_import tests.test_sms_archive_store tests.test_sms_archive_routes`. Der Android-PR-Workflow prüft beide Manifeste und die Übereinstimmung der Debug-Signaturen. Auf einem frischen API-35-AVD legt er in der Standard-APK über die reguläre Archivseite eine künstliche Nachricht an. Anschließend installiert er die optionale APK mit `adb install -r` über dieselbe Installation, ohne Deinstallation oder Zurücksetzen der Daten. Er prüft, dass die alte Nachricht erhalten bleibt, und speist erst dann eine weitere künstliche SMS über den Android-Emulator ein. Er kontrolliert Ablehnen, Erlauben, Vorschau ohne Schreibzugriff, ausdrücklich bestätigte Archivierung, Wiederholung und Neustart mit beiden Nachrichten.

Der CI-Aufruf lautet `bash tools/run_android_sms_device_import_acceptance.sh "$ordinary_apk" "$optional_apk"`. Das prüft den Variantenwechsel innerhalb derselben Revision mit Debug-Signatur, nicht den Wechsel zwischen unterschiedlichen Signaturen oder Android-Installern. Der Test greift nicht auf vorhandene Geräte oder echte SMS zu.
