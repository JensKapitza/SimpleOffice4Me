"""Small RFC 9982 / JSContact 2.0 compatibility adapter.

The canonical contact store remains unchanged. This adapter maps the common
JSContact fields into it while retaining unknown top-level properties as opaque
JSON so later export does not silently discard extensions.
"""
from __future__ import annotations

import json
from typing import Any

from .contact_store import ContactStore

_KNOWN = {
    "@type", "version", "uid", "kind", "name", "emails", "phones",
    "organizations", "titles", "notes", "nicknames", "updated",
}
_UNKNOWN_FIELD = "jscontact_unknown_json"


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _preferred(mapping: Any) -> dict[str, Any]:
    rows = []
    for key, value in _object(mapping).items():
        if isinstance(value, dict):
            pref = value.get("pref")
            rows.append((pref if isinstance(pref, int) and pref >= 0 else 2**31, str(key), value))
    return min(rows, default=(0, "", {}))[2]


def _name_values(card: dict[str, Any]) -> tuple[str, str, str]:
    name = _object(card.get("name"))
    first = ""
    last = ""
    ordered: list[str] = []
    components = name.get("components")
    if isinstance(components, list):
        for component in components:
            if not isinstance(component, dict):
                continue
            value = str(component.get("value") or "").strip()
            kind = str(component.get("kind") or "").strip()
            if not value:
                continue
            if kind == "given" and not first:
                first = value
            elif kind == "surname" and not last:
                last = value
            if kind != "separator":
                ordered.append(value)
    full = str(name.get("full") or "").strip()
    display = full or " ".join(ordered).strip() or " ".join(part for part in (first, last) if part)
    return first, last, display


def _validate(card: dict[str, Any]) -> None:
    if not isinstance(card, dict) or card.get("@type") not in (None, "Card"):
        raise ValueError("JSContact payload must be a Card")
    version = str(card.get("version") or "").strip()
    if version not in {"1.0", "2.0"}:
        raise ValueError("unsupported JSContact version")
    uid = card.get("uid")
    if version == "1.0" and not (isinstance(uid, str) and uid.strip()):
        raise ValueError("JSContact 1.0 requires uid")
    if uid is not None and (not isinstance(uid, str) or not uid.strip() or len(uid) > 500):
        raise ValueError("invalid JSContact uid")


def import_card(store: ContactStore, card: dict[str, Any], actor: str) -> dict[str, Any]:
    """Import a JSContact 1.0/2.0 Card into the existing contact store.

    RFC 9982 makes uid optional for version 2.0. If it is absent, ContactStore
    creates its normal stable internal identifier.
    """
    _validate(card)
    first, last, display = _name_values(card)
    values: dict[str, str] = {
        "first_name": first,
        "last_name": last,
        "display_name": display,
    }

    email = _preferred(card.get("emails"))
    if email.get("address"):
        values["email"] = str(email["address"]).strip()

    phone = _preferred(card.get("phones"))
    if phone.get("number"):
        values["phone"] = str(phone["number"]).strip()

    organization = _preferred(card.get("organizations"))
    if organization.get("name"):
        values["company"] = str(organization["name"]).strip()

    title = _preferred(card.get("titles"))
    if title.get("name"):
        key = "role" if title.get("kind") == "role" else "title"
        values[key] = str(title["name"]).strip()

    note = _preferred(card.get("notes"))
    if note.get("note"):
        values["note"] = str(note["note"]).strip()

    nickname = _preferred(card.get("nicknames"))
    if nickname.get("name"):
        values["nickname"] = str(nickname["name"]).strip()

    unknown = {key: value for key, value in card.items() if key not in _KNOWN}
    if unknown:
        encoded = json.dumps(unknown, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded) > 200_000:
            raise ValueError("JSContact extensions are too large")
        values[f"custom_{_UNKNOWN_FIELD}"] = encoded

    uid = str(card.get("uid") or "").strip()
    source = {"provider": "jscontact", "source_id": uid} if uid else {"provider": "jscontact"}
    return store.upsert(values, actor, contact_id=uid, source=source)


def export_card(store: ContactStore, contact_id: str, actor: str = "") -> dict[str, Any]:
    """Export one stored contact as a JSContact 2.0 Card."""
    contact = store.get(contact_id, actor)
    fields = contact.get("fields", {})
    card: dict[str, Any] = {}

    raw_unknown = fields.get(_UNKNOWN_FIELD)
    if raw_unknown:
        try:
            unknown = json.loads(str(raw_unknown))
        except (TypeError, ValueError, json.JSONDecodeError):
            unknown = {}
        if isinstance(unknown, dict):
            card.update({key: value for key, value in unknown.items() if key not in _KNOWN})

    components = []
    if fields.get("first_name"):
        components.append({"kind": "given", "value": str(fields["first_name"])})
    if fields.get("last_name"):
        components.append({"kind": "surname", "value": str(fields["last_name"])})
    full = str(fields.get("display_name") or "").strip()
    name: dict[str, Any] = {"isOrdered": True}
    if components:
        name["components"] = components
    if full:
        name["full"] = full

    card.update({
        "@type": "Card",
        "version": "2.0",
        "uid": str(contact["contact_id"]),
        "name": name,
    })
    if fields.get("email"):
        card["emails"] = {"email": {"address": str(fields["email"]), "pref": 1}}
    if fields.get("phone"):
        card["phones"] = {"phone": {"number": str(fields["phone"]), "pref": 1}}
    if fields.get("company"):
        card["organizations"] = {"org": {"name": str(fields["company"])}}
    if fields.get("title"):
        card.setdefault("titles", {})["title"] = {"kind": "title", "name": str(fields["title"])}
    if fields.get("role"):
        card.setdefault("titles", {})["role"] = {"kind": "role", "name": str(fields["role"])}
    if fields.get("note"):
        card["notes"] = {"note": {"note": str(fields["note"])}}
    if fields.get("nickname"):
        card["nicknames"] = {"nickname": {"name": str(fields["nickname"])}}
    if contact.get("updated_at"):
        card["updated"] = str(contact["updated_at"])
    return card
