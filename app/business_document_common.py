"""Shared constants and small helpers for business-document modules.

Keep this module dependency-light so invoice, template, export and HTTP modules can
reuse the same storage conventions without importing the full Flask blueprint.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from reportlab.lib.units import mm

TEMPLATE_DIR = "business-templates"
LINK_FILE = "contact-document-links.json"
SETTINGS_FILE = "business-document-settings.json"
INVOICE_DIR = "invoices"
INVOICE_SEQUENCE = "invoice-sequence.json"
CREDIT_NOTE_SEQUENCE = "credit-note-sequence.json"
ZUGFERD_FILENAMES = {"factur-x.xml", "zugferd-invoice.xml", "zugferd.xml"}
MONEY = Decimal("0.01")
QTY = Decimal("0.001")
SUPPORTED_TEMPLATE_SUFFIXES = {".pdf", ".odt", ".ott", ".doc", ".docx", ".rtf", ".odp", ".ppt", ".pptx"}
DIN_LEFT_MARGIN = 25 * mm
DIN_RIGHT_MARGIN = 20 * mm
DIN_TOP_RESERVED = 55 * mm
DIN_BOTTOM_RESERVED = 40 * mm
PAGE_NUMBER_Y = 10 * mm
PAGE_NUMBER_CLEAR_BOTTOM = 6 * mm
PAGE_NUMBER_CLEAR_TOP = 14 * mm
WRITE_OFF_REASONS = {
    "customer_deceased",
    "insolvency",
    "unknown_address",
    "collection_uneconomical",
    "goodwill",
    "other",
}


def safe_filename(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return clean[:100] or "document"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
