"""Local audio output hints (no network, stores nothing).

Flags registered outputs that are offline and an unhealthy worker
state. Proposals are read-only with justifications and never change
data. Only output names, node ids, states, and health messages
already shown on the page are used, never audio contents.
"""

from __future__ import annotations

from typing import Any

MAX_ITEMS = 5


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


def suggest_output_review(
    outputs: list[Any] | None,
    service: Any,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose audio output follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for output in outputs or []:
        if output is None or isinstance(output, (str, bytes)):
            continue
        if _on(_get(output, "online")):
            continue
        name = str(_get(output, "name") or "").strip()
        node = str(_get(output, "node_id") or "").strip()
        label = name or node or "ohne Namen"
        ranked.append(
            (
                (0, label.casefold(), ""),
                {
                    "reason": (
                        f"Audio-Ausgang „{label}“ ist offline — "
                        "Verbindung prüfen oder Ausgang entfernen?"
                    ),
                },
            )
        )
    health = _get(service, "health") or {}
    has_outputs = any(
        o is not None and not isinstance(o, (str, bytes)) for o in outputs or []
    )
    if has_outputs and not _on(_get(health, "ok")):
        message = str(_get(health, "message") or "").strip()
        suffix = f": {message}" if message else ""
        ranked.append(
            (
                (1, "", ""),
                {
                    "reason": (
                        "Audio-Worker meldet ein Problem"
                        f"{suffix} — Scan erneut anstoßen?"
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
