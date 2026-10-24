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
from uuid import UUID, uuid4, uuid5

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
MAX_MERGED_FOLDER_TASK_IDS = 10_000
MAX_MERGED_PREVIEW_GROUPS = 10_000
MAX_MERGED_NOTES = 500
MAX_MERGED_SOURCES = 10_000
MAX_PREVIEW_SELECTION = 6
MAX_PREVIEW_CANDIDATES = 200
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

    def _import_origin(self, document: dict) -> tuple[str, str, str] | None:
        attributes = document.get("attributes")
        origin = attributes.get("folder_import_origin") if isinstance(attributes, dict) else None
        if not isinstance(origin, dict):
            return None
        import_id = str(origin.get("import_id", ""))
        source_path = origin.get("source_path")
        folder_id = str(origin.get("folder_id", ""))
        if not re.fullmatch(r"[0-9a-f]{32}", import_id) or not isinstance(source_path, str):
            return None
        try:
            source_path = self.normalize(source_path)
        except ValueError:
            return None
        expected_id = uuid5(UUID(hex=import_id), source_path).hex
        if folder_id != expected_id:
            return None
        return f"imports/{import_id}/{source_path}", folder_id, source_path

    def import_origin_view_path(self, document: dict) -> str:
        """Return the validated persistent Smart View path for an import origin."""
        origin = self._import_origin(document)
        return origin[0] if origin else ""

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
                parents = set()
            else:
                parents = {parent}
            origin = self._import_origin(doc)
            if origin:
                parents.add(origin[0])
            for source_parent in parents:
                canonical = self._canonical_path(source_parent, merged_into)
                item = folders.setdefault(canonical, {"path": canonical, "members": []})
                if all(member["document_id"] != doc["document_id"] for member in item["members"]):
                    item["members"].append(doc)
                if origin and source_parent == origin[0]:
                    item.setdefault("source_folder_ids", []).append(origin[1])
                    item.setdefault("source_folder_paths", []).append(origin[2])
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
        for item in folders.values():
            if "source_folder_ids" in item:
                item["source_folder_ids"] = sorted(set(item["source_folder_ids"]))
                item["source_folder_paths"] = sorted(set(item["source_folder_paths"]))
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
        folder_info = self._load()["folders"].get(folder, {})
        selected_ids = folder_info.get("preview_document_ids", [])
        if not isinstance(selected_ids, list):
            selected_ids = []
        members_by_id = {str(doc["document_id"]): doc for doc in item["members"]}
        item["preview_document_ids"] = [
            document_id for document_id in selected_ids
            if isinstance(document_id, str) and document_id in members_by_id
        ][:MAX_PREVIEW_SELECTION]
        item["preview_members"] = [
            members_by_id[document_id] for document_id in item["preview_document_ids"]
        ]
        merged_groups = folder_info.get("merged_preview_selections", [])
        item["merged_preview_groups"] = []
        if isinstance(merged_groups, list):
            for group in merged_groups:
                if not isinstance(group, dict):
                    continue
                selected = group.get("document_ids", [])
                if not isinstance(selected, list):
                    continue
                members = [
                    members_by_id[document_id] for document_id in selected
                    if isinstance(document_id, str) and document_id in members_by_id
                ][:MAX_PREVIEW_SELECTION]
                if members:
                    item["merged_preview_groups"].append({
                        "source": str(group.get("source", "")),
                        "members": members,
                    })
        item["preview_candidates"] = item["members"][:MAX_PREVIEW_CANDIDATES]
        item["preview_candidates_truncated"] = len(item["members"]) > MAX_PREVIEW_CANDIDATES
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
        self.view(folder)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            self.view(folder)
            data = self._load()
            if self._canonical_path(folder, data["merged_into"]) != folder:
                raise ValueError("Der Zielordner wurde bereits in einen anderen Ordner fusioniert")
            paths = data["assignments"].setdefault(document_id, [])
            if folder not in paths:
                paths.append(folder)
            self._save(data)

    def set_preview_documents(
        self,
        folder: str,
        document_ids: list[str],
        actor: str,
    ) -> None:
        self._verify_actor(actor)
        folder = self.normalize(folder)
        if not isinstance(document_ids, list):
            raise ValueError("Ungültige Vorschaubildauswahl")
        selected = [str(document_id).strip() for document_id in document_ids]
        if len(selected) > MAX_PREVIEW_SELECTION or len(set(selected)) != len(selected):
            raise ValueError("Wähle höchstens sechs unterschiedliche Dokumente aus")
        if len(selected) not in {0, 1, 4, 6}:
            raise ValueError("Wähle 1, 4 oder 6 Vorschaubilder aus oder lösche die Auswahl")
        self.view(folder)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path.with_suffix(".lock")):
            data = self._load()
            entry = data["folders"].get(folder)
            if entry is None or self._canonical_path(folder, data["merged_into"]) != folder:
                raise ValueError("Sammelordner muss vor der Vorschaubildauswahl aktiviert werden")
            current_ids = {
                str(document["document_id"]) for document in self.view(folder)["members"]
            }
            if any(document_id not in current_ids for document_id in selected):
                raise ValueError("Vorschaubilder müssen zum ausgewählten Sammelordner gehören")
            entry["preview_document_ids"] = selected
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
        source_task_folder_ids = self._folder_task_ids(source_meta)
        target_task_folder_ids = self._folder_task_ids(target_meta)
        source_preview_ids = source_meta.get("preview_document_ids", [])
        target_preview_ids = target_meta.get("preview_document_ids", [])
        source_preview_ids = [
            value for value in source_preview_ids if isinstance(value, str)
        ][:MAX_PREVIEW_SELECTION] if isinstance(source_preview_ids, list) else []
        target_preview_ids = [
            value for value in target_preview_ids if isinstance(value, str)
        ][:MAX_PREVIEW_SELECTION] if isinstance(target_preview_ids, list) else []
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
            "source_task_folder_ids": source_task_folder_ids,
            "target_task_folder_ids": target_task_folder_ids,
            "source_preview_ids": list(source_preview_ids),
            "target_preview_ids": list(target_preview_ids),
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
            source_task_folder_ids = self._folder_task_ids(source_info)
            target_task_folder_ids = self._folder_task_ids(target_info)
            raw_preview_ids = source_info.get("preview_document_ids", [])
            source_preview_ids = [
                value for value in raw_preview_ids if isinstance(value, str)
            ][:MAX_PREVIEW_SELECTION] if isinstance(raw_preview_ids, list) else []
            target_id = str(target_info.get("id", ""))
            merged_task_ids = (set(target_task_folder_ids[1:]) | set(source_task_folder_ids)) - {target_id}
            if len(merged_task_ids) > MAX_MERGED_FOLDER_TASK_IDS:
                raise ValueError("Zu viele übernommene Aufgabenordner-IDs für eine Fusion")
            target_info["merged_folder_ids"] = sorted(merged_task_ids)
            target_preview_groups = target_info.get("merged_preview_selections", [])
            inherited_preview_groups = source_info.get("merged_preview_selections", [])
            if (
                not isinstance(target_preview_groups, list)
                or not isinstance(inherited_preview_groups, list)
                or any(not isinstance(group, dict) for group in target_preview_groups)
                or any(not isinstance(group, dict) for group in inherited_preview_groups)
            ):
                raise ValueError("Ungültige übernommene Vorschaubildauswahl")
            merged_preview_groups = [*target_preview_groups, *inherited_preview_groups]
            if source_preview_ids:
                merged_preview_groups.append({
                    "source": source,
                    "source_folder_id": str(source_info.get("id", "")),
                    "document_ids": source_preview_ids,
                })
            if len(merged_preview_groups) > MAX_MERGED_PREVIEW_GROUPS:
                raise ValueError("Zu viele übernommene Vorschaubildgruppen für eine Fusion")
            target_info["merged_preview_selections"] = merged_preview_groups
            target_notes = target_info.get("merged_notes", [])
            inherited_notes = source_info.get("merged_notes", [])
            if (
                not isinstance(target_notes, list)
                or not isinstance(inherited_notes, list)
                or any(not isinstance(note, dict) for note in target_notes)
                or any(not isinstance(note, dict) for note in inherited_notes)
            ):
                raise ValueError("Ungültige übernommene Sammelordner-Notizen")
            merged_notes = [*target_notes, *inherited_notes]
            target_sources = target_info.get("merged_from", [])
            inherited_sources = source_info.get("merged_from", [])
            if (
                not isinstance(target_sources, list)
                or not isinstance(inherited_sources, list)
                or any(not isinstance(path, str) for path in target_sources)
                or any(not isinstance(path, str) for path in inherited_sources)
            ):
                raise ValueError("Ungültige übernommene Sammelordner-Herkunft")
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
                merged_notes.append({
                    "source": source, "text": source_notes, "actor": actor,
                    "at": datetime.now(timezone.utc).isoformat(),
                })
            if len(merged_notes) > MAX_MERGED_NOTES:
                raise ValueError("Zu viele übernommene Sammelordner-Notizen für eine Fusion")
            merged_sources = list(dict.fromkeys([*target_sources, *inherited_sources, source]))
            if len(merged_sources) > MAX_MERGED_SOURCES:
                raise ValueError("Zu viele Herkunftsordner für eine Fusion")
            target_info["merged_notes"] = merged_notes
            target_info["merged_from"] = merged_sources
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
                "source_task_folder_ids": source_task_folder_ids,
                "source_preview_document_ids": source_preview_ids,
            })
            self._save(data)
        return preview

    def assigned_to(self, document_id: str) -> list[str]:
        return list(self._load()["assignments"].get(document_id, []))

    @staticmethod
    def _folder_task_ids(info: dict) -> list[str]:
        if not isinstance(info, dict):
            raise ValueError("Ungültige Sammelordner-Metadaten für Aufgaben")
        primary = info.get("id")
        if primary is None:
            return []
        if not isinstance(primary, str) or not re.fullmatch(r"[0-9a-f]{32}", primary):
            raise ValueError("Ungültige Sammelordner-ID")
        merged_ids = info.get("merged_folder_ids", [])
        if not isinstance(merged_ids, list) or len(merged_ids) > MAX_MERGED_FOLDER_TASK_IDS:
            raise ValueError("Ungültige übernommene Aufgabenordner-IDs")
        if any(
            not isinstance(folder_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", folder_id)
            for folder_id in merged_ids
        ):
            raise ValueError("Ungültige übernommene Aufgabenordner-ID")
        return list(dict.fromkeys([primary, *merged_ids]))

    def task_folder_ids(self, folder: str, actor: str) -> list[str]:
        self._verify_actor(actor)
        folder = self.normalize(folder)
        data = self._load()
        canonical = self._canonical_path(folder, data["merged_into"])
        info = data["folders"].get(canonical)
        if info is None:
            raise ValueError("Sammelordner muss vor dem Verknüpfen von Aufgaben aktiviert werden")
        return self._folder_task_ids(info)

    def folder_task_id(self, folder: str, actor: str) -> str:
        ids = self.task_folder_ids(folder, actor)
        if not ids:
            raise ValueError("Sammelordner hat keine gültige Aufgaben-ID")
        return ids[0]
