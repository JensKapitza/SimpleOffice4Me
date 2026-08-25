from __future__ import annotations

import base64

import pytest
from flask import Flask

from app import carddav
from app.contact_store import ContactStore


def _auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_reader_can_read_but_not_modify_contact(tmp_path):
    store = ContactStore(tmp_path)
    contact = store.upsert({"display_name": "Ada Lovelace", "email": "ada@example.test"}, "owner")
    store.share(contact["contact_id"], ["editor"], "owner", ["reader", "editor"])

    assert [row["contact_id"] for row in store.contacts("reader")] == [contact["contact_id"]]
    assert store.get(contact["contact_id"], "reader")["fields"]["email"] == "ada@example.test"
    assert store.can_manage(contact["contact_id"], "reader") is False
    assert store.can_manage(contact["contact_id"], "editor") is True

    with pytest.raises(ValueError):
        store.upsert({"display_name": "Changed"}, "reader", contact["contact_id"])
    with pytest.raises(ValueError):
        store.delete(contact["contact_id"], "reader")

    updated = store.upsert({"display_name": "Ada Edited", "email": "ada@example.test"}, "editor", contact["contact_id"])
    assert updated["fields"]["display_name"] == "Ada Edited"

    stored = store.get(contact["contact_id"])
    assert stored["managers"] == ["editor"]
    assert stored["readers"] == ["reader"]


def test_reader_carddav_get_report_and_write_denial(tmp_path):
    store = ContactStore(tmp_path)
    contact = store.upsert({"display_name": "Read Only", "email": "reader@example.test"}, "owner")
    store.share(contact["contact_id"], [], "owner", ["reader"])
    password = "reader-app-password"
    store.activate_carddav("reader", password, "reader")

    app = Flask(__name__)
    app.config.update(TESTING=True, DOCUMENT_ROOT=str(tmp_path))
    app.register_blueprint(carddav.bp)
    client = app.test_client()
    headers = _auth("reader", password)
    url = f"/carddav/addressbooks/reader/default/{contact['contact_id']}.vcf"

    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert "FN:Read Only" in response.get_data(as_text=True)

    report = client.open(
        "/carddav/addressbooks/reader/default/",
        method="REPORT",
        headers={**headers, "Content-Type": "application/xml"},
        data="<card:addressbook-query xmlns:card='urn:ietf:params:xml:ns:carddav'/>",
    )
    assert report.status_code == 207
    assert f"{contact['contact_id']}.vcf" in report.get_data(as_text=True)

    propfind = client.open(url, method="PROPFIND", headers={**headers, "Depth": "0"})
    text = propfind.get_data(as_text=True)
    assert propfind.status_code == 207
    assert "<d:read/>" in text
    assert "<d:write-content/>" not in text

    replacement = "\r\n".join([
        "BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}",
        "FN:Should Not Change", "N:Change;Should;;;", "END:VCARD", "",
    ])
    assert client.put(url, headers={**headers, "Content-Type": "text/vcard"}, data=replacement).status_code == 403
    assert client.delete(url, headers=headers).status_code == 403
    assert store.get(contact["contact_id"])["fields"]["display_name"] == "Read Only"


def test_carddav_vcard_roundtrip_keeps_tags_and_groups(tmp_path):
    store = ContactStore(tmp_path)
    contact = store.upsert({"display_name": "Thunder Bird", "email": "tb@example.test"}, "owner")
    password = "owner-app-password"
    store.activate_carddav("owner", password, "owner")

    app = Flask(__name__)
    app.config.update(TESTING=True, DOCUMENT_ROOT=str(tmp_path))
    app.register_blueprint(carddav.bp)
    client = app.test_client()
    headers = _auth("owner", password)
    url = f"/carddav/addressbooks/owner/default/{contact['contact_id']}.vcf"

    card = "\r\n".join([
        "BEGIN:VCARD", "VERSION:4.0", f"UID:{contact['contact_id']}",
        "FN:Thunder Bird", "N:Bird;Thunder;;;", "EMAIL:tb@example.test",
        "CATEGORIES:Kunde,Projekt A", "X-SIMPLEOFFICE-GROUP:Team,Privat",
        "END:VCARD", "",
    ])
    response = client.put(url, headers={**headers, "Content-Type": "text/vcard"}, data=card)
    assert response.status_code == 204

    returned = client.get(url, headers=headers)
    assert returned.status_code == 200
    text = returned.get_data(as_text=True)
    assert "CATEGORIES:Kunde,Projekt A" in text
    assert "X-SIMPLEOFFICE-GROUP:Privat,Team" in text or "X-SIMPLEOFFICE-GROUP:Team,Privat" in text

    stored = store.get(contact["contact_id"])
    assert stored["tags"] == ["Kunde", "Projekt A"]
    assert stored["groups"] == ["Privat", "Team"]
