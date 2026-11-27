"""Peer-authenticated, opt-in workflow notices in isolated test accounts."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from secrets import token_urlsafe

from app import app
from app.federation_peer_auth import headers
from app.federation_store import FederationStore
from app.federation_trust_store import FederationTrustStore
from app.settings_store import SettingsStore
from app.v3_activity import ActivityStore
from app.v3_automation_timing import timing_envelope
from app.workflow_peer_notices import PATH, PEER_NOTICE
from tests.http_api_fixture import PublicHttpTestCase


class WorkflowPeerNoticeTests(PublicHttpTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(app.config["DOCUMENT_ROOT"])
        flags = {"v3.activity": True, "v3.automation": True, "v3.federation": True}
        SettingsStore(self.root).save({"v3": flags}, "owner")
        self.key = token_urlsafe(30)
        self.other_key = token_urlsafe(30)
        with app.app_context():
            for peer, key in (("peer-a", self.key), ("peer-b", self.other_key)):
                FederationStore(self.root).save_peer(
                    peer, peer, "https://example.invalid", key,
                    policy={"data_classes": {"workflow_notices": {"receive": True}}},
                )
                FederationTrustStore(self.root).set_trust(peer, trust_level="NORMAL", verification="VERIFIED_MUTUAL")
        self.csrf = self._csrf_headers(self.client)
        self.consent = "/automation-v3/peer-notices"
        self.now = datetime.now(timezone.utc)

    def _grant(self):
        fields = {"operation": "grant", "peer": "peer-a"}
        self.assertEqual(403, self.client.post(self.consent, data=fields).status_code)
        self.assertEqual(303, self.client.post(self.consent, data=fields, headers=self.csrf).status_code)
        page = self.client.get("/automation-v3")
        from bs4 import BeautifulSoup
        return BeautifulSoup(page.data, "html.parser").select_one('input[name="grant"]')["value"]

    def _send(self, grant, *, peer="peer-a", at=None, timing=None, **extra):
        now = at or self.now - timedelta(seconds=2)
        payload = {"destination": grant, "id": "a" * 64, "kind": "job",
                   "window": timing_envelope(timing or {"within_seconds": 120}, now), **extra}
        body = json.dumps(payload).encode()
        key = self.key if peer == "peer-a" else self.other_key
        return self.client.post(PATH, base_url="https://localhost", data=body, headers={
            **headers(peer, key, "POST", PATH, body), "Content-Type": "application/json",
        })

    def _notice_rows(self):
        return ActivityStore(self.root).list(event_type=PEER_NOTICE)

    def test_only_owner_can_grant_fixed_notice_and_replay_is_idempotent(self):
        grant = self._grant()
        self.assertEqual(403, self.client.post(PATH, json={"destination": grant}).status_code)
        self.assertEqual(401, self.client.post(PATH, base_url="https://localhost", json={"destination": grant}).status_code)
        self.assertEqual(403, self._send(grant, peer="peer-b").status_code)
        self.assertEqual(400, self._send(grant, arbitrary_text="synthetic").status_code)
        self.assertEqual("delivered", self._send(grant).json["status"])
        self.assertEqual("duplicate", self._send(grant).json["status"])
        notice, = self._notice_rows()
        feed = self.client.get("/automation-v3/copy-notices").json["notices"]
        self.assertEqual([{"id": notice.event_id, "kind": "peer", "at": notice.occurred_at}], feed)
        self.assertEqual({}, notice.metadata)
        self._register(self.client, "other")
        other = app.test_client()
        self._login(other, "other")
        self.assertEqual([], other.get("/automation-v3/copy-notices").json["notices"])
        self.assertNotIn(grant.encode(), other.get("/automation-v3").data)

    def test_expired_early_and_revoked_do_not_create_notices(self):
        grant = self._grant()
        old = self.now - timedelta(seconds=400)
        self.assertEqual("window_missed", self._send(grant, at=old).json["status"])
        self.assertEqual("waiting_for_window", self._send(
            grant, timing={"delay_seconds": 20, "within_seconds": 120},
        ).json["status"])
        self.assertEqual([], self._notice_rows())
        self.client.post(self.consent, headers=self.csrf, data={"operation": "revoke", "grant": grant})
        self.assertEqual(403, self._send(grant).status_code)
        self.assertEqual([], self._notice_rows())

    def test_auth_and_policy_are_rechecked_before_delivery(self):
        grant = self._grant()
        with app.app_context():
            FederationTrustStore(self.root).set_trust("peer-a", trust_level="NONE")
        self.assertEqual(403, self._send(grant).status_code)
        self.assertEqual([], self._notice_rows())


    def test_operator_can_list_only_confirmed_exportable_copy_events(self):
        from app.document_copy_events import capture_copy_completed

        picked = capture_copy_completed(self.root, "owner", "synthetic-owner-copy")
        other = capture_copy_completed(self.root, "other", "synthetic-other-copy")
        result = app.test_cli_runner().invoke(args=["list-workflow-copy-events", "--actor", "owner"])
        self.assertEqual(0, result.exit_code, str(result.exception))
        self.assertIn(picked, result.output)
        self.assertNotIn(other, result.output)
        self.assertNotIn("synthetic-owner-copy", result.output)
