"""API-free company lookup helpers and safe contact-source provenance."""
from __future__ import annotations

import ipaddress
import re
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit

from .document_store import utc_now

_COMPANY_DATA_FIELDS = {
    "display_name", "company", "email", "phone", "website",
    "street", "house_number", "postal", "city", "state", "country",
}
_ADDRESS_FIELDS = {"street", "house_number", "postal", "city", "state", "country"}
_FIELD_LIMITS = {
    "display_name": 300, "company": 300, "email": 254, "phone": 100,
    "website": 500, "street": 300, "house_number": 30, "postal": 20,
    "city": 100, "state": 100, "country": 2,
}


def company_data_source(url: str, fields: set[str]) -> dict[str, Any] | None:
    if not fields:
        return None
    if fields - _COMPANY_DATA_FIELDS:
        raise ValueError("Die Firmendaten-Feldzuordnung ist ungültig.")
    if not url or len(url) > 2000:
        raise ValueError("Bitte für die Firmendaten eine HTTPS-Quelle angeben.")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        port = parsed.port
        host = hostname.encode("idna").decode("ascii").casefold()
        path = parsed.path or "/"
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Die HTTPS-Quelle ist ungültig.") from exc
    try:
        ip_address = ipaddress.ip_address(host)
    except ValueError:
        ip_address = None
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or "." not in host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or not re.fullmatch(r"[a-z0-9.-]{1,253}", host)
        or len(path) > 500
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or host.startswith(".")
        or host.endswith(".")
        or ".." in host
        or any(label.startswith("-") or label.endswith("-") for label in host.split("."))
        or (ip_address is not None and not ip_address.is_global)
        or host.rsplit(".", 1)[-1] in {"example", "home", "internal", "invalid", "lan", "local", "localhost", "test"}
    ):
        raise ValueError("Die Quelle muss eine öffentliche HTTPS-Webseite sein.")
    return {"url": f"https://{host}{path}", "host": host, "fields": sorted(fields)}


def company_data_metadata_from_form(
    values: dict[str, Any],
    existing: dict[str, Any] | None,
    format_address: Callable[[dict[str, str]], str],
) -> dict[str, Any]:
    source_url = str(values.pop("company_data_source", "")).strip()
    source_fields = {
        item.strip()
        for item in str(values.pop("company_data_fields", "")).split(",")
        if item.strip()
    }
    address_values = {
        key: str(values.pop(f"company_data_{key}", "")).strip()
        for key in _ADDRESS_FIELDS
    }
    if source_fields - _COMPANY_DATA_FIELDS:
        raise ValueError("Die Firmendaten-Feldzuordnung ist ungültig.")
    for key in source_fields:
        value = address_values[key] if key in _ADDRESS_FIELDS else str(values.get(key, "")).strip()
        if len(value) > _FIELD_LIMITS[key]:
            raise ValueError(f"Der Firmendaten-Wert für {key} ist zu lang.")
        if key == "country" and value and not re.fullmatch(r"[A-Za-z]{2}", value):
            raise ValueError("Das Land muss als zweistelliger ISO-Code angegeben werden.")
    for key in source_fields - _ADDRESS_FIELDS:
        if not str(values.get(key, "")).strip():
            source_fields.discard(key)
    for key in source_fields.intersection(_ADDRESS_FIELDS):
        if not address_values[key]:
            source_fields.discard(key)
    source = company_data_source(source_url, source_fields)
    metadata: dict[str, Any] = {}
    raw_addresses = existing.get("addresses", []) if existing else []
    existing_addresses = raw_addresses if isinstance(raw_addresses, list) else []
    components = {
        key: value for key, value in address_values.items()
        if key in source_fields and value
    }
    if components.get("house_number"):
        components["street"] = " ".join(
            part for part in (components.get("street", ""), components.pop("house_number")) if part
        )
    if components:
        address_value = format_address(components)
        normalized_address = " ".join(address_value.casefold().split())
        duplicate = any(
            isinstance(item, dict)
            and " ".join(str(item.get("value", "")).casefold().split()) == normalized_address
            for item in existing_addresses
        )
        if duplicate:
            source_fields.difference_update(_ADDRESS_FIELDS)
        else:
            metadata["append_addresses"] = [
                {"label": "Geschäftlich", "value": address_value, "components": components},
            ]
    if source is not None and source_fields:
        source["fields"] = sorted(source_fields)
        metadata["company_data_source"] = source
    return metadata


