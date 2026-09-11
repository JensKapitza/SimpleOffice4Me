from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.telephony_numbering import (
    MasterNumberRegistry,
    prefixes_conflict,
    private_dial_number,
    route_identity,
)


def installation(char: str) -> str:
    return char * 32


def test_private_number_and_canonical_identity() -> None:
    assert private_dial_number("49", "100", "101") == "+49100101"
    assert route_identity("master-de", installation("a"), "100", "101") == {
        "master_id": "master-de",
        "installation_id": installation("a"),
        "base_number": "100",
        "extension": "101",
    }


def test_prefix_overlap_is_rejected() -> None:
    assert prefixes_conflict("42", "421") is True
    assert prefixes_conflict("42", "43") is False


def test_master_approves_unique_server_number() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = MasterNumberRegistry(Path(directory))
        first = store.claim(installation("a"), "100")
        assert first["state"] == "approved"
        assert first["base_number"] == "100"

        with pytest.raises(ValueError):
            store.claim(installation("b"), "100")
        with pytest.raises(ValueError):
            store.claim(installation("b"), "1001")


def test_number_can_be_reused_after_release() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = MasterNumberRegistry(Path(directory))
        store.claim(installation("a"), "200")
        released = store.release(installation("a"))
        assert released["state"] == "released"
        second = store.claim(installation("b"), "200")
        assert second["state"] == "approved"


def test_master_prefix_routes_use_longest_match() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = MasterNumberRegistry(Path(directory))
        store.set_master_route("master-de", "49", "peer-de")
        store.set_master_route("master-nl", "31", "peer-nl")
        route = store.resolve_master_prefix("+49100101")
        assert route["master_id"] == "master-de"
        assert route["next_peer_id"] == "peer-de"
        assert route["remainder"] == "100101"


def test_overlapping_master_prefixes_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = MasterNumberRegistry(Path(directory))
        store.set_master_route("master-a", "49", "peer-a")
        with pytest.raises(ValueError):
            store.set_master_route("master-b", "491", "peer-b")
