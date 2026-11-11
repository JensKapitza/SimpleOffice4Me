"""Conservative, source-linked planning assessments; no repository transitions."""
from __future__ import annotations

from .repository_workspace_context import DOCS
from .repository_workspace_github import REPOSITORY_URL

_FAILURES = {"failure", "timed_out", "action_required", "startup_failure"}
_BAD_STATUSES = {"error", "failure"}
_AREA_WORDS = [
    ("Security", ("security", "cve-", "vulnerab", "sicherheit", "codeql", "dependabot")),
    ("Bildschirmfreigabe", ("screen share", "screen-share", "bildschirm", "viewer-url")),
    ("Server-Einstellungen", ("server settings", "server-einstellungen", "127.0.0.1")),
    ("Sprache", ("language", "sprache", "übersetzung")),
    ("Drucker", ("drucker", "printer", "etikett")),
    ("PDF / ZUGFeRD", ("zugferd", "xrechnung", "invoice", "rechnung", "template", "vorlage")),
    ("Proxy-Weiterleitung", ("proxy", "weiterleitung")),
    ("Desktop", ("electron", "desktop", "pwsh")),
    ("Runtime & Installation", ("python", "venv", "start.sh", "dependency", "dependencies", "pip", "installation")),
    ("Release & Qualität", ("ci", "workflow", "test", "release", "build", "actions")),
    ("WebDAV & SFTP", ("webdav", "sftp", "symlink")),
    ("Mobile & Capture", ("android", "mobile", "capture", "camera", "kamera", "scan", "druck", "printer")),
    ("CRM & Kontakte", ("kontakt", "contact", "carddav", "crm")),
    ("Kalender & Aufgaben", ("calendar", "kalender", "caldav", "vtodo", "aufgabe")),
    ("Mail & IMAP", ("imap", "smtp", "mail", "sieve")),
    ("Federation & Netzwerk", ("federation", "proxy", "network", "netzwerk", "s3")),
    ("Banking & Finanzen", ("banking", "finance", "rechnung", "buchhaltung")),
    ("Bibliothek & Reader", ("epub", "reader", "bücher", "bibliothek")),
    ("Mini Services", ("litellm", "mini service", "mcp")),
    ("Dokumente & Import", ("pdf", "ocr", "dokument", "document", "import", "report")),
]


def text(value, limit: int = 500) -> str:
    return str(value or "")[:limit] if isinstance(value, (str, int)) else ""


def area_for(raw: dict) -> str:
    labels = " ".join(text(label.get("name")) for label in raw.get("labels", []) if isinstance(label, dict))
    haystack = (text(raw.get("title")) + " " + labels).casefold()
    for area, words in _AREA_WORDS:
        if any(word in haystack for word in words):
            return area
    return "Projekt & Allgemein"


def platforms_for(raw: dict) -> list[str]:
    """Title/label hints are an explicit, unverified scope, never device evidence."""
    labels = " ".join(text(row.get("name")) for row in raw.get("labels", []) if isinstance(row, dict))
    words = (text(raw.get("title")) + " " + labels).casefold()
    hints = {"Windows": ("windows", "win32", "win-unpacked", "powershell", "pwsh", "nsis"),
             "Linux": ("linux", "ubuntu", "debian", "appimage", "cups"),
             "macOS": ("macos", "mac os", "darwin"), "Android": ("android", "chaquopy", "apk")}
    return [platform for platform, names in hints.items() if any(name in words for name in names)]


def merge_preparation(item: dict) -> dict:
    """Separate possible preparation from approval; never infer merge readiness."""
    if item.get("type") != "pr":
        return {"state": "not_applicable", "summary": "Kein Pull Request; zugehörige Implementierung separat prüfen."}
    if item.get("status") == "done":
        return {"state": "done", "summary": "Bereits geschlossen; Merge-Beleg und integrierten Stand separat abgleichen."}
    if item.get("status") == "blocked":
        return {"state": "no", "summary": "Derzeit nein: fehlgeschlagene/abgebrochene Prüfungen oder aktuelle Review-Blocker klären."}
    if item.get("status") == "missing_evidence":
        return {"state": "unknown", "summary": "Noch nicht beurteilbar: aktuelle Prüf-/Review-Nachweise fehlen. Keine Merge-Freigabe."}
    if item.get("draft"):
        return {"state": "conditional", "summary": "Prüfunterlagen vorbereitbar; Draft, vollständige Implementierung und technische Freigabe bleiben offen."}
    return {"state": "conditional", "summary": "Bedingt vorbereitbar; Diff, Pflichtchecks, Branch-Regeln und Reviews noch manuell abgleichen. Keine Merge-Freigabe."}


