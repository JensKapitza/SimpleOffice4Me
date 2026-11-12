"""Dated project context; never presented as a live issue/security inventory."""
from .repository_workspace_github import REPOSITORY_URL

CONTEXT_DATE = "2026-11-09"
CONTEXT_SHA = "9085c06ed3a98fbf9cc2bc966d109d04741ceec3"
DOCS = {
    "Dokumente & Import": "DOKUMENTVORSCHAU_INDEXDIENST.md",
    "CRM & Kontakte": "KONTAKTE.md",
    "Kalender & Aufgaben": "KALENDER.md",
    "Mail & IMAP": "IMAP_SIEVE_EMAIL_ARCHIV.md",
    "WebDAV & SFTP": "WEBDAV_DATEIVERWALTUNG.md",
    "Federation & Netzwerk": "FEDERATION_DISCOVERY_TRUST.md",
    "Banking & Finanzen": "V3_FINANCE.md",
    "Mobile & Capture": "ANDROID.md",
    "Bibliothek & Reader": "DIGITALES_BUECHERREGAL.md",
    "Mini Services": "MINI_SERVICES_ACCEPTANCE.md",
    "Security": "RELEASE_SECURITY_CHECKLIST.md",
    "Release & Qualität": "V3_RELEASE_GATE.md",
    "Runtime & Installation": "PYTHON_RUNTIME_SUPPORT.md",
    "Desktop": "ERSTSTART_UND_DESKTOP_SETUP.md",
}

CHECKLIST = [
    {"id": "scope", "title": "Umfang und Stand festhalten",
     "detail": "Issue/PR, aktuellen Commit, Plattform und erwartetes Verhalten erfassen."},
    {"id": "evidence", "title": "Quellen und Aktualität prüfen",
     "detail": "Abrufdatum, Originalquelle und belegte Fakten von Annahmen trennen."},
    {"id": "security", "title": "Security- und Review-Funde einordnen",
     "detail": "Berechtigungen, offene Rückfragen und verworfene/historische Funde unterscheiden."},
    {"id": "ci", "title": "CI am aktuellen Commit prüfen",
     "detail": "Fehlgeschlagen, abgebrochen, übersprungen und nicht ausgeführt getrennt bewerten."},
    {"id": "reproduce", "title": "Reproduktion und nächste Tests planen",
     "detail": "Konkrete Schritte, Testdaten und erwartetes Ergebnis dokumentieren; nichts ausführen."},
    {"id": "docs", "title": "Dokumentation ergänzen",
     "detail": "Konfiguration, Betrieb, Abnahmebeleg und gegebenenfalls Rückrollweg beschreiben."},
    {"id": "deduplicate", "title": "Offene Punkte mit bestehenden Issues abgleichen",
     "detail": "Vor einem Arbeitsauftrag Duplikate und bereits erledigte Punkte ausschließen."},
    {"id": "decision", "title": "Entscheidungsgrundlage festhalten",
     "detail": "Bekannte Blocker und fehlende Nachweise dokumentieren; keine automatische Freigabe."},
]


def documentation_gaps() -> list[dict]:
    """Planning examples grounded in dated sources, requiring current validation."""
    examples = [
        ("release-docs", "Release-Gate mit Python-Runtime-Policy abgleichen", "Release & Qualität",
         "Im geprüften Dokumentstand weichen ältere Release-Angaben von der Runtime-Policy ab.",
         ["Aktuellen Dokumentstand und bestehende Tracker #506/#474 vergleichen.",
          "Nur verbliebene Abweichungen als konkreten Dokumentationsauftrag formulieren."],
         ["docs/V3_RELEASE_GATE.md", "docs/PYTHON_RUNTIME_SUPPORT.md"],
         [("Release-Gate (geprüfter Commit)", f"{REPOSITORY_URL}/blob/{CONTEXT_SHA}/docs/V3_RELEASE_GATE.md"),
          ("Bestehender Tracker #474", f"{REPOSITORY_URL}/issues/474")]),
        ("report-repro", "Report-Anforderung reproduzierbar beschreiben", "Dokumente & Import",
         "Der damalige Tracker #759 benennt noch nicht eindeutig Reportmodul, Format und Reproduktion.",
         ["Aktuellen Stand von #759 lesen und Beispielausgabe anfordern.",
          "Abnahmekriterien im vorhandenen Tracker planen; keine pauschale Aussage zu allen Reports."],
         ["Reporttyp, Eingabedaten, Format und erwartete Ausgabe"],
         [("Bestehender Tracker #759", f"{REPOSITORY_URL}/issues/759")]),
        ("webdav-risk", "WebDAV-Restannahme und Nachweisbedarf festhalten", "Security",
         "Im historischen Security-Tracker wurde eine lokale Symlink-Race-Annahme diskutiert; "
         "ein ausnutzbarer Angriff ist damit nicht nachgewiesen.",
         ["Aktuellen Tracker #874 und zugehörige Funde lesen.",
          "Angreifermodell, Voraussetzungen und fehlenden Reproduktionsnachweis dokumentieren."],
         ["Bedrohungsmodell und begründete Einstufung; keine historischen Alertzahlen als Live-Inventur"],
         [("Security-Tracker #874", f"{REPOSITORY_URL}/issues/874")]),
        ("device-acceptance", "Geräteabnahme für Capture, Import und Druck bündeln", "Mobile & Capture",
         "Build- und Unit-Test-Ergebnisse belegen keine erfolgreiche Prüfung am konkreten Endgerät.",
         ["Bestehenden Tracker #823 und verknüpfte Geräteaufgaben abgleichen.",
          "Gerät, OS, Version, Schritte, Ergebnis und Belege in einer wiederverwendbaren Abnahme erfassen."],
         ["Geräte-Abnahmematrix mit noch ungeprüften Kombinationen"],
         [("Bestehender Tracker #823", f"{REPOSITORY_URL}/issues/823")]),
    ]
    return [{"id": ident, "title": title, "area": area, "cause": cause, "next_steps": steps,
             "documentation": docs, "evidence": [{"label": label, "url": url} for label, url in refs],
             "assessment": f"Datiertes Planungsbeispiel vom {CONTEXT_DATE}. Aktueller Handlungsbedarf ungeprüft; "
                           "vor einem neuen Issue vorhandenen Tracker abgleichen."}
            for ident, title, area, cause, steps, docs, refs in examples] + release_documentation_gaps()


