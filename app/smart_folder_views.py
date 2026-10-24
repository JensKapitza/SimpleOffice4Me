"""Lazy document folder smart views, without creating metadata for every folder."""
from __future__ import annotations

import csv
import hashlib
import io
import logging
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import unicodedata
from uuid import uuid4

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from .document_store_core import CONTROL_DIR, atomic_json_write
from .file_lock import exclusive_file_lock
from .v2.contracts import LogicalObjectId
from .v2.storage_runtime import storage_for

MAX_EXTRACTED_FILES = 100
MAX_EXTRACTED_FILE_BYTES = 256 * 1024
MAX_EXTRACTED_TOTAL_BYTES = 8 * 1024 * 1024
MAX_EXTRACTED_RULES = 20
MAX_EXTRACTED_VALUE_CHARS = 500
MAX_MERGE_EVENTS = 10_000
_LOGGER = logging.getLogger(__name__)


class SmartFolderViews:
    def __init__(self, store, actor: str):
        actor = str(actor or "").strip()
        if not actor:
            raise ValueError("a named user is required")
        self.store = store
        self.actor = actor
        actor_key = hashlib.sha256(actor.encode("utf-8")).hexdigest()
        self.path = Path(store.root) / CONTROL_DIR / "smart-folder-views" / f"{actor_key}.json"

    def _verify_actor(self, actor: str) -> None:
        if str(actor or "").strip() != self.actor:
            raise ValueError("Smart-View-Aktion gehört zu einem anderen Benutzer")

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
            return {
                "folders": {}, "assignments": {}, "merged_into": {},
                "merge_events": [],
            }
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(
            not isinstance(data.get(section, {}), dict)
            for section in ("folders", "assignments", "merged_into")
        ):
            raise ValueError("Ungültige Smart-View-Metadaten")
        merge_events = data.get("merge_events", [])
        if not isinstance(merge_events, list) or any(
            not isinstance(event, dict) for event in merge_events
        ):
            raise ValueError("Ungültiges Smart-View-Fusionsprotokoll")
        return {
            "folders": data.get("folders", {}),
            "assignments": data.get("assignments", {}),
            "merged_into": data.get("merged_into", {}),
            "merge_events": merge_events,
        }

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_write(self.path, data)

    @staticmethod
    def _canonical_path(folder: str, merged_into: dict) -> str:
        seen = set()
        while folder in merged_into:
            if folder in seen:
                raise ValueError("Zyklische Smart-View-Fusion")
            seen.add(folder)
            folder = str(merged_into[folder])
        return folder

    @staticmethod
    def _normalized_folder_name(folder: str) -> str:
        return unicodedata.normalize("NFC", Path(folder).name.strip()).casefold()

    def folders(self) -> list[dict]:
        data = self._load()
        merged_into = data["merged_into"]
        folders: dict[str, dict] = {}
        for doc in self.store.list_documents():
            path = str(doc.get("last_path") or "")
            if not path or path.startswith("[external]"):
                continue
            parent = Path(path).parent.as_posix()
            if parent == ".":
                continue
            canonical = self._canonical_path(parent, merged_into)
            folders.setdefault(canonical, {"path": canonical, "members": []})["members"].append(doc)
        for path, info in data["folders"].items():
            canonical = self._canonical_path(path, merged_into)
            folders.setdefault(canonical, {"path": canonical, "members": []})["active"] = True
            folders[canonical]["notes"] = info.get("notes", "")
        for document_id, paths in data["assignments"].items():
            try:
                doc = self.store.get_document(document_id)
            except (ValueError, OSError):
                continue
            for path in paths:
                canonical = self._canonical_path(path, merged_into)
                members = folders.setdefault(canonical, {"path": canonical, "members": []})["members"]
                if all(member["document_id"] != document_id for member in members):
                    members.append(doc)
        return sorted(
            (dict(item, count=len(item["members"]), active=item.get("active", False)) for item in folders.values()),
            key=lambda item: item["path"].casefold(),
        )

    def activated_folders(self) -> list[dict]:
        """Return persisted assignment targets without scanning document metadata."""
        data = self._load()
        return [
            {"path": path, "active": True}
            for path in sorted(data["folders"], key=str.casefold)
            if self._canonical_path(path, data["merged_into"]) == path
        ]

    def rules_for(self, folder: str) -> dict[str, str]:
        folder = self.normalize(folder)
        return dict(self._load()["folders"].get(folder, {}).get("rules", {}))

    def view(self, folder: str, folders: list[dict] | None = None) -> dict:
        folder = self.normalize(folder)
        folders = folders if folders is not None else self.folders()
        item = next((item for item in folders if item["path"] == folder), None)
        if item is None:
            raise ValueError("Ordner nicht gefunden")
        name = self._normalized_folder_name(folder)
        item["same_name"] = [
            other for other in folders
            if other["path"] != folder
            and self._normalized_folder_name(other["path"]) == name
        ]
        return item

    def activate(self, folder: str, actor: str, notes: str = "") -> dict:
        self._verify_actor(actor)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            if folder not in data["folders"] or self._canonical_path(folder, data["merged_into"]) != folder:
                raise ValueError("Ziel-Sammelordner muss zuerst aktiviert werden")
            paths = data["assignments"].setdefault(document_id, [])
            if folder not in paths:
                paths.append(folder)
            self._save(data)

    def save_rule(self, folder: str, name: str, field: str, actor: str) -> None:
        """Save a bounded field extractor; no arbitrary Python or regex."""
        self._verify_actor(actor)
        folder = self.normalize(folder)
        name = name.strip()
        field = field.strip()
        if (
            not name
            or len(name) > 80
            or not field
            or len(field) > 120
            or name[:1] in {"=", "+", "-", "@"}
        ):
            raise ValueError("Ungültige Extraktionsregel")
        self.view(folder)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            entry = data["folders"].get(folder)
            if entry is None:
                raise ValueError("Sammelordner vor dem Speichern von Regeln erst aktivieren")
            rules = entry.setdefault("rules", {})
            if name not in rules and len(rules) >= MAX_EXTRACTED_RULES:
                raise ValueError("Zu viele Extraktionsregeln für diesen Sammelordner")
            rules[name] = field
            self._save(data)

    def extract_rows(self, folder: str) -> list[dict]:
        """Extract bounded JSON, CSV and XML fields from integrity-checked objects."""
        folder = self.normalize(folder)
        rules = self.rules_for(folder)
        if not rules:
            return []
        storage = storage_for(self.store.root, self.actor)
        rows = []
        total_bytes = 0
        for doc in self.view(folder)["members"][:MAX_EXTRACTED_FILES]:
            path = str(doc.get("last_path") or "")
            if path.startswith("[external]"):
                continue
            try:
                object_id = LogicalObjectId(str(doc["document_id"]))
                described = storage.stat(object_id)
                if not described.ok:
                    _LOGGER.warning(
                        "Smart folder extraction cannot read document %s",
                        object_id.value,
                    )
                    continue
                expected = described.value
                remaining = min(
                    MAX_EXTRACTED_FILE_BYTES,
                    MAX_EXTRACTED_TOTAL_BYTES - total_bytes,
                )
                if expected.size > remaining:
                    continue
                spool = io.BytesIO()
                copied = storage.copy_verified_range_to(
                    object_id,
                    spool,
                    start=0,
                    length=expected.size,
                )
                if (
                    not copied.ok
                    or copied.value.size != expected.size
                    or copied.value.version != expected.version
                ):
                    _LOGGER.warning(
                        "Smart folder extraction failed integrity verification for document %s",
                        object_id.value,
                    )
                    continue
                content_bytes = spool.getvalue()
                if len(content_bytes) != expected.size:
                    continue
                total_bytes += len(content_bytes)
                if b"\x00" in content_bytes:
                    continue
                content = content_bytes.decode("utf-8-sig")
                suffix = Path(path).suffix.casefold()
                if suffix == ".json":
                    parsed = json.loads(content)
                elif suffix == ".csv":
                    parsed = next(iter(csv.DictReader(io.StringIO(content))), {})
                elif suffix == ".xml":
                    parsed = ET.fromstring(content)
                else:
                    continue
                row = {"document_id": doc["document_id"], "path": path}
                for name, field in rules.items():
                    if suffix == ".xml":
                        value = self._xml_field_value(parsed, field)
                    else:
                        value = parsed.get(field) if isinstance(parsed, dict) else None
                    row[name] = str(value)[:MAX_EXTRACTED_VALUE_CHARS] if value is not None else ""
                rows.append(row)
            except (
                DefusedXmlException,
                OSError,
                StopIteration,
                SyntaxError,
                TypeError,
                UnicodeError,
                ValueError,
            ) as exc:
                _LOGGER.warning(
                    "Smart folder extraction skipped document %s (%s)",
                    doc.get("document_id", "unknown"),
                    type(exc).__name__,
                )
                continue
        return rows

    @staticmethod
    def _xml_field_value(root, field: str) -> str | None:
        """Support simple tag paths only; reject ElementPath predicates/functions."""
        if field == ".":
            return root.text
        segments = field.removeprefix("./").split("/")
        if not segments or any(
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", segment)
            for segment in segments
        ):
            return None
        element = root.find(field)
        return element.text if element is not None else None

    def merge_preview(self, source: str, target: str) -> dict:
        """Show consequences before an explicitly confirmed logical merge."""
        source, target = self.normalize(source), self.normalize(target)
        if source == target:
            raise ValueError("Quelle und Ziel müssen verschieden sein")
        if self._normalized_folder_name(source) != self._normalized_folder_name(target):
            raise ValueError("Nur gleichnamige Ordner können fusioniert werden")
        folders = self.folders()
        origin, destination = self.view(source, folders), self.view(target, folders)
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
            "source_paths": [str(item.get("last_path", "")) for item in origin["members"][:200]],
            "source_paths_truncated": len(origin["members"]) > 200,
        }

    def merge_assignments(self, source: str, target: str, actor: str) -> dict:
        """Explicit logical fusion; originals stay put and rule conflicts block the merge."""
        self._verify_actor(actor)
        preview = self.merge_preview(source, target)
        if preview["conflicts"]:
            raise ValueError("Widersprüchliche Extraktionsregeln: " + ", ".join(preview["conflicts"]))
        source_documents = self.view(source)["members"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            if len(data["merge_events"]) >= MAX_MERGE_EVENTS:
                raise ValueError("Das Fusionsprotokoll hat seine sichere Kapazitätsgrenze erreicht")
            source_info = data["folders"].get(source, {})
            target_info = data["folders"].setdefault(target, {"id": uuid4().hex, "created_by": actor})
            source_rules = source_info.get("rules", {})
            target_rules = target_info.setdefault("rules", {})
            if any(name in target_rules and target_rules[name] != expr for name, expr in source_rules.items()):
                raise ValueError("Extraktionsregeln haben sich inzwischen geändert")
            for document in source_documents:
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
            data["merged_into"][source] = target
            if source in data["folders"]:
                data["folders"].pop(source)
            document_ids = sorted(
                str(document["document_id"]) for document in source_documents
            )
            data["merge_events"].append({
                "event_id": uuid4().hex,
                "action": "smart_folder_merged",
                "source": source,
                "target": target,
                "actor": actor,
                "at": datetime.now(timezone.utc).isoformat(),
                "document_count": len(document_ids),
                "document_ids_sha256": hashlib.sha256(
                    "\n".join(document_ids).encode("utf-8")
                ).hexdigest(),
                "rules": sorted(source_rules),
                "notes_transferred": bool(source_notes),
            })
            self._save(data)
        return preview

    def assigned_to(self, document_id: str) -> list[str]:
        return list(self._load()["assignments"].get(document_id, []))
