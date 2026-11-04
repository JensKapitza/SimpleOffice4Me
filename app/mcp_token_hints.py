"""Local MCP token hints (no network, stores nothing).

Flags tokens that expire soon or already expired without revocation,
plus active write tokens worth a permission review. Proposals are
read-only with justifications and never change data. Only names and
expiry states are shown, never secrets (digests stay in the store).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

MAX_ITEMS = 5
EXPIRY_WINDOW_DAYS = 14


def _day(raw: object) -> str:
    """Return a YYYY-MM-DD key; empty when unparseable."""
    text = str(raw or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


def _row_get(row: Any, name: str) -> str:
    """Read a field from dicts, rows, or objects (no I/O)."""
    if isinstance(row, dict):
        return str(row.get(name, "") or "")
    if hasattr(row, "_asdict"):
        try:
            return str(row._asdict().get(name, "") or "")
        except (TypeError, AttributeError):
            return ""
    if hasattr(row, "keys"):
        try:
            return str(row[name] or "")
        except (KeyError, TypeError, IndexError):
            return ""
    return str(getattr(row, name, "") or "")


def suggest_token_review(
    tokens: list[Any],
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose expiring or over-permissioned tokens (never writes)."""
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if not _day(current):
        current = date.today().isoformat()
    cutoff = (
        date.fromisoformat(current) + timedelta(days=EXPIRY_WINDOW_DAYS)
    ).isoformat()
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    for token in tokens or []:
        if token is None or isinstance(token, (str, bytes)):
            continue
        if _row_get(token, "revoked_at"):
            continue
        name = _row_get(token, "name").strip() or "ohne Namen"
        expires = _day(_row_get(token, "expires_at"))
        write = str(_row_get(token, "can_write")).strip() in {"1", "True", "true"}
        if expires and expires < current:
            ranked.append(
                (
                    (0, expires, name.casefold()),
                    {
                        "token_id": _row_get(token, "id"),
                        "reason": (
                            f"Token „{name}“ ist seit {expires} abgelaufen — "
                            "widerrufen oder erneuern."
                        ),
                    },
                )
            )
        elif expires and expires <= cutoff:
            ranked.append(
                (
                    (1, expires, name.casefold()),
                    {
                        "token_id": _row_get(token, "id"),
                        "reason": (
                            f"Token „{name}“ läuft am {expires} ab — "
                            "rechtzeitig erneuern."
                        ),
                    },
                )
            )
        elif write:
            ranked.append(
                (
                    (2, name.casefold(), ""),
                    {
                        "token_id": _row_get(token, "id"),
                        "reason": (
                            f"Token „{name}“ hat Schreibrechte — "
                            "Berechtigung prüfen und ggf. einschränken."
                        ),
                    },
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
