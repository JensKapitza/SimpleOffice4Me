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
            for ident, title, area, cause, steps, docs, refs in examples]
