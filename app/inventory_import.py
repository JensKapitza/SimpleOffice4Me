"""CSV bulk import with mapping, preview, duplicate checks and error reports."""
from __future__ import annotations

import csv
import io
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .document_store import CONTROL_DIR, atomic_json_write, utc_now
from .file_lock import exclusive_file_lock

MAX_FILE_BYTES = 1_000_000
MAX_ROWS = 500
STAGE_TTL_SECONDS = 30 * 60

# Fixed importable columns with accepted header aliases (lowercase, stripped).
COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("name", "titel", "title", "bezeichnung"),
    "type": ("type", "typ", "objekttyp"),
    "identifier": ("identifier", "barcode", "ean", "kennung", "isbn"),
    "location": ("location", "standort", "ort", "lagerort"),
    "serial_number": ("serial_number", "seriennummer", "serial"),
    "manufacturer": ("manufacturer", "hersteller"),
    "model": ("model", "modell"),
    "nfc_id": ("nfc_id", "nfc"),
    "tags": ("tags", "stichworte", "schlagworte"),
    "description": ("description", "beschreibung"),
}
_ALIAS_TO_COLUMN = {alias: column for column, aliases in COLUMNS.items() for alias in aliases}


def _clean(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def sniff_delimiter(header_line: str) -> str:
    """Pick comma or semicolon by header occurrence; default to semicolon ties."""
    if header_line.count(";") >= header_line.count(",") and ";" in header_line:
        return ";"
    if "," in header_line:
        return ","
    raise ValueError("Kopfzeile ohne Trennzeichen (Komma oder Semikolon erwartet)")


def parse_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Decode, map headers and return (unmapped_headers, rows)."""
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("Datei fehlt oder ist größer als 1 MB")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Datei ist kein gültiges UTF-8") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Datei enthält keine Datenzeilen")
    delimiter = sniff_delimiter(lines[0])
    reader = csv.DictReader(io.StringIO("\n".join(lines)), delimiter=delimiter)
    headers = [(header or "").strip() for header in (reader.fieldnames or [])]
    mapping = [_ALIAS_TO_COLUMN.get(header.casefold(), "") for header in headers]
    if "name" not in mapping:
        raise ValueError("Pflichtspalte für den Namen fehlt (name/titel/bezeichnung)")
    rows: list[dict[str, str]] = []
    for record in reader:
        row: dict[str, str] = {}
        for header, column in zip(headers, mapping):
            if column:
                row[column] = _clean((record or {}).get(header, ""), 8000)
        if any(row.values()):
            rows.append(row)
        if len(rows) > MAX_ROWS:
            raise ValueError(f"Mehr als {MAX_ROWS} Datenzeilen werden nicht importiert")
    if not rows:
        raise ValueError("Datei enthält keine Datenzeilen")
    unmapped = sorted({header for header, column in zip(headers, mapping) if not column and header})
    return unmapped, rows


def validate_row(row: dict[str, str], seen_identifiers: set[str]) -> tuple[dict[str, Any] | None, str]:
    """Return (values, problem); problem is empty for importable rows."""
    name = _clean(row.get("name", ""), 300)
    if not name:
        return None, "Name fehlt"
    object_type = _clean(row.get("type", ""), 80) or "object"
    identifier = _clean(row.get("identifier", ""), 120)
    if identifier and identifier.casefold() in seen_identifiers:
        return None, "doppelt in Datei"
    values = {
        "name": name, "type": object_type, "status": "active",
        "description": _clean(row.get("description", ""), 8000),
        "identifier": identifier,
        "location": _clean(row.get("location", ""), 300),
        "tags": ",".join([tag for tag in (
            ["Inventar", "CSV-Import"] + _clean(row.get("tags", ""), 500).split(",")) if tag.strip()]),
        "fields": {
            key: _clean(row.get(key, ""), 600)
            for key in ("serial_number", "manufacturer", "model", "nfc_id")
            if _clean(row.get(key, ""), 600)
        },
    }
    return values, ""


class ImportStageStore:
    """Short-lived server-side staging for previewed CSV rows."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / CONTROL_DIR / "inventory" / "import-staging"
        self.lock_path = self.directory / ".import-stage.lock"

    def _prune(self) -> None:
        if not self.directory.is_dir():
            return
        cutoff = time.time() - STAGE_TTL_SECONDS
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def save(self, rows: list[dict[str, Any]], actor: str) -> str:
        token = uuid.uuid4().hex
        self.directory.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.lock_path):
            self._prune()
            atomic_json_write(self.directory / f"{token}.json", {
                "token": token, "actor": actor, "created_at": utc_now(),
                "rows": rows,
            })
        return token

    def take(self, token: str, actor: str) -> list[dict[str, Any]]:
        """Fetch and consume one stage; tokens are single-use and actor-bound."""
        clean = str(token or "").strip()
        if not clean:
            raise ValueError("Importkennung fehlt")
        path = self.directory / f"{clean}.json"
        if ".." in clean or "/" in clean or "\\" in clean:
            raise ValueError("Importkennung ist ungültig")
        with exclusive_file_lock(self.lock_path):
            try:
                expired = time.time() - path.stat().st_mtime > STAGE_TTL_SECONDS
                stage = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ValueError("Importkennung ist ungültig oder abgelaufen") from None
            try:
                path.unlink()
            except OSError:
                pass
            if expired:
                raise ValueError("Importkennung ist abgelaufen")
        if not isinstance(stage, dict) or stage.get("actor") != actor:
            raise ValueError("Importkennung gehört zu einer anderen Sitzung")
        return stage.get("rows", []) if isinstance(stage.get("rows"), list) else []
