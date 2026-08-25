"""Rate-limited OpenStreetMap/Nominatim address lookup for CRM address assistance."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_CACHE: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_CACHE_TTL = 24 * 60 * 60
_CACHE_SIZE = 500


def _clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").strip().split())[:limit]


def _address_from_result(item: dict[str, Any]) -> dict[str, str]:
    address = item.get("address") if isinstance(item.get("address"), dict) else {}
    city = (
        address.get("city") or address.get("town") or address.get("village")
        or address.get("municipality") or address.get("hamlet") or ""
    )
    street = address.get("road") or address.get("pedestrian") or address.get("footway") or address.get("residential") or ""
    house_number = address.get("house_number") or ""
    street_line = _clean(f"{street} {house_number}")
    return {
        "street": street_line,
        "postal": _clean(address.get("postcode"), 40),
        "city": _clean(city),
        "country": _clean(address.get("country_code"), 10).upper(),
        "country_name": _clean(address.get("country")),
        "state": _clean(address.get("state")),
        "display_name": _clean(item.get("display_name"), 600),
        "lat": _clean(item.get("lat"), 40),
        "lon": _clean(item.get("lon"), 40),
        "osm_type": _clean(item.get("osm_type"), 20),
        "osm_id": _clean(item.get("osm_id"), 40),
    }


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    row = _CACHE.get(key)
    if row is None:
        return None
    created, value = row
    if time.monotonic() - created > _CACHE_TTL:
        _CACHE.pop(key, None)
        return None
    _CACHE.move_to_end(key)
    return value


def _cache_put(key: str, value: list[dict[str, Any]]) -> None:
    _CACHE[key] = (time.monotonic(), value)
    _CACHE.move_to_end(key)
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)


def search_address(query: str, *, country_code: str = "de", limit: int = 5) -> list[dict[str, Any]]:
    """Return normalized Nominatim candidates without exposing the public service to browser autocomplete."""
    global _LAST_REQUEST
    query = _clean(query, 500)
    if len(query) < 3:
        return []
    country_code = _clean(country_code, 8).casefold() or "de"
    limit = max(1, min(int(limit), 10))
    key = f"{country_code}|{limit}|{query.casefold()}"
    with _LOCK:
        cached = _cache_get(key)
        if cached is not None:
            return cached
        elapsed = time.monotonic() - _LAST_REQUEST
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        base = os.environ.get("SIMPLEOFFICE_NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
        params = urlencode({
            "q": query,
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(limit),
            "countrycodes": country_code,
        })
        user_agent = os.environ.get(
            "SIMPLEOFFICE_NOMINATIM_USER_AGENT",
            "SimpleOffice4Me/1.0 (address lookup; configure SIMPLEOFFICE_NOMINATIM_USER_AGENT)",
        )
        request = Request(f"{base}/search?{params}", headers={"User-Agent": user_agent, "Accept": "application/json"})
        _LAST_REQUEST = time.monotonic()
        with urlopen(request, timeout=8) as response:  # noqa: S310 - fixed/configured geocoder endpoint by administrator
            raw = response.read(512 * 1024 + 1)
        if len(raw) > 512 * 1024:
            raise RuntimeError("OSM address response is unexpectedly large")
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, list):
            raise RuntimeError("OSM address service returned an invalid response")
        result = [_address_from_result(item) for item in parsed[:limit] if isinstance(item, dict)]
        _cache_put(key, result)
        return result


def unique_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Treat one complete result as unambiguous; multiple results always require user choice."""
    if len(candidates) != 1:
        return None
    item = candidates[0]
    if item.get("street") and item.get("city") and (item.get("postal") or item.get("country")):
        return item
    return None
