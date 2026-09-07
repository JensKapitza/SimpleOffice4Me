"""Persistent library workflow state.

The library module keeps only library-specific metadata here. Books and other
physical items remain canonical ObjectStore records. Structured shelf
assignments, printer preferences and a short human-readable activity trail live
under .simpleoffice-meta/library.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from ..document_store import CONTROL_DIR, atomic_json_write, utc_now
from ..file_lock import exclusive_file_lock

_LOCATION_CODE_RE = re.compile(r"[A-Z0-9][A-Z0-9._:/-]{1,79}")


def default_printer_settings() -> dict[str, Any]:
    return {
        "uri": "",
        "model": "QL-820NWB",
        "label": "62",
        "font_name": "DejaVuSans",
        "font_size": 56,
        "color": "black",
        "align": "center",
        "bold": False,
        "cut": True,
    }


class LibraryStore:
    """Small locked JSON store for shelves, assignments and print preferences."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "library"
        self.state_path = self.directory / "state.json"
        self.lock_path = self.directory / ".library-write.lock"

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict):
            value = {}
        value.setdefault("version", 1)
        value.setdefault("next_location", 1)
        value.setdefault("locations", [])
        value.setdefault("assignments", {})
        value.setdefault("printer", default_printer_settings())
        value.setdefault("events", [])
        if not isinstance(value["locations"], list):
            value["locations"] = []
        if not isinstance(value["assignments"], dict):
            value["assignments"] = {}
        if not isinstance(value["printer"], dict):
            value["printer"] = default_printer_settings()
        if not isinstance(value["events"], list):
            value["events"] = []
        merged = default_printer_settings()
        merged.update(value["printer"])
        value["printer"] = merged
        return value

    def _write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        state["version"] = 1
        atomic_json_write(self.state_path, state)

    @staticmethod
    def _event(state: dict[str, Any], kind: str, actor: str, detail: dict[str, Any]) -> None:
        events = state.setdefault("events", [])
        events.append({
            "event_id": str(uuid.uuid4()),
            "kind": str(kind),
            "at": utc_now(),
            "actor": str(actor or ""),
            "detail": detail,
        })
        if len(events) > 500:
            del events[:-500]

    def locations(self) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self._read()["locations"] if isinstance(row, dict)]
        return sorted(rows, key=lambda row: (str(row.get("name", "")).casefold(), str(row.get("code", ""))))

    def location(self, location_id: str) -> dict[str, Any]:
        wanted = str(location_id or "").strip()
        row = next((item for item in self.locations() if str(item.get("location_id", "")) == wanted), None)
        if row is None:
            raise ValueError("Unbekannter Bibliotheksstandort")
        return row

    def find_location(self, value: str) -> dict[str, Any] | None:
        needle = str(value or "").strip().casefold()
        if not needle:
            return None
        for row in self.locations():
            if needle in {
                str(row.get("location_id", "")).casefold(),
                str(row.get("code", "")).casefold(),
                str(row.get("name", "")).casefold(),
            }:
                return row
        return None

    def location_path(self, location_id: str) -> str:
        rows = {str(row.get("location_id", "")): row for row in self.locations()}
        current = str(location_id or "")
        names: list[str] = []
        seen: set[str] = set()
        while current and current in rows and current not in seen and len(names) < 12:
            seen.add(current)
            row = rows[current]
            names.append(str(row.get("name", "") or row.get("code", "")))
            current = str(row.get("parent_id", ""))
        return " / ".join(reversed([name for name in names if name]))

    def create_location(self, name: str, actor: str, *, parent_id: str = "", code: str = "") -> dict[str, Any]:
        clean_name = " ".join(str(name or "").split()).strip()[:200]
        if not clean_name:
            raise ValueError("Name des Regals/Standorts fehlt")
        clean_parent = str(parent_id or "").strip()
        requested_code = str(code or "").strip().upper()
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            locations = state["locations"]
            if clean_parent and not any(str(row.get("location_id", "")) == clean_parent for row in locations if isinstance(row, dict)):
                raise ValueError("Übergeordneter Standort ist unbekannt")
            if requested_code:
                if not _LOCATION_CODE_RE.fullmatch(requested_code):
                    raise ValueError("Standortcode darf nur Buchstaben, Zahlen sowie . _ : / - enthalten")
                clean_code = requested_code
            else:
                sequence = max(1, int(state.get("next_location", 1) or 1))
                clean_code = f"LIB-L{sequence:04d}"
                state["next_location"] = sequence + 1
            if any(str(row.get("code", "")).casefold() == clean_code.casefold() for row in locations if isinstance(row, dict)):
                raise ValueError("Dieser Standortcode ist bereits vergeben")
            now = utc_now()
            row = {
                "location_id": str(uuid.uuid4()),
                "code": clean_code,
                "name": clean_name,
                "parent_id": clean_parent,
                "created_at": now,
                "created_by": actor,
                "updated_at": now,
                "updated_by": actor,
            }
            locations.append(row)
            self._event(state, "location_created", actor, {"location_id": row["location_id"], "code": clean_code, "name": clean_name})
            self._write(state)
            return dict(row)

    def assignment(self, object_id: str) -> dict[str, Any] | None:
        value = self._read()["assignments"].get(str(object_id or ""))
        return dict(value) if isinstance(value, dict) else None

    def assign(self, object_id: str, location_id: str, actor: str, *, scanned: str = "") -> dict[str, Any]:
        clean_object = str(object_id or "").strip()
        if not clean_object:
            raise ValueError("Objekt-ID fehlt")
        location = self.location(location_id)
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            now = utc_now()
            assignment = {
                "object_id": clean_object,
                "location_id": location["location_id"],
                "location_code": location["code"],
                "assigned_at": now,
                "assigned_by": actor,
                "scanned": str(scanned or "")[:160],
            }
            state["assignments"][clean_object] = assignment
            self._event(state, "book_assigned", actor, assignment)
            self._write(state)
            return dict(assignment)

    def contents(self, location_id: str) -> list[str]:
        wanted = str(location_id or "").strip()
        return [
            object_id for object_id, value in self._read()["assignments"].items()
            if isinstance(value, dict) and str(value.get("location_id", "")) == wanted
        ]

    def printer_settings(self) -> dict[str, Any]:
        return dict(self._read()["printer"])

    def update_printer_settings(self, values: dict[str, Any], actor: str) -> dict[str, Any]:
        current = self.printer_settings()
        uri = str(values.get("uri", current["uri"]) or "").strip()[:300]
        model = str(values.get("model", current["model"]) or "").strip().upper()[:40]
        label = str(values.get("label", current["label"]) or "").strip().lower()[:30]
        font_name = " ".join(str(values.get("font_name", current["font_name"]) or "").split()).strip()[:80]
        try:
            font_size = int(values.get("font_size", current["font_size"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("Schriftgröße ist ungültig") from exc
        if not 8 <= font_size <= 300:
            raise ValueError("Schriftgröße muss zwischen 8 und 300 liegen")
        color = str(values.get("color", current["color"]) or "black").strip().lower()
        if color not in {"black", "red"}:
            raise ValueError("Druckfarbe muss schwarz oder rot sein")
        align = str(values.get("align", current["align"]) or "center").strip().lower()
        if align not in {"left", "center", "right"}:
            raise ValueError("Unbekannte Textausrichtung")
        if not model:
            raise ValueError("Druckermodell fehlt")
        if not label:
            raise ValueError("Etikettengröße fehlt")
        updated = {
            "uri": uri,
            "model": model,
            "label": label,
            "font_name": font_name or "DejaVuSans",
            "font_size": font_size,
            "color": color,
            "align": align,
            "bold": bool(values.get("bold", current["bold"])),
            "cut": bool(values.get("cut", current["cut"])),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            state["printer"] = updated
            self._event(state, "printer_settings_updated", actor, {key: value for key, value in updated.items() if key != "uri"} | {"uri": uri})
            self._write(state)
        return dict(updated)

    def record_print(self, actor: str, *, kind: str, status: str, summary: str, byte_count: int = 0, error: str = "") -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            state = self._read()
            self._event(state, "print_job", actor, {
                "kind": str(kind)[:40],
                "status": str(status)[:40],
                "summary": " ".join(str(summary or "").split())[:240],
                "bytes": max(0, int(byte_count or 0)),
                "error": " ".join(str(error or "").split())[:500],
            })
            self._write(state)

    def recent_events(self, limit: int = 40) -> list[dict[str, Any]]:
        events = [dict(row) for row in self._read()["events"] if isinstance(row, dict)]
        return list(reversed(events[-max(1, min(int(limit), 100)):]))