def add_planning_fields(item: dict) -> dict:
    """Also upgrades cached snapshots without retaining a parallel record store."""
    if item.get("type") == "pr" and item.get("status") in {"open", "review"} and item.get("test_status", {}).get("state") == "unknown":
        item["status"] = "missing_evidence"
    item.setdefault("platforms", platforms_for(item))
    item.setdefault("platform_assessment", "Nur Titel-/Label-Hinweise, keine bestätigte Betroffenheit. Fehlende Zuordnung bleibt ungeprüft.")
    item["merge_preparation"] = merge_preparation(item)
    return item


def _priority(raw: dict, default: str = "P2") -> str:
    labels = {text(label.get("name")).casefold() for label in raw.get("labels", []) if isinstance(label, dict)}
    for priority in ("P0", "P1", "P2", "P3"):
        if labels & {priority.casefold(), "priority: " + priority.casefold(), "priority:" + priority.casefold()}:
            return priority
    return default


def _base(raw: dict, kind: str) -> dict:
    number = raw.get("number", raw.get("id", 0))
    number = number if isinstance(number, int) and number > 0 else 0
    area = area_for(raw)
    url = f"{REPOSITORY_URL}/{'pull' if kind == 'pr' else 'issues'}/{number}"
    return {
        "id": f"{kind}-{number}", "type": kind, "number": number,
        "title": text(raw.get("title"), 240) or "Ohne Titel", "url": url, "area": area,
        "priority": _priority(raw), "status": "open",
        "platforms": platforms_for(raw),
        "platform_assessment": "Aus Titel/Labels abgeleiteter Prüfumfang, keine Geräteabnahme; Betroffenheit ungeprüft.",
        "cause": "Ursache nicht verifiziert; Originalbeschreibung und Reproduktion prüfen.",
        "blockers": [], "next_steps": ["Originalquelle und aktuellen Stand prüfen.",
                                       "Reproduktions- und Abnahmekriterien dokumentieren."],
        "test_status": {"state": "unknown", "summary": "Kein geprüfter Testnachweis für diesen Eintrag."},
        "documentation": ["Ursache, Reproduktion, erwartetes Ergebnis und Abnahmebeleg"],
        "evidence": [{"label": "GitHub-Original", "url": url}],
        "assessment": "Priorität und Bereich sind Planungseinstufungen, sofern kein P0–P3-Label vorliegt. "
                      "Keine Repository-Labels oder Status geändert.",
        "updated_at": text(raw.get("updated_at"), 40),
    }


def latest_runs(runs: list[dict]) -> list[dict]:
    grouped = {}
    for run in runs:
        sha = text(run.get("head_sha"), 40)
        key = (run.get("workflow_id", run.get("name")), sha, run.get("event"))
        order = (text(run.get("created_at"), 40), int(run.get("id") or 0), int(run.get("run_attempt") or 1))
        if key not in grouped or order > grouped[key][0]:
            grouped[key] = (order, run)
    return [row[1] for row in grouped.values()]


def _test_status(checks: list[dict], runs: list[dict], statuses: list[dict], *, complete: bool) -> dict:
    conclusions = [c.get("conclusion") for c in checks] + [r.get("conclusion") for r in runs]
    states = [s.get("state") for s in statuses]
    suffix = " Vollständigkeit ungeprüft." if not complete else " Pflichtcheck-Regeln nicht geprüft."
    if any(c in _FAILURES for c in conclusions) or any(s in _BAD_STATUSES for s in states):
        return {"state": "failed", "summary": "Fehlgeschlagene Checks/Läufe am aktuellen PR-Commit." + suffix}
    if "cancelled" in conclusions:
        return {"state": "cancelled", "summary": "Mindestens ein Lauf abgebrochen; kein bestandener Testnachweis." + suffix}
    if any(c.get("status") != "completed" for c in checks + runs) or "pending" in states:
        return {"state": "running", "summary": "Checks/Läufe am aktuellen PR-Commit noch nicht abgeschlossen." + suffix}
    if conclusions or states:
        if all(c in {"success", "neutral", "skipped"} for c in conclusions) and all(s == "success" for s in states):
            passed = conclusions.count("success") + states.count("success")
            if passed:
                return {"state": "success", "summary": "Beobachtete Checks/Läufe erfolgreich; übersprungene Checks "
                        "sind kein Testnachweis." + suffix}
    return {"state": "unknown", "summary": "Keine vollständigen Testnachweise am aktuellen PR-Commit." + suffix}


