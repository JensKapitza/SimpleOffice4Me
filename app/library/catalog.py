"""Additive work, edition and physical-copy identities for library books."""
from __future__ import annotations

import uuid
from typing import Any

_IDENTITY_NAMESPACE = uuid.UUID("710b02d6-a5cf-4a52-a8df-569d03535c66")
_PHYSICAL_FIELDS = {
    "barcode",
    "inventory_number",
    "nfc_id",
    "purchase_date",
    "library_copy_id",
    "library_copy_source_id",
    "library_location_code",
    "library_location_id",
    "serial_number",
}


def _is_book(item: dict[str, Any]) -> bool:
    tags = {str(tag).strip().casefold() for tag in item.get("tags", [])}
    return str(item.get("type", "")).strip().casefold() in {"book", "buch"} or bool(tags & {"buch", "isbn"})


def _identity(fields: dict[str, Any], name: str, fallback: str) -> str:
    value = str(fields.get(name, "")).strip()
    if value:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError(f"Ungültige Bibliotheksidentität: {name}") from exc
    return str(uuid.uuid5(_IDENTITY_NAMESPACE, fallback))


def _update_values(item: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name", ""),
        "type": item.get("type", "book"),
        "status": item.get("status", "active"),
        "description": item.get("description", ""),
        "identifier": item.get("identifier", ""),
        "location": item.get("location", ""),
        "expires_at": item.get("expires_at", ""),
        "tags": item.get("tags", []),
        "fields": fields,
    }


def ensure_book_identity(objects: Any, item: dict[str, Any], actor: str) -> dict[str, Any]:
    """Persist stable identities without replacing existing bibliographic data."""
    if not _is_book(item):
        raise ValueError("Nur Bücher können als physische Exemplare vervielfältigt werden.")
    fields = dict(item.get("fields", {})) if isinstance(item.get("fields"), dict) else {}
    object_id = str(item.get("object_id", "")).strip()
    work_id = _identity(fields, "library_work_id", f"work:{object_id}")
    edition_id = _identity(fields, "library_edition_id", f"edition:{work_id}")
    copy_id = _identity(fields, "library_copy_id", f"copy:{object_id}")
    expected = {
        "library_work_id": work_id,
        "library_edition_id": edition_id,
        "library_copy_id": copy_id,
    }
    if all(str(fields.get(key, "")) == value for key, value in expected.items()):
        return item
    fields.update(expected)
    return objects.update(object_id, _update_values(item, fields), actor)


def edition_copies(objects: Any, source_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    """Return physical books explicitly linked to the source edition."""
    try:
        source = objects.object(source_id)
    except ValueError as exc:
        raise ValueError("Das Ausgangsbuch wurde nicht gefunden.") from exc
    if not _is_book(source):
        raise ValueError("Nur Bücher besitzen physische Exemplare.")

    fields = source.get("fields", {}) if isinstance(source.get("fields"), dict) else {}
    edition_value = str(fields.get("library_edition_id", "")).strip()
    if not edition_value:
        return [source]
    try:
        edition_id = str(uuid.UUID(edition_value))
    except ValueError as exc:
        raise ValueError("Ungültige Bibliotheksidentität: library_edition_id") from exc

    maximum = max(1, min(int(limit), 500))
    copies: list[dict[str, Any]] = []
    for item in objects.objects():
        if not _is_book(item):
            continue
        item_fields = item.get("fields", {}) if isinstance(item.get("fields"), dict) else {}
        candidate = str(item_fields.get("library_edition_id", "")).strip()
        try:
            matches = bool(candidate) and str(uuid.UUID(candidate)) == edition_id
        except ValueError:
            matches = False
        if matches:
            copies.append(item)
            if len(copies) > maximum:
                raise ValueError("Zu viele Exemplare für eine einzelne Ausgabe.")

    copies.sort(key=lambda item: (str(item.get("display_id", "")), str(item.get("object_id", ""))))
    return copies


def edition_copy_inventory(objects: Any, source_id: str) -> dict[str, Any]:
    """Build the bounded public projection for one edition's copies."""
    rows = edition_copies(objects, source_id)
    source = next(row for row in rows if row["object_id"] == source_id)
    fields = source.get("fields", {}) if isinstance(source.get("fields"), dict) else {}
    return {
        "ok": True,
        "work_id": str(fields.get("library_work_id", "")),
        "edition_id": str(fields.get("library_edition_id", "")),
        "count": len(rows),
        "copies": [
            {
                "object_id": row["object_id"],
                "display_id": row.get("display_id", ""),
                "copy_id": str(
                    row.get("fields", {}).get("library_copy_id", "")
                    if isinstance(row.get("fields"), dict) else ""
                ),
                "name": row.get("name", ""),
                "identifier": row.get("identifier", ""),
                "location": row.get("location", ""),
            }
            for row in rows
        ],
    }


def create_physical_copy(objects: Any, source_id: str, actor: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create one inventory copy that shares work and edition metadata."""
    try:
        source = objects.object(source_id)
    except ValueError as exc:
        raise ValueError("Das Ausgangsbuch wurde nicht gefunden.") from exc
    source = ensure_book_identity(objects, source, actor)
    source_fields = dict(source.get("fields", {}))
    copy_fields = {key: value for key, value in source_fields.items() if key not in _PHYSICAL_FIELDS}
    copy_fields.update({
        "library_work_id": source_fields["library_work_id"],
        "library_edition_id": source_fields["library_edition_id"],
        "library_copy_id": str(uuid.uuid4()),
        "library_copy_source_id": source["object_id"],
    })
    created = objects.create({
        "name": source["name"],
        "type": source["type"],
        "status": "active",
        "description": source.get("description", ""),
        "identifier": source.get("identifier", ""),
        "location": "",
        "tags": source.get("tags", []),
        "fields": copy_fields,
    }, actor)
    return source, created
