"""Reject SQL injection attempts in federation schema migration helpers."""
import sqlite3
import unittest

from app.federation_peer_schema import _ensure_column


class FederationSchemaSecurityTests(unittest.TestCase):
    def test_unapproved_table_column_and_definition_fail_closed(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE federation_peer_identity (peer_id TEXT)")
        for table, name, definition in (
            ("federation_peer_identity; DROP TABLE federation_peer_identity;--", "public_key", "TEXT NOT NULL DEFAULT ''"),
            ("federation_peer_identity", 'public_key"; DROP TABLE federation_peer_identity;--', "TEXT NOT NULL DEFAULT ''"),
            ("federation_peer_identity", "public_key", "TEXT; DROP TABLE federation_peer_identity;--"),
        ):
            with self.subTest(table=table, name=name, definition=definition):
                with self.assertRaises(ValueError):
                    _ensure_column(db, table, name, definition)
        self.assertIsNotNone(db.execute("SELECT peer_id FROM federation_peer_identity").description)
        db.close()

    def test_approved_migration_adds_column_once(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE federation_peer_identity (peer_id TEXT)")
        for _ in range(2):
            _ensure_column(db, "federation_peer_identity", "public_key", "TEXT NOT NULL DEFAULT ''")
        columns = [row["name"] for row in db.execute("PRAGMA table_info(federation_peer_identity)")]
        self.assertEqual(columns.count("public_key"), 1)
        db.close()


if __name__ == "__main__":
    unittest.main()