def pull_request(raw: dict, detail: dict, runs: list[dict]) -> tuple[dict, list[dict]]:
    item = _base(raw, "pr")
    sha = text((raw.get("head") or {}).get("sha"), 40)
    item["head_sha"] = sha
    item["draft"] = bool(raw.get("draft"))
    current_runs = latest_runs([r for r in runs if r.get("head_sha") == sha and r.get("event") == "pull_request"])
    checks = [c for c in detail.get("checks", []) if c.get("head_sha") == sha]
    statuses = detail.get("statuses", [])
    item["test_status"] = _test_status(checks, current_runs, statuses, complete=detail.get("complete", False))
    item["status"] = "open" if raw.get("draft") else "review"
    item["cause"] = "Änderungsvorschlag; fachliche Ursache und vollständige Akzeptanz sind nicht automatisch geprüft."
    item["next_steps"] = ["Diff und Akzeptanzkriterien am angegebenen Head-SHA prüfen.",
                          "Pflichtchecks, Branch-Regeln und offene Review-Threads manuell abgleichen.",
                          "Nächste Prüfung mit Ergebnis und Beleg dokumentieren."]
    item["documentation"] += ["Testumfang, noch ungeprüfte Plattformen und gegebenenfalls Rückrollweg"]
    item["blockers"] = ["Merge-Freigabe ungeprüft: Branch-Regeln und offene Review-Threads nicht vollständig erfasst."]
    if raw.get("draft"):
        item["blockers"].append("Pull Request ist als Entwurf markiert.")
    state = item["test_status"]["state"]
    if state in {"failed", "cancelled"}:
        item["status"] = "blocked"
        item["priority"] = _priority(raw, "P1" if state == "failed" else "P2")
        item["blockers"].append(item["test_status"]["summary"])
    reviews = {}
    for review in sorted(detail.get("reviews", []), key=lambda r: text(r.get("submitted_at"))):
        user = review.get("user") or {}
        if review.get("state") not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            continue
        reviews[user.get("id", user.get("login"))] = review
    findings = []
    for review in reviews.values():
        if review.get("state") != "CHANGES_REQUESTED":
            continue
        current = review.get("commit_id") == sha
        findings.append({**item, "id": f"review-{review.get('id')}", "type": "review", "status": "review",
                         "title": f"Review-Rückfrage zu PR #{item['number']}", "priority": "P1" if current else "P2",
                         "cause": text(review.get("body"), 600) or "Änderungen im Review angefordert.",
                         "assessment": "Review am aktuellen Commit." if current else
                         "Review bezieht sich auf einen anderen Commit; heutige Relevanz ungeprüft.",
                         "blockers": ["Review-Threads und Erledigungsstand manuell prüfen."],
                         "next_steps": ["Angeforderte Änderungen mit aktuellem Diff abgleichen.",
                                        "Erledigungsnachweis dokumentieren; kein Review automatisch beantworten."]})
        if (review.get("user") or {}).get("type") == "Bot":
            findings[-1]["assessment"] = "Automatisiertes Review. " + findings[-1]["assessment"]
        if current:
            item["status"] = "blocked"
            item["blockers"].append("Am aktuellen Commit wurden Änderungen im Review angefordert.")
    item["assessment"] += " Auch grüne Checks ergeben hier keine automatische Merge-Bereitschaft."
    if not detail.get("complete"):
        item["blockers"].append("CI-/Review-Abdeckung teilweise oder nicht abrufbar.")
    if state == "unknown" and item["status"] != "blocked":
        item["status"] = "missing_evidence"
    add_planning_fields(item)
    for finding in findings:
        add_planning_fields(finding)
    return item, findings


def issue(raw: dict) -> dict:
    item = _base(raw, "issue")
    if raw.get("state") == "closed":
        item["status"] = "done"
        reason = raw.get("state_reason")
        item["cause"] = "Auf GitHub geschlossen" + (" (nicht geplant)" if reason == "not_planned" else "") + "."
        item["assessment"] += " Geschlossen bedeutet weder behoben noch getestet."
    if item["status"] == "open" and any(word in item["title"].casefold() for word in ("abnahme", "nachweis", "release gate")):
        item["status"] = "missing_evidence"
    return add_planning_fields(item)


def completed(raw: dict) -> dict:
    item = issue(raw)
    if "pull_request" in raw:
        item["type"] = "pr"
        item["id"] = f"pr-{item['number']}"
        item["url"] = f"{REPOSITORY_URL}/pull/{item['number']}"
        merged = bool((raw.get("pull_request") or {}).get("merged_at"))
        item["cause"] = "Auf GitHub gemergt." if merged else "Pull Request geschlossen; Merge nicht belegt."
        item["evidence"] = [{"label": "GitHub-Original", "url": item["url"]}]
    return add_planning_fields(item)


