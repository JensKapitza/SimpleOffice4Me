"""Library shelf handoff for adopted capture batches (#658c).

Assign the user-chosen existing shelf to a newly adopted book, or
report the current placement without changing it. Shelf failures
never break the adoption itself: the book survives and a retry with
the same capture token completes the placement.
"""

from __future__ import annotations

from typing import Any


SHELF_ASSIGN_ERROR = (
    "Regalzuordnung fehlgeschlagen. Das Buch ist gespeichert; "
    "bitte erneut versuchen."
)


def _shelf_dict(library: Any, location_id: str, code: str = "") -> dict[str, str]:
    """Public shelf view; never raises, unknown ids yield empty path."""
    clean_id = str(location_id or "").strip()
    path = ""
    try:
        path = library.location_path(clean_id)
    except (OSError, ValueError):
        path = ""
    return {
        "location_id": clean_id,
        "code": str(code or ""),
        "path": str(path or ""),
    }


def _ensure_adopt_shelf(
    object_id: str, location_id: str, actor: str, root: str
) -> tuple[dict[str, str] | None, str]:
    """Assign the requested shelf unless already set; report current otherwise.

    Returns (shelf, error). An empty location_id never changes the
    placement and only reports the current one. Requesting the
    already-assigned shelf writes nothing. Unexpected store problems
    become a fixed German hint ("" when none) so the adopted book
    always survives a shelf failure; a retry completes it.
    """
    from .library.routes import assign_object_to_location
    from .library.store import LibraryStore

    library = LibraryStore(root)
    try:
        current = library.assignment(object_id) or {}
    except (OSError, ValueError):
        current = {}
    wanted = str(location_id or "").strip()
    if not wanted:
        if not isinstance(current, dict) or not current.get("location_id"):
            return None, ""
        return _shelf_dict(
            library, str(current.get("location_id", "")),
            str(current.get("location_code", "") or "")), ""
    if isinstance(current, dict) and current.get("location_id") == wanted:
        return _shelf_dict(
            library, wanted, str(current.get("location_code", "") or "")), ""
    try:
        result = assign_object_to_location(
            object_id, wanted, actor, root=root)
    except (OSError, ValueError, RuntimeError, KeyError):
        return None, SHELF_ASSIGN_ERROR
    placed = result.get("location", {}) if isinstance(result, dict) else {}
    return _shelf_dict(
        library, wanted,
        str(placed.get("code", "") or "") if isinstance(placed, dict) else ""), ""


def default_shelf_for_actor(root: str, actor: str) -> str:
    """Location id of the actor's active/paused scan session, else "".

    Only returned when the location still exists in LibraryStore.
    Missing sessions, finished sessions, unknown actors, deleted
    locations and store errors all yield "" so the caller falls back
    to no preselection. Actor-scoped by the session store owner key;
    never leaks another user's session.
    """
    from .library.scan_sessions import ScanSessionStore
    from .library.store import LibraryStore

    try:
        session = ScanSessionStore(root).get(actor)
    except (OSError, ValueError):
        return ""
    if not isinstance(session, dict):
        return ""
    if str(session.get("status", "")) not in {"active", "paused"}:
        return ""
    location_id = str(session.get("location_id", "") or "").strip()
    if not location_id:
        return ""
    try:
        LibraryStore(root).location(location_id)
    except (OSError, ValueError):
        return ""
    return location_id


def _shelf_notices(
    shelf: dict[str, str] | None, shelf_error: str, requested: bool
) -> list[str]:
    """One-line shelf outcome for API consumers; UI uses shelf/shelf_error."""
    if shelf_error:
        return [shelf_error]
    if shelf:
        label = shelf.get("code") or shelf.get("path") or "-"
        verb = "zugewiesen" if requested else "bereits zugewiesen"
        return [f"Regal {label} {verb}."]
    return ["Ohne Regal übernommen."]
