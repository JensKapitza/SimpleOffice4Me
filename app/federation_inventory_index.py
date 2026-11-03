"""Shared inventory index scope for opt-in friend stock checks.

Unlike the document catalog, this index carries only the minimal
fields needed for an exact ISBN/GTIN availability answer: the
canonical identifier, its kind, and a neutral display name. Prices,
locations, notes, images, document contents, owner data, and all
other inventory fields are never collected, transported, or stored.

Both sides decide separately with an explicit default-deny flag:
the serving peer needs ``policy.inventory.send is True``, the
syncing peer needs ``policy.inventory.receive is True`` before any
network access. Only locally synchronized rows are ever consulted.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contextflow.inventory_match import classify_identifier, clean_identifier
from .object_store import ObjectStore

INVENTORY_INDEX_SCHEMA = "sofp-inventory-index/v1"
INVENTORY_INDEX_MAX_ROWS = 5000
IDENTIFIER_TYPES = ("isbn", "gtin")
DISPLAY_NAME_MAX_LENGTH = 160


def build_inventory_rows(root: str | Path) -> list[dict[str, str]]:
    """Collect shareable inventory rows: canonical ISBN/GTIN plus name.

    Only active objects with an ISBN/GTIN-shaped identifier qualify;
    plain barcodes, title-only entries, and inactive objects are
    excluded. Each row carries exactly three fields.
    """
    rows: list[dict[str, str]] = []
    for item in ObjectStore(root).objects():
        if str(item.get("status", "") or "") != "active":
            continue
        identifier = clean_identifier(item.get("identifier", ""))
        if not identifier:
            continue
        kind, key = classify_identifier(identifier)
        if kind not in IDENTIFIER_TYPES:
            continue
        name = " ".join(str(item.get("name", "") or "").split())[:DISPLAY_NAME_MAX_LENGTH]
        rows.append({
            "identifier": key,
            "identifier_type": kind,
            "display_name": name,
        })
    rows.sort(key=lambda row: (row["identifier"], row["identifier_type"]))
    return rows[:INVENTORY_INDEX_MAX_ROWS]


def inventory_generation(rows: list[dict[str, str]]) -> str:
    """Content hash over the served rows; drives stale-row cleanup."""
    digest = hashlib.sha256()
    for row in rows:
        digest.update(json.dumps(
            [row["identifier"], row["identifier_type"], row["display_name"]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()
