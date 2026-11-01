"""Exact per-candidate inventory matching and guarded photo attach.

A series candidate carries its own optional identifier (barcode or ISBN).
Matching is exact only: canonical ISBN-13, else canonical 14-digit GTIN
(valid check digit required), else exact stripped barcode equality over
the local catalog. Overlong input is rejected, never truncated; malformed
GTIN-shaped input is rejected, never raw-matched. Titles are never
consulted, nothing is created, nothing leaves the machine.

Attach revalidates server-side that the identifier resolves to exactly
one item and that the claimed target is that item; any mismatch,
ambiguity, or missing upload is refused. Photos land in the existing
inventory sidecar via InventoryEnrichmentStore.save_photo.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..inventory import InventoryEnrichmentStore, normalize_isbn
from ..object_store import ObjectStore
from .vision import validate_image

IDENTIFIER_MAX_LENGTH = 120


class MatchError(ValueError):
    """Base for identifier resolution failures (never a server fault)."""


class EmptyIdentifierError(MatchError):
    """No identifier supplied."""


class NoMatchError(MatchError):
    """Identifier resolves to no local item."""


class AmbiguousMatchError(MatchError):
    """Identifier resolves to more than one local item."""

    def __init__(self, message: str, count: int = 0) -> None:
        super().__init__(message)
        self.count = count


class TargetMismatchError(MatchError):
    """Claimed target is not the unique resolution result."""


class IdentifierTooLongError(MatchError):
    """Identifier exceeds the lookup length bound (never truncated)."""


class MalformedGtinError(MatchError):
    """Numeric GTIN-shaped value with an invalid check digit."""


def clean_identifier(value: object) -> str:
    """Single-line stripped identifier (never truncated for lookup)."""
    return " ".join(str(value or "").split())


def _stored_candidates(item: dict[str, Any]) -> list[str]:
    fields = item.get("fields", {})
    if not isinstance(fields, dict):
        fields = {}
    return [
        str(item.get("identifier", "") or "").strip(),
        str(fields.get("isbn", "") or "").strip(),
        str(fields.get("barcode", "") or "").strip(),
    ]


def _normalized_isbn_or_empty(value: str) -> str:
    try:
        return normalize_isbn(value)
    except ValueError:
        return ""


GTIN_LENGTHS = (8, 12, 13, 14)


def _gtin_check_digit(payload: str) -> str:
    total = sum(
        int(char) * (3 if position % 2 == 0 else 1)
        for position, char in enumerate(reversed(payload))
    )
    return str((10 - total % 10) % 10)


def _gtin_digits(value: str) -> str:
    compact = re.sub(r"[\s\-]", "", value or "")
    if not re.fullmatch(r"[0-9]*", compact):
        return ""
    return compact


def _is_gtin_shaped(value: str) -> bool:
    return len(_gtin_digits(value)) in GTIN_LENGTHS


def normalize_gtin(value: str) -> str:
    """Canonical 14-digit GTIN, or "" unless numeric 8/12/13/14 with valid check digit."""
    digits = _gtin_digits(value)
    if len(digits) not in GTIN_LENGTHS:
        return ""
    if _gtin_check_digit(digits[:-1]) != digits[-1]:
        return ""
    return digits.zfill(14)


def find_exact_matches(
    root: str | Path, identifier: object
) -> tuple[str, list[dict[str, str]]]:
    """Resolve an identifier to all exactly matching local items.

    ISBN-10/13 input compares canonical ISBN-13; valid numeric GTIN-8/12/13/14
    compares canonical 14-digit values; anything else compares exact stripped
    strings. Malformed GTIN-shaped input is rejected, never raw-matched.
    Returns (kind, matches) with matches as [{object_id, name}] sorted by
    object_id. Titles are never consulted.
    """
    query = clean_identifier(identifier)
    if not query:
        raise EmptyIdentifierError("Bitte eine Kennung eingeben.")
    if len(query) > IDENTIFIER_MAX_LENGTH:
        raise IdentifierTooLongError("Kennung ist zu lang (maximal 120 Zeichen).")
    canonical_isbn = _normalized_isbn_or_empty(query)
    canonical_gtin = "" if canonical_isbn else normalize_gtin(query)
    if not canonical_isbn and not canonical_gtin and _is_gtin_shaped(query):
        raise MalformedGtinError("Ungültige GTIN-Prüfziffer.")
    kind = "isbn" if canonical_isbn else ("gtin" if canonical_gtin else "barcode")
    matches: dict[str, dict[str, str]] = {}
    for item in ObjectStore(root).iter_objects():
        object_id = str(item.get("object_id", ""))
        if not object_id or object_id in matches:
            continue
        for stored in _stored_candidates(item):
            if not stored:
                continue
            if canonical_isbn:
                hit = _normalized_isbn_or_empty(stored) == canonical_isbn
            elif canonical_gtin:
                hit = normalize_gtin(stored) == canonical_gtin
            else:
                hit = stored == query
            if hit:
                matches[object_id] = {
                    "object_id": object_id,
                    "name": str(item.get("name", "")),
                }
                break
    return kind, [matches[key] for key in sorted(matches)]


def resolve_unique_target(
    root: str | Path, identifier: object, item_id: object
) -> dict[str, str]:
    """Return the single match iff the identifier resolves uniquely to item_id.

    Raises EmptyIdentifierError, NoMatchError, AmbiguousMatchError, or
    TargetMismatchError. The claimed id is compared strictly (stripped)
    against the resolved object_id; sequence-id aliases never satisfy it.
    """
    _kind, matches = find_exact_matches(root, identifier)
    if not matches:
        raise NoMatchError("Keine Übereinstimmung im Inventar.")
    if len(matches) > 1:
        raise AmbiguousMatchError(
            f"Mehrdeutig: {len(matches)} Treffer, bitte manuell prüfen.",
            count=len(matches),
        )
    target = matches[0]
    if str(item_id or "").strip() != target["object_id"]:
        raise TargetMismatchError("Das Ziel stimmt nicht mit der Kennung überein.")
    return target


def attach_photo(
    root: str | Path, object_id: str, upload: Any, actor: str
) -> dict[str, Any]:
    """Validate one upload and append it to the item's photo sidecar.

    Raises VisionError/ValueError on invalid uploads; never creates objects.
    """
    if upload is None:
        raise ValueError("Kein Foto ausgewählt")
    validate_image(getattr(upload, "filename", ""), getattr(upload, "content_type", ""), upload.stream)
    upload.stream.seek(0)
    photo = InventoryEnrichmentStore(root).save_photo(object_id, upload, actor)
    if photo is None:
        raise ValueError("Kein Foto ausgewählt")
    return photo
