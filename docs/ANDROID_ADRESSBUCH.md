# Kontakte und Kalender der Android-APK

Im **nahtlos eingebauten** Einstieg **Gerätequellen → Mit Android abgleichen**
verwaltet die APK ihre eigenen Kontakte und Kalender. Keine zweite App, kein
separates App-Passwort. In diesem ersten Stand ist der Abgleich ausdrücklich
eine Aktion im Vordergrund und läuft ausschließlich von SimpleOffice zu Android.

1. In der APK in SimpleOffice anmelden. **Kontakte abgleichen** auswählen oder
   die gewünschten Kalender ankreuzen und **Ausgewählte Kalender abgleichen**
   wählen. Erst dann die jeweilige Android-Freigabe bestätigen.
2. In der Android-Kontakte-App das SimpleOffice-Konto sichtbar machen. In der
   normalen Kalender-App die daraus übernommenen Kalender einblenden.
3. Einen synthetischen Testkontakt und einen Einzel-, einen Ganztags- und einen
   Serientermin in SimpleOffice anlegen. Einen Eintrag eines anderen Kontos
   unverändert lassen. Namen und Termin ändern, dann den Abgleich zweimal
   wiederholen. Dieselben Android-Zeilen müssen aktualisiert werden. Besonders
   die verschobene und die abgesagte Serieninstanz prüfen.
4. In den Geräteeinstellungen nur den Kontaktzugriff entziehen: Der
   Kontaktabgleich muss ablehnen, während Kalender verfügbar bleibt. Weitere
   Abnahmepunkte: Login eines zweiten SimpleOffice-Benutzers, alte Ansicht in
   den Hintergrund schicken und falsche Kalender-ID ablehnen.

Der Geburtstag kann vollständig oder als `--MM-TT` ohne Jahr gespeichert werden.
Für bekanntes Alter plus Tag, Monat und Stichtag gibt es eine Vorschau; das
Datum wird erst durch Speichern übernommen. Bei widersprüchlichen Angaben
einer möglichen Dublette zeigt **! Geburtstag prüfen** beide Werte und die
verfügbaren Quellen. Namensgleichheit allein reicht nicht. Der optionale
Geburtstagskalender übernimmt auch 29. Februar unverändert; sein technischer
Serienanfang ist kein Geburtsjahr.

Vor jedem Abgleich zeigt die APK zuerst die Anzahl der übertragbaren Kontakte
bzw. Termine und beim Kalenderabgleich zusätzlich die Namen genau der
ausgewählten Kalender. Die Vorschau bezieht sich auf die Auswahl, die beim
Start erfasst wurde; spätere Änderungen im Formular ändern diese Freigabe
nicht. Bis dahin liest sie nur die
SimpleOffice-Daten und fordert keine Android-Berechtigung an. Erst
**Übernehmen** startet die Freigabe und das Schreiben ins SimpleOffice-Konto.
Die Vorschau gilt zwei Minuten. Vor dem Schreiben prüft die APK die aktuelle
Kontokennung und Datenrevision erneut. Bei Änderungen, Wechsel der Ansicht
oder Ablauf ist eine neue Vorschau nötig. Abbrechen verändert nichts.

Unter **Mit Android abgleichen** bleibt der Status auf der geöffneten Seite
sichtbar: Vorschau, Android-Freigabe, laufender Abgleich, Ergebnis oder Abbruch.
Der Status ist kein gespeichertes Protokoll und enthält keine Kontakt- oder
Termindetails. Die eigentliche Bestätigung wird weiterhin separat angezeigt.

Ein manueller Kontaktabgleich lädt bis zu 1.000 sichtbare Kontakte in
Seiten zu je 25. Die App verlangt dieselbe Kontokennung und dieselbe Revision
für alle Seiten und beginnt erst nach vollständiger Prüfung mit der
Übernahme. Ändert sich das Adressbuch beim Laden, muss der Abgleich neu
gestartet werden. Ein Abbruch nach Beginn der Android-Schreibvorgänge kann
bereits übernommene Kontakte hinterlassen; ein erneuter Abgleich verwendet
deren stabile IDs. Andere Konten werden nicht bearbeitet.

