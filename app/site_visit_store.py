"""Private, versioned storage for field visits, infrastructure and findings."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock
from .security_controls import protect_value, unprotect_value

VISIT_STATES = {"draft", "in_progress", "completed", "archived"}
FINDING_STATES = {"unreviewed", "confirmed", "corrected", "not_assessable", "rejected"}
EVIDENCE_CLASSES = {"observed", "measured", "derived", "risk", "unknown"}
RELATIONS = {"causes", "supports", "contradicts", "possible_cause", "resolved_by", "affects", "follows"}
FIELD_TYPES = {"text", "number", "choice", "date"}
MAX_FINDINGS = 2000
MAX_ASSETS = 2000
MAX_ROOMS = 300
MAX_EDGES = 5000
MAX_HISTORY = 500


def _text(value: Any, limit: int, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if len(result) > limit:
        raise ValueError(f"Text darf höchstens {limit} Zeichen enthalten")
    if required and not result:
        raise ValueError("Pflichtfeld fehlt")
    return result


def _number(value: Any, minimum: float, maximum: float, *, default: float = 0) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültiger Zahlenwert") from exc
    if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
        raise ValueError("Zahlenwert außerhalb des erlaubten Bereichs")
    return round(parsed, 3)


def _identifier(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        return str(uuid.UUID(raw))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Ungültige Kennung") from exc


def _cycle(edges: list[dict[str, Any]], source: str, target: str) -> bool:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(str(edge.get("source_id", "")), []).append(str(edge.get("target_id", "")))
    pending = [target]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == source:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, []))
    return False


class SiteVisitStore:
    """Per-visit JSON documents with ownership checks and atomic updates."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "site-visits"

    @staticmethod
    def _actor_key(actor: str) -> str:
        return hashlib.sha256(str(actor).encode("utf-8")).hexdigest()[:24]

    def _path(self, visit_id: str) -> Path:
        return self.directory / f"{_identifier(visit_id)}.json"

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Ortstermin-Datensatz kann nicht gelesen werden") from exc
        if not isinstance(value, dict):
            raise ValueError("Ungültiger Ortstermin-Datensatz")
        return value

    def _owned(self, visit_id: str, actor: str) -> dict[str, Any]:
        record = self._read(self._path(visit_id))
        if not record or record.get("owner_key") != self._actor_key(actor):
            raise ValueError("Ortstermin nicht gefunden")
        return record

    def list(self, actor: str) -> list[dict[str, Any]]:
        self.directory.mkdir(parents=True, exist_ok=True)
        rows = []
        for path in self.directory.glob("*.json"):
            record = self._read(path)
            if record and record.get("owner_key") == self._actor_key(actor):
                rows.append(self._summary(record))
        return sorted(rows, key=lambda item: (item.get("updated_at", ""), item.get("title", "").casefold()), reverse=True)

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "visit_id": record["visit_id"], "title": record.get("title", ""),
            "customer": record.get("customer", ""), "site": record.get("site", ""),
            "visit_date": record.get("visit_date", ""), "status": record.get("status", "draft"),
            "updated_at": record.get("updated_at", ""), "asset_count": len(record.get("assets", [])),
            "finding_count": len(record.get("findings", [])), "todo_id": record.get("todo_id", ""),
        }

    def get(self, visit_id: str, actor: str) -> dict[str, Any]:
        record = self._owned(visit_id, actor)
        public = copy.deepcopy(record)
        for asset in public.get("assets", []):
            asset.pop("credentials", None)
            asset["has_credentials"] = bool(next((row for row in record.get("assets", []) if row.get("asset_id") == asset.get("asset_id")), {}).get("credentials"))
        public.pop("owner_key", None)
        return public

    def create(self, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        title = _text(values.get("title"), 240, required=True)
        now = utc_now()
        visit_id = str(uuid.uuid4())
        record = {
            "visit_id": visit_id, "owner": _text(actor, 120, required=True),
            "owner_key": self._actor_key(actor), "title": title,
            "customer": _text(values.get("customer"), 240), "site": _text(values.get("site"), 300),
            "address": _text(values.get("address"), 600), "visit_date": _text(values.get("visit_date"), 40),
            "status": "draft", "notes": _text(values.get("notes"), 8000),
            "rooms": [], "assets": [], "connections": [], "findings": [], "finding_edges": [],
            "custom_field_definitions": [], "snapshots": [], "reports": [], "attachments": [], "scans": [],
            "geo": {}, "todo_id": _text(values.get("todo_id"), 80), "floorplan_attachment_id": "", "created_at": now, "created_by": actor,
            "updated_at": now, "updated_by": actor, "audit": [],
        }
        self._audit(record, actor, "created", "visit", visit_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(visit_id)
        with exclusive_file_lock(path.with_suffix(".lock")):
            atomic_json_write(path, record)
        return self.get(visit_id, actor)

    def apply_scan(self, visit_id: str, actor: str, scan: dict[str, Any]) -> dict[str, Any]:
        item = {key: scan[key] for key in ("cidr", "ports", "probed_hosts", "device_count", "scanned_at", "scanner", "limitations")}
        item["scan_id"] = str(uuid.uuid4())
        item["created_by"] = actor
        devices = scan.get("devices", [])
        def apply(record: dict[str, Any]) -> None:
            known_ips = {row.get("ip") for row in record["assets"]}
            additions = sum(1 for device in devices if device.get("ip") not in known_ips)
            if len(record["assets"]) + additions > MAX_ASSETS:
                raise ValueError("Nicht alle Scanergebnisse passen in den Gerätedatensatz; es wurde nichts übernommen")
            for device in devices:
                asset = next((row for row in record["assets"] if row.get("ip") == device.get("ip")), None)
                if asset:
                    indexed = {(row["port"], row["protocol"]): row for row in asset.get("ports", [])}
                    for port in device.get("ports", []):
                        indexed[(port["port"], port["protocol"])] = port
                    asset["ports"] = list(indexed.values())[:64]
                    asset["updated_at"] = utc_now()
                    asset["updated_by"] = actor
                else:
                    record["assets"].append({
                        "asset_id": str(uuid.uuid4()), "name": device["ip"], "kind": "LAN-Gerät",
                        "manufacturer": "", "model": "", "serial": "", "asset_tag": "",
                        "ip": device["ip"], "mac": "", "room_id": "", "x": 0, "y": 0,
                        "status": "observed", "description": "TCP-Verbindungstest. Betriebssystem, MAC-Adresse und Geräteeigentümer wurden nicht ermittelt.",
                        "voltage": "", "power_w": None, "circuit": "", "provenance": "lan_scan",
                        "ports": device.get("ports", [])[:64], "credentials": {}, "created_at": utc_now(),
                        "created_by": actor, "updated_at": utc_now(), "updated_by": actor,
                    })
            self._append(record, "scans", item, 100)
        self._mutate(visit_id, actor, "lan_scan_completed", "network-scan", item["scan_id"], apply)
        return item

    def add_scan(self, visit_id: str, actor: str, scan: dict[str, Any]) -> dict[str, Any]:
        item = {key: scan[key] for key in ("cidr", "ports", "probed_hosts", "device_count", "scanned_at", "scanner", "limitations")}
        item["scan_id"] = str(uuid.uuid4()); item["created_by"] = actor
        self._mutate(visit_id, actor, "lan_scan_completed", "network-scan", item["scan_id"], lambda row: self._append(row, "scans", item, 100))
        return item

    def record_event(self, visit_id: str, actor: str, action: str, target_type: str, target_id: str) -> None:
        self._mutate(visit_id, actor, action, target_type, target_id, lambda _row: None)

    def attach_todo(self, visit_id: str, actor: str, task_id: str) -> dict[str, Any]:
        task_id = _text(task_id, 80)
        return self._mutate(visit_id, actor, "todo_linked", "visit", visit_id, lambda row: row.update(todo_id=task_id))

    def update(self, visit_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"title": (240, True), "customer": (240, False), "site": (300, False), "address": (600, False), "visit_date": (40, False), "notes": (8000, False)}
        updates = {key: _text(values.get(key), limit, required=required) for key, (limit, required) in allowed.items() if key in values}
        if "status" in values:
            status = str(values["status"] or "")
            if status not in VISIT_STATES:
                raise ValueError("Ungültiger Status")
            updates["status"] = status
        if "latitude" in values or "longitude" in values:
            updates["geo"] = {
                "latitude": _number(values.get("latitude"), -90, 90),
                "longitude": _number(values.get("longitude"), -180, 180),
                "captured_at": utc_now(),
            }
        return self._mutate(visit_id, actor, "updated", "visit", visit_id, lambda row: row.update(updates))

    def add_room(self, visit_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        room = {
            "room_id": str(uuid.uuid4()), "name": _text(values.get("name"), 160, required=True),
            "floor": _text(values.get("floor"), 80), "width_m": _number(values.get("width_m"), 0, 10000),
            "height_m": _number(values.get("height_m"), 0, 10000), "plan_note": _text(values.get("plan_note"), 2000),
            "map_x": _number(values.get("map_x"), 0, 100), "map_y": _number(values.get("map_y"), 0, 100),
            "map_w": _number(values.get("map_w"), 5, 100, default=45), "map_h": _number(values.get("map_h"), 5, 100, default=35),
            "created_at": utc_now(), "created_by": actor,
        }
        def place_and_append(row: dict[str, Any]) -> None:
            index = len(row["rooms"])
            room["map_x"] = float((index % 2) * 50)
            room["map_y"] = float((index // 2) * 36)
            room["map_w"] = 46.0
            room["map_h"] = 32.0
            self._append(row, "rooms", room, MAX_ROOMS)
        record = self._mutate(visit_id, actor, "room_added", "room", room["room_id"], place_and_append)
        return next(item for item in record["rooms"] if item["room_id"] == room["room_id"])

    def add_asset(self, visit_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        room_id = _text(values.get("room_id"), 36)
        asset = {
            "asset_id": str(uuid.uuid4()), "name": _text(values.get("name"), 240, required=True),
            "kind": _text(values.get("kind"), 100), "manufacturer": _text(values.get("manufacturer"), 160),
            "model": _text(values.get("model"), 160), "serial": _text(values.get("serial"), 180),
            "asset_tag": _text(values.get("asset_tag"), 120), "ip": _text(values.get("ip"), 80),
            "mac": _text(values.get("mac"), 40), "room_id": room_id,
            "x": _number(values.get("x"), 0, 100), "y": _number(values.get("y"), 0, 100),
            "status": _text(values.get("status"), 80) or "observed", "description": _text(values.get("description"), 4000),
            "voltage": _text(values.get("voltage"), 80), "power_w": None if values.get("power_w") in (None, "") else _number(values.get("power_w"), 0, 100000),
            "circuit": _text(values.get("circuit"), 160),
            "circuit_limit_w": None if values.get("circuit_limit_w") in (None, "") else _number(values.get("circuit_limit_w"), 1, 100000),
            "provenance": _text(values.get("provenance"), 80) or "manual",
            "ports": self._ports(values.get("ports", [])), "credentials": {}, "created_at": utc_now(), "created_by": actor,
            "updated_at": utc_now(), "updated_by": actor,
        }
        return self._mutate(visit_id, actor, "asset_added", "asset", asset["asset_id"], lambda row: self._validate_and_append_asset(row, asset))

    @staticmethod
    def _validate_and_append_asset(record: dict[str, Any], asset: dict[str, Any]) -> None:
        if asset["room_id"] and not any(room["room_id"] == asset["room_id"] for room in record["rooms"]):
            raise ValueError("Unbekannter Raum")
        if asset["room_id"]:
            siblings = sum(1 for row in record["assets"] if row.get("room_id") == asset["room_id"])
            asset["x"] = float(12 + (siblings % 4) * 24)
            asset["y"] = float(36 + (siblings // 4) * 24)
        SiteVisitStore._append(record, "assets", asset, MAX_ASSETS)

    def update_asset(self, visit_id: str, actor: str, asset_id: str, values: dict[str, Any]) -> dict[str, Any]:
        asset_id = _identifier(asset_id)
        allowed_text = {"name": 240, "kind": 100, "manufacturer": 160, "model": 160, "serial": 180, "asset_tag": 120, "ip": 80, "mac": 40, "status": 80, "description": 4000, "voltage": 80, "circuit": 160}
        updates = {key: _text(values.get(key), limit) for key, limit in allowed_text.items() if key in values}
        if "power_w" in values:
            updates["power_w"] = None if values.get("power_w") in (None, "") else _number(values.get("power_w"), 0, 100000)
        if "circuit_limit_w" in values:
            updates["circuit_limit_w"] = None if values.get("circuit_limit_w") in (None, "") else _number(values.get("circuit_limit_w"), 1, 100000)
        if "ports" in values:
            updates["ports"] = self._ports(values.get("ports"))
        if "x" in values:
            updates["x"] = _number(values.get("x"), 0, 100)
        if "y" in values:
            updates["y"] = _number(values.get("y"), 0, 100)
        if "room_id" in values:
            room_id = _text(values.get("room_id"), 36)
            current = self._owned(visit_id, actor)
            if room_id and not any(room["room_id"] == room_id for room in current["rooms"]):
                raise ValueError("Unbekannter Raum")
            updates["room_id"] = room_id
        def apply(record: dict[str, Any]) -> None:
            asset = self._find(record, "assets", "asset_id", asset_id)
            asset.update(updates); asset.update(updated_at=utc_now(), updated_by=actor)
        return self._mutate(visit_id, actor, "asset_updated", "asset", asset_id, apply)

    def set_credentials(self, visit_id: str, actor: str, asset_id: str, username: str, password: str, note: str = "") -> None:
        asset_id = _identifier(asset_id)
        username = _text(username, 240)
        password = _text(password, 2000)
        note = _text(note, 500)
        encrypted = {"username": protect_value(username, "site-visit-credential"), "password": protect_value(password, "site-visit-credential"), "note": protect_value(note, "site-visit-credential"), "updated_at": utc_now(), "updated_by": actor}
        def apply(record: dict[str, Any]) -> None:
            self._find(record, "assets", "asset_id", asset_id)["credentials"] = encrypted
        self._mutate(visit_id, actor, "credentials_updated", "asset", asset_id, apply)

    def credentials(self, visit_id: str, actor: str, asset_id: str) -> dict[str, str]:
        record = self._owned(visit_id, actor)
        encrypted = self._find(record, "assets", "asset_id", _identifier(asset_id)).get("credentials", {})
        return {key: unprotect_value(str(encrypted.get(key, "")), "site-visit-credential") for key in ("username", "password", "note")}

    def add_finding(self, visit_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        status = str(values.get("status", "unreviewed") or "unreviewed")
        evidence = str(values.get("evidence_class", "unknown") or "unknown")
        if status not in FINDING_STATES or evidence not in EVIDENCE_CLASSES:
            raise ValueError("Ungültiger Befundstatus oder Evidenztyp")
        finding = {
            "finding_id": str(uuid.uuid4()), "title": _text(values.get("title"), 240, required=True),
            "description": _text(values.get("description"), 12000), "status": status,
            "evidence_class": evidence, "confidence": _number(values.get("confidence"), 0, 100),
            "source": _text(values.get("source"), 600), "observed_at": _text(values.get("observed_at"), 80) or utc_now(),
            "room_id": _text(values.get("room_id"), 36), "asset_id": _text(values.get("asset_id"), 36),
            "custom_values": self._custom_values(values.get("custom_values", {})),
            "include_in_report": str(values.get("include_in_report", "")) == "1" and status in {"confirmed", "corrected"},
            "attachments": [], "created_at": utc_now(), "created_by": actor,
            "updated_at": utc_now(), "updated_by": actor, "history": [],
        }
        return self._mutate(visit_id, actor, "finding_added", "finding", finding["finding_id"], lambda row: self._validate_and_append_finding(row, finding))

    def update_finding(self, visit_id: str, actor: str, finding_id: str, values: dict[str, Any]) -> dict[str, Any]:
        finding_id = _identifier(finding_id)
        allowed = {"title": (240, True), "description": (12000, False), "source": (600, False), "observed_at": (80, False), "exclusion_reason": (1000, False)}
        updates = {key: _text(values.get(key), limit, required=required) for key, (limit, required) in allowed.items() if key in values}
        if "status" in values:
            status = str(values["status"] or "")
            if status not in FINDING_STATES:
                raise ValueError("Ungültiger Befundstatus")
            updates["status"] = status
        if "evidence_class" in values:
            evidence = str(values["evidence_class"] or "")
            if evidence not in EVIDENCE_CLASSES:
                raise ValueError("Ungültige Evidenzklasse")
            updates["evidence_class"] = evidence
        if "confidence" in values:
            updates["confidence"] = _number(values.get("confidence"), 0, 100)
        if "include_in_report" in values:
            updates["include_in_report"] = str(values.get("include_in_report")) == "1"
        if "custom_values" in values:
            updates["custom_values"] = self._custom_values(values["custom_values"])
        def apply(record: dict[str, Any]) -> None:
            finding = self._find(record, "findings", "finding_id", finding_id)
            before = {key: finding.get(key) for key in updates}
            finding.update(updates)
            if finding.get("status") not in {"confirmed", "corrected"}:
                finding["include_in_report"] = False
            finding["history"].append({"at": utc_now(), "by": actor, "before": before, "after": updates})
            finding["history"] = finding["history"][-100:]
            finding.update(updated_at=utc_now(), updated_by=actor)
        return self._mutate(visit_id, actor, "finding_updated", "finding", finding_id, apply)

    def _validate_and_append_finding(self, record: dict[str, Any], finding: dict[str, Any]) -> None:
        if finding["room_id"] and not any(row["room_id"] == finding["room_id"] for row in record["rooms"]):
            raise ValueError("Unbekannter Raum")
        if finding["asset_id"] and not any(row["asset_id"] == finding["asset_id"] for row in record["assets"]):
            raise ValueError("Unbekanntes Gerät")
        self._append(record, "findings", finding, MAX_FINDINGS)

    def add_edge(self, visit_id: str, actor: str, source_id: str, target_id: str, relation: str, note: str = "") -> dict[str, Any]:
        source_id, target_id = _identifier(source_id), _identifier(target_id)
        if source_id == target_id or relation not in RELATIONS:
            raise ValueError("Ungültige Befundverknüpfung")
        edge = {"edge_id": str(uuid.uuid4()), "source_id": source_id, "target_id": target_id, "relation": relation, "note": _text(note, 1000), "created_at": utc_now(), "created_by": actor}
        def apply(record: dict[str, Any]) -> None:
            self._find(record, "findings", "finding_id", source_id); self._find(record, "findings", "finding_id", target_id)
            if any(row["source_id"] == source_id and row["target_id"] == target_id and row["relation"] == relation for row in record["finding_edges"]):
                raise ValueError("Diese Verknüpfung besteht bereits")
            if _cycle(record["finding_edges"], source_id, target_id):
                raise ValueError("Die Verknüpfung würde einen Befundzyklus erzeugen")
            self._append(record, "finding_edges", edge, MAX_EDGES)
        self._mutate(visit_id, actor, "finding_linked", "finding-edge", edge["edge_id"], apply)
        return edge

    def add_connection(self, visit_id: str, actor: str, values: dict[str, Any]) -> dict[str, Any]:
        asset_from, asset_to = _text(values.get("from_asset"), 36), _text(values.get("to_asset"), 36)
        connection = {
            "connection_id": str(uuid.uuid4()), "from_asset": asset_from, "to_asset": asset_to,
            "from_port": _text(values.get("from_port"), 120), "to_port": _text(values.get("to_port"), 120),
            "kind": _text(values.get("kind"), 80, required=True), "cable_type": _text(values.get("cable_type"), 120),
            "length_m": _number(values.get("length_m"), 0, 100000), "label": _text(values.get("label"), 240),
            "protocol": _text(values.get("protocol"), 60), "direction": _text(values.get("direction"), 60),
            "voltage": _text(values.get("voltage"), 80), "power_w": _number(values.get("power_w"), 0, 100000),
            "circuit": _text(values.get("circuit"), 160), "created_at": utc_now(), "created_by": actor,
        }
        def apply(record: dict[str, Any]) -> None:
            known = {row["asset_id"] for row in record["assets"]}
            if (asset_from and asset_from not in known) or (asset_to and asset_to not in known):
                raise ValueError("Unbekanntes Gerät in der Verbindung")
            self._append(record, "connections", connection, MAX_EDGES)
        self._mutate(visit_id, actor, "connection_added", "connection", connection["connection_id"], apply)
        return connection

    def add_custom_field(self, visit_id: str, actor: str, label: str, field_type: str, choices: list[str] | None = None) -> dict[str, Any]:
        field_type = str(field_type or "")
        if field_type not in FIELD_TYPES:
            raise ValueError("Nicht unterstützter Feldtyp")
        field = {"field_id": str(uuid.uuid4()), "label": _text(label, 80, required=True), "type": field_type, "choices": [_text(item, 80) for item in (choices or []) if str(item).strip()][:30], "created_at": utc_now(), "created_by": actor}
        def apply(record: dict[str, Any]) -> None:
            if any(row["label"].casefold() == field["label"].casefold() for row in record["custom_field_definitions"]):
                raise ValueError("Ein Feld mit diesem Namen existiert bereits")
            self._append(record, "custom_field_definitions", field, 100)
        self._mutate(visit_id, actor, "custom_field_added", "custom-field", field["field_id"], apply)
        return field

    @staticmethod
    def _ports(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, str):
            rows = []
            tokens = [token.strip() for token in value.replace(";", ",").split(",") if token.strip()]
            if len(tokens) > 64 or any(not token.isdigit() or not 1 <= int(token) <= 65535 for token in tokens):
                raise ValueError("Ports als TCP-Portnummern von 1 bis 65535 eingeben")
            for token in tokens:
                port = int(token)
                if not any(row["port"] == port for row in rows):
                    rows.append({"port": port, "protocol": "tcp", "state": "open", "source": "manuelle Erfassung"})
            return SiteVisitStore._ports(rows)
        if not isinstance(value, list):
            return []
        result = []
        for row in value[:64]:
            if not isinstance(row, dict):
                continue
            try:
                port = int(row.get("port", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                result.append({"port": port, "protocol": _text(row.get("protocol", "tcp"), 8) or "tcp", "state": _text(row.get("state", "open"), 24), "observed_at": _text(row.get("observed_at", ""), 80) or utc_now(), "source": _text(row.get("source", ""), 160)})
        return result

    @staticmethod
    def _custom_values(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key)[:80]: str(item or "").strip()[:1000] for key, item in list(value.items())[:100]}

    def add_attachment(self, visit_id: str, actor: str, filename: str, content_type: str, data: bytes, target_type: str, target_id: str, purpose: str = "evidence") -> dict[str, Any]:
        if not data or len(data) > 16 * 1024 * 1024:
            raise ValueError("Anhang muss zwischen 1 Byte und 16 MiB groß sein")
        allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv", "application/json": ".json"}
        content_type = str(content_type).split(";", 1)[0].lower()
        extension = allowed.get(content_type)
        if extension is None:
            raise ValueError("Dateityp nicht unterstützt. Zulässig sind Bild, PDF, Text, CSV und JSON.")
        signatures = {"image/jpeg": data.startswith(b"\xff\xd8\xff"), "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"), "image/webp": len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP", "application/pdf": data.startswith(b"%PDF-")}
        if content_type in signatures and not signatures[content_type]:
            raise ValueError("Der Dateiinhalt passt nicht zum angegebenen Dateityp")
        if content_type in {"text/plain", "text/csv", "application/json"}:
            if b"\0" in data:
                raise ValueError("Textdatei enthält unzulässige Binärdaten")
            try:
                decoded = data.decode("utf-8")
                if content_type == "application/json": json.loads(decoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Die Textdatei ist kein gültiger UTF-8-Inhalt") from exc
        filename = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(filename or "Anhang").name)[:180] or "Anhang"
        if target_type not in {"visit", "asset", "finding", "room"}:
            raise ValueError("Ungültige Anhangzuordnung")
        record = self._owned(visit_id, actor)
        if target_type == "asset": self._find(record, "assets", "asset_id", _identifier(target_id))
        elif target_type == "finding": self._find(record, "findings", "finding_id", _identifier(target_id))
        elif target_type == "room": self._find(record, "rooms", "room_id", _identifier(target_id))
        else: target_id = record["visit_id"]
        attachment_id = str(uuid.uuid4())
        path = self.directory / visit_id / "attachments" / f"{attachment_id}{extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data); temp.replace(path)
        purpose = purpose if purpose in {"evidence", "floorplan"} else "evidence"
        attachment = {"attachment_id": attachment_id, "filename": filename, "content_type": content_type.split(";", 1)[0].lower(), "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "target_type": target_type, "target_id": target_id, "purpose": purpose, "created_at": utc_now(), "created_by": actor, "relative_path": path.relative_to(self.directory).as_posix()}
        try:
            def apply(record: dict[str, Any]) -> None:
                self._append(record, "attachments", attachment, 1000)
                if purpose == "floorplan":
                    if attachment["content_type"] not in {"image/jpeg", "image/png", "image/webp"}:
                        raise ValueError("Grundriss muss als JPEG-, PNG- oder WebP-Bild vorliegen")
                    record["floorplan_attachment_id"] = attachment_id
            self._mutate(visit_id, actor, "attachment_added", "attachment", attachment_id, apply)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return attachment

    def attachment_path(self, visit_id: str, actor: str, attachment_id: str) -> tuple[Path, dict[str, Any]]:
        record = self._owned(visit_id, actor)
        attachment_id = _identifier(attachment_id)
        item = self._find(record, "attachments", "attachment_id", attachment_id)
        path = (self.directory / visit_id / item["relative_path"]).resolve()
        expected = (self.directory / visit_id / "attachments").resolve()
        if expected not in path.parents or not path.is_file():
            raise ValueError("Anhang nicht gefunden")
        return path, item

    def add_snapshot(self, visit_id: str, actor: str, name: str) -> dict[str, Any]:
        snapshot = {"snapshot_id": str(uuid.uuid4()), "name": _text(name, 160, required=True), "created_at": utc_now(), "created_by": actor}
        def apply(record: dict[str, Any]) -> None:
            snapshot["assets"] = [self._snapshot_asset(item) for item in record["assets"]]
            snapshot["connections"] = copy.deepcopy(record["connections"])
            snapshot["findings"] = [{key: value for key, value in row.items() if key not in {"history", "attachments"}} for row in record["findings"]]
            record.setdefault("snapshots", []).append(snapshot)
            record["snapshots"] = record["snapshots"][-100:]
        self._mutate(visit_id, actor, "snapshot_created", "snapshot", snapshot["snapshot_id"], apply)
        return snapshot

    @staticmethod
    def _snapshot_asset(asset: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in asset.items() if key not in {"credentials", "attachments"}}

    def snapshot_diff(self, visit_id: str, actor: str, older_id: str, newer_id: str) -> dict[str, Any]:
        record = self._owned(visit_id, actor)
        older = self._find(record, "snapshots", "snapshot_id", _identifier(older_id))
        newer = self._find(record, "snapshots", "snapshot_id", _identifier(newer_id))
        return self._diff(older, newer)

    @staticmethod
    def _diff(older: dict[str, Any], newer: dict[str, Any]) -> dict[str, Any]:
        result = {"added": [], "removed": [], "changed": []}
        for collection, key in (("assets", "asset_id"), ("connections", "connection_id"), ("findings", "finding_id")):
            old_rows = {row[key]: row for row in older.get(collection, [])}
            new_rows = {row[key]: row for row in newer.get(collection, [])}
            result["added"].extend({"type": collection, "item": new_rows[item_id]} for item_id in new_rows.keys() - old_rows.keys())
            result["removed"].extend({"type": collection, "item": old_rows[item_id]} for item_id in old_rows.keys() - new_rows.keys())
            result["changed"].extend({"type": collection, "before": old_rows[item_id], "after": new_rows[item_id]} for item_id in new_rows.keys() & old_rows.keys() if old_rows[item_id] != new_rows[item_id])
        for key in result: result[key].sort(key=lambda row: str(row.get("item", row.get("after", {})).get("name", row.get("item", {}).get("title", ""))).casefold())
        return result

    def record_report(self, visit_id: str, actor: str, report: dict[str, Any]) -> dict[str, Any]:
        entry = {"report_id": str(uuid.uuid4()), "created_at": utc_now(), "created_by": actor, "finding_ids": list(report.get("finding_ids", [])), "sha256": str(report.get("sha256", ""))}
        self._mutate(visit_id, actor, "report_generated", "report", entry["report_id"], lambda row: self._append(row, "reports", entry, 100))
        return entry

    @staticmethod
    def power_totals(record: dict[str, Any]) -> list[dict[str, Any]]:
        totals: dict[str, float] = {}
        unknown: dict[str, int] = {}
        limits: dict[str, float] = {}
        for asset in record.get("assets", []):
            circuit = str(asset.get("circuit", "")).strip() or "Ohne Zuordnung"
            if asset.get("power_w") is None:
                unknown[circuit] = unknown.get(circuit, 0) + 1
            else:
                totals[circuit] = totals.get(circuit, 0.0) + float(asset.get("power_w", 0) or 0)
            if asset.get("circuit_limit_w") is not None:
                limits[circuit] = max(limits.get(circuit, 0.0), float(asset["circuit_limit_w"]))
        result = []
        for circuit in sorted(set(totals) | set(unknown) | set(limits)):
            limit = limits.get(circuit)
            used = round(totals.get(circuit, 0), 2)
            result.append({"circuit": circuit, "power_w": used, "unknown_devices": unknown.get(circuit, 0), "limit_w": limit, "remaining_w": round(limit - used, 2) if limit is not None else None, "over_limit": limit is not None and used > limit})
        return result

    @staticmethod
    def _append(record: dict[str, Any], key: str, item: dict[str, Any], limit: int) -> None:
        if len(record.setdefault(key, [])) >= limit:
            raise ValueError("Maximale Anzahl für diesen Ortstermin erreicht")
        record[key].append(item)

    @staticmethod
    def _find(record: dict[str, Any], collection: str, key: str, value: str) -> dict[str, Any]:
        item = next((row for row in record.get(collection, []) if row.get(key) == value), None)
        if item is None:
            raise ValueError("Eintrag nicht gefunden")
        return item

    @staticmethod
    def _audit(record: dict[str, Any], actor: str, action: str, target_type: str, target_id: str) -> None:
        record.setdefault("audit", []).append({"at": utc_now(), "by": actor, "action": action, "target_type": target_type, "target_id": target_id})
        record["audit"] = record["audit"][-MAX_HISTORY:]

    def _mutate(self, visit_id: str, actor: str, action: str, target_type: str, target_id: str, callback) -> dict[str, Any]:
        path = self._path(visit_id)
        with exclusive_file_lock(path.with_suffix(".lock")):
            record = self._owned(visit_id, actor)
            callback(record)
            record["updated_at"] = utc_now(); record["updated_by"] = actor
            self._audit(record, actor, action, target_type, target_id)
            atomic_json_write(path, record)
        return self.get(visit_id, actor)
