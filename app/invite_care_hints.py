"""Local invite care hints (no network, stores nothing).

Flags pending requests whose username is already taken or filed
twice, and invitations that expired or expire soon. Proposals are
read-only with justifications and never change data. Only usernames,
states, and counts already shown on the page are used, never
passwords or hashes.
"""

from __future__ import annotations

import time
from typing import Any

MAX_ITEMS = 5
EXPIRING_SECONDS = 24 * 3600


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


def _epoch(value: Any) -> int | None:
    """Coerce an epoch timestamp; None when unparseable."""
    try:
        return int(str(value or "").strip() or 0) or None
    except (TypeError, ValueError):
        try:
            return int(float(str(value or "").strip()))
        except (TypeError, ValueError):
            return None


def suggest_invite_review(
    items: list[Any] | None,
    account_users: list[Any] | None = None,
    invitations: list[Any] | None = None,
    now: int | None = None,
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose invite follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    current = now if isinstance(now, int) and now > 0 else int(time.time())
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    taken = {
        str(_get(user, "username") or "").strip().casefold()
        for user in account_users or []
        if user is not None and not isinstance(user, (str, bytes))
    } - {""}
    seen: set[str] = set()
    for item in items or []:
        if item is None or isinstance(item, (str, bytes)):
            continue
        name = str(_get(item, "username") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            ranked.append(
                (
                    (1, key, ""),
                    {
                        "reason": (
                            f"Doppelte Anträge für „{name}“ — "
                            "zusammenführen oder ablehnen?"
                        ),
                    },
                )
            )
        seen.add(key)
        if key in taken:
            ranked.append(
                (
                    (0, key, ""),
                    {
                        "reason": (
                            f"Antrag „{name}“: Benutzername bereits "
                            "vergeben — vor Freigabe klären (sonst Fehler)."
                        ),
                    },
                )
            )
    for invitation in invitations or []:
        if invitation is None or isinstance(invitation, (str, bytes)):
            continue
        name = str(_get(invitation, "username") or "").strip() or "ohne Namen"
        expires = _epoch(_get(invitation, "expires_at"))
        if expires is None:
            continue
        if expires <= current:
            ranked.append(
                (
                    (2, name.casefold(), ""),
                    {
                        "reason": (
                            f"Einladung für „{name}“ abgelaufen — "
                            "neu ausstellen oder widerrufen?"
                        ),
                    },
                )
            )
        elif expires - current <= EXPIRING_SECONDS:
            ranked.append(
                (
                    (3, name.casefold(), ""),
                    {
                        "reason": (
                            f"Einladung für „{name}“ läuft bald ab — "
                            "ggf. erinnern."
                        ),
                    },
                )
            )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
