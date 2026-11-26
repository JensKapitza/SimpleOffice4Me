"""Explicit, read-only catalog evidence for scanner intake.

The scanner must ask before calling this module, then let the user choose an
editable candidate. No object or draft is written here.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
import re
from threading import Lock
import time
from typing import Any, Callable
import unicodedata
from urllib.parse import urlencode

from . import inventory_catalog_http as catalog_http
from .inventory_metadata import normalize_isbn

BOOK_PROVIDERS = ("openlibrary", "google_books")
PROVIDER_NAMES = {"openlibrary": "Open Library", "google_books": "Google Books", "ebay": "eBay Browse API"}
MAX_RESULTS = 5
CACHE_TTL_SECONDS = 600
CACHE_LIMIT = 128
_cache_lock = Lock()
# Local cache entries need no shared identity across application starts.
_cache: OrderedDict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = OrderedDict()
Fetch = Callable[[str, str], dict[str, Any] | None]
_CODE = re.compile(r"[0-9Xx -]{8,32}", re.ASCII)
_EDITION_LIMITS = {"subtitle": 250, "publisher": 250, "published_date": 60, "edition": 120, "format": 60}
_YEAR = re.compile(r"\b(?:1[0-9]{3}|20[0-9]{2})\b")


@dataclass(frozen=True)
class Identifier:
    kind: str
    value: str


def normalize_catalog_code(value: Any) -> Identifier:
    """Accept ISBN-10/13 and checksum-valid EAN-8/EAN-13/UPC-A/GTIN-14."""
    value = value.strip() if isinstance(value, str) else ""
    if not _CODE.fullmatch(value):
        raise ValueError("Ungültige ISBN/EAN")
    code = value.replace(" ", "").replace("-", "")
    if len(code) == 10 or (len(code) == 13 and code.startswith(("978", "979"))):
        return Identifier("book", normalize_isbn(code))
    if len(code) not in {8, 12, 13, 14} or not code.isascii() or not code.isdecimal():
        raise ValueError("Ungültige EAN/GTIN")
    total = sum(int(digit) * (3 if offset % 2 == 0 else 1) for offset, digit in enumerate(reversed(code[:-1])))
    if not any(digit != "0" for digit in code) or (total + int(code[-1])) % 10:
        raise ValueError("Ungültige EAN-Prüfziffer")
    return Identifier("product", code)


def _line(value: Any, length: int = 250) -> str:
    if not isinstance(value, str):
        return ""
    clean = "".join(" " if char.isspace() else char for char in value[: length * 4]
                    if char.isspace() or not unicodedata.category(char).startswith("C"))
    return " ".join(clean.split())[:length]


def _key(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", _line(value, 251)).casefold().split())


def _rows(value: Any) -> list:
    return value[:MAX_RESULTS] if isinstance(value, list) else []


def _identifiers(values: list, kind: str) -> set[str]:
    result = set()
    for value in values[:20]:
        try:
            identifier = normalize_catalog_code(value)
        except ValueError:
            continue
        if identifier.kind == kind:
            result.add(identifier.value if kind == "book" else identifier.value.zfill(14))
    return result


def _names(rows: Any) -> str:
    return "; ".join(name for row in _rows(rows) if isinstance(row, dict) and (name := _line(row.get("name"), 120)))


def _book_candidate(provider: str, code: str, fields: dict) -> dict[str, Any]:
    if len(_line(fields.get("title"), 251)) > 250:
        return {}
    result = {
        name: value for name, cap in (("title", 250), ("subtitle", 250), ("authors", 600),
                                      ("publisher", 250), ("published_date", 60), ("edition", 120), ("format", 60))
        if (value := _line(fields.get(name), cap))
    }
    if not result.get("title"):
        return {}
    if any(isinstance(raw := fields.get(name), str)
           and (len(raw) > cap * 4 or len(_line(raw, cap + 1)) > cap)
           for name, cap in _EDITION_LIMITS.items()):
        result["edition_fields_truncated"] = True
    return {**result, "identifier": code, "isbn": code, "provider": provider,
            "metadata_source": PROVIDER_NAMES[provider], "exact": True}


def _parse_openlibrary(payload: dict, code: str) -> list[dict]:
    data = payload.get(f"ISBN:{code}")
    if not isinstance(data, dict) or not isinstance(ids := data.get("identifiers"), dict):
        return []
    identifier_lists = [ids.get(name) for name in ("isbn_10", "isbn_13")]
    isbn_values = [raw for values in identifier_lists for raw in _rows(values)]
    # A dictionary key echo alone is not evidence; ISBN fields must agree on one edition.
    if _identifiers(isbn_values, "book") != {code}:
        return []
    candidate = _book_candidate("openlibrary", code, {
        "title": data.get("title"), "subtitle": data.get("subtitle"),
        "authors": _names(data.get("authors")), "publisher": _names(data.get("publishers")),
        "published_date": data.get("publish_date"), "edition": data.get("edition_name"),
        "format": data.get("physical_format"),
    })
    publishers = data.get("publishers")
    if candidate and (
        any(isinstance(values, list) and len(values) > MAX_RESULTS for values in (*identifier_lists, publishers))
        or any(isinstance(row, dict) and isinstance(raw := row.get("name"), str)
               and (len(raw) > 480 or len(_line(raw, 121)) > 120) for row in _rows(publishers))
    ):
        candidate["edition_fields_truncated"] = True
    return [candidate] if candidate else []


def _parse_google(payload: dict, code: str) -> list[dict]:
    candidates = []
    clipped = False
    for row in _rows(payload.get("items")):
        data = row.get("volumeInfo") if isinstance(row, dict) else None
        if not isinstance(data, dict):
            continue
        raw_ids = data.get("industryIdentifiers")
        if isinstance(raw_ids, list) and len(raw_ids) > MAX_RESULTS:
            clipped = True
        identifiers = [item.get("identifier") for item in _rows(raw_ids)
                       if isinstance(item, dict) and item.get("type") in {"ISBN_10", "ISBN_13"}]
        if _identifiers(identifiers, "book") != {code}:
            continue
        candidate = _book_candidate("google_books", code, {
            "title": data.get("title"), "subtitle": data.get("subtitle"),
            "authors": "; ".join(name for val in _rows(data.get("authors")) if (name := _line(val, 120))),
            "publisher": data.get("publisher"), "published_date": data.get("publishedDate"),
        })
        if candidate:
            candidates.append(candidate)
    if clipped:
        for candidate in candidates:
            candidate["edition_fields_truncated"] = True
    return candidates


def _product_candidates(payload: dict, code: str) -> list[dict]:
    candidates = []
    for row in _rows(payload.get("items")):
        if not isinstance(row, dict) or _identifiers([row.get("gtin")], "product") != {code.zfill(14)}:
            continue
        fields = {key: value for key in ("title", "variant", "size", "unit") if (value := _line(row.get(key)))}
        if not fields.get("title"):
            continue
        # A marketplace listing is a review hint only, even with a complete exact variant.
        candidates.append({**fields, "identifier": code, "barcode": code, "provider": "ebay",
                           "metadata_source": PROVIDER_NAMES["ebay"], "exact": True,
                           "complete_variant": all(fields.get(key) for key in ("variant", "size", "unit"))})
    return candidates


def _with_evidence(candidate: dict) -> dict:
    field_names = (
        "isbn", "barcode", "title", "subtitle", "authors", "publisher", "published_date",
        "edition", "format", "variant", "size", "unit",
    )
    values = {name: candidate[name] for name in field_names if candidate.get(name)}
    provider = candidate["provider"]
    identity = json.dumps([provider, candidate["identifier"], values], sort_keys=True, ensure_ascii=False)
    candidate_id = "catalog:" + provider + ":" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    evidence = {
        name: {"source_id": f"{candidate_id}:{name}", "kind": "external_catalog",
               "provider": provider, "candidate_id": candidate_id, "field": name,
               "label": PROVIDER_NAMES[provider]}
        for name in values
    }
    return {**candidate, "candidate_id": candidate_id, "field_evidence": evidence}


def _conflicting(left: dict, right: dict) -> bool:
    if _key(left["title"]) != _key(right["title"]):
        return True
    for field in ("subtitle", "publisher", "edition", "format"):
        lvalue, rvalue = _key(left.get(field)), _key(right.get(field))
        if lvalue and rvalue and lvalue != rvalue:
            return True
    lyears, ryears = (set(_YEAR.findall(row.get("published_date", ""))) for row in (left, right))
    return len(lyears) > 1 or len(ryears) > 1 or bool(lyears and ryears and lyears != ryears)


def _same_edition(left: dict, right: dict) -> bool:
    if (_conflicting(left, right) or left.get("edition_fields_truncated")
            or right.get("edition_fields_truncated")):
        return False
    # Unqualified common titles or only ISBN echoes do not corroborate a recommendation.
    lyear, ryear = (_YEAR.search(row.get("published_date", "")) for row in (left, right))
    has_publisher = bool(_key(left.get("publisher")) and _key(right.get("publisher")))
    has_edition = bool(_key(left.get("edition")) and _key(right.get("edition")))
    return has_edition or (has_publisher and bool(lyear and ryear and lyear[0] == ryear[0]))


def _request_book(provider: str, code: str, api_key: str) -> dict | None:
    if provider == "openlibrary":
        query = urlencode({"bibkeys": f"ISBN:{code}", "format": "json", "jscmd": "data"})
        return catalog_http.get_json("openlibrary.org", "/api/books?" + query)
    if not api_key:
        return None
    query = urlencode({"q": f"isbn:{code}", "maxResults": MAX_RESULTS,
                       "fields": "totalItems,items(volumeInfo(title,subtitle,authors,publisher,publishedDate,industryIdentifiers))"})
    return catalog_http.get_json("www.googleapis.com", "/books/v1/volumes?" + query,
                                 headers={"X-Goog-Api-Key": api_key})


def _cached_book(provider: str, code: str, api_key: str) -> list[dict] | None:
    if provider == "google_books" and not api_key:
        return None
    # Per-process namespace for this in-memory cache only: distinct entries
    # per API key without retaining the key itself. This is not credential
    # protection, so the built-in hash states the intent explicitly.
    key_marker = hash(api_key) if provider == "google_books" else 0
    cache_key = (provider, code, key_marker)
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.pop(cache_key, None)
        if entry is not None and entry[0] > now:
            _cache[cache_key] = entry
            return deepcopy(entry[1])
    rows = _book_rows(provider, _request_book(provider, code, api_key), code)
    if rows is None or not rows:
        return rows
    with _cache_lock:
        _cache[cache_key] = (time.monotonic() + CACHE_TTL_SECONDS, deepcopy(rows))
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)
    return rows


def _book_rows(provider: str, payload: dict | None, code: str) -> list[dict] | None:
    if payload is None:
        return None
    if not isinstance(payload, dict) or "error" in payload:
        raise ValueError("Ungültige Katalogantwort")
    if provider == "openlibrary":
        if payload and not isinstance(payload.get(f"ISBN:{code}"), dict):
            raise ValueError("Ungültige Open-Library-Antwort")
        return _parse_openlibrary(payload, code)
    if not isinstance(payload.get("items"), list) and payload.get("totalItems") != 0:
        raise ValueError("Ungültige Google-Books-Antwort")
    rows = _parse_google(payload, code)
    total = payload.get("totalItems")
    items = payload.get("items", [])
    if (isinstance(items, list) and len(items) > MAX_RESULTS) or (type(total) is int and total > len(_rows(items))):
        for row in rows:
            row["partial_provider_results"] = True
    return rows


def _state(identifier: Identifier, candidates: list[dict], providers: dict[str, str],
           observed_title: str) -> tuple[str, str, dict | None]:
    if not candidates:
        if "unavailable" in providers.values() or "no_match" not in providers.values():
            return "unavailable", "provider_unavailable", None
        return "not_found", "no_exact_match", None
    if observed_title and any(_key(row["title"]) != _key(observed_title) for row in candidates):
        return "review", "observed_title_differs", None
    if identifier.kind == "product":
        variants = {tuple(_key(row.get(key)) for key in ("variant", "size", "unit"))
                    for row in candidates if row["complete_variant"]}
        if len(variants) > 1 or any(row.get("variant_match") == "differs" for row in candidates):
            return "review", "conflicting_variant", None
        return "review", ("verify_variant" if variants else "missing_variant_details"), None
    if any(row.get("partial_provider_results") or row.get("edition_fields_truncated") for row in candidates):
        return "review", "provider_results_truncated", None
    if any(_conflicting(left, right) for i, left in enumerate(candidates) for right in candidates[i + 1:]):
        return "review", "conflicting_edition", None
    pair = next(((left, right) for i, left in enumerate(candidates) for right in candidates[i + 1:]
                 if left["provider"] != right["provider"] and _same_edition(left, right)), None)
    if pair:
        recommended = dict(pair[0])
        recommended["corroborated_by"] = [row["provider"] for row in pair]
        recommended["corroborating_candidate_ids"] = [row["candidate_id"] for row in pair]
        return "confirmed", "independent_edition_match", recommended
    reason = "edition_unconfirmed" if len({row["provider"] for row in candidates}) > 1 else "single_source"
    return "review", reason, None



def proven_edition_ids(catalog: dict) -> frozenset[str]:
    """Revalidate the exact bounded two-source attestation in persisted drafts."""
    if catalog.get("kind") != "book" or catalog.get("status") != "confirmed" or catalog.get("stale"):
        return frozenset()
    rec = catalog.get("recommended")
    rows = catalog.get("candidates")
    providers = catalog.get("providers")
    if not isinstance(rec, dict) or not isinstance(rows, list) or not isinstance(providers, dict):
        return frozenset()
    ids = rec.get("corroborating_candidate_ids")
    if (not isinstance(ids, list) or len(ids) != 2 or not all(isinstance(i, str) for i in ids)
            or len(set(ids)) != 2 or rec.get("candidate_id") not in ids or not 2 <= len(rows) <= 2 * MAX_RESULTS):
        return frozenset()
    try:
        identity = normalize_catalog_code(catalog.get("identifier"))
    except ValueError:
        return frozenset()
    if identity.kind != "book":
        return frozenset()
    indexed = {}
    for row in rows:
        if (not isinstance(row, dict) or row.get("exact") is not True
                or row.get("isbn") != identity.value or row.get("identifier") != identity.value
                or row.get("provider") not in BOOK_PROVIDERS
                or row.get("partial_provider_results") or row.get("edition_fields_truncated")
                or not isinstance(row.get("title"), str) or not row["title"]
                or any(field in row and not isinstance(row[field], str) for field in _EDITION_LIMITS)):
            return frozenset()
        tagged = _with_evidence(row)
        key = tagged["candidate_id"]
        if key != row.get("candidate_id") or tagged["field_evidence"] != row.get("field_evidence") or key in indexed:
            return frozenset()
        indexed[key] = row
    if any(i not in indexed for i in ids):
        return frozenset()
    left, right = (indexed[i] for i in ids)
    pair_providers = [left["provider"], right["provider"]]
    if (len(set(pair_providers)) != 2 or rec.get("corroborated_by") != pair_providers
            or any(providers.get(provider) != "matched" for provider in pair_providers)
            or not _same_edition(left, right)
            or any(_conflicting(a, b) for i, a in enumerate(rows) for b in rows[i + 1:])):
        return frozenset()
    return frozenset(ids)


def edition_field_key(field: str, value: Any) -> str:
    if field == "published_date" and isinstance(value, str):
        years = set(_YEAR.findall(value))
        return next(iter(years)) if len(years) == 1 else ""
    return _key(value)

def _variant_match(row: dict, observed: dict[str, str]) -> str:
    actual = {key: _key(row.get(key)) for key in ("variant", "size", "unit")}
    if any(value and actual[key] and actual[key] != value for key, value in observed.items()):
        return "differs"
    if all(observed.get(key) and actual[key] == observed[key] for key in actual):
        return "matches_observed"
    return "unverified"


def lookup_catalog(code: str, *, observed_title: str = "", observed_variant: str = "",
                   observed_size: str = "", observed_unit: str = "", book_fetch: Fetch | None = None,
                   product_fetch: Fetch | None = None) -> dict[str, Any]:
    """Fetch whitelisted public facts by normalized code only; suggest, never save."""
    identifier = normalize_catalog_code(code)
    providers = BOOK_PROVIDERS if identifier.kind == "book" else ("ebay",)
    statuses: dict[str, str] = {}
    candidates: list[dict] = []
    api_key = os.getenv("SIMPLEOFFICE_GOOGLE_BOOKS_API_KEY", "").strip() if book_fetch is None else ""
    for provider in providers:
        try:
            if identifier.kind == "book":
                rows = (_book_rows(provider, book_fetch(provider, identifier.value), identifier.value) if book_fetch
                        else _cached_book(provider, identifier.value, api_key))
            else:
                payload = product_fetch(provider, identifier.value) if product_fetch else None
                if payload is not None and (not isinstance(payload, dict) or not isinstance(payload.get("items"), list)):
                    raise ValueError("Ungültige Produktkatalogantwort")
                rows = _product_candidates(payload, identifier.value) if payload is not None else None
        except (OSError, ValueError, http.client.HTTPException):
            statuses[provider] = "unavailable"
            continue
        statuses[provider] = "not_configured" if rows is None else ("matched" if rows else "no_match")
        if rows:
            candidates.extend(rows)
    if identifier.kind == "product":
        observed = {key: _key(value) for key, value in
                    (("variant", observed_variant), ("size", observed_size), ("unit", observed_unit))}
        for row in candidates:
            row["variant_match"] = _variant_match(row, observed)
    candidates.sort(key=lambda row: (_key(row["title"]) != _key(observed_title),
                                     row.get("variant_match") == "differs",
                                     -sum(bool(row.get(field)) for field in ("edition", "publisher", "published_date",
                                                                             "authors", "variant", "size", "unit")),
                                     row["provider"], _key(row["title"])))
    tagged = [_with_evidence(row) for row in candidates]
    candidates = list({row["candidate_id"]: row for row in tagged}.values())
    status, reason, recommended = _state(identifier, candidates, statuses, _line(observed_title, 251))
    return {"kind": identifier.kind, "identifier": identifier.value, "status": status, "reason": reason,
            "providers": statuses, "candidates": candidates, "recommended": recommended}