def append_company_data_source(
    existing: object,
    source: dict[str, Any] | None,
    actor: str,
) -> list[dict[str, Any]]:
    entries = [item for item in existing if isinstance(item, dict)] if isinstance(existing, list) else []
    if source is not None:
        cleaned = company_data_source(str(source.get("url", "")), set(source.get("fields", [])))
        if cleaned is not None:
            entries.append({**cleaned, "retrieved_at": utc_now(), "recorded_by": actor})
    return entries


def append_company_data_addresses(
    existing: list[dict[str, Any]],
    incoming: object,
    actor: str,
    changed_at: str,
    format_address: Callable[[dict[str, str]], str],
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(incoming, (list, tuple)):
        return existing
    original_count = len(existing)
    allowed_fields = _ADDRESS_FIELDS
    for raw_address in incoming[:10]:
        if not isinstance(raw_address, dict):
            continue
        components = {
            str(key): str(value).strip()
            for key, value in dict(raw_address.get("components", {})).items()
            if str(key) in allowed_fields and str(value).strip()
        }
        value = str(raw_address.get("value", "")).strip() or format_address(components)
        normalized = " ".join(value.casefold().split())
        if not normalized or any(
            " ".join(str(item.get("value", "")).casefold().split()) == normalized
            for item in existing
        ):
            continue
        if len(existing) >= 100:
            raise ValueError("a contact may have at most 100 addresses")
        existing.append({
            "id": str(uuid.uuid4()),
            "label": str(raw_address.get("label", "Geschäftlich")).strip() or "Geschäftlich",
            "value": value,
            "normalized": normalized,
            "components": components,
            "created_at": changed_at,
            "created_by": actor,
        })
    if len(existing) == original_count and metadata:
        source = metadata.get("company_data_source")
        if isinstance(source, dict):
            address_fields = {"street", "house_number", "postal", "city", "state", "country"}
            source["fields"] = [field for field in source.get("fields", []) if field not in address_fields]
            if not source["fields"]:
                metadata.pop("company_data_source", None)
    return existing


def company_name(contact: dict[str, Any]) -> str:
    fields = contact.get("fields", {})
    return str(fields.get("company") or fields.get("display_name") or "").strip()


def company_people(company: dict[str, Any], contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find visible people assigned to a company, including legacy exact-name links."""
    company_id = str(company.get("contact_id", ""))
    names = {
        value.casefold()
        for value in (company_name(company), str(company.get("fields", {}).get("display_name", "")).strip())
        if value
    }
    rows = []
    for contact in contacts:
        if contact.get("contact_id") == company_id:
            continue
        fields = contact.get("fields", {})
        explicit = str(fields.get("company_contact_id", "")) == company_id
        legacy = not fields.get("company_contact_id") and str(fields.get("company", "")).strip().casefold() in names
        if explicit or legacy:
            rows.append(contact)
    return rows


def company_people_for_store(store: Any, company: dict[str, Any], actor: str = "") -> list[dict[str, Any]]:
    return company_people(company, store.contacts(actor))


def company_search_items(store, query: str, actor: str, excluded: str = "", limit: int = 10) -> list[dict[str, str]]:
    query_key = " ".join(query.casefold().split())
    ranked_rows: list[tuple[tuple, dict[str, str]]] = []
    for contact in store.search(query, actor):
        if contact.get("contact_id") == excluded:
            continue
        fields = contact.get("fields", {})
        name = company_name(contact)
        if not name:
            continue
        item = {
            "source": "contact",
            "contact_id": str(contact["contact_id"]),
            "company_name": name,
            "display_name": str(fields.get("display_name", "")),
            "email": str(fields.get("email", "")),
        }
        normalized_name = " ".join(name.casefold().split())
        rank = (
            normalized_name != query_key,
            not normalized_name.startswith(query_key),
            normalized_name,
            item["display_name"].casefold(),
            item["contact_id"],
        )
        ranked_rows.append((rank, item))
        ranked_rows.sort(key=lambda row: row[0])
        if len(ranked_rows) > limit:
            ranked_rows.pop()
    return [item for _rank, item in ranked_rows]
