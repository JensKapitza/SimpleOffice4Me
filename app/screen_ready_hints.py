"""Local screen sharing hints (no network, stores nothing).

Flags missing native sender tools on the current platform so users
pick a working path up front. Proposals are read-only with
justifications and never change data. Only platform names and
tool-availability booleans already shown on the page are used,
never paths or binaries.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 3


def _get(row: Any, name: str) -> Any:
    """Read a field from dicts, rows, or objects (no I/O)."""
    if isinstance(row, dict):
        return row.get(name)
    if hasattr(row, "_asdict"):
        try:
            return row._asdict().get(name)
        except (TypeError, AttributeError):
            return None
    if hasattr(row, "keys"):
        try:
            return row[name]
        except (KeyError, TypeError, IndexError):
            return None
    return getattr(row, name, None)


def _on(value: Any) -> bool:
    """Interpret a flag strictly as opt-in."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip() in {"1", "True", "true", "yes", "on"}


def suggest_screen_review(
    status: Any,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose screen sharing follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    if status is None or isinstance(status, (str, bytes)):
        return []
    hints: list[dict[str, Any]] = []
    platform = str(_get(status, "platform") or "").strip().lower()
    windows = _get(status, "windows") or {}
    linux = _get(status, "linux") or {}
    if platform == "windows":
        state = str(_get(windows, "wireless_display") or "").strip().lower()
        if state == "not-installed":
            hints.append(
                {
                    "reason": (
                        "Drahtlose Anzeige nicht installiert — über die "
                        "Windows-Features nachinstallieren oder "
                        "Browser-Capture nutzen."
                    ),
                }
            )
    elif platform == "linux":
        if not (
            _on(_get(linux, "gnome_network_displays"))
            or _on(_get(linux, "gnome_network_displays_flatpak"))
            or _on(_get(linux, "miracle_sinkctl"))
        ):
            hints.append(
                {
                    "reason": (
                        "Kein natives Linux-Sendeprogramm gefunden — "
                        "Browser-Capture nutzen oder GNOME Network "
                        "Displays installieren."
                    ),
                }
            )
    return hints[:limit]
