"""User-triggered marketplace metadata lookup for fast inventory capture.

Public Amazon/eBay search pages are queried only after an authenticated user
explicitly starts a marketplace search. Official APIs remain optional fallbacks
when credentials are configured. The implementation never follows arbitrary
hosts and never attempts to bypass provider protection pages.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import ssl
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup
from flask import Blueprint, g, jsonify, request

from .auth import login_required
from .inventory import InventoryEnrichmentStore

bp = Blueprint("inventory_marketplace", __name__, url_prefix="/inventory/marketplace")

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 8
MAX_RESULTS = 3
EBAY_TOKEN_SKEW_SECONDS = 60
_EBAY_TOKEN_CACHE: dict[str, Any] = {"token": "", "expires_at": 0.0}
_PUBLIC_HOSTS = {"amazon": "www.amazon.de", "ebay": "www.ebay.de"}
_PUBLIC_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) "
    "Gecko/20100101 Firefox/140.0"
)


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())[:limit]


def _json_request(
    host: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> dict[str, Any]:
    connection = http.client.HTTPSConnection(
        host,
        443,
        timeout=HTTP_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("Marketplace-Antwort ist zu groß")
        if response.status < 200 or response.status >= 300:
            raise ValueError(f"Marketplace antwortet mit HTTP {response.status}")
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise ValueError("Marketplace ist derzeit nicht erreichbar") from exc
    finally:
        connection.close()
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Marketplace lieferte keine gültigen JSON-Daten") from exc
    if not isinstance(value, dict):
        raise ValueError("Marketplace-Antwort hat ein ungültiges Format")
    return value


def _amazon_fallback(query: str) -> str:
    return "https://www.amazon.de/s?k=" + quote(query, safe="")


def _ebay_fallback(query: str) -> str:
    return "https://www.ebay.de/sch/i.html?_nkw=" + quote(query, safe="")


def provider_status() -> dict[str, bool]:
    return {
        "amazon": all(
            os.environ.get(name, "").strip()
            for name in (
                "SIMPLEOFFICE_AMAZON_ACCESS_KEY",
                "SIMPLEOFFICE_AMAZON_SECRET_KEY",
                "SIMPLEOFFICE_AMAZON_PARTNER_TAG",
            )
        ),
        "ebay": all(
            os.environ.get(name, "").strip()
            for name in ("SIMPLEOFFICE_EBAY_CLIENT_ID", "SIMPLEOFFICE_EBAY_CLIENT_SECRET")
        ),
    }


def _public_path(provider: str, query: str) -> str:
    encoded = quote(query, safe="")
    if provider == "amazon":
        return "/s?k=" + encoded
    if provider == "ebay":
        return "/sch/i.html?_nkw=" + encoded
    raise ValueError("Unbekannter Marketplace")


def _html_request(provider: str, query: str) -> str:
    """Fetch one public search page without following redirects or protection pages."""
    host = _PUBLIC_HOSTS.get(provider)
    if not host:
        raise ValueError("Unbekannter Marketplace")
    connection = http.client.HTTPSConnection(
        host,
        443,
        timeout=HTTP_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "GET",
            _public_path(provider, query),
            headers={
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
                "Cache-Control": "no-cache",
                "User-Agent": _PUBLIC_USER_AGENT,
            },
        )
        response = connection.getresponse()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("Marketplace-Seite ist zu groß")
        if response.status in {301, 302, 303, 307, 308}:
            raise ValueError("Marketplace hat die öffentliche Suche umgeleitet")
        if response.status in {403, 429}:
            raise ValueError("Marketplace blockiert den direkten Abruf derzeit")
        if response.status != 200:
            raise ValueError(f"Marketplace antwortet mit HTTP {response.status}")
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise ValueError("Marketplace ist derzeit nicht erreichbar") from exc
    finally:
        connection.close()
    text = data.decode("utf-8", errors="replace")
    lowered = text.lower()
    protection_markers = (
        "enter the characters you see below",
        "sorry, we just need to make sure you're not a robot",
        "pardon our interruption",
        "robot check",
    )
    if any(marker in lowered for marker in protection_markers):
        raise ValueError("Marketplace zeigt eine Schutzseite; normale Suche kann geöffnet werden")
    return text


def _safe_result_url(provider: str, value: Any) -> str:
    base = f"https://{_PUBLIC_HOSTS[provider]}/"
    target = urljoin(base, str(value or ""))
    parsed = urlsplit(target)
    host = (parsed.hostname or "").lower()
    root = "amazon.de" if provider == "amazon" else "ebay.de"
    if parsed.scheme != "https" or not (host == root or host.endswith("." + root)):
        return ""
    if parsed.username or parsed.password:
        return ""
    return target[:1000]


def _price_parts(value: Any) -> tuple[str, str]:
    text = _clean(value, 120)
    currency = "EUR" if "€" in text or "eur" in text.lower() else ""
    match = re.search(r"(?<!\d)(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})|\d+(?:[.,]\d{1,2})?)(?!\d)", text)
    if not match:
        return "", currency
    amount = match.group(1)
    if "," in amount:
        amount = amount.replace(".", "").replace(",", ".")
    return amount, currency


def _public_result(
    provider: str,
    title: Any,
    url: Any,
    *,
    price: Any = "",
    external_id: Any = "",
) -> dict[str, Any] | None:
    clean_title = _clean(title, 300)
    clean_url = _safe_result_url(provider, url)
    if not clean_title or not clean_url:
        return None
    amount, currency = _price_parts(price)
    provider_name = "Amazon" if provider == "amazon" else "eBay"
    return {
        "provider": provider,
        "title": clean_title,
        "authors": "",
        "manufacturer": "",
        "model": "",
        "categories": "",
        "description": "",
        "isbn": "",
        "barcode": "",
        "market_price": amount,
        "currency": currency,
        "price_source": provider_name if amount else "",
        "metadata_source": f"{provider_name} öffentliche Suche",
        "external_id": _clean(external_id, 120),
        "url": clean_url,
    }


def parse_amazon_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[dict[str, Any]] = []
    for row in soup.select('[data-component-type="s-search-result"]'):
        title_node = row.select_one("h2 a span") or row.select_one("h2 span")
        link_node = row.select_one("h2 a[href]") or row.select_one("a.a-link-normal[href]")
        if not title_node or not link_node:
            continue
        price_node = row.select_one(".a-price .a-offscreen")
        result = _public_result(
            "amazon",
            title_node.get_text(" ", strip=True),
            link_node.get("href"),
            price=price_node.get_text(" ", strip=True) if price_node else "",
            external_id=row.get("data-asin"),
        )
        if result:
            results.append(result)
        if len(results) >= MAX_RESULTS:
            break
    return results


def parse_ebay_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[dict[str, Any]] = []
    for row in soup.select("li.s-item, div.s-item"):
        title_node = row.select_one(".s-item__title")
        link_node = row.select_one("a.s-item__link[href]")
        if not title_node or not link_node:
            continue
        title = _clean(title_node.get_text(" ", strip=True), 300)
        title = re.sub(r"^(?:Neues Angebot|New Listing)\s*", "", title, flags=re.IGNORECASE)
        if not title or title.lower() in {"shop on ebay", "auf ebay shoppen"}:
            continue
        price_node = row.select_one(".s-item__price")
        url = link_node.get("href")
        item_match = re.search(r"/itm/(?:[^/?#]+/)?(\d+)", str(url or ""))
        result = _public_result(
            "ebay",
            title,
            url,
            price=price_node.get_text(" ", strip=True) if price_node else "",
            external_id=item_match.group(1) if item_match else "",
        )
        if result:
            results.append(result)
        if len(results) >= MAX_RESULTS:
            break
    return results


def _public_search(provider: str, query: str) -> list[dict[str, Any]]:
    html = _html_request(provider, query)
    return parse_amazon_html(html) if provider == "amazon" else parse_ebay_html(html)


def _amazon_signing_key(secret: str, day: str, region: str, service: str) -> bytes:
    date_key = hmac.new(("AWS4" + secret).encode(), day.encode(), hashlib.sha256).digest()
    region_key = hmac.new(date_key, region.encode(), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode(), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def _amazon_request(query: str) -> dict[str, Any]:
    access_key = os.environ.get("SIMPLEOFFICE_AMAZON_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("SIMPLEOFFICE_AMAZON_SECRET_KEY", "").strip()
    partner_tag = os.environ.get("SIMPLEOFFICE_AMAZON_PARTNER_TAG", "").strip()
    if not access_key or not secret_key or not partner_tag:
        raise ValueError("Amazon Product Advertising API ist nicht konfiguriert")

    host = "webservices.amazon.de"
    region = "eu-west-1"
    service = "ProductAdvertisingAPI"
    target = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    path = "/paapi5/searchitems"
    payload = {
        "Keywords": query,
        "ItemCount": MAX_RESULTS,
        "PartnerTag": partner_tag,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.de",
        "Resources": [
            "Images.Primary.Medium",
            "ItemInfo.ByLineInfo",
            "ItemInfo.Classifications",
            "ItemInfo.ExternalIds",
            "ItemInfo.Features",
            "ItemInfo.ProductInfo",
            "ItemInfo.Title",
            "Offers.Listings.Price",
        ],
        "SearchIndex": "All",
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    canonical_headers = (
        "content-encoding:amz-1.0\n"
        "content-type:application/json; charset=utf-8\n"
        f"host:{host}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-target:{target}\n"
    )
    signed_headers = "content-encoding;content-type;host;x-amz-date;x-amz-target"
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_request = "\n".join(("POST", path, "", canonical_headers, signed_headers, payload_hash))
    scope = f"{day}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest())
    )
    signature = hmac.new(
        _amazon_signing_key(secret_key, day, region, service),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return _json_request(
        host,
        "POST",
        path,
        headers={
            "Authorization": authorization,
            "Content-Encoding": "amz-1.0",
            "Content-Type": "application/json; charset=utf-8",
            "Host": host,
            "X-Amz-Date": amz_date,
            "X-Amz-Target": target,
        },
        body=body,
    )


def _display_value(container: Any, key: str) -> str:
    if not isinstance(container, dict):
        return ""
    value = container.get(key)
    if not isinstance(value, dict):
        return ""
    return _clean(value.get("DisplayValue"), 500)


def _amazon_identifiers(info: dict[str, Any]) -> tuple[str, str]:
    external = info.get("ExternalIds") if isinstance(info.get("ExternalIds"), dict) else {}
    isbn = ""
    barcode = ""
    for key in ("ISBNs", "EANs", "UPCs"):
        values = external.get(key) if isinstance(external.get(key), dict) else {}
        rows = values.get("DisplayValues") if isinstance(values.get("DisplayValues"), list) else []
        if not rows:
            continue
        first = _clean(rows[0], 120)
        if key == "ISBNs" and first:
            isbn = first
        elif first and not barcode:
            barcode = first
    return isbn, barcode


def parse_amazon_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    search = payload.get("SearchResult") if isinstance(payload.get("SearchResult"), dict) else {}
    items = search.get("Items") if isinstance(search.get("Items"), list) else []
    results: list[dict[str, Any]] = []
    for item in items[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        info = item.get("ItemInfo") if isinstance(item.get("ItemInfo"), dict) else {}
        byline = info.get("ByLineInfo") if isinstance(info.get("ByLineInfo"), dict) else {}
        offers = item.get("Offers") if isinstance(item.get("Offers"), dict) else {}
        listings = offers.get("Listings") if isinstance(offers.get("Listings"), list) else []
        listing = listings[0] if listings and isinstance(listings[0], dict) else {}
        price = listing.get("Price") if isinstance(listing.get("Price"), dict) else {}
        features = info.get("Features") if isinstance(info.get("Features"), dict) else {}
        feature_values = features.get("DisplayValues") if isinstance(features.get("DisplayValues"), list) else []
        contributors = byline.get("Contributors") if isinstance(byline.get("Contributors"), list) else []
        creator_names = [
            _clean(row.get("Name"), 160)
            for row in contributors
            if isinstance(row, dict) and _clean(row.get("Name"), 160)
        ]
        isbn, barcode = _amazon_identifiers(info)
        results.append(
            {
                "provider": "amazon",
                "title": _display_value(info, "Title"),
                "authors": "; ".join(creator_names[:8]),
                "manufacturer": _display_value(byline, "Manufacturer") or _display_value(byline, "Brand"),
                "model": _display_value(info.get("ProductInfo"), "Model"),
                "categories": _display_value(info.get("Classifications"), "Binding"),
                "description": "\n".join(_clean(value, 500) for value in feature_values[:8] if _clean(value, 500)),
                "isbn": isbn,
                "barcode": barcode,
                "market_price": str(price.get("Amount") or ""),
                "currency": _clean(price.get("Currency"), 3).upper(),
                "price_source": "Amazon",
                "metadata_source": "Amazon Product Advertising API",
                "external_id": _clean(item.get("ASIN"), 40),
                "url": _clean(item.get("DetailPageURL"), 1000),
            }
        )
    return results


def _ebay_token() -> str:
    now = time.time()
    cached = str(_EBAY_TOKEN_CACHE.get("token") or "")
    if cached and float(_EBAY_TOKEN_CACHE.get("expires_at") or 0) > now + EBAY_TOKEN_SKEW_SECONDS:
        return cached
    client_id = os.environ.get("SIMPLEOFFICE_EBAY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SIMPLEOFFICE_EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ValueError("eBay Browse API ist nicht konfiguriert")
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    ).encode("ascii")
    payload = _json_request(
        "api.ebay.com",
        "POST",
        "/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)),
        },
        body=body,
    )
    token = _clean(payload.get("access_token"), 4096)
    if not token:
        raise ValueError("eBay hat kein Zugriffstoken geliefert")
    expires = int(payload.get("expires_in") or 7200)
    _EBAY_TOKEN_CACHE.update(token=token, expires_at=now + max(60, expires))
    return token


def _ebay_request(query: str) -> dict[str, Any]:
    marketplace = os.environ.get("SIMPLEOFFICE_EBAY_MARKETPLACE_ID", "EBAY_DE").strip() or "EBAY_DE"
    params = urlencode({"q": query, "limit": str(MAX_RESULTS)})
    return _json_request(
        "api.ebay.com",
        "GET",
        "/buy/browse/v1/item_summary/search?" + params,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {_ebay_token()}",
            "X-EBAY-C-MARKETPLACE-ID": marketplace,
        },
    )


def parse_ebay_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("itemSummaries") if isinstance(payload.get("itemSummaries"), list) else []
    results: list[dict[str, Any]] = []
    for item in items[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        price = item.get("price") if isinstance(item.get("price"), dict) else {}
        categories = item.get("categories") if isinstance(item.get("categories"), list) else []
        category_names = [
            _clean(row.get("categoryName"), 160)
            for row in categories
            if isinstance(row, dict) and _clean(row.get("categoryName"), 160)
        ]
        results.append(
            {
                "provider": "ebay",
                "title": _clean(item.get("title"), 300),
                "authors": "",
                "manufacturer": "",
                "model": "",
                "categories": "; ".join(category_names[:6]),
                "description": _clean(item.get("shortDescription"), 2000),
                "isbn": "",
                "barcode": "",
                "market_price": _clean(price.get("value"), 32),
                "currency": _clean(price.get("currency"), 3).upper(),
                "price_source": "eBay",
                "metadata_source": "eBay Browse API",
                "external_id": _clean(item.get("itemId"), 120),
                "url": _clean(item.get("itemWebUrl"), 1000),
            }
        )
    return results


def _api_search(provider: str, query: str) -> list[dict[str, Any]]:
    payload = _amazon_request(query) if provider == "amazon" else _ebay_request(query)
    return parse_amazon_results(payload) if provider == "amazon" else parse_ebay_results(payload)


def search_marketplace(provider: str, query: str) -> dict[str, Any]:
    provider = provider.strip().lower()
    query = _clean(query, 200)
    if provider not in {"amazon", "ebay"}:
        raise ValueError("Unbekannter Marketplace")
    if not query:
        raise ValueError("Suchbegriff fehlt")

    api_configured = provider_status()[provider]
    fallback = _amazon_fallback(query) if provider == "amazon" else _ebay_fallback(query)
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    source = "public_page"

    try:
        results = _public_search(provider, query)
        if not results:
            warnings.append("Öffentliche Suche enthielt keine auslesbaren Treffer")
    except ValueError as exc:
        warnings.append(_clean(exc, 240))

    if not results and api_configured:
        source = "api"
        try:
            results = _api_search(provider, query)
        except ValueError as exc:
            warnings.append(_clean(exc, 240))

    return {
        "provider": provider,
        "configured": api_configured,
        "api_configured": api_configured,
        "public_lookup": True,
        "source": source,
        "results": results[:MAX_RESULTS],
        "fallback_url": fallback,
        "warning": " · ".join(dict.fromkeys(warnings)),
    }


@bp.get("/search")
@login_required
def search():
    provider = _clean(request.args.get("provider"), 20).lower()
    query = _clean(request.args.get("q"), 200)
    if provider not in {"amazon", "ebay"} or not query:
        return jsonify({"ok": False, "error": "Marketplace oder Suchbegriff fehlt."}), 400
    store = InventoryEnrichmentStore(os.environ.get("SIMPLEOFFICE_DOCUMENT_ROOT", "database/documents"))
    allowed, retry_after = store.consume_rate_limit(str(g.user["username"]), f"marketplace-{provider}", interval=3)
    if not allowed:
        response = jsonify({"ok": False, "error": "Marketplace-Suche kurz begrenzt.", "retry_after": retry_after})
        response.status_code = 429
        response.headers["Retry-After"] = str(retry_after)
        response.headers["Cache-Control"] = "no-store"
        return response
    try:
        payload = search_marketplace(provider, query)
        payload["ok"] = True
        response = jsonify(payload)
        response.headers["Cache-Control"] = "no-store"
        return response
    except ValueError as exc:
        fallback = _amazon_fallback(query) if provider == "amazon" else _ebay_fallback(query)
        response = jsonify({
            "ok": False,
            "error": _clean(exc, 300),
            "provider": provider,
            "configured": provider_status().get(provider, False),
            "fallback_url": fallback,
        })
        response.status_code = 502
        response.headers["Cache-Control"] = "no-store"
        return response
