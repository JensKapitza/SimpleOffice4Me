"""Lazy document folder smart views, without creating metadata for every folder."""
from __future__ import annotations

import json
import csv
import io
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from .document_store_core import CONTROL_DIR, atomic_json_write
from .file_lock import exclusive_file_lock


class SmartFolderViews:
    def __init__(self, store):
        self.store = store
        self.path = Path(store.root) / CONTROL_DIR / "smart_folders.json"

    @staticmethod
    def normalize(folder: str) -> str:
        value = folder.strip().replace(chr(92), "/")
        parts = Path(value).parts
        if not value or value.startswith("/") or any(part in ("", ".", "..") for part in parts):
            raise ValueError("Ungültiger Ordner")
        if any(part.startswith(".") for part in parts):
            raise ValueError("Systemordner sind nicht erlaubt")
        return Path(*parts).as_posix()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"folders": {}, "assignments": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {"folders": data.get("folders", {}), "assignments": data.get("assignments", {})}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    def folders(self) -> list[dict]:
        data = self._load()
        folders: dict[str, dict] = {}
        for doc in self.store.list_documents():
            path = str(doc.get("last_path") or "")
            if not path or path.startswith("[external]"):
                continue
            parent = Path(path).parent.as_posix()
            if parent == ".":
                continue
            folders.setdefault(parent, {"path": parent, "members": []})["members"].append(doc)
        for path, info in data["folders"].items():
            folders.setdefault(path, {"path": path, "members": []})["active"] = True
            folders[path]["notes"] = info.get("notes", "")
        for document_id, paths in data["assignments"].items():
            try:
                doc = self.store.get_document(document_id)
            except (ValueError, OSError):
                continue
            for path in paths:
                members = folders.setdefault(path, {"path": path, "members": []})["members"]
                if all(member["document_id"] != document_id for member in members):
                    members.append(doc)
        return sorted(
            (dict(item, count=len(item["members"]), active=item.get("active", False)) for item in folders.values()),
            key=lambda item: item["path"].casefold(),
        )

    def view(self, folder: str) -> dict:
        folder = self.normalize(folder)
        item = next((item for item in self.folders() if item["path"] == folder), None)
        if item is None:
            raise ValueError("Ordner nicht gefunden")
        name = Path(folder).name.casefold()
        item["same_name"] = [other for other in self.folders() if other["path"] != folder and Path(other["path"]).name.casefold() == name]
        return item

    def activate(self, folder: str, actor: str, notes: str = "") -> dict:
        folder = self.normalize(folder)
        self.view(folder)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            entry = data["folders"].setdefault(folder, {"id": uuid4().hex, "created_by": actor})
            entry["notes"] = notes[:20000]
            self._save(data)
        return entry

    def assign(self, document_id: str, folder: str) -> None:
        folder = self.normalize(folder)
        self.store.get_document(document_id)
        if folder not in {item["path"] for item in self.folders()}:
            raise ValueError("Zielordner nicht gefunden")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            paths = data["assignments"].setdefault(document_id, [])
            if folder not in paths:
                paths.append(folder)
            self._save(data)

    def save_rule(self, folder: str, name: str, field: str, actor: str) -> None:
        """Save a bounded field extractor; no arbitrary Python or regex."""
        folder = self.normalize(folder)
        if not name.strip() or len(name) > 80 or not field.strip() or len(field) > 120 or field.strip().startswith(("/", "..")):
            raise ValueError("Ungültige Extraktionsregel")
        self.view(folder)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            entry = data["folders"].setdefault(folder, {"id": uuid4().hex, "created_by": actor})
            entry.setdefault("rules", {})[name.strip()] = field.strip()
            self._save(data)

    def extract_rows(self, folder: str, *, max_files: int = 250, max_bytes: int = 1024 * 1024) -> list[dict]:
        """Extract simple JSON keys, CSV columns and XML tags from local documents."""
        folder = self.normalize(folder)
        rules = self._load()["folders"].get(folder, {}).get("rules", {})
        rows = []
        for doc in self.view(folder)["members"][:max_files]:
            path = str(doc.get("last_path") or "")
            if path.startswith("[external]"):
                continue
            try:
                from .safe_paths import resolve_file_under
                source = resolve_file_under(self.store.root, path)
                if source.stat().st_size > max_bytes:
                    continue
                content = source.read_text(encoding="utf-8-sig")
                suffix = source.suffix.casefold()
                if suffix == ".json":
                    parsed = json.loads(content)
                elif suffix == ".csv":
                    parsed = next(iter(csv.DictReader(io.StringIO(content))), {})
                elif suffix == ".xml":
                    if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
                        continue
                    parsed = ET.fromstring(content)
                else:
                    continue
                row = {"document_id": doc["document_id"], "path": path}
                for name, field in rules.items():
                    if suffix == ".xml":
                        element = parsed.find(field) if field != "." else parsed
                        value = element.text if element is not None else None
                    else:
                        value = parsed.get(field) if isinstance(parsed, dict) else None
                    row[name] = str(value)[:2000] if value is not None else ""
                rows.append(row)
            except (OSError, ValueError, UnicodeError, ET.ParseError, json.JSONDecodeError, StopIteration):
                continue
        return rows

    def merge_preview(self, source: str, target: str) -> dict:
        """Show consequences before an explicitly confirmed logical merge."""
        source, target = self.normalize(source), self.normalize(target)
        if source == target:
            raise ValueError("Quelle und Ziel müssen verschieden sein")
        origin, destination = self.view(source), self.view(target)
        data = self._load()
        source_meta = data["folders"].get(source, {})
        target_meta = data["folders"].get(target, {})
        collisions = [
            name for name, expression in source_meta.get("rules", {}).items()
            if name in target_meta.get("rules", {}) and target_meta["rules"][name] != expression
        ]
        return {
            "source": source, "target": target,
            "documents": len(origin["members"]),
            "target_documents": len(destination["members"]),
            "conflicts": collisions,
            "notes": bool(source_meta.get("notes")),
            "source_rules": sorted(source_meta.get("rules", {})),
            "target_rules": sorted(target_meta.get("rules", {})),
            "source_paths": [str(item.get("last_path", "")) for item in origin["members"]],
        }

    def merge_assignments(self, source: str, target: str, actor: str) -> dict:
        """Explicit logical fusion; originals stay put and rule conflicts block the merge."""
        preview = self.merge_preview(source, target)
        if preview["conflicts"]:
            raise ValueError("Widersprüchliche Extraktionsregeln: " + ", ".join(preview["conflicts"]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            source_info = data["folders"].get(source, {})
            target_info = data["folders"].setdefault(target, {"id": uuid4().hex, "created_by": actor})
            source_rules = source_info.get("rules", {})
            target_rules = target_info.setdefault("rules", {})
            if any(name in target_rules and target_rules[name] != expr for name, expr in source_rules.items()):
                raise ValueError("Extraktionsregeln haben sich inzwischen geändert")
            source_members = self.view(source)["members"]
            for document in source_members:
                document_id = document["document_id"]
                assignments = data["assignments"].setdefault(document_id, [])
                if target not in assignments:
                    assignments.append(target)
                if source in assignments:
                    assignments.remove(source)
            target_rules.update(source_rules)
            source_notes = source_info.get("notes", "")
            if source_notes:
                target_info.setdefault("merged_notes", []).append({
                    "source": source, "text": source_notes, "actor": actor,
                    "at": datetime.now(timezone.utc).isoformat(),
                })
            target_info.setdefault("merged_from", []).append(source)
            if source in data["folders"]:
                data["folders"].pop(source)
            self._save(data)
        return preview

    def assigned_to(self, document_id: str) -> list[str]:
        return list(self._load()["assignments"].get(document_id, []))
