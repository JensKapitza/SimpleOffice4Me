"""Private mixed-intake state on existing capture inbox records."""

from __future__ import annotations

from typing import Any
from unicodedata import normalize

from .inventory_catalog import edition_field_key, normalize_catalog_code, proven_edition_ids

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
    changed = {key for key in changes if updated.get(key, "") != previous.get(key, "")}
    if not changed:
        return
    state["manual"] = updated
    for key in changed:
        state["selected"].pop(key, None)
    state["revision"] += 1
    # Recommendations are scoped to a specific identity/edition or variant.
    # Preserve the separate photo evidence but make the next lookup explicit.
    if changed & {"title", "barcode", "kind", "publisher", "published_date", "edition", "variant", "size", "unit"}:
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



_EDITION_OBSERVATIONS = {"title", "publisher", "published_date", "edition"}


def catalog_view(state: dict) -> dict:
    """Reconcile current evidence without discarding either source or trusting arrival order."""
    catalog = state["catalog"]
    if catalog.get("status") != "confirmed" or catalog.get("stale"):
        return catalog
    proven = proven_edition_ids(catalog)
    try:
        bound = normalize_catalog_code(state["manual"].get("barcode"))
        matches = bound.kind == "book" and bound.value == catalog.get("identifier")
    except ValueError:
        matches = False
    if not proven or not matches:
        return {**catalog, "status": "review", "reason": "edition_proof_refresh", "recommended": None}
    if state["ocr"].get("status") == "completed":
        rows = [row for row in catalog["candidates"] if row["candidate_id"] in proven]
        for observation in state["ocr"].get("fields", []):
            field = observation.get("field")
            if field not in _EDITION_OBSERVATIONS or observation.get("unlabelled"):
                continue
            observed = edition_field_key(field, observation.get("value"))
            known = {edition_field_key(field, row.get(field)) for row in rows} - {""}
            if observed and known and observed not in known:
                return {**catalog, "status": "review", "reason": "labelled_ocr_differs", "recommended": None}
    return catalog


def _compatible_edition_ids(ids: set[str], proven: frozenset[str]) -> bool:
    return len(ids) < 2 or ids <= proven


def compatible_selection(state: dict) -> bool:
    proven = proven_edition_ids(catalog_view(state))
    rows = [row for row in state["selected"].values() if row.get("candidate_id")]
    if any(row["kind"] == "local_ai" and row["candidate_id"] not in proven for row in rows):
        return False
    return _compatible_edition_ids({row["candidate_id"] for row in rows}, proven)


def current_proposal(state: dict) -> dict:
    proposal = state["proposal"]
    if not proposal:
        return proposal
    proven = proven_edition_ids(catalog_view(state))
    return {**proposal, "fields": [row for row in proposal.get("fields", [])
                                  if not row.get("candidate_id") or row["candidate_id"] in proven]}

def status(state: dict, *, has_conflict: bool = False) -> str:
    if state["confirmed"]:
        return "confirmed"
    catalog = catalog_view(state)
    if has_conflict or catalog.get("stale") or catalog.get("status") in {"review", "unavailable", "not_found"}:
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
    rows.extend(current_proposal(state).get("fields", []))
    return rows


def selectable_fields(state: dict) -> list[dict]:
    """Do not combine distinct public editions or local AI proposals by field."""
    rows = evidence_rows(state)
    selected = state["selected"]
    proven = proven_edition_ids(catalog_view(state))
    values: dict[str, set[str]] = {}
    for row in rows:
        # A guessed OCR title and a derivative AI proposal are not new observations.
        if row.get("unlabelled") or row["kind"] == "local_ai":
            continue
        field = row["field"]
        value = " ".join(normalize("NFC", row["value"]).casefold().split())
        if field == "published_date":
            value = edition_field_key(field, row["value"]) or value
        values.setdefault(field, set()).add(value)
    conflicts = {field for field, options in values.items() if len(options) > 1}
    return [
        {**row, "conflict": row["field"] in conflicts,
         "selectable": not row.get("candidate_id") or _compatible_edition_ids({
             row["candidate_id"],
             *(value["candidate_id"] for field, value in selected.items()
               if field != row["field"] and value.get("candidate_id")),
         }, proven)}
        for row in rows
    ]


def verified_book_suggestions(state: dict, evidence: list[dict]) -> list[dict]:
    """Fill only empty fields from one independently verified edition."""
    if state["confirmed"] or state["manual"].get("kind") == "object" or any(row["conflict"] for row in evidence):
        return []
    proven = proven_edition_ids(catalog_view(state))
    result = {}
    for row in evidence:
        field = row["field"]
        if (field in _EDITION_OBSERVATIONS and row["kind"] == "external_catalog"
                and row.get("candidate_id") in proven and row["selectable"]
                and not state["manual"].get(field) and field not in state["selected"]):
            # Leave the actual provider and source ID on each field, even when only
            # one provider supplied that field for the corroborated edition.
            result.setdefault(field, row)
    return list(result.values())


def choose_verified_book_suggestions(state: dict) -> None:
    rows = verified_book_suggestions(state, selectable_fields(state))
    if not rows:
        raise ValueError("Kein passender Buchvorschlag für die leeren Felder.")
    values = manual_fields({row["field"]: row["value"] for row in rows})
    for row in rows:
        state["selected"][row["field"]] = {**row, "value": values[row["field"]]}
    state["revision"] += 1


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
