"""Managed invoice loading with permission guard for invoice routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import abort

from .business_document_generation import invoice
from .contact_store import ContactStore


def _managed_invoice(root: Path, invoice_id: str, actor: str) -> dict[str, Any]:
    try:
        row = invoice(root, invoice_id)
        contacts = ContactStore(root)
        contact = contacts.get(str(row.get("contact_id", "")), actor)
    except ValueError:
        abort(404)
    if not contacts.can_manage_contact(contact, actor):
        abort(403)
    return row