PLATFORM_CHECKLISTS = {
    "Windows": [
        ("setup", "Eigene venv: setup → run", "Git Bash/MSYS, leere und vorhandene venv, Pfade mit Leerzeichen; echten HTTP-Start und Shutdown prüfen."),
        ("package", "Paketierte App und Installer", "Echte EXE/ASAR mit Backend starten; NSIS-Installation, Portable-Selbstentpackung, Neustart und Prozess-/Portreste getrennt prüfen."),
        ("devices", "Druck und Bildschirmfreigabe", "Native Drucker, Benutzertrennung, Offline-Fall sowie Viewer lokal/über Proxy auf zweitem Gerät prüfen."),
    ],
    "Linux": [
        ("setup", "venv, Installation und Start", "Unterstütztes CPython, setup → run, Health-/HTTP-Antwort, Shutdown und Wiederstart ohne Datenverlust."),
        ("package", "AppImage / DEB auf Zielsystem", "Paket tatsächlich starten; Ubuntu-Host, Desktop und Browser-Kompatibilität getrennt protokollieren."),
        ("devices", "CUPS, Proxy und Bildschirm", "Echten System-/Brother-Druck, Rechte, Offline-Fall, Proxy-WebSocket und tatsächlichen Medienpfad prüfen."),
    ],
    "macOS": [
        ("setup", "Eigene venv und Start", "setup → run, Python/Architektur, Leerzeichen im Pfad; optionale native Pakete gesondert prüfen."),
        ("package", "Paketierter Start und Beenden", "App auf Zielarchitektur starten, Backend/HTTP, Berechtigungen und sauberen Shutdown protokollieren."),
        ("devices", "Native Geräte und Freigaben", "Systemdruck, Bildschirmberechtigung, zweiten Viewer sowie lokale/Proxy-URL prüfen."),
    ],
    "Android": [
        ("setup", "APK auf echtem ARM64-Gerät", "ABI, OS, App-/Python-/Wheel-Version und Commit erfassen; APK-Build ist kein Startnachweis."),
        ("capture", "Import und Berechtigungen", "Kamera/Dateien/Ordner, große Mengen, Abbruch/Retry, Permission-Verweigerung und Netzwechsel prüfen."),
        ("devices", "Druck und Bildschirm-Capture", "Native Print-Framework-Anbindung soweit vorhanden, Brother-Gerät, MediaProjection sowie sichtbaren/stoppbaren Betrieb prüfen."),
    ],
}


def platform_checklists() -> dict[str, list[dict]]:
    """Personal markers only; no checklist check grants platform acceptance."""
    common = [
        ("documents", "PDF / ZUGFeRD und Stammdaten", "Freitext/strukturierte Adresse, Legacy/visuelle Vorlage, Mehrseiten, Konto-Snapshot, PDF/XML-Validierung prüfen."),
        ("settings", "Sprache und Server-Einstellungen", "Apply, Reload, Anmeldung und Neustart; gespeicherte Werte, tatsächliche Bindung und Zugriff-URLs vergleichen."),
        ("evidence", "Abnahmeprotokoll vervollständigen", "Commit, Gerät/OS/Version, Testdaten, Schritte, Soll/Ist, Ergebnis und Log/Screenshot ohne Secrets festhalten."),
    ]
    return {platform: [{"id": "platform-" + platform.lower() + "-" + ident,
                       "title": title, "detail": detail}
                      for ident, title, detail in rows + common]
            for platform, rows in PLATFORM_CHECKLISTS.items()}


