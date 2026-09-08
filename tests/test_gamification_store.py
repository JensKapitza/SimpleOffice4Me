import sqlite3

import pytest

from app.gamification_store import GamificationStore


def test_independent_votes_and_consensus(tmp_path):
    store = GamificationStore(tmp_path)
    session = store.create_session("Familienrunde", "local", "owner", {"providers": ["images"]})
    item = store.add_item(session, "images", "image:123")
    proposal = store.propose(item, "tag", "Wald", "a")
    store.vote(proposal, "a", True)
    store.vote(proposal, "b", True)
    store.vote(proposal, "c", True)
    store.vote(proposal, "d", False)
    result = store.consensus(proposal)
    assert result["total"] == 4
    assert result["approvals"] == 3
    assert result["ratio"] == 0.75
    assert result["reached"] is True


def test_second_vote_replaces_first_instead_of_counting_twice(tmp_path):
    store = GamificationStore(tmp_path)
    session = store.create_session("Runde", "local", "owner", {})
    item = store.add_item(session, "contacts", "contact:1")
    proposal = store.propose(item, "city", "Koeln", "owner")
    store.vote(proposal, "person", False)
    store.vote(proposal, "person", True)
    result = store.consensus(proposal, min_votes=1)
    assert result == {"total": 1, "approvals": 1, "ratio": 1.0, "reached": True}


def test_session_creation_is_audited(tmp_path):
    store = GamificationStore(tmp_path)
    session = store.create_session("Runde", "organization", "admin", {})
    with sqlite3.connect(store.path) as db:
        row = db.execute("SELECT actor, action FROM game_audit WHERE session_id=?", (session,)).fetchone()
    assert row == ("admin", "session.created")


def test_challenge_is_bound_to_session_creator_and_becomes_proposal(tmp_path):
    store = GamificationStore(tmp_path)
    session = store.create_session("Lokale Runde", "local", "owner", {})
    item = store.add_item(session, "contacts", "contact:1")
    challenge = store.add_challenge(item, "city", "text", "Welcher Ort?", {"display_name": "Test"})

    assert store.get_challenge_for_actor(challenge, "stranger") is None
    assert store.get_challenge_for_actor(challenge, "owner")["kind"] == "city"

    proposal = store.answer_challenge(challenge, "owner", "Duisburg")
    assert proposal
    assert store.get_challenge_for_actor(challenge, "owner") is None

    with sqlite3.connect(store.path) as db:
        row = db.execute(
            "SELECT p.field_name, p.value_json, c.status, c.answered_by "
            "FROM annotation_proposal p JOIN game_challenge c ON c.item_id=p.item_id "
            "WHERE p.id=? AND c.id=?",
            (proposal, challenge),
        ).fetchone()
    assert row == ("city", '"Duisburg"', "answered", "owner")


def test_challenge_cannot_be_answered_by_other_actor(tmp_path):
    store = GamificationStore(tmp_path)
    session = store.create_session("Lokale Runde", "local", "owner", {})
    item = store.add_item(session, "documents", "document:1")
    challenge = store.add_challenge(item, "tags", "tags", "Tags?")

    with pytest.raises(ValueError):
        store.answer_challenge(challenge, "stranger", "Vertrag")
