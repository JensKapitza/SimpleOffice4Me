"""Private mixed-intake state on existing capture inbox records."""

from __future__ import annotations

from typing import Any

FIELD_LIMITS = {
    "title": 300, "kind": 16, "barcode": 120, "manufacturer": 240,
    "size": 80, "unit": 30, "variant": 160, "publisher": 300, "published_date": 80,
    "edition": 160, "location_id": 80,
}
MAX_DRAFTS = 60


class IntakeRevisionConflict(ValueError):
    """A newer manual edit or a confirmation precludes this action."""


def manual_fields(data: Any) -> dict[str, str]:
    if not isinstance(data, dict):
        raise ValueError("Ungültige Entwurfsdaten.")
    fields = {}
    for key, value in data.items():
        if key not in FIELD_LIMITS or not isinstance(value, str):
            raise ValueError("Ungültiges Entwurfsfeld.")
        value = " ".join(value.split())
        if len(value) > FIELD_LIMITS[key] or any(ord(c) < 32 for c in value):
            raise ValueError("Der eingegebene Wert ist zu lang oder ungültig.")
        if key == "kind" and value not in {"", "book", "object"}:
            raise ValueError("Bitte Buch oder Gegenstand auswählen.")
        fields[key] = value
    return fields


def new_draft(sequence: int, manual: dict[str, str]) -> dict[str, Any]:
    return {
        "sequence": sequence, "revision": 1, "manual": manual_fields(manual),
        "ocr": {}, "catalog": {}, "proposal": {}, "selected": {}, "confirmed": {},
    }


def edit_draft(state: dict, changes: dict[str, str]) -> None:
    previous = state["manual"]
    updated = {**previous, **manual_fields(changes)}
    if updated == previous:
        return
    state["manual"] = updated
    for key in changes:
        if updated.get(key, "") != previous.get(key, ""):
            state["selected"].pop(key, None)
    state["revision"] += 1
    # A provider recommendation was scoped to the observed title/identifier.
    # Preserve the separate photo evidence but make the next lookup explicit.
    if any(updated.get(k) != previous.get(k) for k in ("title", "barcode", "kind")):
        if state["catalog"]:
            state["catalog"]["stale"] = True
        state["selected"] = {
            key: value for key, value in state["selected"].items()
            if value["kind"] not in {"external_catalog", "local_ai"}
        }
    for key in list(state["selected"]):
        if state["selected"][key]["kind"] == "local_ai":
            del state["selected"][key]
    state["proposal"] = {}


def status(state: dict) -> str:
    if state["confirmed"]:
        return "confirmed"
    if state["catalog"].get("stale") or state["catalog"].get("status") in {"review", "unavailable", "not_found"}:
        return "question"
    if state["ocr"].get("status") in {"failed", "unavailable", "empty"}:
        return "question"
    return "in_review"


SUGGESTIBLE = {"title", "manufacturer", "size", "unit", "variant", "publisher", "published_date", "edition"}


def evidence_rows(state: dict) -> list[dict]:
    rows = []
    for key, value in state["manual"].items():
        if key in SUGGESTIBLE and value:
            rows.append({"field": key, "value": value, "source_id": f"manual:{key}",
                         "kind": "manual", "label": "Manuell"})
    for item in [] if state["catalog"].get("stale") else state["catalog"].get("candidates", []):
        for key, source in item["field_evidence"].items():
            if key in SUGGESTIBLE:
                rows.append({**source, "value": item[key]})
    rows.extend(state["ocr"].get("fields", []))
    rows.extend(state["proposal"].get("fields", []))
    return rows


def selectable_fields(state: dict) -> list[dict]:
    """Do not combine distinct public editions or local AI proposals by field."""
    rows = evidence_rows(state)
    chosen = {value["candidate_id"] for value in state["selected"].values()
              if value.get("candidate_id")}
    return [
        {**row, "conflict": len({item["value"].casefold() for item in rows
                                if item["field"] == row["field"]}) > 1,
         "selectable": not chosen or not row.get("candidate_id")
                       or row["candidate_id"] in chosen}
        for row in rows
    ]


def select_evidence(state: dict, source_id: str) -> None:
    row = next((row for row in selectable_fields(state)
                if row["source_id"] == source_id and row["selectable"]), None)
    if row is None:
        raise ValueError("Diese Quelle gehört nicht zum aktuellen Entwurf.")
    if row["kind"] == "manual":
        state["selected"].pop(row["field"], None)
    else:
        clean = manual_fields({row["field"]: row["value"]})
        state["selected"][row["field"]] = {**row, "value": clean[row["field"]]}
    state["revision"] += 1


def intake_storage_supported(root) -> bool:
    """Do not add plaintext mixed-draft media to an encrypted deployment."""
    from .v2.cutover import load_cutover_state

    try:
        return load_cutover_state(root).mode == "v1"
    except (OSError, ValueError):
        return False