def release_documentation_gaps() -> list[dict]:
    """User-reported work scopes dated 2026-11-11, not a live defect inventory."""
    rows = [
        ("startup", "Startskript / venv: vollständiges Setup und echter Start", "Runtime & Installation", ["Windows", "Linux", "macOS"],
         "Früherer Prüfauftrag zu setup/run und eigener venv. Aktueller PR-/Abnahmestand ungeprüft.",
         ["#997 und heutigen main-Stand abgleichen.", "Leere venv einrichten, mit derselben venv starten, HTTP/Interpreter und sauberes Beenden dokumentieren."], 997, "pull"),
        ("windows-package", "Windows-Paketierung und Laufzeit-Smoke abgleichen", "Desktop", ["Windows"],
         "Paketinhalt, win-unpacked-Smoke und Installer sind verschiedene Abnahmeschritte; aktueller Ergebnisstand ungeprüft.",
         ["#995/#996 und aktuelle Laufzeit-Artefakte abgleichen.", "EXE/Backend, NSIS und Portable-Selbstentpackung getrennt prüfen."], 996, "pull"),
        ("dependencies", "Abhängigkeiten und generierte Lockdateien abgleichen", "Runtime & Installation", [],
         "Historischer Auftrag zu watchdog-/pip-Konsistenz. Kein Beleg eines heute noch offenen Fehlers.",
         ["#998 und aktuelle Manifest-/Lockdateien vergleichen.", "Installation, Resolver und Audit am aktuellen Commit prüfen."], 998, "pull"),
        ("proxy", "Proxy-Weiterleitung end-to-end prüfen", "Proxy-Weiterleitung", [],
         "Gemeldete Weiterleitungslücke; technische Ursache und heutige Betroffenheit ungeprüft.",
         ["Lokale und Proxy-Route mit TLS/Auth vergleichen.", "Redirect-Ziel, Backend-Neustart und WebSocket-Upgrade protokollieren."], 995, "pull"),
        ("printers", "Drucker: Geräte- und Benutzerabnahme dokumentieren", "Drucker", [],
         "Frühere Geräteabnahme offen gemeldet; aktueller Stand der vorhandenen Tracker ungeprüft.",
         ["#859/#823 und vorhandene Ergebnisse prüfen.", "Echten System-/Brother-Druck, persistente Auswahl, Admin-Regel, Benutzertrennung und Offline-Fall testen."], 859, "issues"),
        ("invoice", "PDF / ZUGFeRD: Freitext und Vorlagenfallback prüfen", "PDF / ZUGFeRD", [],
         "Gemeldete Freitext-/Vorlagenprobleme; gemeinsame Ursache und aktueller Fehlerstand nicht belegt.",
         ["#1004 und zugehörige PRs am aktuellen Head abgleichen.", "PDF-only-Fallback, XML/PDF-Validierung und unveränderlichen Konto-/Adress-Snapshot prüfen."], 1004, "issues"),
        ("language", "Sprache nach Apply und Neustart prüfen", "Sprache", [],
         "Nutzermeldung zu Apply/Persistenz; Reproduktion am aktuellen Commit fehlt.",
         ["Vor neuem Issue vorhandene Tracker durchsuchen.", "Sprache ändern; Reload, Anmeldung und Neustart mit gespeichertem Wert vergleichen."], None, "issues"),
        ("server", "Server-Einstellungen und 127.0.0.1 einordnen", "Server-Einstellungen", [],
         "Gemeldeter Neustart-/Bindungsfehler; Loopback ist kontextabhängig, nicht grundsätzlich falsch.",
         ["Vorhandene Tracker prüfen und Bind-/Proxy-Konfiguration festhalten.", "Werte speichern, neu starten und tatsächlichen Listener sowie lokale/LAN-URLs vergleichen."], None, "issues"),
        ("screen", "Bildschirmfreigabe lokal und über Proxy abnehmen", "Bildschirmfreigabe", [],
         "Gemeldete Viewer-URL-/Freigabelücke; aktueller Medienpfad und Reproduktion ungeprüft.",
         ["Vorhandene Bildschirm-/Support-Tracker auf Duplikate prüfen.", "Zweites Gerät, lokale/Proxy-URL, Autorisierung, sichtbares Bild sowie Stop/Neustart prüfen."], None, "issues"),
    ]
    return [{"id": "release-" + ident, "title": title, "area": area, "platforms": platforms,
             "cause": cause, "status": "missing_evidence", "next_steps": steps,
             "tests": "Ungeprüft; keine historischen Testzahlen als aktuellen Nachweis übernehmen.",
             "documentation": ["Commit, Plattform/Version, Reproduktion, Soll/Ist, Logs, bekannte Grenzen und vorhandene Tracker"],
             "evidence": [{"label": "Vorhandenen Tracker abgleichen", "url": f"{REPOSITORY_URL}/{kind}/{number}"}]
                         if number else [{"label": "Vorhandene Issues durchsuchen", "url": REPOSITORY_URL + "/issues"}],
             "assessment": "Nutzermeldung / ungeprüfter Planungsauftrag vom 2026-11-11. Nicht erneut geprüft; weder bestätigter aktueller Fehler noch neu angelegtes Issue."}
            for ident, title, area, platforms, cause, steps, number, kind in rows]
