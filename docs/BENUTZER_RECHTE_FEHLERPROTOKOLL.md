# Benutzerrechte und datensparsame Fehlerprotokolle

## Zweck

Die Administration kann Konten sperren, mehrere Administratoren bestimmen,
Funktionsgruppen freigeben und technische Fehler über eine Vorgangs-ID
untersuchen. Der erste vorhandene beziehungsweise neu registrierte Benutzer
wird bei der additiven Einrichtung Administrator. Eine Herabstufung oder
Sperrung des letzten aktiven Administrators ist nicht möglich.

## Rechte und Sperren

Steuerbar sind Dokumente und Suche, Kalender, Kontakte, Mail, WebDAV-
Einstellungen, Synchronisation sowie Projekte und Zeiten. Administratoren
haben stets Vollzugriff. Bestehende Konten behalten zunächst ihre bisherigen
Funktionszugriffe; ein Administrator kann daraus explizite Sperren machen.
Jede Änderung erhöht die Authentifizierungsversion und beendet damit alle
vorhandenen Sitzungen des betroffenen Kontos. Eine Kontosperre verhindert auch
eine neue lokale oder Google-Anmeldung.

Die Funktionsschalter schützen die Weboberfläche. Protokolle wie WebDAV,
CalDAV, CardDAV, SFTP und SSH verwenden zusätzlich ihre eigenen getrennten
App-Passwörter, Schlüssel und Ordnerrechte. Diese Zugangsdaten müssen dort
separat widerrufen werden; eine stillschweigende Rechteausweitung findet nicht
statt.

## Fehler- und Auditansicht

Unter **Administration – Fehler und Audit** sieht ein Administrator Zeitpunkt,
zufällige Vorgangs-ID, Exception-Klasse, Endpunkt, HTTP-Methode, Pfad, einen
stabilen Fingerprint und ausschließlich Dateiname/Funktion/Zeilennummer der
letzten Programmstellen. Query-Strings, Formulardaten, Request-Bodies, vollständige Stacktraces,
Cookies, Passwörter und Tokens werden nicht in der Datenbank gespeichert. Die
Vorgangs-ID wird dem Benutzer bei HTTP 500 angezeigt und als
`X-Request-ID` ausgeliefert. Fehler lassen sich als bearbeitet markieren.

Rollen-, Sperr- und Rechteänderungen sowie Anmeldeergebnisse werden getrennt
auditiert. Das rotierende Betriebslog besitzt zusätzlich einen Filter für
Authorization-Header, Cookies, Passwörter, Tokens, OAuth-Codes, API-Schlüssel
und URL-Geheimnisse. Eine vollständige automatische Erkennung beliebiger
fachlicher Geheimnisse ist unmöglich; deshalb loggt der zentrale Fehlerpfad
grundsätzlich weder Exception-Nachrichten noch Anfragedaten.

## CRA, Standards und Designentscheidungen

Der [Cyber Resilience Act, Verordnung (EU) 2024/2847](https://eur-lex.europa.eu/eli/reg/2024/2847/oj/deu),
insbesondere Artikel 13, Anhang I Teil I und Anhang VII, verlangt risikobasierte
Sicherheitsmaßnahmen, Schutz vor unbefugtem Zugriff, begrenzte Auswirkungen
von Sicherheitsvorfällen sowie technische Dokumentation. Die Umsetzung
unterstützt diese Ziele durch Least Privilege, sperrbare Konten,
Sitzungswiderruf und nachvollziehbare Sicherheitsereignisse. Sie allein stellt
keine vollständige CRA-Konformitätsbewertung oder CE-Dokumentation dar.

Die Logverwaltung folgt den betrieblichen Grundsätzen aus
[NIST SP 800-92](https://csrc.nist.gov/pubs/sp/800/92/final). Die Liste bewusst
nicht protokollierter Geheimnisse orientiert sich ergänzend am
[OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html#data-to-exclude).

## Migration, Ausfall und Rückkehr

Die Migration ist ausschließlich additiv. Nutzer, Dokumente und bestehende
Rechte werden nicht gelöscht. Kann die Fehlerdatenbank selbst nicht schreiben,
bleibt die ursprüngliche Fehlerantwort verfügbar; die Diagnose erzeugt keinen
zweiten Ausfall. Zur Deaktivierung kann der Admin-Blueprint entfernt werden.
Die zusätzlichen Tabellen und Spalten können ungenutzt bestehen bleiben.

## Tests und Grenzen

Tests decken Erstadministrator, Mehradministrator-Schutz, Selbstsperrschutz,
Kontosperre, Sitzungswiderruf, Funktionssperren, Admin-Bypass, unbefugte
Logzugriffe, Auditierung, Fehlerkorrelation und Secret-Redaktion ab. Noch nicht
enthalten sind MFA, Gruppenrollen, zeitlich begrenzte Rechte, SIEM-Export und
eine automatisierte Aufbewahrungsfrist für Logs; diese Entscheidungen müssen
an das konkrete Betriebskonzept angepasst werden.
