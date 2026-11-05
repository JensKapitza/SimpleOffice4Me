"""Local gateway care hints (no network, stores nothing).

Flags a degraded or failed gateway, a missing gateway key, and a
disabled autostart while the service is enabled. Proposals are
read-only with justifications and never change data. Only states and
booleans already shown on the page are used, never keys or URLs.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 4


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


def suggest_gateway_review(
    service: Any,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose gateway follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    if service is None or isinstance(service, (str, bytes)):
        return []
    hints: list[dict[str, Any]] = []
    state = str(_get(service, "state") or "").strip().lower()
    settings = _get(service, "settings") or {}
    if state in {"degraded", "failed"}:
        code = str(_get(_get(service, "health") or {}, "code") or "").strip()
        suffix = f" (Code: {code})" if code and code != "unknown" else ""
        hints.append(
            {
                "reason": (
                    f"Gateway ist {state}{suffix} — Key, Modell und "
                    "Netzwerk prüfen, dann Verbindungstest anstoßen."
                ),
            }
        )
    if _on(_get(settings, "enabled")):
        if not _on(_get(settings, "api_key_configured")) and not _on(
            _get(settings, "provider_key_configured")
        ):
            hints.append(
                {
                    "reason": (
                        "Kein Gateway-Key hinterlegt — Key eintragen, "
                        "sonst bleiben Aufrufe erfolglos."
                    ),
                }
            )
        if not _on(_get(settings, "autostart")):
            hints.append(
                {
                    "reason": (
                        "Autostart ist aus, obwohl der Dienst aktiviert "
                        "ist — nach Serverneustart manuell starten oder "
                        "Autostart einschalten?"
                    ),
                }
            )
    return hints[:limit]
