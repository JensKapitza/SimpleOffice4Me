import json

from app.contact_store import ContactStore
from app.gamification_adapters import contact_candidates


def _write_contacts(root, contacts):
    store = ContactStore(root)
    store.initialize()
    store.contacts_path.write_text(json.dumps({"contacts": contacts}), encoding="utf-8")


def _contact(contact_id, name, owner="owner", *, fields=None, readers=None, tags=None):
    payload = {
        "contact_id": contact_id,
        "fields": {"display_name": name, **(fields or {})},
        "addresses": [],
        "owner": owner,
        "managers": [],
        "readers": readers or [],
        "tags": tags or [],
        "groups": [],
        "changes": [],
        "created_by": owner,
    }
    return payload


def test_contact_adapter_only_returns_actor_visible_contacts(tmp_path):
    _write_contacts(tmp_path, [
        _contact("owned", "Owned"),
        _contact("shared", "Shared", owner="other", readers=["owner"]),
        _contact("hidden", "Hidden", owner="other"),
    ])

    candidates = contact_candidates(tmp_path, "owner")
    refs = {candidate.object_ref for candidate in candidates}

    assert refs == {"contact:owned", "contact:shared"}


def test_contact_adapter_excludes_crm_and_financial_contacts(tmp_path):
    _write_contacts(tmp_path, [
        _contact("normal", "Normal", fields={"phone": "123"}),
        _contact("customer", "Customer", fields={"customer_number": "K-1"}),
        _contact("bank", "Bank", fields={"bank_iban": "DE00"}),
        _contact("tagged", "Tagged", tags=["CRM"]),
    ])

    candidates = contact_candidates(tmp_path, "owner")

    assert [candidate.object_ref for candidate in candidates] == ["contact:normal"]
    assert candidates[0].data["display_name"] == "Normal"
    assert "phone" in candidates[0].data["existing_fields"]
    assert "123" not in str(candidates[0].data)


def test_contact_adapter_never_copies_notes_or_sharing_metadata(tmp_path):
    _write_contacts(tmp_path, [
        _contact("one", "One", fields={"note": "private text", "email": "a@example.invalid"}),
    ])

    candidate = contact_candidates(tmp_path, "owner")[0]

    assert set(candidate.data) == {"display_name", "existing_fields"}
    assert "private text" not in str(candidate.data)
    assert "a@example.invalid" not in str(candidate.data)
