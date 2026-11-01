'''Book metadata lookup for inventory capture (ISBN).

Split from app.inventory (file-size gate); names are re-exported
there so existing app.inventory.<name> imports keep working.
'''
from __future__ import annotations

import http.client
import json
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

from .inventory_values import single_line as _single_line

HTTP_TIMEOUT_SECONDS = 5
MAX_METADATA_BYTES = 2 * 1024 * 1024
ALLOWED_METADATA_HOSTS = {"www.googleapis.com", "openlibrary.org"}
FIREFOX_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) "
    "Gecko/20100101 Firefox/140.0"
)


def _isbn13_checksum(prefix: str) -> str:
    total = sum(int(char) * (1 if index % 2 == 0 else 3) for index, char in enumerate(prefix))
    return str((10 - total % 10) % 10)


def normalize_isbn(value: Any) -> str:
    """Return a validated canonical ISBN-13, converting valid ISBN-10 values."""
    raw = re.sub(r"[^0-9Xx]", "", str(value or ""))
    if len(raw) == 13 and raw.isdigit():
        if not raw.startswith(("978", "979")):
            raise ValueError("ISBN-13 muss mit 978 oder 979 beginnen")
        if _isbn13_checksum(raw[:12]) != raw[-1]:
            raise ValueError("Ungültige ISBN-Prüfziffer")
        return raw
    if len(raw) == 10 and raw[:9].isdigit() and (raw[-1].isdigit() or raw[-1].upper() == "X"):
        digits = [int(char) for char in raw[:9]] + [10 if raw[-1].upper() == "X" else int(raw[-1])]
        if sum((10 - index) * digit for index, digit in enumerate(digits)) % 11:
            raise ValueError("Ungültige ISBN-Prüfziffer")
        prefix = "978" + raw[:9]
        return prefix + _isbn13_checksum(prefix)
    raise ValueError("ISBN muss eine gültige ISBN-10 oder ISBN-13 sein")


