"""Local contact quality suggestions (no network, stores nothing).

Two read-only proposal kinds complement the exact duplicate detection:
typo-tolerant fuzzy pairs (writing variants such as Meier/Meyer that the
exact buckets miss) and normalization proposals for display names and
street addresses (whitespace, casing, common abbreviations, reversed
"Last, First" order). Proposals never change data; applying them uses
the existing contact routes (combine/merge, contact editing) after
explicit confirmation.
"""

from __future__ import annotations

import re
from typing import Any

MAX_FIXES = 5
MAX_FUZZY_PAIRS = 5
MIN_FUZZY_LENGTH = 6
FUZZY_DISTANCE = 2
MAX_SCAN_CONTACTS = 2000

_WS_RE = re.compile(r"\s+")
_STREET_DOT_RE = re.compile(r"str\.", flags=re.IGNORECASE)
_STREET_WORD_RE = re.compile(r"\bstr\b", flags=re.IGNORECASE)


def _collapse(value: object) -> str:
    """Collapse whitespace and strip (no I/O)."""
    return _WS_RE.sub(" ", str(value or "")).strip()


def normalize_person_name(name: object) -> str:
    """Propose a cleaned display name or "" when nothing looks wrong.

    Fixes collapsed whitespace, all-upper/all-lower casing, and a
    single "Last, First" reversal. Umlauts survive via casefold-safe
    title casing; special particles (von, van, de) are left alone only
    when the name already mixes cases.
    """
    raw = _collapse(name)
    if not raw:
        return ""
    if "," in raw:
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) == 2 and all(parts) and "," not in parts[1]:
            flipped = f"{parts[1]} {parts[0]}"
            if _collapse(flipped) != raw:
                return _collapse(flipped)
        return ""
    stripped = str(name or "").strip()
    if stripped and raw != stripped:
        return raw
    letters = [char for char in raw if char.isalpha()]
    if len(letters) >= 2 and (all(not char.islower() for char in letters) or all(not char.isupper() for char in letters)):
        return " ".join(word[:1].upper() + word[1:].lower() for word in raw.split(" "))
    return ""


def normalize_street(value: object) -> str:
    """Propose a cleaned street spelling or "" when nothing looks wrong.

    Expands a trailing "Str"/"Str." abbreviation to "straße", fixes the
    "Strasse" spelling, and collapses whitespace. Only the street token
    is touched; house numbers and additions stay as typed.
    """
    raw = _collapse(value)
    if not raw:
        return ""
    fixed = raw.replace("Strasse", "Straße").replace("strasse", "straße")
    fixed = _STREET_DOT_RE.sub("straße", fixed)
    fixed = _STREET_WORD_RE.sub("Straße", fixed)
    fixed = _collapse(fixed)
    if fixed.startswith("straße"):
        fixed = "Straße" + fixed[len("straße"):]
    if fixed != raw and fixed:
        return fixed
    stripped = str(value or "").strip()
    if stripped and raw != stripped:
        return raw
    return ""


def _loose(value: object) -> str:
    """Lowercase alphanumeric key for fuzzy comparison (no I/O)."""
    return re.sub(r"[^\w]+", "", _collapse(value).casefold(), flags=re.UNICODE)


def _levenshtein_limited(first: str, second: str, limit: int) -> int:
    """Bounded edit distance with early exit above the limit."""
    if first == second:
        return 0
    if abs(len(first) - len(second)) > limit:
        return limit + 1
    previous = list(range(len(second) + 1))
    for index_a, char_a in enumerate(first, 1):
        current = [index_a]
        row_min = index_a
        for index_b, char_b in enumerate(second, 1):
            cost = 0 if char_a == char_b else 1
            step = min(previous[index_b] + 1, current[index_b - 1] + 1, previous[index_b - 1] + cost)
            current.append(step)
            if step < row_min:
                row_min = step
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _display_name(contact: dict[str, Any]) -> str:
    fields = contact.get("fields", {})
    if not isinstance(fields, dict):
        return ""
    return _collapse(fields.get("display_name", ""))


def _street_values(contact: dict[str, Any]) -> list[str]:
    rows = contact.get("addresses", [])
    values: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                text = _collapse(row.get("value", ""))
                if text:
                    values.append(text)
    return values


def suggest_contact_quality(
    contacts: list[dict[str, Any]],
    max_fixes: int = MAX_FIXES,
    max_pairs: int = MAX_FUZZY_PAIRS,
) -> list[dict[str, Any]]:
    """Propose name/street fixes plus fuzzy duplicate pairs (never writes)."""
    fixes: list[dict[str, Any]] = []
    keys: list[tuple[str, int]] = []
    pool = [row for row in (contacts or []) if isinstance(row, dict)][:MAX_SCAN_CONTACTS]
    for contact in pool:
        contact_id = str(contact.get("contact_id", "") or "")
        name = _display_name(contact)
        if name and contact_id:
            keys.append((_loose(name), len(keys)))
            proposed = normalize_person_name(name)
            if proposed and proposed != name and len(fixes) < max(0, int(max_fixes)):
                fixes.append(
                    {
                        "kind": "name",
                        "contact_id": contact_id,
                        "display": name,
                        "current": name,
                        "proposed": proposed,
                        "reason": f"Name „{name}\" wirkt falsch geschrieben — Vorschlag: „{proposed}“.",
                    }
                )
        for street in _street_values(contact):
            proposed = normalize_street(street)
            if proposed and proposed != street and len(fixes) < max(0, int(max_fixes)):
                fixes.append(
                    {
                        "kind": "street",
                        "contact_id": contact_id,
                        "display": name or contact_id,
                        "current": street,
                        "proposed": proposed,
                        "reason": f"Straße „{street}\" wirkt falsch geschrieben — Vorschlag: „{proposed}“.",
                    }
                )
                break
    pairs: list[dict[str, Any]] = []
    blocks: dict[tuple[str, int], list[int]] = {}
    for position, (key, _) in enumerate(keys):
        if len(key) >= MIN_FUZZY_LENGTH:
            blocks.setdefault((key[:1], len(key) // 2), []).append(position)
    seen: set[tuple[int, int]] = set()
    for bucket in blocks.values():
        for offset, left_pos in enumerate(bucket):
            for right_pos in bucket[offset + 1:]:
                if (left_pos, right_pos) in seen:
                    continue
                seen.add((left_pos, right_pos))
                left_key, right_key = keys[left_pos][0], keys[right_pos][0]
                if left_key == right_key:
                    continue
                if _levenshtein_limited(left_key, right_key, FUZZY_DISTANCE) > FUZZY_DISTANCE:
                    continue
                left, right = pool[left_pos], pool[right_pos]
                left_name, right_name = _display_name(left), _display_name(right)
                pairs.append(
                    {
                        "kind": "fuzzy_pair",
                        "contact_id": str(left.get("contact_id", "") or ""),
                        "display": left_name,
                        "current": left_name,
                        "proposed": right_name,
                        "reason": (
                            f"„{left_name}“ und „{right_name}“ unterscheiden sich nur in "
                            "einzelnen Zeichen — mögliche Dublette, bitte auf der "
                            "Kombinieren-Seite prüfen."
                        ),
                    }
                )
                if len(pairs) >= max(0, int(max_pairs)):
                    return fixes + pairs
    return fixes + pairs
