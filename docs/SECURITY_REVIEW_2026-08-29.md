# Sicherheitsreview vom 29.08.2026

## Umfang

Geprüft wurden die sicherheitsrelevante Flask-Grundkonfiguration,
Cookie-Sitzungen, lokale und Google-Anmeldung, öffentliche Kontoerstellung,
DAV-/MCP-Abgrenzung, Geheimnisspeicherung, zustandsändernde HTTP-Endpunkte,
externe Prozesse, Downloads, CI-Abhängigkeiten, SBOM und CRA-Nachweise.

Die Prüfung kombiniert Quelltextsuche, gezielte Regressionstests, den
vorhandenen `pip-audit`-CI-Check und die manuelle Bewertung von Datenflüssen.
Sie ist kein Penetrationstest und keine CRA-Konformitätsbewertung.

Der isolierte Neuaufbau aus `pyproject.toml` meldete am 29.08.2026 mit
`python -m pip_audit --local` keine bekannte Schwachstelle in den installierten
Drittanbieter-Abhängigkeiten. Das lokale Projekt `simpleoffice4me` selbst ist
nicht auf PyPI gelistet und wurde erwartungsgemäß vom Abhängigkeitsdienst
übersprungen; dafür gelten Quelltextprüfung und Anwendungstests.

## Behobene Befunde

| Priorität | Befund | Abhilfe | Test/Nachweis |
| --- | --- | --- | --- |
| kritisch | Cookie-authentifizierte Schreibzugriffe ohne CSRF-Nachweis | Synchronizer-Token für POST/PUT/PATCH/DELETE; automatische Formular-/Fetch-Integration; Protokollzugänge explizit getrennt | `test_browser_mutations_require_session_csrf_token` |
| hoch | Unbegrenzte lokale Login-Versuche und abweichende Prüfung unbekannter Nutzer | persistente Konto-/IP- und Netz-Drosselung; Dummy-Hash-Prüfung | `test_login_throttle_is_persistent_and_identifier_is_hashed` |
| hoch | Öffentliche Registrierung blieb nach der Ersteinrichtung offen | nur erstes Konto standardmäßig öffentlich; weitere Konten über Administration, bewusster Opt-in möglich | `test_public_registration_is_closed_after_bootstrap_by_default` |
| hoch | Google-OAuth legte unbekannte Konten automatisch an | Autoprovisionierung nach Ersteinrichtung standardmäßig deaktiviert | Konfigurationsnachweis in `tools/cra_check.py` |
| hoch | Google-Zugriffs- und Refresh-Tokens im Klartext in SQLite | AES-GCM mit zweckgetrennt aus Installationskennung abgeleitetem Schlüssel; Migration bei nächster Anmeldung | `test_local_registration_and_google_registration` |
| mittel | Zustandsändernder Logout per GET | Produktiv nur POST mit CSRF | Routentest über globale CSRF-Abdeckung |
| mittel | Unvollständige Browser-Sicherheitsheader | HSTS für HTTPS sowie COOP/CORP ergänzt | `test_security_headers_are_present` |
| mittel | Statische SBOM-Seriennummer und nicht im CI geprüfter Nachweis | bestandsabhängige UUID; CRA-/SBOM-Schritte im Dependency-Audit | `test_sbom_contains_application_dependencies` |

## Weiter offen und vor Release zu entscheiden

- Die Content-Security-Policy benötigt wegen vorhandener Inline-Skripte noch
  `unsafe-inline`. Eine nonce-basierte Umstellung ist ein eigener, größerer
  Frontend-Umbau und bleibt als Härtungsaufgabe offen.
- Der vollständige Schutz gespeicherter Geschäftsdaten hängt vom verschlüsselten
  Datenträger/Backup der Zielumgebung ab. Die Anwendung verschlüsselt gezielt
  Zugangsdaten, ersetzt aber keine Volume-Verschlüsselung.
- Betriebssystempakete und externe Werkzeuge wie Ghostscript, LibreOffice,
  ClamAV, osmium und Java-Validatoren sind nicht Bestandteil des Python-SBOM.
  Sie müssen je Release separat inventarisiert und geprüft werden.
- Herstelleridentität, Produktklassifizierung, Supportzeitraum, Risikoanalyse,
  EU-Konformitätserklärung, CE-Prozess und CRA-Meldeorganisation sind vor einer
  Konformitätsbehauptung verbindlich festzulegen.
- Ein externer Penetrationstest für die öffentlich erreichbare Zielkonfiguration
  bleibt vor kommerziellem Marktbereitstellen empfohlen.
