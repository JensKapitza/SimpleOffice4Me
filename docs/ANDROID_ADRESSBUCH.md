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
