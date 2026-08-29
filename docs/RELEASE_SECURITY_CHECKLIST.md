# Sicherheits-Checkliste vor Release

- [ ] Versionsnummer, Hersteller-/Kontaktangabe und Supportzeitraum dokumentiert.
- [ ] `python -m unittest discover -s tests -v` erfolgreich.
- [ ] `python tools/cra_check.py` erfolgreich.
- [ ] SBOM mit `python tools/generate_sbom.py` erzeugt und dem Release beigefügt.
- [ ] `python -m pip_audit` ohne unbehandelte kritische Befunde oder mit dokumentierter Risikoentscheidung.
- [ ] Änderungen an Authentifizierung, Freigaben, Import, Backup, externen Schnittstellen und Datenmigration geprüft.
- [ ] Sicherheitsrelevante Änderungen, offene Risiken und Upgrade-Hinweise im Release-Nachweis festgehalten.
- [ ] Öffentliche Registrierung und Google-Autoprovisionierung bleiben deaktiviert oder die begründete Ausnahme ist dokumentiert.
- [ ] HTTPS, Proxy-Vertrauensgrenze, HSTS und widerrufbare DAV-/MCP-Zugänge in der Zielumgebung geprüft.
- [ ] Admin-Inventar aktualisiert; fehlende oder veraltete externe Programme und Betriebssystempakete bewertet.
- [ ] Private Sicherheitsmeldungen erreichen eine dauerhaft überwachte Herstellerstelle; CRA-Meldeverantwortliche sind benannt.
- [ ] Falls CRA anwendbar: technische Akte, Risikoanalyse und EU-Konformitätsprozess durch verantwortliche Stelle geprüft.
