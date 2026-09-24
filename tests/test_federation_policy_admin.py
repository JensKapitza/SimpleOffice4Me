import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, g

from app.federation_peer_admin import bp
from app.federation_trust_store import FederationTrustStore
from app.security_controls import protect_browser_mutation
from app.v2.federation_policy import FederationPolicyStore


class FederationPolicyAdminTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "documents"
        self.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        self.app.config.update(
            TESTING=True,
            TEST_CSRF_PROTECTION=True,
            SECRET_KEY="test",
            DOCUMENT_ROOT=str(self.root),
        )
        self.app.register_blueprint(bp)
        self.app.add_url_rule("/login", endpoint="auth.login", view_func=lambda: "Login")
        self.user = {"id": 1, "username": "admin", "is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}
        FederationTrustStore(self.root).remember("peer-c", country="DE", source="test")

    def _latest_flash(self):
        with self.client.session_transaction() as session:
            flashes = session.get("_flashes", [])
            return flashes[-1][1] if flashes else ""

    def test_global_block_is_persisted_and_audited(self):
        with patch("app.federation_peer_admin._audit_policy", return_value=True) as audit:
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/block",
                data={"scope": "all", "reason": "local deny"},
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        blocks = FederationPolicyStore(self.root).active_blocks()
        self.assertEqual(1, len(blocks))
        self.assertEqual("peer-c", blocks[0]["peer_id"])
        self.assertEqual("all", blocks[0]["scope"])
        audit.assert_called_once()
        self.assertIn("Peer gesperrt", self._latest_flash())

    def test_object_block_is_persisted_and_can_remove_one_object(self):
        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/block",
                data={
                    "scope": "documents",
                    "object_refs": "object-1, object-2",
                    "reason": "restricted documents",
                },
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        store = FederationPolicyStore(self.root)
        self.assertEqual([], store.active_blocks())
        blocks = store.active_object_blocks(peer_id="peer-c")
        self.assertEqual(["object-1", "object-2"], [row["object_ref"] for row in blocks])
        self.assertIn("Objektsperre", self._latest_flash())

        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/unblock",
                data={"scope": "documents", "object_ref": "object-1"},
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        remaining = store.active_object_blocks(peer_id="peer-c")
        self.assertEqual(["object-2"], [row["object_ref"] for row in remaining])

    def test_global_block_does_not_create_empty_job_or_authorization_stores(self):
        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/block",
                data={"scope": "all"},
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        self.assertFalse(
            (self.root / ".simpleoffice-v2" / "authorization.sqlite3").exists()
        )
        self.assertFalse(
            (self.root / ".simpleoffice-v2" / "jobs.sqlite3").exists()
        )

    def test_scope_block_can_be_removed_without_changing_trust(self):
        trust = FederationTrustStore(self.root)
        trust.set_trust("peer-c", "HIGH", "VERIFIED_ADMIN")
        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/block",
                data={"scope": "chat"},
                headers=self.headers,
            )
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/unblock",
                data={"scope": "chat"},
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        self.assertEqual([], FederationPolicyStore(self.root).active_blocks())
        self.assertEqual("HIGH", trust.get_trust("peer-c")["trust_level"])

    def test_route_constraint_form_persists_direct_and_relay_rules(self):
        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/route",
                data={
                    "scope": "documents",
                    "direct_only": "1",
                    "max_hops": "2",
                    "allowed_relays": "peer-x, peer-y",
                    "denied_relays": "peer-z",
                },
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        rules = [
            rule for rule in FederationPolicyStore(self.root).route_constraints(
                "peer-c", scope="documents"
            )
            if rule.scope == "documents"
        ]
        self.assertEqual(1, len(rules))
        self.assertTrue(rules[0].direct_only)
        self.assertEqual(2, rules[0].max_hops)
        self.assertEqual(("peer-x", "peer-y"), rules[0].allowed_relays)
        self.assertEqual(("peer-z",), rules[0].denied_relays)

    def test_confirmation_form_persists_quorum(self):
        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/confirmation",
                data={
                    "scope": "relay",
                    "verifier_peers": "peer-x, peer-y, peer-z",
                    "quorum": "2",
                },
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        rules = [
            rule for rule in FederationPolicyStore(self.root).trust_requirements(
                "peer-c", scope="relay"
            )
            if rule.scope == "relay"
        ]
        self.assertEqual(1, len(rules))
        self.assertEqual(("peer-x", "peer-y", "peer-z"), rules[0].verifier_peers)
        self.assertEqual(2, rules[0].quorum)

    def test_preview_reports_explicit_block_without_creating_transfer_or_auth_store(self):
        FederationPolicyStore(self.root).block("peer-c", scope="relay")
        authorization_path = self.root / ".simpleoffice-v2" / "authorization.sqlite3"
        jobs_path = self.root / ".simpleoffice-v2" / "jobs.sqlite3"
        self.assertFalse(authorization_path.exists())
        self.assertFalse(jobs_path.exists())

        with patch("app.federation_peer_admin._audit_policy", return_value=True):
            response = self.client.post(
                "/admin/federation/peer-discovery/policy/peer-c/preview",
                data={
                    "scope": "relay",
                    "route": "peer-a, peer-c",
                    "object_refs": "object-1",
                },
                headers=self.headers,
            )

        self.assertEqual(302, response.status_code)
        self.assertFalse(authorization_path.exists())
        self.assertFalse(jobs_path.exists())
        message = self._latest_flash()
        self.assertIn("abgelehnt", message)
        self.assertIn("explicit_block", message)

    def test_policy_page_exposes_all_policy_sections(self):
        with patch(
            "app.federation_peer_admin.local_profile",
            return_value={"peer_id": "peer-a"},
        ), patch(
            "app.federation_peer_admin.render_template",
            return_value="rendered",
        ) as rendered:
            response = self.client.get(
                "/admin/federation/peer-discovery/policy/peer-c"
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual(b"rendered", response.data)
        context = rendered.call_args.kwargs
        self.assertEqual("peer-c", context["peer"]["peer_id"])
        self.assertIn("relay", context["policy_scopes"])
        self.assertEqual("peer-a, peer-c", context["default_route"])


class FederationPolicyTemplateTests(unittest.TestCase):
    def test_template_contains_block_route_confirmation_and_preview_controls(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "templates" / "admin" / "federation_peer_policy.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Peer sperren", text)
        self.assertIn("Objekt-IDs (optional)", text)
        self.assertIn("Nur direkte Zustellung", text)
        self.assertIn("Nie über diese Peers", text)
        self.assertIn("Signierte Bestätiger", text)
        self.assertIn("Mindestens N", text)
        self.assertIn("Policy-Vorschau", text)
        self.assertIn('aria-live="polite"', text)


if __name__ == "__main__":
    unittest.main()
