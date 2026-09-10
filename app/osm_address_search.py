"""Search and autocomplete helpers for the local OSM address index."""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable


def clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def normal(value: str) -> str:
    value = clean(value, 1200).casefold()
    value = value.replace("straße", "strasse").replace("str.", "strasse")
    return " ".join(re.sub(r"[^0-9a-zäöüß]+", " ", value).split())


def search(
    query: str,
    *,
    db_factory: Callable[[], sqlite3.Connection],
    db_available: bool,
    country_code: str = "de",
    limit: int = 5,
) -> list[dict[str, Any]]:
    query = clean(query, 500)
    if len(query) < 3 or not db_available:
        return []
    tokens = [token for token in normal(query).split() if len(token) >= 2][:8]
    if not tokens:
        return []
    country = clean(country_code, 8).upper()
    maximum = max(1, min(int(limit), 20))

    def select(db: sqlite3.Connection, selected: list[str]) -> list[sqlite3.Row]:
        token_patterns = [f"%{token}%" for token in selected[:8]]
        token_patterns.extend([""] * (8 - len(token_patterns)))
        params: list[Any] = []
        for pattern in token_patterns:
            params.extend((pattern, pattern))
        params.extend((country, country, maximum))
        return db.execute(
            """SELECT * FROM address
               WHERE (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR normalized LIKE ?)
                 AND (? = '' OR country = ?)
               ORDER BY CASE WHEN postal <> '' THEN 0 ELSE 1 END,
                        city COLLATE NOCASE,
                        street COLLATE NOCASE,
                        house_number
               LIMIT ?""",
            params,
        ).fetchall()

    fallback = False
    with db_factory() as db:
        rows = select(db, tokens)
        if not rows and len(tokens) >= 3 and any(token.isdigit() for token in tokens):
            for omitted in sorted((token for token in tokens if not token.isdigit()), key=len):
                reduced = list(tokens)
                reduced.remove(omitted)
                if not any(token.isdigit() for token in reduced) or not any(not token.isdigit() for token in reduced):
                    continue
                rows = [row for row in select(db, reduced) if not row["city"]]
                if rows:
                    fallback = True
                    break
    return [
        {
            "street": clean(f"{row['street']} {row['house_number']}"),
            "postal": row["postal"],
            "city": row["city"],
            "country": row["country"],
            "country_name": "Deutschland" if row["country"] == "DE" else row["country"],
            "state": row["state"],
            "display_name": clean(f"{row['street']} {row['house_number']}, {row['postal']} {row['city']}, {row['country']}"),
            "lat": row["lat"],
            "lon": row["lon"],
            "osm_type": row["osm_type"],
            "osm_id": row["osm_id"],
            "match_quality": "fallback" if fallback else "exact",
        }
        for row in rows
    ]


def unique_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for candidate in candidates:
        identity = tuple(
            clean(candidate.get(key), 300).casefold()
            for key in ("street", "postal", "city", "state", "country")
        )
        current = unique.get(identity)
        if current is None or (
            current.get("match_quality") == "fallback"
            and candidate.get("match_quality") != "fallback"
        ):
            unique[identity] = candidate
    if len(unique) != 1:
        return None
    item = next(iter(unique.values()))
    if item.get("match_quality") == "fallback":
        return None
    if item.get("street") and item.get("city") and (item.get("postal") or item.get("country")):
        return item
    return None


def field_suggestions(
    candidates: list[dict[str, Any]], field: str, *, limit: int = 8
) -> list[dict[str, Any]]:
    selected = clean(field, 20).casefold()
    if selected not in {"city", "postal", "street", "state"}:
        return []
    maximum = max(1, min(int(limit), 20))
    seen: set[str] = set()
    suggestions: list[dict[str, Any]] = []
    for candidate in candidates:
        value = clean(candidate.get(selected), 300)
        identity = value.casefold()
        if not value or identity in seen:
            continue
        seen.add(identity)
        suggestion: dict[str, Any] = {"field": selected, "value": value}
        if selected == "postal":
            cities: dict[str, str] = {}
            for row in candidates:
                if clean(row.get("postal"), 300).casefold() != identity:
                    continue
                city = clean(row.get("city"), 300)
                if city:
                    cities.setdefault(city.casefold(), city)
            if len(cities) == 1:
                suggestion["fills"] = {"city": next(iter(cities.values()))}
        suggestions.append(suggestion)
        if len(suggestions) >= maximum:
            break
    return suggestions
