"""Dispatch legacy and visual business templates without coupling routes to renderers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .business_document_generation import (
    _invoice_content_pdf,
    _merge_content_with_template,
    active_template,
    business_settings,
    render_business_pdf,
    templates,
)
from .business_template_editor import active_visual_template, visual_template, visual_template_choices
from .business_template_render import render_visual_invoice_pdf, render_visual_letter_pdf


def business_template_choices(root: Path, document_type: str) -> list[dict[str, Any]]:
    """Combine legacy backgrounds with compatible visual templates.

    An activated visual template wins the default selection for its document type
    without changing the legacy global-template setting.
    """
    visual = visual_template_choices(root, document_type)
    visual_active = any(bool(row.get("active")) for row in visual)
    legacy_source = list(templates(root))
    legacy = (
        [{**row, "active": False} for row in legacy_source]
        if visual_active
        else legacy_source
    )
    return legacy + visual


def business_template(root: Path, template_id: str, document_type: str) -> dict[str, Any]:
    """Resolve an explicit legacy or visual template and enforce its document type."""
    template_id = str(template_id or "").strip()
    if not template_id:
        visual_default = active_visual_template(root, document_type)
        return visual_default if visual_default is not None else active_template(root)
    try:
        return active_template(root, template_id)
    except ValueError as legacy_error:
        try:
            row, _layout = visual_template(root, template_id)
        except ValueError:
            raise legacy_error
        if row.get("document_type") != document_type:
            raise ValueError(f"visual template is not valid for {document_type}")
        return row


def render_invoice_with_template(
    root: Path,
    template_row: dict[str, Any],
    row: dict[str, Any],
) -> bytes:
    if template_row.get("source") == "visual":
        return render_visual_invoice_pdf(root, template_row["template_id"], row)
    return _merge_content_with_template(root, template_row, _invoice_content_pdf(row))


def render_letter_with_template(
    root: Path,
    template_row: dict[str, Any],
    *,
    recipient: str,
    subject: str,
    body: str,
    cover: bool = False,
) -> bytes:
    if template_row.get("source") == "visual":
        return render_visual_letter_pdf(
            root,
            template_row["template_id"],
            business=business_settings(root),
            recipient=recipient,
            subject=subject,
            body=body,
        )
    return render_business_pdf(
        root,
        template_row,
        recipient=recipient,
        subject=subject,
        markdown=body,
        cover=cover,
    )
