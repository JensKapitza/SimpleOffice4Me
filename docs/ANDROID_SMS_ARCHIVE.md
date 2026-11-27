# Android-SMS-Archiv: lokaler Kern

`app.sms_archive_store.SmsArchive` ist ein serverseitiger Ablage- und
Wiederherstellungskern. Er liest, sendet und löscht keine SMS. Er ist nicht
über HTTP erreichbar und übernimmt keine App-Berechtigungsprüfung. Ein
späterer Communications-Einstieg muss Sitzung, Funktionsrecht und CSRF prüfen
und den Eigentümer sowie die Gerätezuordnung serverseitig ableiten.

- Der Kern speichert im konfigurierten Dokumentstamm unter
  `.simpleoffice-meta/sms-archive.sqlite3`. Für ein von der APK-Deinstallation
  unabhängiges Archiv muss dieser Stamm auf einem Server oder sonst außerhalb
  des privaten APK-Speichers liegen. Die Existenz einer SQLite-Datei im
  APK-Speicher allein belegt kein unabhängiges Backup.
- Archiviert werden bewusst ausgewählte SMS (Eingang/Ausgang), maximal 50
  je Transaktion. Der Schlüssel enthält den Eigentümer, eine stabile zufällige
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
- Mailversand ist nicht enthalten. Eine spätere Übergabe an den Mail-Editor
  muss ausgewählte Nachrichten und Zieladresse vorher sichtbar machen; der
  Versand gilt nie als Voraussetzung oder Nachweis für eine sichere Löschung.
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
python -m unittest -v tests.test_sms_archive_store
```
