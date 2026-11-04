"""Local health attention hints (no network, stores nothing).

Flags registered health checks that are not healthy so required and
optional components can be inspected through the normal health routes.
Proposals are read-only with justifications and never change data.
Only check names, states and short messages are shown.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


def _field(item: Any, name: str) -> Any:
    """Read a field from dicts or objects (no I/O)."""
    if isinstance(item, dict):
        return item.get(name, "")
    return getattr(item, name, "")


def _text(value: Any) -> str:
    """Render a short one-line text (no I/O)."""
    return " ".join(str(value or "").split())[:160]


def suggest_unhealthy_checks(
    checks: list[Any] | None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose non-healthy checks, required first (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for check in checks or []:
        if isinstance(check, (str, bytes)) or check is None:
            continue
        status = str(_field(check, "status") or "").strip().casefold()
        if not status or status == "healthy":
            continue
        required = bool(_field(check, "required"))
        name = _text(_field(check, "name")) or "ohne Namen"
        message = _text(_field(check, "message"))
        detail = f" — {message}" if message else ""
        scope = "Kernprüfung" if required else "optionale Prüfung"
        ranked.append(
            (
                (0 if required else 1, status, name.casefold()),
                {
                    "name": name,
                    "status": status,
                    "required": required,
                    "reason": (
                        f"„{name}“ ({scope}) meldet „{status}“{detail} — "
                        "Details prüfen."
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
