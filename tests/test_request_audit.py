import json
import tempfile
import unittest
from pathlib import Path

from flask import Response, g

from app import app
from app.db import ensure_auth_database, get_db
from app.request_audit import audit_mutation_response


class RequestAuditTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {key: app.config.get(key) for key in ("DATABASE", "TESTING")}
        app.config.update(TESTING=True, DATABASE=str(Path(self.temp.name) / "audit.sqlite"))
        with app.app_context():
            ensure_auth_database()

    def tearDown(self):
        app.config.update(self.previous)
        self.temp.cleanup()

    def test_authenticated_mutation_records_who_what_when_without_secrets(self):
        with app.test_request_context(
            "/documents/example/abc", method="POST",
            data={"title": "visible field", "password": "must-not-be-logged", "api_token": "also-secret"},
        ):
            g.user = {"id": None, "username": "alice"}
            g.request_id = "request-123"
            response = audit_mutation_response(Response(status=200))
            self.assertEqual(200, response.status_code)
            row = get_db().execute(
                "SELECT * FROM security_event WHERE action = 'http_mutation' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual("alice", row["actor_name"])
            self.assertEqual("success", row["outcome"])
            self.assertTrue(row["occurred_at"])
            detail = json.loads(row["detail"])
            self.assertEqual("POST", detail["method"])
            self.assertEqual("request-123", detail["request_id"])
            self.assertIn("title", detail["form_fields"])
            self.assertNotIn("password", detail["form_fields"])
            self.assertNotIn("api_token", detail["form_fields"])
            serialized = json.dumps(detail)
            self.assertNotIn("must-not-be-logged", serialized)
            self.assertNotIn("also-secret", serialized)

    def test_read_requests_are_not_added_to_mutation_audit(self):
        with app.test_request_context("/documents/example", method="GET"):
            g.user = {"id": None, "username": "alice"}
            audit_mutation_response(Response(status=200))
            count = get_db().execute(
                "SELECT COUNT(*) FROM security_event WHERE action = 'http_mutation'"
            ).fetchone()[0]
            self.assertEqual(0, count)

    def test_denied_authenticated_mutation_is_visible(self):
        with app.test_request_context("/admin/restricted", method="DELETE"):
            g.user = {"id": None, "username": "alice"}
            g.request_id = "denied-1"
            audit_mutation_response(Response(status=403))
            row = get_db().execute(
                "SELECT outcome, detail FROM security_event WHERE action = 'http_mutation' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual("denied", row["outcome"])
            self.assertEqual(403, json.loads(row["detail"])["status"])


if __name__ == "__main__":
    unittest.main()
