# Sicherheits- und Schwachstellenprozess

## Vertrauliche Meldung

Schwachstellen bitte über [GitHub Private Vulnerability Reporting](https://github.com/JensKapitza/SimpleOffice4Me/security/advisories/new) melden. Reproduzierbare Angriffsdaten, Zugangsdaten und personenbezogene Daten gehören nicht in öffentliche Issues. Der Betreiber dokumentiert Eingang, betroffene Versionen, Risiko, Abhilfe und Veröffentlichung in einem vertraulichen Vorgang. Vor einem Release ist zusätzlich eine dauerhaft überwachte Hersteller-Kontaktadresse festzulegen.

## Bearbeitung

1. Eingang zeitlich erfassen und betroffene Versionen ermitteln.
2. Risiko, Ausnutzbarkeit und Daten-/Zugriffsauswirkung bewerten.
3. Schutzmaßnahme oder Update entwickeln, testen und als Sicherheitsupdate versionieren.
4. Betroffene Nutzer mit Anleitung und Versionsbezug informieren.
5. Ereignis, Entscheidung und Korrektur im Revisions-/Release-Nachweis festhalten.

## CRA-Fristen bei Herstellerpflicht

Bei aktiv ausgenutzten Schwachstellen und schweren Sicherheitsvorfällen gelten ab 11.09.2026 die CRA-Meldepflichten: Frühwarnung innerhalb von 24 Stunden, Meldung innerhalb von 72 Stunden; Abschlussberichte richten sich nach Art. 14. Die offizielle Einordnung und Meldung erfolgt über die CRA Single Reporting Platform bzw. die zuständige CSIRT/ENISA-Struktur.

## Unterstützte Versionen

Der konkrete Supportzeitraum wird pro Release festgelegt. Sicherheitsupdates müssen mindestens für die erklärte Supportdauer bereitgestellt werden. Ein Release ohne dokumentierten Supportzeitraum darf nicht als CRA-fertig freigegeben werden.

## Sichere Standardkonfiguration

- Nach dem ersten Konto ist die öffentliche Registrierung geschlossen. Eine bewusste Ausnahme ist nur mit `SIMPLEOFFICE_ALLOW_PUBLIC_REGISTRATION=1` möglich.
- Neue Google-Konten werden nicht automatisch angelegt. Eine bewusste Ausnahme ist nur mit `SIMPLEOFFICE_GOOGLE_AUTO_PROVISION=1` möglich.
- Produktiver Zugriff erfolgt über HTTPS; `SIMPLEOFFICE_TRUSTED_PROXY_HOPS` wird nur auf die tatsächliche Zahl eigener Reverse-Proxies gesetzt.
- Browseränderungen benötigen ein sitzungsgebundenes CSRF-Token. DAV und MCP verwenden stattdessen getrennte, widerrufbare Protokoll-Zugangsdaten.
- OAuth-Zugriffs- und Refresh-Tokens werden authentifiziert verschlüsselt gespeichert. Vorhandene Klartextwerte werden beim nächsten erfolgreichen Google-Login ersetzt.
