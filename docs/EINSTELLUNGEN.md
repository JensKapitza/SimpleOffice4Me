# Einstellungen

Die Einstellungen sind unter **Einstellungen** verfügbar und werden im Hauptarchiv als `.simpleoffice-meta/settings.json` gespeichert. Jede Änderung erhält zusätzlich eine Revision im lokalen Revisionsarchiv.

Enthalten sind derzeit:

- Standardsprache für neue Sitzungen: Deutsch oder Englisch.
- Zeitzone als IANA-Name, z. B. `Europe/Berlin`.
- Standardzustand, Standard-Tags und Archiv-Vorauswahl für Web-Uploads.
- Termin-Sichtbarkeit, externer Hinweistext und Standarddauer.
- Ablaufzeit neuer passwortgeschützter HTTPS-Freigaben.
- Admin-only: maximale Uploadgröße im Reiter **Dokumente & Medien** (Standard 512 MiB, zulässig 1–4096 MiB). Sie begrenzt die gesamte HTTP-Anfrage und damit auch Uploadpfade, die dieses zentrale Limit verwenden; S3-Uploads können zusätzlich enger begrenzt sein.
- Admin-only: Vorschau-, Archiv-OCR- und ressourcenabhängige Archiv-Inhaltssuche im Reiter **Dokumente & Medien**. Konfigurierbar sind die Lastschwelle je CPU (0,1–8,0), die Mindestmenge freien Arbeitsspeichers (64–65.536 MiB) und der Datenträgerreservewert (128–1.048.576 MiB).

Die Sprachwahl in der Navigationsleiste gilt sofort für die aktuelle Sitzung. Die Standardsprache gilt für neue Sitzungen. Die Navigation und die neue Einstellungsseite sind auf Deutsch und Englisch verfügbar; weitere Fachseiten verwenden die bestehende deutsche Fachterminologie und können schrittweise in Sprachschlüssel überführt werden.

Die Seite ändert absichtlich weder Berechtigungen, Aufbewahrungsfristen noch bestehende Dokumente. Neue Uploads übernehmen dagegen die konfigurierten Standard-Tags und den Standardzustand.

Der Archiv-Inhaltstiefenindex bleibt standardmäßig automatisch und ressourcenabhängig. Administratoren können ihn deaktivieren und CPU-Lastschwelle, Mindestmenge freien Arbeitsspeichers sowie Datenträgerreserve einstellen. Alle drei Grenzen werden unabhängig geprüft; „Nur Katalog indexieren“ kann sie nicht umgehen. Die Werte gelten für den nächsten Archivindexlauf. Legacy-Umgebungsvariablen werden nur übernommen, solange der jeweilige persistente Wert noch nicht gespeichert wurde. Nach einer Änderung gleicht der nächste Indexlauf vorhandene abgeleitete Archivindizes an; Originalarchive werden nicht verändert.

Das Uploadlimit wird in den Anwendungseinstellungen revisionsgesichert gespeichert und beim Start des Webprozesses geladen. Eine Änderung oder Rücksetzung wird nach einem Neustart wirksam. Die bisherige Umgebungsvariable `SIMPLEOFFICE_MAX_UPLOAD_MIB` dient als Migrationswert, solange noch kein Uploadlimit gespeichert wurde.
