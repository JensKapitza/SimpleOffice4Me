"""Exact local barcode suggestions; records and locations are owner scoped."""
from uuid import UUID

from .inventory_catalog import normalize_catalog_code


def match_key(value):
    try:
        code = normalize_catalog_code(value)
    except ValueError:
        return None
    return code.kind, code.value if code.kind == "book" else code.value.zfill(14)


def _codes(item):
    values = item["fields"]
    return {key for value in (
        item["identifier"], *(values.get(name) for name in ("barcode", "isbn", "ean", "upc", "gtin")),
    ) if (key := match_key(value))}


def _stable_copy_id(item):
    try:
        return str(UUID(item["fields"].get("library_copy_id", "")))
    except ValueError:
        return item["object_id"]


def matching_owned_copies(objects, library, actor, keys, *, object_id=None):
    """Scan at most once regardless of how many active drafts share codes."""
    rows = {key: [] for key in keys}
    if not rows:
        return rows
    picked = []
    for item in objects.iter_owned_inventory(actor, object_id=object_id):
        codes = _codes(item).intersection(rows)
        if codes:
            picked.append((item, codes))
    paths = library.owned_assignment_paths(actor, {item["object_id"] for item, _ in picked}) if picked else {}
    for item, codes in sorted(picked, key=lambda row: (row[0]["sequence_id"], row[0]["object_id"])):
        fields = item["fields"]
        location = paths.get(item["object_id"], "")
        if item["object_id"] not in paths and not (
            fields.get("library_location_id") or fields.get("library_location_code")
        ) and item.get("updated_by") == actor:
            # Owner's last-saved free-text place, not a stale mirror of an
            # unavailable structured assignment.
            location = item["location"]
        projection = {
            "object_id": item["object_id"], "copy_id": _stable_copy_id(item), "title": item["name"],
            "reference": item["sequence_id"], "location": location,
        }
        for key in codes:
            rows[key].append(projection)
    return rows
