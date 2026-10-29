# Einstellungen

Die Einstellungen sind unter **Einstellungen** verfügbar und werden im Hauptarchiv als `.simpleoffice-meta/settings.json` gespeichert. Jede Änderung erhält zusätzlich eine Revision im lokalen Revisionsarchiv.

Enthalten sind derzeit:

- Standardsprache für neue Sitzungen: Deutsch oder Englisch.
- Zeitzone als IANA-Name, z. B. `Europe/Berlin`.
- Standardzustand, Standard-Tags und Archiv-Vorauswahl für Web-Uploads.
- Termin-Sichtbarkeit, externer Hinweistext und Standarddauer.
- Ablaufzeit neuer passwortgeschützter HTTPS-Freigaben.
- Admin-only: maximale Uploadgröße im Reiter **Dokumente & Medien** (Standard 512 MiB, zulässig 1–4096 MiB). Sie begrenzt die gesamte HTTP-Anfrage und damit auch Uploadpfade, die dieses zentrale Limit verwenden; S3-Uploads können zusätzlich enger begrenzt sein.
- Admin-only: Vorschau- und Archiv-OCR-Einstellungen im Reiter **Dokumente & Medien**.

Die Sprachwahl in der Navigationsleiste gilt sofort für die aktuelle Sitzung. Die Standardsprache gilt für neue Sitzungen. Die Navigation und die neue Einstellungsseite sind auf Deutsch und Englisch verfügbar; weitere Fachseiten verwenden die bestehende deutsche Fachterminologie und können schrittweise in Sprachschlüssel überführt werden.

Die Seite ändert absichtlich weder Berechtigungen, Aufbewahrungsfristen noch bestehende Dokumente. Neue Uploads übernehmen dagegen die konfigurierten Standard-Tags und den Standardzustand.

Das Uploadlimit wird in den Anwendungseinstellungen revisionsgesichert gespeichert und beim Start des Webprozesses geladen. Eine Änderung oder Rücksetzung wird nach einem Neustart wirksam. Die bisherige Umgebungsvariable `SIMPLEOFFICE_MAX_UPLOAD_MIB` dient als Migrationswert, solange noch kein Uploadlimit gespeichert wurde.
