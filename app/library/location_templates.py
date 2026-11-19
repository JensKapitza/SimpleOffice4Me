"""Reusable nested storage-location templates (Issue #1111, P1).

Templates describe shelf, cabinet, box and drawer structures once so whole
subtrees can be created with a single action. Instantiation reuses
LibraryStore.create_location, keeping codes, events and ObjectStore links.
"""
from __future__ import annotations

from typing import Any

TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "template_id": "shelf",
        "name": "Regal mit Fächern",
        "description": "Regal mit nummerierten Fächern (z. B. Regal 2, Fach 1–4).",
        "child_prefix": "Fach",
        "default_count": 4,
        "max_count": 12,
    },
    {
        "template_id": "cabinet",
        "name": "Schrank mit Böden",
        "description": "Schrank mit nummerierten Böden.",
        "child_prefix": "Boden",
        "default_count": 3,
        "max_count": 10,
    },
    {
        "template_id": "moving_box",
        "name": "Umzugskarton mit Fächern",
        "description": "Karton mit Innenschachteln (Fach A, B, C …).",
        "child_prefix": "Schachtel",
        "default_count": 3,
        "max_count": 12,
    },
    {
        "template_id": "drawer_unit",
        "name": "Kommode mit Schubladen",
        "description": "Kommode mit nummerierten Schubladen.",
        "child_prefix": "Schublade",
        "default_count": 4,
        "max_count": 12,
    },
)


def template_catalog() -> list[dict[str, Any]]:
    """Return the JSON-safe public template catalog."""
    return [dict(entry) for entry in TEMPLATES]


def get_template(template_id: str) -> dict[str, Any]:
    wanted = str(template_id or "").strip()
    for entry in TEMPLATES:
        if entry["template_id"] == wanted:
            return dict(entry)
    raise ValueError("Unbekannte Standortvorlage")


def instantiate(
    store: Any,
    template_id: str,
    name: str,
    actor: str,
    *,
    parent_id: str = "",
    count: Any = None,
) -> dict[str, Any]:
    """Create a root location with numbered children below an optional parent."""
    template = get_template(template_id)
    clean_name = " ".join(str(name or "").split()).strip()
    if not clean_name:
        raise ValueError("Name für den neuen Standort fehlt")
    maximum = int(template["max_count"])
    if count in (None, ""):
        wanted = int(template["default_count"])
    else:
        try:
            wanted = int(str(count).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("Anzahl muss eine ganze Zahl sein") from exc
    if not 1 <= wanted <= maximum:
        raise ValueError(f"Anzahl muss zwischen 1 und {maximum} liegen")
    clean_parent = str(parent_id or "").strip()
    if clean_parent:
        store.location(clean_parent)
    root = store.create_location(clean_name, actor, parent_id=clean_parent)
    children = [
        store.create_location(
            f"{template['child_prefix']} {number}", actor, parent_id=root["location_id"],
        )
        for number in range(1, wanted + 1)
    ]
    return {"template": template, "root": root, "children": children}
