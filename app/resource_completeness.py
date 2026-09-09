"""Recursive 'Hab ich alles?' verification for Resource Commander."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .resource_compare import compare_files
from .resource_provider import ProviderError

MAX_VERIFY_NODES = 20000


def _join(base: str, name: str) -> str:
    base = str(base or "").strip("/")
    name = str(name or "").strip("/")
    return "/".join(part for part in (base, name) if part)


def verify_complete(source_provider, source_path: str, target_provider, target_path: str) -> dict[str, Any]:
    """Fully verify every source file exists below target; target extras are allowed."""
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    checked_files = checked_folders = 0
    bytes_read_source = bytes_read_target = 0
    stack: list[tuple[str, str, str]] = [(str(source_path or ""), str(target_path or ""), "")]
    nodes = 0

    while stack:
        source_dir, target_dir, relative_dir = stack.pop()
        source_items = list(source_provider.list(source_dir))
        target_items = list(target_provider.list(target_dir))
        target_by_name = {item.name.casefold(): item for item in target_items}
        for source in source_items:
            nodes += 1
            if nodes > MAX_VERIFY_NODES:
                raise ProviderError("Vollstaendigkeitspruefung ueberschreitet das Sicherheitslimit von 20000 Eintraegen")
            relative = _join(relative_dir, source.name)
            target = target_by_name.get(source.name.casefold())
            if target is None:
                row = {"name": source.name, "path": relative, "status": "left_only", "left": source.to_dict(), "reason": "Fehlt im Ziel"}
                rows.append(row)
                missing.append(row)
                continue
            if source.kind != target.kind:
                row = {"name": source.name, "path": relative, "status": "different", "left": source.to_dict(), "right": target.to_dict(), "reason": "Datei-/Ordnertyp unterschiedlich"}
                rows.append(row)
                missing.append(row)
                continue
            if source.kind == "folder":
                checked_folders += 1
                rows.append({"name": source.name, "path": relative, "status": "folders", "left": source.to_dict(), "right": target.to_dict()})
                stack.append((source.resource_id, target.resource_id, relative))
                continue

            checked_files += 1
            compared = compare_files(source_provider, source.resource_id, target_provider, target.resource_id, mode="full")
            bytes_read_source += int(compared.bytes_read_left)
            bytes_read_target += int(compared.bytes_read_right)
            row = {"name": source.name, "path": relative, **asdict(compared)}
            rows.append(row)
            if compared.status != "identical":
                missing.append(row)

    return {
        "complete": not missing,
        "rows": rows,
        "required_missing": missing,
        "required_uncertain": [],
        "extra_target_allowed": True,
        "checked_files": checked_files,
        "checked_folders": checked_folders,
        "bytes_read_source": bytes_read_source,
        "bytes_read_target": bytes_read_target,
        "source_path": str(source_path or ""),
        "target_path": str(target_path or ""),
        "mode": "full-recursive",
    }
