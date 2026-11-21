# Sicherheit in SimpleOffice4Me

Für vertrauliche Meldungen, unterstützte Versionen und Betreibermaßnahmen gilt der [Sicherheits- und Schwachstellenprozess](docs/SECURITY.md). Die folgenden Schutzziele gelten projektweit für Implementierung, Review und Scan. Die Arbeitsweise für Agenten steht in [AGENTS.md](AGENTS.md), die Vorgaben für den Offline-Scanner im [Scannerprofil](.oss-scanner/threat_model.md).

## Schutzgüter und Vertrauensgrenzen

Schutzgüter sind Dokumente und Anhänge, Zugangsdaten und Sitzungen, Finanzdaten, Personal-, Kontakt-, Mail- und Kalenderdaten sowie die Befugnis, auf dem Host Dateien zu verändern oder Dienste und Prozesse zu starten. Erreichbarkeit hängt von Startmodus, LAN-Proxy und aktivierten Diensten ab. Ein lokaler Browser, ein angemeldeter Benutzer und ein bekannter Peer können jeweils unvertrauenswürdige Eingaben liefern.

| Übergang | Was beim Implementieren und Prüfen gelten muss |
| --- | --- |
| Browser → Flask → Dokument oder Verwaltungsaktion | Identität und Objekt-, Rollen- oder Funktionsrecht serverseitig vor dem Lesen oder Ändern prüfen. Zustandswechsel mit Browseranmeldung brauchen CSRF-Schutz. Auch Direktdownload, Vorschau, Export, Cache und wiederverwendete Links müssen Benutzerwechsel und Rechteentzug berücksichtigen. |
| DAV, MCP, S3 und Föderation → lokaler Bestand | Protokollkennung, Geltungsbereich und Widerruf prüfen. Die Identität eines Peers belegt keine Freigabe für ein bestimmtes Objekt. Bei wiederholbaren Schreibvorgängen müssen Wiederholung und Nebenläufigkeit die fachliche Integrität wahren. |
| Upload, Mail, Archiv, PDF, Bild und OCR → Parser, Dateisystem und Browser | Dateiziele unter dem erlaubten Stamm auflösen. Symlinks, Archivpfade, Typwechsel, aktive Inhalte und Änderungen zwischen Prüfung und Nutzung berücksichtigen. Größe, entpackte Datenmenge, Laufzeit und Parallelität begrenzen. Fehler müssen einen konsistenten Zustand hinterlassen. |
| Externe URL, OAuth, Mail, Banking und Speicher → Netzwerk und interne Daten | Ziel, Redirects, TLS und Vertrauenskontext prüfen. Externe Antworten als Eingabe behandeln. Sitzungen, Token und fachliche Inhalte vor Offenlegung in Logs, URLs und Fehlermeldungen schützen. Für Finanzaktionen auch Zuordnung, Autorisierung und Doppelverarbeitung prüfen. |
| Desktop, LAN-Proxy, lokale Dienste und Werkzeuge → Host | Bind-Adresse, tatsächlichen Aufrufer und erforderliche Rechte bestimmen. Prozessargumente, Konfiguration, Proxyheader und Dienststeuerung dürfen keine zusätzliche Hostbefugnis eröffnen. |

Datenbank-, Template-, Shell- und Deserialisierungsgrenzen sind dort zu prüfen, wo Daten ihre Bedeutung wechseln. Für einen Chat-Anhang betrifft dies beispielsweise die Chatnachricht, die Dokumentreferenz und den späteren Download. Ein Positivtest für das Senden deckt einen Rechteentzug vor dem Download nicht ab.

## Befunde, Priorität und Nachweis

Für jeden Befund festhalten, wer welchen Einstieg erreicht, welches Schutzgut betroffen ist, welche Prüfung greift oder fehlt und welche Auswirkung unter welchen Voraussetzungen folgt. Der Scanner verwendet die bestehende [Schweregradeinteilung](.oss-scanner/threat_model.md). Unklare Reichweite, unbekannte Konfiguration und fehlende Tests bleiben als offene Fragen erkennbar. Die Annahme eines lokalen Betriebs, ein grüner Scannerlauf oder die Größe des Repositories begründen keine Ausnahme.

Die vollständige zeilenweise Prüfung, die Nachweisführung je Revision und ein erster geprüfter Ausschnitt sind im [Scanleitfaden](docs/SICHERHEITSPRUEFUNG.md) beschrieben.
