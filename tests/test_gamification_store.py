import sqlite3

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