def ci_findings(runs: list[dict], pulls: list[dict], main_sha: str) -> list[dict]:
    heads = {p.get("head", {}).get("sha") for p in pulls}
    result = []
    for run in latest_runs(runs):
        sha = run.get("head_sha")
        # Old-head runs and push/PR workflows are never mixed into current PR evidence.
        relevant = (sha in heads and run.get("event") == "pull_request") or (bool(main_sha) and sha == main_sha)
        if not relevant or run.get("conclusion") not in _FAILURES | {"cancelled"}:
            continue
        ident = run.get("id")
        item = _base({"number": ident, "title": text(run.get("name")), "updated_at": run.get("updated_at")}, "ci")
        item.update(id=f"ci-{ident}", url=f"{REPOSITORY_URL}/actions/runs/{ident}", status="blocked",
                    head_sha=text(sha, 40), area="Release & Qualität",
                    priority="P2" if run.get("conclusion") == "cancelled" else "P1")
        item["cause"] = "Abgebrochener Workflow; Ursache ungeprüft." if run.get("conclusion") == "cancelled" else \
                        "Fehlgeschlagener Workflow; technische Ursache ohne Logprüfung ungeklärt."
        item["test_status"] = {"state": "cancelled" if run.get("conclusion") == "cancelled" else "failed",
                               "summary": "GitHub meldet: " + text(run.get("conclusion"))}
        item["blockers"] = ["Kein erfolgreicher Abschluss dieses beobachteten Workflows am angegebenen Commit."]
        item["next_steps"] = ["Fehlgeschlagenen Job und ersten ursächlichen Logfehler lesen.",
                              "Infrastrukturfehler von tatsächlich ausgeführten Anwendungstests trennen.",
                              "Reproduktion und nächsten Prüfschritt dokumentieren; keinen Lauf neu starten."]
        item["documentation"] = ["Workflow, Job, Head-SHA, tatsächlicher Testumfang und Logbeleg"]
        item["evidence"] = [{"label": "Workflow auf GitHub", "url": item["url"]}]
        result.append(item)
    return result


def security_findings(alerts: list[dict], kind: str) -> list[dict]:
    result = []
    for alert in alerts:
        number = alert.get("number")
        if not isinstance(number, int) or alert.get("state") != "open":
            continue
        info = (alert.get("rule") or {}) if kind == "code-scanning" else (alert.get("security_advisory") or {})
        severity = info.get("security_severity_level", info.get("severity", "unknown"))
        title = info.get("description") if kind == "code-scanning" else info.get("summary")
        item = _base({"number": number, "title": title or f"{kind} #{number}"}, "security")
        item.update(id=f"security-{kind}-{number}", area="Security", status="open",
                    priority="P1" if severity in {"critical", "high", "error"} else "P2",
                    url=f"{REPOSITORY_URL}/security/{kind}/{number}")
        item["cause"] = f"GitHub-Fund ({kind}), gemeldeter Schweregrad: {text(severity, 40)}. Ausnutzbarkeit ungeprüft."
        item["next_steps"] = ["Originalfund, betroffenen Code/Paketstand und Voraussetzungen prüfen.",
                              "Fehlalarm, Abhilfe und verbleibendes Risiko mit Beleg dokumentieren."]
        item["documentation"] = ["Betroffenheit, Risiko, geplanter Test und begründete Einstufung"]
        item["assessment"] = "Berechtigungsgebundener GitHub-Fund; kein selbst durchgeführter Security-Test."
        item["evidence"] = [{"label": "Security-Fund auf GitHub", "url": item["url"]}]
        result.append(item)
    return result


def normalise(payload: dict) -> list[dict]:
    items = [issue(row) for row in payload.get("issues", []) if "pull_request" not in row]
    for raw in payload.get("pulls", []):
        item, reviews = pull_request(raw, payload.get("details", {}).get(raw.get("number"), {}), payload.get("runs", []))
        items.extend([item, *reviews])
    items.extend(ci_findings(payload.get("runs", []), payload.get("pulls", []), payload.get("main_sha", "")))
    current_ids = {item["id"] for item in items}
    items.extend(item for item in (completed(row) for row in payload.get("closed", [])[:10]) if item["id"] not in current_ids)
    for kind in ("code-scanning", "dependabot"):
        items.extend(security_findings(payload.get(kind, []), kind))
    for item in items:
        add_planning_fields(item)
        doc = DOCS.get(item["area"])
        if doc:
            item["documentation"].append("Projektbezug: docs/" + doc)
    return sorted(items, key=lambda item: (item["status"] == "done", item["priority"], item["type"], -item["number"]))