Die ausgewählten Kalender (einschließlich Geburtstage) werden ebenfalls in
Seiten zu 25 geladen, insgesamt bis zu 1.000 Termine und Serienausnahmen pro
Abgleich. Die Revision umfasst die Kalenderwahl und Nutzdaten. Geänderte Daten
oder Rechte beim Laden beenden die Übernahme vor Android-Schreibvorgängen.
Serien werden vor ihren Ausnahmen vollständig geprüft und dann in kleinen
Android-Transaktionen übernommen. Bei Abbruch nach Beginn können fertige
Transaktionen stehen bleiben; „Abgleichen“ setzt mit denselben IDs erneut an.
Prüfe bei mehr als 1.000 Zeilen weniger Kalender pro Abgleich.

Noch nicht abgedeckt: automatischer Hintergrundabgleich und Entfernen nicht
mehr gelieferter Kontakte, Termine oder Serienausnahmen. Unvollständige oder
zu große Antworten führen nicht zu automatischen Löschungen. Kein Rückschreiben
und keine Änderungen an fremden Android-Konten. Die Geräteabnahme ist erst
erfolgreich, wenn die normalen Kontakt- und Kalender-Apps die Daten anzeigen,
nicht bereits wenn serverseitige Tests grün sind.

## Automatisierte Abnahme an echten Android-Datenanbietern

Der APK-Workflow führt bei Änderungen an der Android-Übernahme einen
Instrumentierungstest in einem frischen Emulator aus. Er prüft die
Kontaktfreigabe, eine synthetische Übernahme, zwei Wiederholungen nach
einer Namensänderung und einen unveränderten Kontakt außerhalb des
SimpleOffice-Kontos. Der Test liest veröffentlichte Android-Kontakte aus dem
Systemanbieter und verwendet den echten SimpleOffice-Writer. Er prüft
zusätzlich den Abbruch und Wiederanlauf einer synthetischen Übernahme mit
131 Kontakten. Bereits fertige Teilschritte behalten beim Wiederholen ihre
Android-Zeilen und Versionen, und ein unabhängiger Kontakt bleibt unberührt.
Für den Kalender prüft er Vorkommen vor und nach der Zeitumstellung,
eine verschobene und abgesagte Instanz, einen Schaltgeburtstag und ein
unberührtes fremdes Kalenderereignis. Der Test prüft dafür das gespeicherte
Ereignis und die vom Anbieter aufgebaute sichtbare Kalenderinstanz getrennt.
Eine fehlende Instanz bekommt eine begrenzte Wartezeit, ohne fehlende oder
veränderte Datensätze zu akzeptieren. WebView-Dialog und die Oberfläche einer
bestimmten Android-App bleiben separate Tests.

Für die manuelle Reproduktion muss ein **frischer, entbehrlicher AVD** laufen,
der die APK-ABI `arm64-v8a` anbietet und auf dem noch kein SimpleOffice
installiert ist. Der Test legt synthetische Kontakte, Kalender und Termine an. Den AVD danach
verwerfen, nicht ein bestehendes Android-System dafür verwenden.

```sh
(cd android/apk && gradle --no-daemon assembleDebug assembleDebugAndroidTest)
bash tools/run_android_contacts_provider_acceptance.sh \
  android/apk/app/build/outputs/apk/debug/app-debug.apk \
  android/apk/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk \
  emulator-5554
```

Nur die drei `INSTRUMENTATION_RESULT`-Werte
`simpleoffice_contacts_provider=pass`, `simpleoffice_contacts_bulk_resume=pass`
und `simpleoffice_calendar_provider=pass` zusammen mit
`INSTRUMENTATION_CODE: -1` gelten als bestanden. Der bisherige
APK-Pakettest allein belegt keinen Android-Lauf.
