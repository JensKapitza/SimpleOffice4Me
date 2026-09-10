"""Public compatibility facade for the local OpenStreetMap address index.

The implementation is split by responsibility so project-owned source files stay
below the 1000-line maintenance limit while existing imports remain stable.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .osm_address_build import build_index
from .osm_address_search import field_suggestions, search, unique_candidate
from .osm_address_storage import (
    GEOFABRIK_REGIONS,
    LocalAddressIndex as _StorageAddressIndex,
    _city,
    _clean,
    _normal,
    _open_geofabrik,
    _remote_total,
    human_bytes,
)


class LocalAddressIndex(_StorageAddressIndex):
    """Local OSM index with build and search behavior composed from split modules."""

    def build(
        self,
        source: str | Path,
        *,
        city: str = "",
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, int]:
        return build_index(
            self,
            source,
            city=city,
            progress=progress,
            human_bytes_fn=human_bytes,
        )

    def search(
        self,
        query: str,
        *,
        country_code: str = "de",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        return search(
            query,
            db_factory=self._db,
            db_available=self.db_path.is_file(),
            country_code=country_code,
            limit=limit,
        )


def search_address(
    query: str,
    *,
    root: str | Path,
    country_code: str = "de",
    limit: int = 5,
) -> list[dict[str, Any]]:
    return LocalAddressIndex(root).search(
        query,
        country_code=country_code,
        limit=limit,
    )


__all__ = [
    "GEOFABRIK_REGIONS",
    "LocalAddressIndex",
    "field_suggestions",
    "human_bytes",
    "search_address",
    "unique_candidate",
]