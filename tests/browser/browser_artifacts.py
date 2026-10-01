"""HTML snapshot helpers for the manual browser smoke test."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return safe[:80] or "page"


def make_html_snapshot_writer(output_dir: Path):
    """Return a snapshot writer bound to the browser artifact directory."""

    html_dir = output_dir / "html"

    def write_html_snapshot(
        page,
        label: str,
        prefix: str,
        summary: dict[str, object],
        failures: list[str],
    ) -> None:
        markup = page.content()
        filename = f"{slug(prefix)}-{slug(label)}.html"
        path = html_dir / filename
        path.write_text(
            markup + ("\n" if not markup.endswith("\n") else ""),
            encoding="utf-8",
        )
        snapshots: list[dict[str, object]] = summary["html5"]["snapshots"]  # type: ignore[index,assignment]
        has_doctype = bool(
            re.match(r"^\s*<!doctype\s+html(?:\s[^>]*)?>", markup, re.IGNORECASE)
        )
        snapshots.append(
            {
                "label": label,
                "url": page.url,
                "file": str(path.relative_to(output_dir)),
                "html5_doctype": has_doctype,
            }
        )
        if not has_doctype:
            failures.append(f"{label}: HTML5-Doctype fehlt.")

    return write_html_snapshot
