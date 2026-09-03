"""Local replication rules and optional restic snapshots.

Replication intentionally creates readable copies.  Restic remains a separate,
encrypted snapshot layer; repository passwords are accepted only for one run and
are never persisted in SimpleOffice metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import click
from flask import current_app
from .document_store import CONTROL_DIR, DocumentStore, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .revision_history import RevisionHistory


CATEGORIES = ("documents", "images", "media", "notes", "contacts", "calendar", "forms", "projects", "settings")
CONTROL_FILES = {
    "notes": ("notes.json",), "contacts": ("contacts.json",), "calendar": ("calendar.json", "calendar-booking.json"),
    "forms": ("form-definitions.json", "form-records.json"), "projects": ("projects.json",),
    "settings": ("settings.json",),
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | {".mp3", ".wav", ".ogg", ".mp4", ".mkv", ".mov", ".avi", ".webm"}


class ReplicationStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve(); self.control = self.root / CONTROL_DIR
        self.path = self.control / "replication.json"; self.history = RevisionHistory(self.root)

    def status(self) -> dict[str, Any]:
        self._initialize(); return self._read()

    def add_target(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        path = Path(str(values.get("path", "")).strip()).expanduser()
        label = str(values.get("label", "")).strip()
        if not label or not path.is_dir(): raise ValueError("Zielname und vorhandener Zielordner sind erforderlich")
        path = path.resolve()
        if path == self.root or self.root in path.parents or path in self.root.parents: raise ValueError("Hauptdatenverzeichnis und Unterordner dürfen kein Spiegelungsziel sein")
        initial_import = DocumentStore(self.root).import_directory(path, label, actor) if any(path.iterdir()) else {"copied": 0, "unchanged": 0, "skipped": 0}
        data = self.status(); target = {"target_id": str(uuid.uuid4()), "label": label, "path": str(path), "created_at": utc_now(), "created_by": actor, "enabled": True, "last_run": None, "last_error": "", "initial_import": initial_import, "last_import": initial_import}
        data["targets"].append(target); self._save(data, actor, "replication_target_created", target["target_id"]); return target

    def set_target_enabled(self, target_id: str, enabled: bool, actor: str) -> dict[str, Any]:
        data = self.status(); target = next((item for item in data["targets"] if item["target_id"] == target_id), None)
        if target is None: raise ValueError("Unbekanntes Spiegelungsziel")
        target["enabled"] = enabled
        target["paused_at"] = utc_now() if not enabled else None
        target["paused_by"] = actor if not enabled else None
        self._save(data, actor, "replication_target_resumed" if enabled else "replication_target_paused", target_id)
        return target

    def import_target(self, target_id: str, actor: str) -> dict[str, Any]:
        data = self.status(); target = next((item for item in data["targets"] if item["target_id"] == target_id), None)
        if target is None: raise ValueError("Unbekanntes Spiegelungsziel")
        if target.get("enabled", True) is False: raise ValueError("Spiegelungsziel ist pausiert")
        path = Path(target["path"])
        if not path.is_dir(): raise ValueError("Spiegelungsziel ist nicht verbunden")
        result = DocumentStore(self.root).import_directory(path, target["label"], actor)
        target["last_import"] = {**result, "at": utc_now()}
        self._save(data, actor, "replication_target_imported", target_id)
        return result

    def add_rule(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        data = self.status(); target_id = str(values.get("target_id", ""));
        if not any(item["target_id"] == target_id for item in data["targets"]): raise ValueError("Unbekanntes Spiegelungsziel")
        categories = self._categories(values.get("categories", [])); label = str(values.get("label", "")).strip()
        if not label or not categories: raise ValueError("Name und mindestens eine Datenklasse sind erforderlich")
        rule = {"rule_id": str(uuid.uuid4()), "label": label, "target_id": target_id, "categories": categories,
                "tags": self._tags(values.get("tags", "")), "enabled": True, "last_run": None, "last_result": {}}
        data["rules"].append(rule); self._save(data, actor, "replication_rule_created", rule["rule_id"]); return rule

    def run_rule(self, rule_id: str, actor: str) -> dict[str, Any]:
        data = self.status(); rule = next((item for item in data["rules"] if item["rule_id"] == rule_id), None)
        if not rule: raise ValueError("Unbekannte Spiegelungsregel")
        target = next(item for item in data["targets"] if item["target_id"] == rule["target_id"]); target_path = Path(target["path"])
        if target.get("enabled", True) is False: raise ValueError("Spiegelungsziel ist pausiert")
        if not target_path.is_dir(): raise ValueError("Spiegelungsziel ist nicht verbunden")
        copied = unchanged = 0; manifest: dict[str, Any] = {"schema": 1, "created_at": utc_now(), "rule_id": rule_id, "files": []}
        for source, relative, category, document_id in self._sources(rule):
            destination = target_path / "SimpleOffice-Spiegelung" / relative
            digest = self._hash(source)
            entry = {"path": str(relative), "category": category, "sha256": digest, "document_id": document_id}
            if destination.is_file() and self._hash(destination) == digest: unchanged += 1
            else:
                destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination); copied += 1
            manifest["files"].append(entry)
        manifest_path = target_path / "SimpleOffice-Spiegelung" / "manifests" / f"{rule_id}.json"; manifest_path.parent.mkdir(parents=True, exist_ok=True); atomic_json_write(manifest_path, manifest)
        result = {"at": utc_now(), "copied": copied, "unchanged": unchanged, "files": len(manifest["files"]), "manifest": str(manifest_path)}
        rule["last_run"] = result["at"]; rule["last_result"] = result; target["last_run"] = result["at"]; target["last_error"] = ""
        self._save(data, actor, "replication_completed", rule_id); return result

    def run_all(self, actor: str) -> dict[str, Any]:
        results, errors = [], []
        for rule in self.status()["rules"]:
            if not rule.get("enabled", True): continue
            try: results.append({"rule_id": rule["rule_id"], **self.run_rule(rule["rule_id"], actor)})
            except ValueError as exc: errors.append({"rule_id": rule["rule_id"], "error": str(exc)})
        return {"completed": results, "errors": errors}

    def add_restic_repository(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        label, repository = str(values.get("label", "")).strip(), str(values.get("repository", "")).strip()
        if not label or not repository: raise ValueError("Name und restic-Repository sind erforderlich")
        data = self.status(); item = {"repository_id": str(uuid.uuid4()), "label": label, "repository": repository,
                "categories": self._categories(values.get("categories", [])) or list(CATEGORIES), "tags": self._tags(values.get("tags", "")), "last_run": None, "last_error": ""}
        data["restic"].append(item); self._save(data, actor, "restic_repository_created", item["repository_id"]); return item

    def run_restic(self, repository_id: str, action: str, password: str, actor: str, restore_path: str = "") -> dict[str, Any]:
        data = self.status(); item = next((value for value in data["restic"] if value["repository_id"] == repository_id), None)
        if not item: raise ValueError("Unbekanntes restic-Repository")
        executable = shutil.which("restic")
        if not executable: raise ValueError("restic ist nicht installiert")
        if not password: raise ValueError("Restic-Passwort wird für diesen Lauf benötigt und nicht gespeichert")
        env = {"PATH": os.environ.get("PATH", ""), "RESTIC_PASSWORD": password}; command = [executable, "-r", item["repository"]]
        if action == "init": command += ["init"]
        elif action == "check": command += ["check"]
        elif action == "snapshots": command += ["snapshots", "--json"]
        elif action == "backup":
            source_root = self._restic_staging(item); command += ["backup", "--tag", "simpleoffice", *sum((["--tag", tag] for tag in item["tags"]), []), str(source_root)]
        elif action == "restore":
            destination = Path(restore_path).expanduser()
            if not destination.is_dir(): raise ValueError("Vorhandener Wiederherstellungsordner erforderlich")
            command += ["restore", "latest", "--target", str(destination)]
        else: raise ValueError("Ungültige restic-Aktion")
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=7200, check=False)
        output = (result.stdout + "\n" + result.stderr).strip()[-12000:]
        item["last_run"] = utc_now(); item["last_error"] = output if result.returncode else ""; self._save(data, actor, "restic_" + action, repository_id)
        if result.returncode: raise ValueError(output or f"restic beendet mit {result.returncode}")
        return {"output": output, "at": item["last_run"]}

    def _sources(self, rule: dict[str, Any]):
        categories, tags = set(rule["categories"]), set(rule.get("tags", [])); store = DocumentStore(self.root)
        if categories & {"documents", "images", "media"}:
            for document in store.list_documents():
                source = self.root / document.get("last_path", "")
                suffix = source.suffix.lower(); category = "images" if suffix in IMAGE_SUFFIXES else "media" if suffix in MEDIA_SUFFIXES else "documents"
                if category not in categories or not source.is_file() or (tags and not tags.intersection(document.get("tags", []))): continue
                yield source, Path("documents") / document["document_id"] / source.name, category, document["document_id"]
        for category in categories:
            for filename in CONTROL_FILES.get(category, ()):
                source = self.control / filename
                if source.is_file(): yield source, Path("control") / filename, category, ""

    def _restic_staging(self, item: dict[str, Any]) -> Path:
        stage = self.control / "restic-staging" / item["repository_id"]
        if stage.exists(): shutil.rmtree(stage)
        stage.mkdir(parents=True, exist_ok=True)
        rule = {"categories": item["categories"], "tags": item["tags"]}
        manifest = {"created_at": utc_now(), "files": []}
        for source, relative, category, document_id in self._sources(rule):
            destination = stage / relative; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, destination)
            manifest["files"].append({"path": str(relative), "category": category, "document_id": document_id, "sha256": self._hash(source)})
        atomic_json_write(stage / "manifest.json", manifest); return stage

    @staticmethod
    def _hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""): digest.update(block)
        return digest.hexdigest()
    @staticmethod
    def _tags(value: Any) -> list[str]: return [item.strip() for item in (value.split(",") if isinstance(value, str) else value or []) if item.strip()]
    @staticmethod
    def _categories(value: Any) -> list[str]: return [item for item in value if item in CATEGORIES]
    def _initialize(self) -> None:
        self.control.mkdir(parents=True, exist_ok=True)
        if not self.path.exists(): atomic_json_write(self.path, {"targets": [], "rules": [], "restic": []})
    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8")); return data if isinstance(data, dict) else {"targets": [], "rules": [], "restic": []}
        except (OSError, json.JSONDecodeError): return {"targets": [], "rules": [], "restic": []}
    def _save(self, data: dict[str, Any], actor: str, action: str, object_id: str) -> None:
        with exclusive_file_lock(self.control / ".replication-write.lock"):
            atomic_json_write(self.path, data); self.history.record(action, actor, "replication", object_id, data)


@click.command("run-replications")
@click.option("--actor", default="system", show_default=True)
def run_replications_command(actor: str) -> None:
    result = ReplicationStore(current_app.config["DOCUMENT_ROOT"]).run_all(actor)
    click.echo(json.dumps(result, ensure_ascii=False))
    if result["errors"]: raise click.ClickException("one or more replications failed")


def init_app(app) -> None:
    app.cli.add_command(run_replications_command)
    # Federation builds on the same document/replication storage layer. Register
    # protocol, offline catalog exchange and administrator surfaces together.
    from . import federation_admin, federation_catalog_http, federation_http
    app.register_blueprint(federation_http.bp)
    app.register_blueprint(federation_catalog_http.bp)
    app.register_blueprint(federation_admin.bp)
