"""Consent, isolation, durable support notices and stale-command regressions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from flask import Blueprint, Flask, g
from app import screen_share, screen_support
from app.security_controls import protect_browser_mutation


class ScreenSupportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test", DOCUMENT_ROOT=self.directory.name)
        blueprint = Blueprint("support_test", __name__, url_prefix="/screen")
        screen_support.register_support(blueprint)
        self.app.register_blueprint(blueprint)
        self.actor = {"id": "owner", "username": "owner"}
        self.app.before_request(lambda: setattr(g, "user", self.actor))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40, "X-Screen-Code": "ABCD1234"}
        self.url = "/screen/api/sessions/test_session/support"
        screen_share._sessions.clear()
        screen_support._runtime.clear()
        screen_share._sessions["test_session"] = {"owner": "owner", "join_code": "ABCD1234", "updated_at": screen_share.time.time(), "closed": False}
        self.addCleanup(screen_share._sessions.clear)
        self.addCleanup(screen_support._runtime.clear)
        self.audit = mock.patch.object(screen_share, "audit").start()
        self.addCleanup(mock.patch.stopall)

    def get(self, role="sender", **query):
        return self.client.get(self.url, query_string={"role": role, **query}, headers=self.headers)

    def post(self, kind, role="sender", **body):
        return self.client.post(self.url, json={"role": role, "type": kind, **body}, headers=self.headers)

    def receiver(self):
        self.actor["id"] = "receiver"
        self.assertEqual(200, self.get("receiver").status_code)

    def grant(self):
        self.receiver()
        self.assertEqual(200, self.post("request", "receiver").status_code)
        self.actor["id"] = "owner"
        response = self.post("grant")
        self.assertEqual(200, response.status_code)
        return response.json["state"]["generation"]

    def fields(self):
        self.assertEqual(200, self.post("fields", epoch="page-1", fields=[{"id": "field1", "label": "Name", "type": "text"}]).status_code)

    def test_claim_is_bound_to_one_authenticated_receiver_and_code(self):
        self.receiver()
        self.actor["id"] = "intruder"
        self.assertEqual(403, self.get("receiver").status_code)
        self.assertEqual(403, self.get("sender").status_code)
        self.actor["id"] = "receiver"
        self.headers["X-Screen-Code"] = "wrong"
        self.assertEqual(403, self.get("receiver").status_code)

    def test_owner_alone_grants_and_revocation_invalidates_generation(self):
        self.fields()
        generation = self.grant()
        self.actor["id"] = "receiver"
        self.assertEqual(403, self.post("grant", "receiver").status_code)
        command = {"epoch": "page-1", "field_id": "field1", "value": "private-value", "generation": generation}
        self.assertEqual(200, self.post("input", "receiver", **command).status_code)
        self.actor["id"] = "owner"
        self.assertEqual(1, len(self.get().json["commands"]))
        self.assertEqual(200, self.post("revoke").status_code)
        self.actor["id"] = "receiver"
        self.assertEqual(403, self.post("input", "receiver", **command).status_code)
        self.actor["id"] = "owner"
        self.assertEqual([], self.get().json["commands"])
        persisted = next(Path(self.directory.name).rglob("test_session.json")).read_text()
        self.assertNotIn("private-value", persisted)
        self.assertNotIn("private-value", str(self.audit.call_args_list))

    def test_navigation_epoch_and_field_allowlist_reject_stale_or_arbitrary_edits(self):
        self.fields()
        generation = self.grant()
        self.actor["id"] = "receiver"
        base = {"epoch": "page-1", "field_id": "field1", "value": "name", "generation": generation}
        self.assertEqual(409, self.post("input", "receiver", **{**base, "epoch": "old"}).status_code)
        self.assertEqual(400, self.post("input", "receiver", **{**base, "field_id": "password"}).status_code)
        self.assertEqual(400, self.post("input", "receiver", **{**base, "value": 42}).status_code)
        self.assertEqual(403, self.post("fields", "receiver", epoch="evil", fields=[]).status_code)

    def test_receiver_rejoin_or_leave_requires_fresh_consent(self):
        for kind in ("join", "leave"):
            self.actor["id"] = "owner"
            generation = self.grant()
            self.actor["id"] = "receiver"
            response = self.post(kind, "receiver")
            self.assertEqual("none", response.json["state"]["permission"])
            self.assertEqual("", response.json["state"]["generation"])
            self.assertNotEqual(generation, response.json["state"]["generation"])

    def test_notice_history_acknowledgment_and_archive_survive_runtime_loss(self):
        self.receiver()
        response = self.post("notice", "receiver", text="Bitte Ton prüfen <script>alert(1)</script>")
        sequence = response.json["events"][-1]["seq"]
        self.actor["id"] = "owner"
        self.assertEqual(200, self.post("ack", event_seq=sequence).status_code)
        self.assertEqual(200, self.post("ack", event_seq=sequence).status_code)
        self.assertEqual(2, len(self.get().json["events"]))
        with self.app.test_request_context():
            screen_support.session_ended("test_session")
        screen_share._sessions.clear()
        screen_support._runtime.clear()
        history = self.get()
        self.assertEqual(200, history.status_code)
        self.assertTrue(history.json["closed"])
        self.assertEqual(["notice", "ack", "ended"], [event["type"] for event in history.json["events"]])
        self.assertEqual(410, self.post("notice", text="later").status_code)
        self.actor["id"] = "intruder"
        self.assertEqual(403, self.get().status_code)

    def test_input_queue_is_sender_only_and_acknowledged_separately_from_history(self):
        self.fields()
        generation = self.grant()
        self.actor["id"] = "receiver"
        self.assertEqual(200, self.post("input", "receiver", epoch="page-1", field_id="field1", value="name", generation=generation).status_code)
        self.assertEqual([], self.get("receiver").json["commands"])
        self.actor["id"] = "owner"
        commands = self.get().json["commands"]
        self.assertEqual(1, len(commands))
        self.assertEqual(commands, self.get().json["commands"])
        self.assertEqual([], self.get(after_command=commands[0]["seq"]).json["commands"])

    def test_bounded_history_and_input_validation(self):
        self.assertEqual(400, self.post("notice", text=" ").status_code)
        self.assertEqual(400, self.post("notice", text="a" * 1001).status_code)
        self.assertEqual(400, self.post("fields", epoch="page", fields=[{"id": "x", "label": "password", "type": "password"}]).status_code)
        with mock.patch.object(screen_support, "_MAX_EVENTS", 3):
            self.assertEqual(200, self.post("notice", text="one").status_code)
            self.assertEqual(409, self.post("notice", text="two").status_code)
        self.assertEqual(400, self.post("ack", event_seq=True).status_code)
        self.assertEqual(400, self.get(after="x").status_code)
        self.assertEqual(403, self.client.post(self.url, json={"role": "sender", "type": "revoke"}).status_code)

    def test_first_receiver_join_same_account_notice_ack_and_event_directions(self):
        response = self.post("join", "receiver")
        self.assertEqual(200, response.status_code)
        self.assertEqual("receiver", response.json["events"][-1]["from"])
        notice = self.post("notice", "receiver", text="same-account notice")
        seq = notice.json["events"][-1]["seq"]
        self.assertEqual(200, self.post("ack", event_seq=seq).status_code)

    def test_receiver_heartbeat_timeout_revokes_pending_input(self):
        self.fields()
        generation = self.grant()
        future = screen_support.time.time() + screen_support._RECEIVER_TIMEOUT + 1
        with mock.patch.object(screen_support.time, "time", return_value=future):
            self.assertEqual("none", self.get().json["state"]["permission"])
            self.actor["id"] = "receiver"
            self.assertEqual(403, self.post("input", "receiver", epoch="page-1", field_id="field1", value="late", generation=generation).status_code)

    def test_unchanged_poll_does_not_rewrite_history_and_full_history_allows_revoke(self):
        self.grant()
        with mock.patch.object(screen_support, "_save", wraps=screen_support._save) as save:
            self.get()
            self.assertEqual(0, save.call_count)
        record_path = next(Path(self.directory.name).rglob("test_session.json"))
        record = json.loads(record_path.read_text())
        with mock.patch.object(screen_support, "_MAX_EVENTS", len(record["events"])):
            response = self.post("revoke")
            self.assertEqual(200, response.status_code)
            self.assertEqual("none", response.json["state"]["permission"])
            self.assertTrue(response.json["closed"])

    def test_input_result_records_outcome_without_field_value(self):
        self.fields()
        generation = self.grant()
        self.actor["id"] = "receiver"
        self.post("input", "receiver", epoch="page-1", field_id="field1", value="private-edit", generation=generation)
        self.assertEqual(403, self.post("input-result", "receiver", command_seq=1, outcome="applied").status_code)
        self.actor["id"] = "owner"
        self.assertEqual(200, self.post("input-result", command_seq=1, outcome="applied").status_code)
        self.assertEqual("applied", self.get().json["events"][-1]["outcome"])
        self.assertNotIn("private-edit", next(Path(self.directory.name).rglob("test_session.json")).read_text())

    def test_storage_failure_reports_fixed_error_and_does_not_block_capture_stop(self):
        self.get()
        with mock.patch.object(screen_support, "_save", side_effect=OSError("synthetic private path")):
            result = self.post("notice", text="notice")
            self.assertEqual(503, result.status_code)
            self.assertNotIn("synthetic private", result.get_data(as_text=True))
            with self.app.test_request_context():
                screen_support.session_ended("test_session")

    def test_frontend_regressions(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js required")
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([node, "--test", "tests/screen_support_frontend.test.cjs", "tests/screen_workspace_frontend.test.cjs"], cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
