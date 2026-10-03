import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from app.federation_moderation import FederationModerationStore
from app.federation_moderation_admin import bp as admin_bp
from app.federation_moderation_http import bp
from app.federation_http import bp as transfer_bp
from app.federation_peer_auth import headers
from app.federation_store import FederationStore
from app.security_controls import protect_browser_mutation


class FederationModerationHttpTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test", DOCUMENT_ROOT=str(self.root))
        self.app.register_blueprint(bp)
        self.app.register_blueprint(admin_bp)
        self.app.register_blueprint(transfer_bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.user = {"id": 1, "username": "admin", "is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.env = patch.dict(os.environ, {"SIMPLEOFFICE_FEDERATION_TOKEN": "shared", "SIMPLEOFFICE_FEDERATION_PEER_ID": "local"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = self.app.test_client()
        self.store = FederationStore(self.root)
        self.store.save_peer("reporter", "Reporter", "https://reporter.example", "reporter-token")
        self.moderation = FederationModerationStore(self.root)
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.csrf = {"X-CSRF-Token": "x" * 40}

    def _report(self, payload=None, peer="reporter", token="reporter-token"):
        path = "/federation/v1/moderation/reports"
        body = json.dumps(payload or {"peer_id": "bad", "reason": "abuse"}).encode()
        proof = headers(peer, token, "POST", path, body)
        return self.client.post(path, data=body, headers=proof), proof, body

    def test_authenticated_report_and_replay(self):
        response, proof, body = self._report()
        self.assertEqual(201, response.status_code)
        self.assertEqual("reporter", self.moderation.reports()[0]["reporter_peer"])
        self.assertEqual(set(), self.store.banned_peer_ids())
        replay = self.client.post("/federation/v1/moderation/reports", headers=proof, data=body)
        self.assertEqual(401, replay.status_code)

    def test_bearer_spoofed_reporter_and_invalid_signature_are_rejected(self):
        bearer = self.client.post("/federation/v1/moderation/reports", json={"peer_id": "bad", "reason": "abuse"}, headers={"Authorization": "Bearer shared"})
        self.assertEqual(401, bearer.status_code)
        response, _, _ = self._report({"peer_id": "bad", "reason": "abuse", "reporter_peer": "victim"})
        self.assertEqual(400, response.status_code)
        response, _, _ = self._report(token="wrong-token")
        self.assertEqual(401, response.status_code)
        self.assertEqual([], self.moderation.reports())

    def test_disabled_banned_empty_token_and_duplicate_tokens_cannot_report(self):
        self.moderation.ban("reporter", "abuse", actor="admin")
        self.assertEqual(401, self._report()[0].status_code)
        self.moderation.unban("reporter", actor="admin")
        self.store.save_peer("reporter", "Reporter", "https://reporter.example", "", enabled=False)
        self.assertEqual(401, self._report()[0].status_code)
        self.store.save_peer("empty", "Empty", "https://empty.example", "")
        self.assertEqual(401, self._report(peer="empty", token="")[0].status_code)
        self.store.save_peer("duplicate", "Duplicate", "https://duplicate.example", "reporter-token")
        self.store.save_peer("reporter", "Reporter", "https://reporter.example", "", enabled=True)
        self.assertEqual(401, self._report()[0].status_code)

    def test_oversized_and_non_object_reports_are_rejected(self):
        response, _, _ = self._report({"peer_id": "bad", "reason": "x" * 5000})
        self.assertEqual(413, response.status_code)
        path = "/federation/v1/moderation/reports"
        response = self.client.post(path, data=b"[]", headers=headers("reporter", "reporter-token", "POST", path, b"[]"))
        self.assertEqual(400, response.status_code)

    def test_feed_is_authenticated_signed_and_never_cacheable(self):
        path = "/federation/v1/moderation/blacklist"
        self.assertEqual(401, self.client.get(path).status_code)
        response = self.client.get(path, headers=headers("reporter", "reporter-token", "GET", path))
        self.assertEqual(200, response.status_code)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertIn("signature", response.json)

    def test_shared_token_and_unsigned_identity_cannot_bypass_active_ban(self):
        path = "/federation/v1/capabilities"
        self.assertEqual(200, self.client.get(path, headers={"Authorization": "Bearer shared"}).status_code)
        self.moderation.ban("bad", "abuse", actor="admin")
        self.assertEqual(401, self.client.get(path, headers={"Authorization": "Bearer shared", "X-SimpleOffice-Peer-ID": "reporter"}).status_code)
        signed = headers("reporter", "reporter-token", "GET", path)
        signed["Authorization"] = "Bearer shared"
        self.assertEqual(200, self.client.get(path, headers=signed).status_code)

    def test_admin_requires_csrf_role_and_explicit_confirmation(self):
        path = "/admin/federation/moderation/actions"
        data = {"operation": "ban", "peer_id": "bad", "reason": "abuse", "confirm": "1"}
        self.assertEqual(403, self.client.post(path, data=data).status_code)
        self.user["is_admin"] = False
        self.assertEqual(403, self.client.post(path, data=data, headers=self.csrf).status_code)
        self.user["is_admin"] = True
        data.pop("confirm")
        self.client.post(path, data=data, headers=self.csrf)
        self.assertEqual(set(), self.store.banned_peer_ids())
        data["confirm"] = "1"
        self.assertEqual(302, self.client.post(path, data=data, headers=self.csrf).status_code)
        self.assertEqual({"bad"}, self.store.banned_peer_ids())


if __name__ == "__main__":
    unittest.main()