def isbn_from_barcode(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 13 and digits.startswith(("978", "979")):
        try:
            return normalize_isbn(digits)
        except ValueError:
            return ""
    return ""


def _http_json(url: str) -> dict[str, Any]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_METADATA_HOSTS
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Nicht erlaubte Metadatenquelle")
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    connection = http.client.HTTPSConnection(
        parsed.hostname, 443, timeout=HTTP_TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.7,en;q=0.5",
                "Cache-Control": "no-cache",
                "User-Agent": FIREFOX_USER_AGENT,
            },
        )
        response = connection.getresponse()
        declared = response.getheader("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_METADATA_BYTES:
                    raise ValueError("Metadatenantwort ist zu groß")
            except ValueError as exc:
                if str(exc) == "Metadatenantwort ist zu groß":
                    raise
        data = response.read(MAX_METADATA_BYTES + 1)
        if response.status != 200:
            raise ValueError("Metadatenquelle antwortet nicht erfolgreich")
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        raise ValueError("Metadatenquelle ist nicht erreichbar") from exc
    finally:
        connection.close()
    if len(data) > MAX_METADATA_BYTES:
        raise ValueError("Metadatenantwort ist zu groß")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Ungültige Metadatenantwort")
    return value


def _google_item_isbns(item: dict[str, Any]) -> set[str]:
    info = item.get("volumeInfo") if isinstance(item.get("volumeInfo"), dict) else {}
    identifiers = info.get("industryIdentifiers") if isinstance(info.get("industryIdentifiers"), list) else []
    normalized: set[str] = set()
    for row in identifiers:
        if not isinstance(row, dict):
            continue
        try:
            normalized.add(normalize_isbn(row.get("identifier", "")))
        except ValueError:
            continue
    return normalized


def parse_google_books(payload: dict[str, Any], isbn: str) -> dict[str, Any]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return {}
    candidates = [candidate for candidate in items if isinstance(candidate, dict)]
    isbn_sets = [(candidate, _google_item_isbns(candidate)) for candidate in candidates]
    item = next((candidate for candidate, values in isbn_sets if isbn in values), None)
    if item is None:
        item = next((candidate for candidate, values in isbn_sets if not values), None)
    if item is None:
        return {}
    info = item.get("volumeInfo") if isinstance(item.get("volumeInfo"), dict) else {}
    sale = item.get("saleInfo") if isinstance(item.get("saleInfo"), dict) else {}
    price = sale.get("retailPrice") if isinstance(sale.get("retailPrice"), dict) else sale.get("listPrice")
    if not isinstance(price, dict):
        price = {}
    authors = info.get("authors") if isinstance(info.get("authors"), list) else []
    categories = info.get("categories") if isinstance(info.get("categories"), list) else []
    amount = price.get("amount")
    return {
        "isbn": isbn,
        "title": _single_line(info.get("title"), 300),
        "subtitle": _single_line(info.get("subtitle"), 300),
        "authors": "; ".join(_single_line(author, 160) for author in authors if _single_line(author, 160)),
        "publisher": _single_line(info.get("publisher"), 240),
        "published_date": _single_line(info.get("publishedDate"), 40),
        "description": str(info.get("description") or "").strip()[:8000],
        "page_count": str(info.get("pageCount") or ""),
        "language": _single_line(info.get("language"), 20),
        "categories": "; ".join(_single_line(category, 160) for category in categories if _single_line(category, 160)),
        "market_price": "" if amount is None else str(amount),
        "currency": _single_line(price.get("currencyCode"), 3).upper(),
        "price_source": "Google Books" if amount is not None else "",
        "metadata_source": "Google Books",
    }


def parse_openlibrary(payload: dict[str, Any], isbn: str) -> dict[str, Any]:
    value = payload.get(f"ISBN:{isbn}") if isinstance(payload, dict) else None
    if not isinstance(value, dict):
        return {}
    authors = value.get("authors") if isinstance(value.get("authors"), list) else []
    publishers = value.get("publishers") if isinstance(value.get("publishers"), list) else []
    subjects = value.get("subjects") if isinstance(value.get("subjects"), list) else []
    author_names = [_single_line(item.get("name"), 160) for item in authors if isinstance(item, dict)]
    publisher_names = [_single_line(item.get("name"), 180) for item in publishers if isinstance(item, dict)]
    subject_names = [_single_line(item.get("name"), 160) for item in subjects[:12] if isinstance(item, dict)]
    return {
        "isbn": isbn,
        "title": _single_line(value.get("title"), 300),
        "subtitle": _single_line(value.get("subtitle"), 300),
        "authors": "; ".join(item for item in author_names if item),
        "publisher": "; ".join(item for item in publisher_names if item),
        "published_date": _single_line(value.get("publish_date"), 80),
        "description": "",
        "page_count": str(value.get("number_of_pages") or ""),
        "language": "",
        "categories": "; ".join(item for item in subject_names if item),
        "market_price": "",
        "currency": "",
        "price_source": "",
        "metadata_source": "Open Library",
    }


def _normalized_isbn_values(values: Any) -> set[str]:
    rows = values if isinstance(values, list) else [values]
    normalized: set[str] = set()
    for value in rows:
        try:
            normalized.add(normalize_isbn(value))
        except ValueError:
            continue
    return normalized


def parse_openlibrary_search(payload: dict[str, Any], isbn: str) -> dict[str, Any]:
    """Parse the current Open Library Search API and require an exact ISBN match."""
    docs = payload.get("docs") if isinstance(payload, dict) else None
    if not isinstance(docs, list):
        return {}
    item = next(
        (
            candidate
            for candidate in docs
            if isinstance(candidate, dict) and isbn in _normalized_isbn_values(candidate.get("isbn", []))
        ),
        None,
    )
    if item is None:
        return {}
    authors = item.get("author_name") if isinstance(item.get("author_name"), list) else []
    publishers = item.get("publisher") if isinstance(item.get("publisher"), list) else []
    subjects = item.get("subject") if isinstance(item.get("subject"), list) else []
    languages = item.get("language") if isinstance(item.get("language"), list) else []
    subtitle = item.get("subtitle")
    if isinstance(subtitle, list):
        subtitle = next((value for value in subtitle if value), "")
    return {
        "isbn": isbn,
        "title": _single_line(item.get("title"), 300),
        "subtitle": _single_line(subtitle, 300),
        "authors": "; ".join(_single_line(author, 160) for author in authors if _single_line(author, 160)),
        "publisher": "; ".join(_single_line(publisher, 180) for publisher in publishers[:8] if _single_line(publisher, 180)),
        "published_date": _single_line(item.get("first_publish_year"), 80),
        "description": "",
        "page_count": str(item.get("number_of_pages_median") or ""),
        "language": "; ".join(_single_line(language, 20) for language in languages[:4] if _single_line(language, 20)),
        "categories": "; ".join(_single_line(subject, 160) for subject in subjects[:12] if _single_line(subject, 160)),
        "market_price": "",
        "currency": "",
        "price_source": "",
        "metadata_source": "Open Library Search",
    }


def merge_book_metadata(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    result = dict(primary or {})
    for key, value in (fallback or {}).items():
        if not result.get(key) and value not in (None, "", []):
            result[key] = value
    sources = []
    for candidate in (
        primary.get("metadata_source") if primary else "",
        fallback.get("metadata_source") if fallback else "",
    ):
        if candidate and candidate not in sources:
            sources.append(candidate)
    if sources:
        result["metadata_source"] = " + ".join(sources)
    return result


def _metadata_candidate(metadata: dict[str, Any]) -> dict[str, str]:
    """Keep only reviewable provider fields; never expose raw provider payloads."""
    fields = ("metadata_source", "title", "authors", "publisher", "published_date")
    return {key: value[:300] for key in fields if (value := str(metadata.get(key) or "").strip())}
