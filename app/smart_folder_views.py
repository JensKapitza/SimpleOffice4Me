"""Lazy document folder smart views, without creating metadata for every folder."""
from __future__ import annotations

import json
from pathlib import Path
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
        if parts[0].startswith("."):
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

    def assigned_to(self, document_id: str) -> list[str]:
        return list(self._load()["assignments"].get(document_id, []))
