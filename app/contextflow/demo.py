"""Synthetic demo seeding for ContextFlow (Batch 3).

Builds one complete follow-up story on real stores under an isolated root:
contact, mail case, completed visit with two open findings, and a confirmed
link whose completion lies five days back. All data is synthetic; never run
this against a production root. Uses only public store APIs (invoice rows
are stored JSON, same documented shape as the readers expect).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..business_document_generation import INVOICE_DIR
from ..calendar_store import CalendarStore
from ..contact_store import ContactStore
from ..document_store import CONTROL_DIR, DocumentStore
from ..mail_case_store import MailCaseStore
from ..preview_service import PreviewService
from ..site_visit_store import SiteVisitStore
from ..todo_store import TodoStore
from .case_links import CaseLinkStore

BERLIN = ZoneInfo("Europe/Berlin")

DEMO_MARKER = ".contextflow-demo.json"
DEMO_TOOL_TAG = "contextflow_demo"
DEMO_DOCUMENTS_MARKER = "contextflow-demo-documents.json"
DEMO_DOCUMENT_TAG = "contextflow-demo-datei"


class DemoRootError(ValueError):
    """A directory is not a verified synthetic demo root, or reset refused."""


def _is_protected_root(path: Path) -> bool:
    """Never wipe a filesystem root or the repository itself, marker or not."""
    try:
        resolved = path.resolve()
    except OSError:
        return True
    if str(resolved) == resolved.anchor:
        return True
    try:
        repo = Path(__file__).resolve().parent.parent.parent
    except OSError:
        return True
    return resolved == repo


def _link_kind(path: Path) -> str | None:
    """Classify links/mounts that a recursive delete must never cross."""
    try:
        if path.is_symlink():
            return "symlink"
    except OSError:
        return "unreadable"
    try:
        if os.path.ismount(path):
            return "mount"
    except OSError:
        return "unreadable"
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return "junction"
        except OSError:
            return "unreadable"
    return None


def is_verified_demo_root(path: Path) -> bool:
    """True only for a directory carrying our own synthetic-demo marker.

    The marker must be a plain file (never a link) whose JSON content marks
    it as synthetic and names this demo tool. Anything else fails closed.
    """
    try:
        if not path.is_dir() or path.is_symlink():
            return False
        marker = path / DEMO_MARKER
        if not marker.is_file() or marker.is_symlink():
            return False
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (
        isinstance(data, dict)
        and data.get("synthetic") is True
        and data.get("tool") == DEMO_TOOL_TAG
    )


def _rmtree(path: Path) -> None:
    """Delete a directory tree including read-only files.

    Plain ``shutil.rmtree`` fails on Windows read-only files such as Git
    objects; the retry makes them writable first. ``onexc`` (3.12+) and
    ``onerror`` (3.11) spellings are both covered.
    """
    def _fix_readonly(func, p, _exc):
        os.chmod(p, os.stat(p).st_mode | stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_fix_readonly)
    else:
        shutil.rmtree(path, onerror=_fix_readonly)


def reset_demo_root(path: Path) -> Path:
    """Empty a verified synthetic demo root in place and rewrite its marker.

    Refuses anything without a verified marker, protected roots (filesystem
    root, repository root), and roots containing junctions, mounts or
    unreadable entries — checked BEFORE deleting anything, so a refusal
    never leaves partial state. Symlinks are removed, never followed.
    """
    if _is_protected_root(path):
        raise DemoRootError(f"refusing to reset protected root: {path}")
    if not is_verified_demo_root(path):
        raise DemoRootError(
            f"refusing reset without verified demo marker: {path} "
            "(pass an empty dir or a previous demo root)"
        )
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise DemoRootError(f"demo root is not listable: {path} ({exc})") from exc
    for child in children:
        kind = _link_kind(child)
        if kind in ("junction", "mount", "unreadable"):
            raise DemoRootError(
                f"refusing reset: {kind} inside demo root: {child} "
                "(remove it manually)"
            )
    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                _rmtree(child)
            else:
                # Plain files and symlinks are removed, never followed.
                if not child.is_symlink():
                    try:
                        os.chmod(child, os.stat(child).st_mode | stat.S_IWRITE)
                    except OSError:
                        pass
                child.unlink()
        except OSError as exc:
            raise DemoRootError(f"reset incomplete at {child} ({exc})") from exc
    (path / DEMO_MARKER).write_text(
        json.dumps({"synthetic": True, "tool": DEMO_TOOL_TAG}, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def seed_demo(root: str | Path, actor: str = "demo") -> dict[str, Any]:
    """Seed the synthetic P0 story; return ids and the completion date."""
    root = Path(root).expanduser()
    completed_on = (datetime.now(BERLIN).date() - timedelta(days=5)).isoformat()

    contact = ContactStore(root).upsert(
        {"display_name": "Musterkunde Demo (synthetisch)", "email": "demo@example.test"},
        actor,
    )
    contact_id = str(contact["contact_id"])
    case_id = MailCaseStore(root).create_case(
        actor,
        "Drucker defekt — Anfahrt und Befund (synthetisch)",
        "demo@example.test",
        "demo-mail-0001",
    )
    visits = SiteVisitStore(root)
    visit = visits.create(
        actor,
        {
            "title": "Ortstermin Drucker (synthetisch)",
            "customer": "Musterkunde Demo (synthetisch)",
            "site": "Werkstatt (synthetisch)",
            "address": "Musterweg 1, 12345 Musterstadt",
            "visit_date": completed_on + "T10:00",
            "notes": "Anfahrt und Befund (synthetisch)",
        },
    )
    visit_id = str(visit["visit_id"])
    # Note: add_asset returns the full visit record (production API), not the
    # asset, so the asset id is read back from the record's asset list.
    after_asset = visits.add_asset(
        visit_id,
        actor,
        {
            "name": "Buchscanner (synthetisch)",
            "kind": "Buchscanner",
            "description": "Einzug transportiert nicht (synthetisch)",
        },
    )
    scanner_id = str(after_asset["assets"][-1]["asset_id"])
    visits.add_finding(
        visit_id,
        actor,
        {
            "title": "Papierstau, Ersatzteil offen (synthetisch)",
            "description": "Papierstau im Einzug des Buchscanners (synthetisch)",
            "observed_at": completed_on + "T10:00",
            "asset_id": scanner_id,
        },
    )
    visits.add_finding(
        visit_id,
        actor,
        {
            "title": "Firmware veraltet (synthetisch)",
            "description": "Update steht aus (synthetisch)",
            "observed_at": completed_on + "T11:00",
        },
    )
    visits.update(visit_id, actor, {"status": "completed"})

    rows = [
        {
            "invoice_id": "DEMO-2026-001",
            "contact_id": contact_id,
            "invoice_number": "RE-2026-001",
            "status": "draft",
            "issue_date": completed_on,
            "currency": "EUR",
        }
    ]
    directory = root / CONTROL_DIR / INVOICE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    for row in rows:
        path = directory / f"{row['invoice_id']}.json"
        path.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    CalendarStore(root).add(
        "Rückfrage Kunde (synthetisch)",
        "Kundenrückfrage (synthetisch)",
        completed_on + "T10:00",
        completed_on + "T10:30",
        contact_id,
        actor,
    )
    TodoStore(root).add("Alte Aufgabe (synthetisch)", actor, {"contact_id": contact_id})

    link = CaseLinkStore(root).confirm(
        actor,
        mail_case_id=case_id,
        contact_id=contact_id,
        visit_id=visit_id,
        invoice_id="DEMO-2026-001",
        completed_on=completed_on,
        now=datetime.now(BERLIN),
    )
    documents = seed_demo_documents(root, actor)
    return {
        "actor": actor,
        "contact_id": contact_id,
        "case_id": case_id,
        "visit_id": visit_id,
        "asset_id": scanner_id,
        "device_name": "Buchscanner (synthetisch)",
        "link_id": str(link["link_id"]),
        "completed_on": completed_on,
        "invoice_id": "DEMO-2026-001",
        "demo_documents": documents,
        "synthetic": True,
    }


def _demo_png_bytes() -> bytes:
    """Render one small synthetic photo (labeled shapes, no real motif)."""
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 420), "#f1f5f9")
    draw = ImageDraw.Draw(image)
    draw.rectangle((60, 60, 580, 140), fill="#0f766e")
    draw.rectangle((60, 170, 360, 360), fill="#e2e8f0", outline="#334155", width=4)
    draw.rectangle((400, 170, 580, 360), fill="#fef3c7", outline="#92400e", width=4)
    draw.text((80, 85), "DEMO-FOTO (synthetisch)", fill="#ffffff")
    draw.text((80, 200), "Rechnung 2024", fill="#0f172a")
    draw.text((420, 200), "Bank", fill="#0f172a")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _demo_pdf_bytes() -> bytes:
    """Build one minimal valid one-page PDF (synthetic demo text)."""
    body = "Demo PDF synthetisch - Rechnung 2024"
    content = (
        b"BT /F1 18 Tf 40 150 Td (" + body.encode("latin-1") + b") Tj ET"
    )
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode("ascii") + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_at = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode("ascii") + b"\n"
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += str(offset).encode("ascii").zfill(10) + b" 00000 n \n"
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode("ascii")
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_at).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(out)


def _demo_source_files(staging: Path) -> list[Path]:
    """Write synthetic demo sources outside the document root."""
    files = {
        "demo-foto.png": _demo_png_bytes(),
        "demo-notizen.txt": (
            "Rechnung über 240 Euro aus dem Jahr 2024.\n"
            "Musterkunde Demo (synthetisch).\n"
        ).encode("utf-8"),
        "demo-bericht.md": (
            "# Demo-Bericht (synthetisch)\n\n"
            "Der Buchscanner wurde geprüft. Die Bank im Wartebereich "
            "bleibt bestehen.\n"
        ).encode("utf-8"),
        "demo-seite.pdf": _demo_pdf_bytes(),
    }
    paths = []
    for name, data in files.items():
        path = staging / name
        path.write_bytes(data)
        paths.append(path)
    return paths


def seed_demo_documents(root: str | Path, actor: str = "demo") -> dict[str, Any]:
    """Import synthetic demo files once and pre-generate their previews.

    The demo server runs no index worker, so previews are generated
    eagerly at seed time; without this, image thumbnails stay missing
    in the demo. Seeding is idempotent via a marker file: restarts
    without --fresh reuse the imported documents instead of
    duplicating them.
    """
    root = Path(root).expanduser()
    store = DocumentStore(root)
    marker = root / CONTROL_DIR / DEMO_DOCUMENTS_MARKER
    if marker.is_file() and not marker.is_symlink():
        try:
            cached = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        entries = cached.get("documents", []) if isinstance(cached, dict) else []
        if entries and all(
            isinstance(entry, dict) and entry.get("document_id") for entry in entries
        ):
            try:
                for entry in entries:
                    store.get_document(str(entry["document_id"]))
            except ValueError:
                pass
            else:
                cached["synthetic"] = True
                return cached
    staging = Path(tempfile.mkdtemp(prefix="contextflow-demo-docs-"))
    try:
        sources = _demo_source_files(staging)
        service = PreviewService(root)
        entries: list[dict[str, Any]] = []
        previews: dict[str, str] = {}
        for source in sources:
            target = store.import_file(source, actor)
            metadata = store.get_document(target)
            document_id = str(metadata["document_id"])
            store.set_tags(document_id, [DEMO_DOCUMENT_TAG], actor)
            preview = service.generate(target, metadata)
            store.set_preview_metadata(document_id, preview)
            entries.append(
                {
                    "name": source.name,
                    "document_id": document_id,
                    "preview": str(preview.get("status", "")),
                    "converter": str(preview.get("converter", "")),
                }
            )
            previews[document_id] = str(preview.get("status", ""))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    result = {"documents": entries, "previews": previews, "synthetic": True}
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return result
