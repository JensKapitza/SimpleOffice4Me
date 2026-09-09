"""Organization membership for company gamification rounds.

Membership is stored against real local user ids. Merely knowing an organization
name never grants access; callers still need the normal object ACL and explicit
session participation in the gamification store.
"""
from __future__ import annotations

import re
from typing import Any

ORG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
ROLES = frozenset({"member", "manager", "owner"})


def ensure_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS game_organization (
            org_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(created_by) REFERENCES user(id)
        );
        CREATE TABLE IF NOT EXISTS game_organization_member (
            org_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(org_id,user_id),
            FOREIGN KEY(org_id) REFERENCES game_organization(org_id) ON DELETE CASCADE,
            FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS game_organization_member_user
            ON game_organization_member(user_id,org_id);
    """)
    db.commit()


def _user(db, username: str):
    return db.execute(
        "SELECT id,username,is_admin,is_disabled FROM user WHERE username=?",
        (str(username).strip(),),
    ).fetchone()


def create_organization(db, org_id: str, name: str, actor: str) -> dict[str, Any]:
    ensure_tables(db)
    org_id = str(org_id).strip().casefold()
    name = str(name).strip()[:160]
    owner = _user(db, actor)
    if owner is None or owner["is_disabled"] or not owner["is_admin"]:
        raise ValueError("administrator required")
    if not ORG_ID_RE.fullmatch(org_id) or not name:
        raise ValueError("invalid organization")
    db.execute(
        "INSERT INTO game_organization(org_id,name,created_by) VALUES(?,?,?)",
        (org_id, name, int(owner["id"])),
    )
    db.execute(
        "INSERT INTO game_organization_member(org_id,user_id,role) VALUES(?,?,?)",
        (org_id, int(owner["id"]), "owner"),
    )
    db.commit()
    return {"org_id": org_id, "name": name, "owner": actor}


def add_member(db, org_id: str, username: str, role: str, actor: str) -> None:
    ensure_tables(db)
    org_id = str(org_id).strip().casefold()
    role = str(role).strip().casefold()
    target = _user(db, username)
    operator = _user(db, actor)
    if target is None or target["is_disabled"] or operator is None or operator["is_disabled"]:
        raise ValueError("unknown organization user")
    if role not in ROLES:
        raise ValueError("invalid organization role")
    authorization = db.execute(
        "SELECT role FROM game_organization_member WHERE org_id=? AND user_id=?",
        (org_id, int(operator["id"])),
    ).fetchone()
    operator_role = str(authorization["role"]) if authorization is not None else ""
    if not operator["is_admin"] and operator_role not in {"owner", "manager"}:
        raise ValueError("organization manager required")
    current_target = db.execute(
        "SELECT role FROM game_organization_member WHERE org_id=? AND user_id=?",
        (org_id, int(target["id"])),
    ).fetchone()
    target_role = str(current_target["role"]) if current_target is not None else ""
    # Managers may maintain ordinary membership, but ownership is a stronger
    # administrative boundary and can only be changed by an admin or owner.
    if not operator["is_admin"] and operator_role != "owner" and (role == "owner" or target_role == "owner"):
        raise ValueError("organization owner required")
    if db.execute("SELECT 1 FROM game_organization WHERE org_id=?", (org_id,)).fetchone() is None:
        raise ValueError("unknown organization")
    db.execute(
        "INSERT INTO game_organization_member(org_id,user_id,role) VALUES(?,?,?) "
        "ON CONFLICT(org_id,user_id) DO UPDATE SET role=excluded.role",
        (org_id, int(target["id"]), role),
    )
    db.commit()


def organizations_for_user(db, username: str) -> list[dict[str, Any]]:
    ensure_tables(db)
    user = _user(db, username)
    if user is None or user["is_disabled"]:
        return []
    rows = db.execute(
        "SELECT o.org_id,o.name,m.role FROM game_organization o "
        "JOIN game_organization_member m ON m.org_id=o.org_id "
        "WHERE m.user_id=? ORDER BY o.name COLLATE NOCASE,o.org_id",
        (int(user["id"]),),
    ).fetchall()
    return [dict(row) for row in rows]


def common_organization(db, first: str, second: str, *, org_id: str = "") -> str | None:
    ensure_tables(db)
    left = _user(db, first)
    right = _user(db, second)
    if left is None or right is None or left["is_disabled"] or right["is_disabled"]:
        return None
    params: list[Any] = [int(left["id"]), int(right["id"])]
    sql = (
        "SELECT a.org_id FROM game_organization_member a "
        "JOIN game_organization_member b ON b.org_id=a.org_id "
        "WHERE a.user_id=? AND b.user_id=?"
    )
    if org_id:
        sql += " AND a.org_id=?"
        params.append(str(org_id).strip().casefold())
    sql += " ORDER BY a.org_id LIMIT 1"
    row = db.execute(sql, tuple(params)).fetchone()
    return str(row["org_id"]) if row else None


def bind_participant(game_store, db, session_id: str, participant: str, *, org_id: str, actor: str,
                     role: str = "answer") -> None:
    """Add a company participant only when both real users share the requested org."""
    if common_organization(db, actor, participant, org_id=org_id) != str(org_id).strip().casefold():
        raise ValueError("users do not share the organization")
    with game_store._db() as game_db:
        session = game_db.execute(
            "SELECT scope,created_by,policy_json FROM game_session WHERE id=? AND status='active'",
            (session_id,),
        ).fetchone()
    if session is None or session["scope"] != "organization" or str(session["created_by"]) != str(actor):
        raise ValueError("organization session is not owned by actor")
    game_store.add_participant(session_id, participant, role=role, actor=actor)
