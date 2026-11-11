# Support mit Bildschirmfreigabe

## Arbeitsbereich und Navigation

Die Bildschirmseite ist der dauerhafte Support-Arbeitsbereich. Die Anwendung
wird darin in einem gleichursprünglichen Frame geöffnet. Bildschirmaufnahme,
WebRTC-Verbindung und Supportsteuerung gehören zum äußeren Dokument. Normale
Seitenwechsel innerhalb des Anwendungsframes ersetzen dieses Dokument nicht;
die Freigabe läuft weiter. Der aktive Zustand und die Möglichkeit zum Beenden
bleiben im Arbeitsbereich sichtbar.

Für den Support die Anwendung innerhalb dieses Arbeitsbereichs bedienen. Ein
vollständiges Neuladen, das Verlassen des äußeren Arbeitsbereichs oder das
Schließen des Browser-/Anwendungsfensters beendet den Capture-Kontext. Eine
gespeicherte Session-Kennung kann einen Browser-Medienstream nicht erhalten.
Nach einem solchen Abbruch ist eine neue sichtbare Bildschirmfreigabe nötig.
Auch ein Backend-Neustart erfordert eine neue Verbindung, da WebRTC-Signaling
weiterhin im Arbeitsspeicher verwaltet wird.

Bei der Bildschirmauswahl ein Fenster oder einen Bildschirm wählen, der die zu
unterstützende Anwendung zeigt. Browserberechtigungen, sichtbare Aufnahmeanzeige
und die Beenden-Funktion des Betriebssystems bleiben wirksam. App-Navigation
innerhalb des Frames ist keine Zusage für eine Aufnahme über Browserneustarts.

## Eingaben anfordern und freigeben

Die unterstützende Seite fordert Eingaben ausdrücklich an. Der Nutzer sieht die
Anforderung und kann sie erlauben oder ablehnen. Eine erteilte Freigabe lässt
sich jederzeit widerrufen; nach Ende der Sitzung gilt sie nicht weiter.
Eine Anforderung allein berechtigt noch zu keiner Änderung.

Die Eingabefunktion ist auf unterstützte Formularfelder in der geöffneten
SimpleOffice-Anwendung begrenzt. Die unterstützende Person kann dort nach der
Freigabe Feldwerte anpassen. **Speichern und Absenden bleiben beim Nutzer.**
Die Funktion führt keine beliebigen Betriebssystemeingaben aus und steuert
keine fremden Programme. Passwort- und Datei-Uploadfelder gehören nicht zum
Supporteditor. Ferneingaben lösen keine `input`-/`change`-Ereignisse aus, weil
diese in bestehenden Formularen automatisch speichern können. Abhängige
Berechnungen oder dynamische Felder benötigen deshalb gegebenenfalls eine
eigene Interaktion des Nutzers. Die normalen Validierungs- und Berechtigungsprüfungen der
Anwendung gelten beim anschließenden Speichern unverändert.

Eine bestehende Anwendungsberechtigung wird durch den Verbindungscode nicht
erweitert. Support nur mit der tatsächlich eingeladenen Person durchführen und
die Freigabe nach Abschluss beenden.

## Hinweise und Verlauf

Beide Seiten können schriftliche Hinweise senden, beispielsweise wenn kein Ton
verfügbar ist oder ein Arbeitsschritt erläutert werden soll. Eingehende Hinweise
erscheinen als sichtbare Meldung im Support-Arbeitsbereich. Eine Bestätigung
dokumentiert, dass der Hinweis zur Kenntnis genommen wurde; sie ist keine
Freigabe zum Bearbeiten von Daten.

Der Verlauf bewahrt Hinweise und deren Bestätigungen sowie Anforderung,
Erteilung, Ablehnung und Widerruf der Eingabefreigabe mit Zeitpunkt und Richtung
auf. Auch die erfolgreiche oder verworfene Verarbeitung einer Feldeingabe wird
ohne deren Feldwert erfasst. Diese Ereignisse werden unabhängig von der flüchtigen WebRTC-Signalisierung
dauerhaft gespeichert. Das Ende der Medienverbindung löscht den Verlauf nicht.
Es werden keine Bildschirmaufzeichnung und keine eingegebenen Formularwerte als
Supportverlauf gespeichert. Vertrauliche Daten daher auch nicht in Hinweistexte
schreiben.

Eine dauerhafte Verlaufsablage stellt die Medienverbindung nach einem Neustart
nicht wieder her. Nachrichten- und Ereignisgrenzen verhindern unbegrenztes
Wachstum; bei Erreichen einer Grenze muss die Oberfläche den Fehler anzeigen.

## Prüfung und Grenzen

Automatisierte Prüfungen müssen mindestens normale Navigation im
Anwendungsframe, Anfordern/Erlauben/Ablehnen/Widerrufen, abgewiesene Eingaben
ohne Freigabe, fremde Sitzungen, sichere Textdarstellung, Meldungsbestätigungen
und das Fortbestehen des Verlaufs abdecken. Veraltete Nachrichten dürfen keine
widerrufene Freigabe wieder aktivieren. Ein Dokumentwechsel darf keine
Feldänderung auf ein inzwischen anderes Formular umlenken.

Die bisherigen Signaling-Quoten und das Verhalten bei Netzwerkfehlern gelten
weiter; siehe [Bildschirmfreigabe](screen-sharing-miracast.md). Automatisierte
Tests mit Medien-Doubles ersetzen keine reale Zwei-Geräte-Abnahme von
Bildschirmauswahl, Audio, TURN/LAN-Verbindung oder Android-MediaProjection.
Windows, Linux, macOS und Android jeweils mit tatsächlicher Seiten-Navigation,
sichtbarem Stopp und Wiederverbindung nach Abbruch prüfen.

## Betrieb und automatisierte Prüfung

Der Verlauf liegt lokal unter `.simpleoffice-meta/screen-support/` im
Dokumentenroot, mit atomarem Dateiaustausch und privaten Dateirechten.
Nach Sitzungsende kann nur der Eigentümer den archivierten Verlauf abrufen;
der letzte Verlauf wird auf der Bildschirmseite wieder geladen. Die Ablage
gehört in das lokale Backup und wird nicht automatisch gelöscht oder nach S3
projiziert. Ein Datenträgerfehler erscheint als Supportfehler und darf das
Beenden der Bildschirmaufnahme nicht verhindern.

Die aktive Freigabe ist an einen angemeldeten Empfänger gebunden. Bleibt dessen
Support-Heartbeat länger als 15 Sekunden aus, wird das Eingaberecht beim nächsten
Supportzugriff widerrufen. Nach Trennung oder erneutem Verbinden ist eine neue
Zustimmung erforderlich. Pro Sitzung sind 500 Verlaufseinträge vorgesehen;
ein abschließender Widerruf bleibt auch bei voller Historie möglich und beendet
dann den Supportkanal. Feldbefehle sind separat auf 128 wartende Einträge begrenzt.

Prüfbefehle:

```sh
python -m unittest discover -s tests -p 'test_screen*.py' -v
node --test tests/screen*test.cjs
python tests/browser/screen_support_smoke.py
```

Der letzte Befehl benötigt Playwright und Chromium (optional
`SCREEN_BROWSER_EXECUTABLE`). Er verwendet eine isolierte Flask-Testanwendung,
zwei Browserkontexte und einen synthetischen Canvas-Medienstream; er prüft
WebRTC, Navigation, Freigabe, Feldbearbeitung ohne Auto-Save, Hinweis/Bestätigung,
Widerruf und Verlauf nach Neuladen, jedoch keine reale Bildschirmaufnahme.
