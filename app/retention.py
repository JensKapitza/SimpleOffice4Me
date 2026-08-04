"""Explainable, non-destructive document deadline evaluation."""

from __future__ import annotations

import calendar
import fnmatch
from datetime import datetime, timezone
from typing import Any, Iterable


DEADLINE_KINDS = {"retention", "work"}


def parse_deadline(value: str) -> datetime:
    """Parse an ISO date or datetime and normalize it to UTC."""
    text = str(value).strip()
    if not text:
        raise ValueError("deadline date is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("deadline must be an ISO date or datetime") from exc
    if len(text) == 10:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def add_years(value: datetime, years: int) -> datetime:
    """Add calendar years while keeping leap-day rules deterministic."""
    if not 1 <= years <= 100:
        raise ValueError("deadline years must be between 1 and 100")
    day = min(value.day, calendar.monthrange(value.year + years, value.month)[1])
    return value.replace(year=value.year + years, day=day)


def normalize_rule(
    rule: dict[str, Any],
    *,
    source_type: str,
    source: str,
    document: dict[str, Any],
) -> dict[str, Any] | None:
    """Turn one document/folder/tag rule into an explainable deadline."""
    kind = str(rule.get("kind", "retention")).strip().casefold()
    if kind not in DEADLINE_KINDS:
        raise ValueError(f"unknown deadline kind: {kind}")

    pattern = str(rule.get("tag", "")).strip()
    matched_tag = ""
    if pattern:
        matched_tag = next(
            (
                tag for tag in document.get("tags", [])
                if fnmatch.fnmatchcase(str(tag).casefold(), pattern.casefold())
            ),
            "",
        )
        if not matched_tag:
            return None

    if rule.get("expires_at"):
        expires_at = parse_deadline(str(rule["expires_at"]))
        starts_at = None
    elif rule.get("years") is not None:
        years = int(rule["years"])
        if matched_tag:
            start_text = str(document.get("tagged_at", {}).get(matched_tag, ""))
        else:
            start_field = str(rule.get("start", "first_seen_at"))
            start_text = str(document.get(start_field, ""))
        if not start_text:
            return {
                "id": str(rule.get("id", "")),
                "kind": kind,
                "label": str(rule.get("label", "Frist")),
                "source_type": source_type,
                "source": source,
                "tag": matched_tag,
                "error": "Fristbeginn fehlt",
            }
        starts_at = parse_deadline(start_text)
        expires_at = add_years(starts_at, years)
    else:
        raise ValueError("deadline rule needs expires_at or years")

    return {
        "id": str(rule.get("id", "")),
        "kind": kind,
        "label": str(rule.get("label", "Frist")).strip() or "Frist",
        "source_type": source_type,
        "source": source,
        "tag": matched_tag,
        "starts_at": starts_at.isoformat() if starts_at else "",
        "expires_at": expires_at.isoformat(),
    }


def evaluate_deadlines(
    document: dict[str, Any],
    rules: Iterable[tuple[dict[str, Any], str, str]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate all applicable rules without changing document metadata."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    findings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for rule, source_type, source in rules:
        try:
            finding = normalize_rule(
                rule,
                source_type=source_type,
                source=source,
                document=document,
            )
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "id": str(rule.get("id", "")),
                    "source_type": source_type,
                    "source": source,
                    "error": str(exc),
                }
            )
            continue
        if finding is None:
            continue
        if finding.get("error"):
            errors.append(finding)
            continue
        finding["expired"] = parse_deadline(finding["expires_at"]) < current
        findings.append(finding)

    retention = [item for item in findings if item["kind"] == "retention"]
    work = [item for item in findings if item["kind"] == "work"]
    retention_until = max((parse_deadline(item["expires_at"]) for item in retention), default=None)
    work_until = min((parse_deadline(item["expires_at"]) for item in work), default=None)
    work_locked = any(item["expired"] for item in work)
    return {
        "evaluated_at": current.isoformat(),
        "findings": sorted(findings, key=lambda item: (item["expires_at"], item["kind"])),
        "errors": errors,
        "retention_until": retention_until.isoformat() if retention_until else None,
        "work_until": work_until.isoformat() if work_until else None,
        "work_locked": work_locked,
        "has_retention_deadline": bool(retention),
        "all_retention_expired": bool(retention) and all(item["expired"] for item in retention),
    }
