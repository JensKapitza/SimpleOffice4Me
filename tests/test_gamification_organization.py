import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.gamification_organization import (
    add_member,
    bind_participant,
    common_organization,
    create_organization,
    organizations_for_user,
)
from app.gamification_store import GamificationStore


class GamificationOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute(
            "CREATE TABLE user (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, is_disabled INTEGER NOT NULL DEFAULT 0)"
        )
        self.db.executemany(
            "INSERT INTO user(id,username,is_admin,is_disabled) VALUES(?,?,?,?)",
            [(1, "admin", 1, 0), (2, "anna", 0, 0), (3, "max", 0, 0), (4, "other", 0, 0)],
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.game = GamificationStore(Path(self.tmp.name))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_real_users_must_share_organization(self):
        create_organization(self.db, "firma-a", "Firma A", "admin")
        add_member(self.db, "firma-a", "anna", "manager", "admin")
        add_member(self.db, "firma-a", "max", "member", "anna")
        self.assertEqual(common_organization(self.db, "anna", "max"), "firma-a")
        self.assertIsNone(common_organization(self.db, "anna", "other"))
        self.assertEqual(organizations_for_user(self.db, "max")[0]["org_id"], "firma-a")

    def test_company_session_refuses_non_member(self):
        create_organization(self.db, "firma-a", "Firma A", "admin")
        add_member(self.db, "firma-a", "anna", "member", "admin")
        session = self.game.create_session("Firma", "organization", "admin", {"org_id": "firma-a"})
        bind_participant(self.game, self.db, session, "anna", org_id="firma-a", actor="admin")
        with self.assertRaises(ValueError):
            bind_participant(self.game, self.db, session, "other", org_id="firma-a", actor="admin")
        with self.game._db() as game_db:
            rows = game_db.execute("SELECT participant FROM game_participant WHERE session_id=?", (session,)).fetchall()
        self.assertEqual([row["participant"] for row in rows], ["anna"])

    def test_non_admin_cannot_create_organization(self):
        with self.assertRaises(ValueError):
            create_organization(self.db, "firma-a", "Firma A", "anna")

    def test_manager_cannot_promote_owner_or_demote_existing_owner(self):
        create_organization(self.db, "firma-a", "Firma A", "admin")
        add_member(self.db, "firma-a", "anna", "manager", "admin")
        add_member(self.db, "firma-a", "max", "member", "admin")
        with self.assertRaises(ValueError):
            add_member(self.db, "firma-a", "max", "owner", "anna")
        with self.assertRaises(ValueError):
            add_member(self.db, "firma-a", "admin", "member", "anna")
        roles = {
            row["username"]: row["role"]
            for row in self.db.execute(
                "SELECT u.username,m.role FROM game_organization_member m JOIN user u ON u.id=m.user_id WHERE m.org_id='firma-a'"
            ).fetchall()
        }
        self.assertEqual(roles["admin"], "owner")
        self.assertEqual(roles["max"], "member")


if __name__ == "__main__":
    unittest.main()
