"""Storage path helpers for business documents."""
from __future__ import annotations

from pathlib import Path

from .business_document_common import INVOICE_DIR, LINK_FILE, TEMPLATE_DIR
from .document_store import CONTROL_DIR


def template_directory(root: Path) -> Path:
    path = root / CONTROL_DIR / TEMPLATE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def template_index(root: Path) -> Path:
    return template_directory(root) / "templates.json"


def contact_link_path(root: Path) -> Path:
    path = root / CONTROL_DIR / LINK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def invoice_store_path(root: Path, invoice_id: str) -> Path:
    path = root / CONTROL_DIR / INVOICE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{invoice_id}.json"
