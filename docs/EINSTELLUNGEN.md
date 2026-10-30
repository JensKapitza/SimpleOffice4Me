# Einstellungen

Die Einstellungen sind unter **Einstellungen** verfügbar und werden im Hauptarchiv als `.simpleoffice-meta/settings.json` gespeichert. Jede Änderung erhält zusätzlich eine Revision im lokalen Revisionsarchiv.

Enthalten sind derzeit:

- Standardsprache für neue Sitzungen: Deutsch oder Englisch.
- Zeitzone als IANA-Name, z. B. `Europe/Berlin`.
- Standardzustand, Standard-Tags und Archiv-Vorauswahl für Web-Uploads.
- Termin-Sichtbarkeit, externer Hinweistext und Standarddauer.
- Ablaufzeit neuer passwortgeschützter HTTPS-Freigaben.
- Admin-only: ausgehende automatische Fehlerberichte im Reiter **Integrationen & Dienste** aktivieren oder deaktivieren. Es werden nur freigegebene technische Diagnosedaten übertragen; lokale Fehlerprotokolle und manuelle Fehlerberichte bleiben unabhängig davon verfügbar.
- Admin-only: öffentliche Abfrage explizit veröffentlichter Federation-Peer-Profile im Reiter **Integrationen & Dienste** aktivieren oder deaktivieren. Registrierung, Trust, Auflösung und Rendezvous bleiben authentifiziert.
- Admin-only: maximale Uploadgröße im Reiter **Dokumente & Medien** (Standard 512 MiB, zulässig 1–4096 MiB). Sie begrenzt die gesamte HTTP-Anfrage und damit auch Uploadpfade, die dieses zentrale Limit verwenden; S3-Uploads können zusätzlich enger begrenzt sein.
- Admin-only: Vorschau-, Archiv-OCR- und ressourcenabhängige Archiv-Inhaltssuche im Reiter **Dokumente & Medien**. Konfigurierbar sind Lastschwelle je CPU (0,1–8,0), Mindestmenge freien Arbeitsspeichers (64–65.536 MiB), Datenträgerreserve (128–1.048.576 MiB), Pause je Archiveintrag (0–100 ms; Standard 2 ms), maximale Kataloggröße (100–200.000 Einträge; Standard 20.000), Volltextbudget pro Archiv (1–64 MiB; Standard 8 MiB), Eintragsgröße für Inhaltsverarbeitung (1–8.192 MiB; Standard 512 MiB), Höchstzahl inhaltlich verarbeiteter Einträge (1–10.000; Standard 500), Textbudget je Archiveintrag (1–32 MiB; Standard 2 MiB) und maximales Kompressionsverhältnis (10–10.000; Standard 200).

Die Sprachwahl in der Navigationsleiste gilt sofort für die aktuelle Sitzung. Die Standardsprache gilt für neue Sitzungen. Die Navigation und die neue Einstellungsseite sind auf Deutsch und Englisch verfügbar; weitere Fachseiten verwenden die bestehende deutsche Fachterminologie und können schrittweise in Sprachschlüssel überführt werden.

Die Seite ändert absichtlich weder Berechtigungen, Aufbewahrungsfristen noch bestehende Dokumente. Neue Uploads übernehmen dagegen die konfigurierten Standard-Tags und den Standardzustand.

Der Archiv-Inhaltstiefenindex bleibt standardmäßig automatisch und ressourcenabhängig. Administratoren können ihn deaktivieren und CPU-Lastschwelle, Mindestmenge freien Arbeitsspeichers sowie Datenträgerreserve einstellen. Alle drei Grenzen werden unabhängig geprüft; „Nur Katalog indexieren“ kann sie nicht umgehen. Die Pause je Archiveintrag steuert den Kompromiss zwischen Indexdurchsatz und anderer Arbeit und gilt für neu gestartete Archivindexläufe. Die Kataloggröße begrenzt Zahl und Suchbarkeit der Einträge; Archive oberhalb der Grenze erhalten einen Teilindex. Eintragsgröße, Inhaltsanzahl und Kompressionsverhältnis begrenzen die Textextraktion; das Textbudget je Eintrag und je Archiv begrenzt den abgeleiteten Suchtext. Dateinamen bleiben katalogisiert. Legacy-Umgebungsvariablen werden nur übernommen, solange der jeweilige persistente Wert noch nicht gespeichert wurde. Ressourcenänderungen gleichen beim nächsten Lauf vorhandene abgeleitete Archivindizes an; Originalarchive werden nicht verändert.

Das Uploadlimit wird in den Anwendungseinstellungen revisionsgesichert gespeichert und beim Start des Webprozesses geladen. Eine Änderung oder Rücksetzung wird nach einem Neustart wirksam. Die bisherige Umgebungsvariable `SIMPLEOFFICE_MAX_UPLOAD_MIB` dient als Migrationswert, solange noch kein Uploadlimit gespeichert wurde.
