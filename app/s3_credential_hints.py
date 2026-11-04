"""Local S3 credential hints (no network, stores nothing).

Flags expired, soon-expiring, and long-unused credentials, plus
active credentials while the overlay is disabled. Proposals are
read-only with justifications and never change data. Only labels
and dates are shown, never keys or secrets.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

MAX_ITEMS = 5
EXPIRY_WINDOW_DAYS = 14
UNUSED_AFTER_DAYS = 90


def _day(value: Any) -> str:
    """Return a YYYY-MM-DD key from ISO text; empty when unparseable."""
    text = str(value or "").strip()[:10]
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        if text.replace("-", "").isdigit():
            return text
    return ""


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


def suggest_credential_review(
    credentials: list[Any] | None,
    enabled: bool = True,
    today: str = "",
    max_items: int = MAX_ITEMS,
) -> list[dict[str, Any]]:
    """Propose credential follow-ups (never writes)."""
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = MAX_ITEMS
    current = (str(today or "").strip()[:10]) or date.today().isoformat()
    if not _day(current):
        current = date.today().isoformat()
    soon = (
        date.fromisoformat(current) + timedelta(days=EXPIRY_WINDOW_DAYS)
    ).isoformat()
    stale = (
        date.fromisoformat(current) - timedelta(days=UNUSED_AFTER_DAYS)
    ).isoformat()
    ranked: list[tuple[tuple, dict[str, Any]]] = []
    active = 0
    for credential in credentials or []:
        if credential is None or isinstance(credential, (str, bytes)):
            continue
        if _on(_get(credential, "revoked")):
            continue
        active += 1
        label = str(_get(credential, "label") or "").strip() or "ohne Namen"
        expires = _day(_get(credential, "expires_at"))
        if expires and expires < current:
            ranked.append(
                (
                    (0, expires, label.casefold(), ""),
                    {
                        "reason": (
                            f"Zugang „{label}“ ist seit {expires} abgelaufen — "
                            "erneuern oder widerrufen."
                        ),
                    },
                )
            )
        elif expires and expires <= soon:
            ranked.append(
                (
                    (1, expires, label.casefold(), ""),
                    {
                        "reason": (
                            f"Zugang „{label}“ läuft am {expires} ab — "
                            "rechtzeitig erneuern."
                        ),
                    },
                )
            )
        else:
            used = _day(_get(credential, "last_used_at"))
            if used and used < stale:
                ranked.append(
                    (
                        (2, used, label.casefold(), ""),
                        {
                            "reason": (
                                f"Zugang „{label}“ seit {used} ungenutzt — "
                                "widerrufen?"
                            ),
                        },
                    ),
                )
    if not _on(enabled) and active:
        ranked.append(
            (
                (3, "", "", ""),
                {
                    "reason": (
                        f"Overlay deaktiviert, aber {active} "
                        f"{'aktive Zugänge' if active != 1 else 'aktiver Zugang'} "
                        "vorhanden — aktivieren oder aufräumen?"
                    ),
                },
            )
        )
    ranked.sort(key=lambda entry: entry[0])
    return [entry[1] for entry in ranked[:limit]]
