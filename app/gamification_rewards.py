"""Reward layer kept strictly separate from truth and authorization decisions."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .gamification_store import GamificationStore

ACCEPTED_FIX_XP = 10
ACHIEVEMENTS = (
    ("first_fix", 1, "Erste bestätigte Verbesserung"),
    ("quality_10", 10, "10 bestätigte Verbesserungen"),
    ("quality_50", 50, "50 bestätigte Verbesserungen"),
)


def _ensure(store: GamificationStore) -> None:
    with store._db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS game_reward (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant TEXT NOT NULL,
                proposal_id TEXT NOT NULL UNIQUE,
                xp INTEGER NOT NULL CHECK(xp >= 0),
                reason TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                FOREIGN KEY(proposal_id) REFERENCES annotation_proposal(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS game_reward_participant
                ON game_reward(participant, awarded_at DESC);
        """)


def award_accepted_proposal(store: GamificationStore, proposal_id: str) -> bool:
    """Award XP once, only for a human proposal that has actually been accepted."""
    _ensure(store)
    with store._db() as db:
        row = db.execute(
            "SELECT p.proposed_by,p.source,i.session_id "
            "FROM annotation_proposal p "
            "JOIN annotation_acceptance a ON a.proposal_id=p.id "
            "JOIN game_item i ON i.id=p.item_id WHERE p.id=?",
            (proposal_id,),
        ).fetchone()
        if row is None or str(row["source"]) != "human":
            return False
        existing = db.execute("SELECT 1 FROM game_reward WHERE proposal_id=?", (proposal_id,)).fetchone()
        if existing is not None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO game_reward(participant,proposal_id,xp,reason,awarded_at) VALUES(?,?,?,?,?)",
            (str(row["proposed_by"]), proposal_id, ACCEPTED_FIX_XP, "accepted_fix", now),
        )
        store._audit(
            db, str(row["session_id"]), str(row["proposed_by"]), "reward.awarded",
            {"proposal_id": proposal_id, "xp": ACCEPTED_FIX_XP, "reason": "accepted_fix"},
        )
    return True


def _streak(days: list[date], today: date | None = None) -> int:
    unique = sorted(set(days), reverse=True)
    if not unique:
        return 0
    current = today or datetime.now(timezone.utc).date()
    if unique[0] not in {current, current - timedelta(days=1)}:
        return 0
    expected = unique[0]
    count = 0
    for value in unique:
        if value != expected:
            break
        count += 1
        expected -= timedelta(days=1)
    return count


def profile(store: GamificationStore, participant: str) -> dict[str, Any]:
    _ensure(store)
    participant = str(participant).strip()
    with store._db() as db:
        rows = db.execute(
            "SELECT xp,awarded_at FROM game_reward WHERE participant=? ORDER BY awarded_at",
            (participant,),
        ).fetchall()
    xp = sum(int(row["xp"]) for row in rows)
    accepted = len(rows)
    days: list[date] = []
    for row in rows:
        try:
            days.append(datetime.fromisoformat(str(row["awarded_at"])).date())
        except ValueError:
            continue
    streak = _streak(days)
    achievements = [
        {"id": achievement_id, "title": title}
        for achievement_id, threshold, title in ACHIEVEMENTS
        if accepted >= threshold
    ]
    if streak >= 3:
        achievements.append({"id": "streak_3", "title": "3-Tage-Serie"})
    if streak >= 7:
        achievements.append({"id": "streak_7", "title": "7-Tage-Serie"})
    return {
        "participant": participant,
        "xp": xp,
        "accepted_fixes": accepted,
        "streak": streak,
        "achievements": achievements,
    }


def leaderboard(store: GamificationStore, limit: int = 20,
                participants: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Return a ranking only for an explicitly supplied visibility set.

    ``None`` is deliberately not interpreted as all users. Callers must decide
    whose identity may be shown; this prevents a reward view becoming a user
    directory by accident.
    """
    _ensure(store)
    limit = max(1, min(int(limit), 100))
    visible = sorted({str(value).strip() for value in (participants or ()) if str(value).strip() and not str(value).startswith("peer:")})
    if not visible:
        return []
    with store._db() as db:
        db.execute("CREATE TEMP TABLE IF NOT EXISTS game_visible_participant(participant TEXT PRIMARY KEY)")
        db.execute("DELETE FROM game_visible_participant")
        db.executemany(
            "INSERT INTO game_visible_participant(participant) VALUES(?)",
            ((participant,) for participant in visible),
        )
        rows = db.execute(
            """SELECT r.participant,SUM(r.xp) xp,COUNT(*) accepted_fixes
               FROM game_reward r
               JOIN game_visible_participant v ON v.participant=r.participant
               GROUP BY r.participant
               ORDER BY xp DESC,accepted_fixes DESC,r.participant
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
