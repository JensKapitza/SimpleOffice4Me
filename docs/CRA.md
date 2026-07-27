# Cyber Resilience Act (CRA) – technische Akte

Stand: 26.07.2026. Diese Datei ist eine technische Arbeitsgrundlage und keine rechtliche Konformitätserklärung.

## Rechtsgrundlage und Geltung

- [Verordnung (EU) 2024/2847 – EUR-Lex](https://eur-lex.europa.eu/eli/reg/2024/2847/oj)
- [EU-Kommission: Cyber Resilience Act](https://digital-strategy.ec.europa.eu/en/policies/cyber-resilience-act)
- [EU-Kommission: CRA-Meldepflichten](https://digital-strategy.ec.europa.eu/en/policies/cra-reporting)

SimpleOffice4Me ist eine selbst gehostete Software mit digitalen Elementen. Ob sie im Einzelfall als Produkt auf dem Unionsmarkt bereitgestellt wird und welche Herstellerrolle gilt, ist vor Vertrieb rechtlich zu bewerten. Die volle Anwendung des CRA beginnt am 11.12.2027; die Meldepflichten nach Art. 14 gelten ab 11.09.2026.

## Anforderungen und Umsetzung

| CRA-Thema | Umsetzung im Projekt | Nachweis / offener Punkt |
| --- | --- | --- |
| Sichere Konfiguration und Zugriffsschutz (Anhang I Teil I) | Login, Passwort-Hashing, Google-OAuth mit State-Prüfung, sichere Cookie-Attribute, Security-Header einschließlich CSP | `app/auth.py`, `app/__init__.py`; CSRF-Schutz und Rate-Limits vor öffentlichem Betrieb ergänzen |
| Schutz von Vertraulichkeit und Integrität | Dateihashes, Integritätsstatus, revisionssichere Ereignisse, kontrollierte Freigaben und keine automatische Löschung nach Spiegelung | `app/document_store.py`, `app/revision_history.py`, `app/replication_store.py` |
| Schwachstellenbehandlung (Anhang I Teil II) | Sicherheitskontakt, Triage, Behebung, Advisory und Meldeprozess festgelegt | `docs/SECURITY.md`; verantwortliche Herstellerstelle und Erreichbarkeit bei Release eintragen |
| Komponenten-/Abhängigkeitsübersicht | CycloneDX-SBOM wird aus der installierten Python-Umgebung erstellt | `tools/generate_sbom.py`, Ausgabe `artifacts/sbom.cdx.json` |
| Sicherheitsupdates und bekannte Schwachstellen | CI führt `pip-audit` aus; Pull Requests und Releases erhalten einen Sicherheitscheck | `.github/workflows/security.yml`, `docs/RELEASE_SECURITY_CHECKLIST.md` |
| Technische Dokumentation und Risikobewertung (Anhang VII) | Dieses Mapping, Architektur-/Betriebsdokumente, Tests und SBOM sind versioniert | Vor formaler Konformitätsbewertung Risikoanalyse, Supportzeitraum, Produktversion und EU-Konformitätserklärung ergänzen |
| Incident-/Vulnerability-Reporting, Art. 14 | Fristen dokumentiert: 24 h Frühwarnung, 72 h Meldung, Abschlussbericht gemäß Vorfallart | Tatsächliche Meldung über die CRA Single Reporting Platform erfolgt organisatorisch, nicht automatisiert durch diese Anwendung |

## Wiederholbare Prüfungen

```bash
python -m pip install -e '.[security]'
python tools/cra_check.py
python tools/generate_sbom.py
python -m pip_audit
python -m unittest discover -s tests -v
```

Die SBOM gehört zum jeweiligen Release-Artefakt. Abhängigkeitswarnungen werden vor Release bewertet, dokumentiert und entweder behoben oder mit Risiko, Entscheidung und Termin begründet.

Am 26.07.2026 wurde nach einem `pip-audit`-Befund die Bildbibliothek von Pillow 11.x auf mindestens 12.3 angehoben. Befunde des isolierten Prüfwerkzeugs (`pip`) gehören zur Build-Umgebung und werden dort separat aktualisiert.

## Nicht automatisch erfüllbar

CE-Kennzeichnung, EU-Konformitätserklärung, Hersteller-/Bevollmächtigtenangaben, Klassifizierung als wichtiges oder kritisches Produkt, Marktüberwachungskommunikation und die Meldung an die CRA-Plattform sind organisatorische bzw. rechtliche Aufgaben. Sie dürfen nicht allein aus einem erfolgreichen CI-Lauf abgeleitet werden.
