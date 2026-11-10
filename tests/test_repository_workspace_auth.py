"""Public isolation, Device Flow state-machine and read-only boundary regressions."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app import app, unhandled_application_error
from app.db import ensure_auth_database, get_db
from app import repository_workspace_auth as auth
from app import repository_workspace_oauth as oauth
from app import repository_workspace_store as store
from app.repository_workspace_github import WorkspaceError, WorkspaceUnauthorized
from app.repository_workspace_store import WorkspaceAuthError

PREFIX = "/repository-workspace/api"
CLIENT_ID = "Iv23.syntheticclient1234"
DEVICE_CODE = "d" * 40
ACCESS_TOKEN = "ghu_" + "synthetic" * 6
DEVICE = {"device_code": DEVICE_CODE, "user_code": "ABCD-EFGH",
          "verification_uri": "https://github.com/login/device", "expires_in": 900, "interval": 5}
TOKEN = {"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 28800,
         "refresh_token": "deliberately-not-persisted"}


class RepositoryWorkspaceAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        keys = ("DATABASE", "DOCUMENT_ROOT", "TESTING", "TEST_CSRF_PROTECTION", "GITHUB_WORKSPACE_CLIENT_ID")
        self.saved = {key: app.config.get(key) for key in keys}
        app.config.update(DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
                          DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"), TESTING=True,
                          TEST_CSRF_PROTECTION=True, GITHUB_WORKSPACE_CLIENT_ID=CLIENT_ID)
        with app.app_context():
            ensure_auth_database()
        self.now = 1_800_000_000
        self.clock = patch("app.repository_workspace_auth.time.time", side_effect=lambda: self.now)
        self.clock.start()
        self.data_patch = patch("app.repository_workspace._data")
        self.data = self.data_patch.start().return_value
        self.data.snapshot.side_effect = lambda token, **kwargs: {"items": [{"title": "Private" if token else "Public"}]}
        self.client = self.new_client()

    def tearDown(self):
        self.data_patch.stop()
        self.clock.stop()
        app.config.update(self.saved)
        self.temp.cleanup()

    def new_client(self):
        client = app.test_client()
        with client.session_transaction() as cookie:
            cookie["_csrf_token"] = "synchronizer-token-for-this-test-browser"
        return client

    def post(self, path, client=None, **kwargs):
        client = client or self.client
        return client.post(PREFIX + path, headers={"X-CSRF-Token": "synchronizer-token-for-this-test-browser"}, **kwargs)

    def start(self, client=None):
        with patch.object(oauth, "post", return_value=dict(DEVICE)) as provider:
            response = self.post("/auth/start", client)
        self.assertEqual(200, response.status_code, response.data)
        provider.assert_called_once_with(oauth.DEVICE_URL, {"client_id": CLIENT_ID, "scope": "offline_access"})
        return response

    def authenticate(self, client=None):
        client = client or self.client
        self.start(client)
        self.now += 5
        with patch.object(oauth, "post", return_value=dict(TOKEN)), patch.object(auth, "GitHubReadClient") as read:
            read.return_value.get.return_value = {"login": "workspace-tester"}
            result = self.post("/auth/poll", client)
        self.assertEqual("authenticated", result.json["status"], result.data)
        return result

    def row(self, client=None):
        client = client or self.client
        with client.session_transaction() as cookie:
            key = store.session_key(cookie[auth.SESSION_KEY])
        with app.app_context():
            return store.read(key)

    def test_public_data_without_configuration_or_account(self):
        app.config["GITHUB_WORKSPACE_CLIENT_ID"] = ""
        status = self.client.get(PREFIX + "/status")
        self.assertFalse(status.json["authenticated"])
        self.assertFalse(status.json["can_authenticate"])
        response = self.client.get(PREFIX + "/snapshot")
        self.assertEqual("Public", response.json["items"][0]["title"])
        self.data.snapshot.assert_called_once_with("", refresh=False, cache_key="public")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])
        self.assertEqual(503, self.post("/auth/start").status_code)

    def test_authorization_failure_never_serializes_exception_details(self):
        self.authenticate()
        self.data.snapshot.side_effect = WorkspaceUnauthorized("synthetic-private-token-and-stack-detail", status=401)
        response = self.client.get(PREFIX + "/snapshot")
        self.assertEqual(401, response.status_code)
        self.assertNotIn("synthetic-private-token-and-stack-detail", response.get_data(as_text=True))
        self.assertFalse(response.json["auth"]["authenticated"])

    def test_provider_errors_use_fixed_messages_even_if_exception_contains_private_data(self):
        for error in (WorkspaceError("synthetic-private-provider-detail", status=502),
                      WorkspaceAuthError("synthetic-private-provider-detail", 503)):
            with self.subTest(status=error.status):
                self.data.snapshot.side_effect = error
                response = self.client.get(PREFIX + "/snapshot")
                self.assertNotIn("synthetic-private-provider-detail", response.get_data(as_text=True))
                self.assertIn(response.status_code, (401, 502))

    def test_public_page_does_not_unlock_office_documents(self):
        response = self.client.get("/repository-workspace/")
        self.assertEqual(200, response.status_code)
        self.assertIn(b"data-repository-workspace", response.data)
        self.assertIn("no-store", response.headers["Cache-Control"])
        protected = self.client.get("/documents/")
        self.assertEqual(302, protected.status_code)
        self.assertIn("/auth/login", protected.headers["Location"])

    def test_remote_https_allows_optional_device_flow(self):
        with patch.object(oauth, "post", return_value=dict(DEVICE)) as provider:
            response = self.post("/auth/start", base_url="https://localhost",
                                 environ_overrides={"REMOTE_ADDR": "198.51.100.20"})
        self.assertEqual(200, response.status_code)
        provider.assert_called_once()
        self.assertIn("Secure", response.headers["Set-Cookie"])

    def test_provider_verification_link_is_never_trusted_blindly(self):
        with patch.object(oauth, "post", return_value={**DEVICE, "verification_uri": "https://evil.example/login"}):
            response = self.post("/auth/start")
        self.assertEqual(502, response.status_code)
        self.assertNotIn("evil.example", response.get_data(as_text=True))
        with app.app_context():
            self.assertEqual(0, get_db().execute("SELECT COUNT(*) FROM repository_workspace_session").fetchone()[0])

    def test_configuration_change_invalidates_existing_account(self):
        self.authenticate()
        key = self.row()["session_key"]
        app.config["GITHUB_WORKSPACE_CLIENT_ID"] = "Iv23.anotherclient1234"
        self.assertFalse(self.client.get(PREFIX + "/status").json["authenticated"])
        with app.app_context():
            self.assertIsNone(store.read(key))
        self.assertEqual("Public", self.client.get(PREFIX + "/snapshot").json["items"][0]["title"])

    def test_network_failure_discards_pending_secret_and_remains_read_only(self):
        self.start()
        key = self.row()["session_key"]
        self.now += 5
        with patch.object(oauth, "post", side_effect=WorkspaceAuthError("GitHub nicht erreichbar.", 502)):
            response = self.post("/auth/poll")
        self.assertEqual(502, response.status_code)
        with app.app_context():
            self.assertIsNone(store.read(key))
        self.assertEqual("Public", self.client.get(PREFIX + "/snapshot").json["items"][0]["title"])

    def test_cookie_mutations_require_real_csrf(self):
        for endpoint in ("/auth/start", "/auth/poll", "/auth/logout", "/refresh"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(403, self.client.post(PREFIX + endpoint).status_code)
        self.data.snapshot.assert_not_called()

    def test_remote_plain_http_cannot_authenticate_even_with_localhost_host(self):
        with patch.object(oauth, "post") as provider:
            response = self.post("/auth/start", environ_overrides={"REMOTE_ADDR": "198.51.100.20"})
        self.assertEqual(403, response.status_code)
        provider.assert_not_called()
        status = self.client.get(PREFIX + "/status", base_url="http://office.example.test")
        self.assertFalse(status.json["can_authenticate"])

    def test_device_code_encrypted_only_on_server_and_repeat_start_reuses_pending(self):
        response = self.start()
        self.assertNotIn(DEVICE_CODE, response.get_data(as_text=True))
        self.assertNotIn(DEVICE_CODE, str(response.headers))
        row = self.row()
        self.assertTrue(row["protected"].startswith("enc:v1:"))
        self.assertNotIn(DEVICE_CODE, json.dumps(row))
        with patch.object(oauth, "post") as provider:
            duplicate = self.post("/auth/start")
        provider.assert_not_called()
        self.assertEqual(response.json, duplicate.json)

    def test_success_rotates_capability_and_does_not_log_into_office(self):
        self.start()
        with self.client.session_transaction() as cookie:
            previous = cookie[auth.SESSION_KEY]
        self.now += 5
        with patch.object(oauth, "post", return_value=dict(TOKEN)), patch.object(auth, "GitHubReadClient") as read:
            read.return_value.get.return_value = {"login": "workspace-tester"}
            response = self.post("/auth/poll")
        self.assertEqual("authenticated", response.json["status"])
        with self.client.session_transaction() as cookie:
            self.assertNotEqual(previous, cookie[auth.SESSION_KEY])
            self.assertNotIn("user_id", cookie)
            self.assertNotIn(ACCESS_TOKEN, str(dict(cookie)))
        with app.app_context():
            self.assertIsNone(store.read(store.session_key(previous)))
            self.assertEqual(0, get_db().execute("SELECT COUNT(*) FROM user").fetchone()[0])
        self.assertNotIn(ACCESS_TOKEN, response.get_data(as_text=True))
        self.assertNotIn(ACCESS_TOKEN, str(response.headers))
        self.assertNotIn("deliberately-not-persisted", str(self.row()))
        self.assertTrue(self.row()["protected"].startswith("enc:v1:"))

    def test_account_data_never_shared_with_other_visitor_or_after_logout(self):
        self.authenticate()
        other = self.new_client()
        response = self.client.get(PREFIX + "/snapshot")
        self.assertEqual("Private", response.json["items"][0]["title"])
        private_key = self.data.snapshot.call_args.kwargs["cache_key"]
        self.assertTrue(private_key.startswith("private:"))
        self.assertFalse(other.get(PREFIX + "/status").json["authenticated"])
        self.assertEqual("Public", other.get(PREFIX + "/snapshot").json["items"][0]["title"])
        self.assertEqual(200, self.post("/auth/logout").status_code)
        self.data.invalidate_cache.assert_called_with(private_key)
        self.assertEqual("Public", self.client.get(PREFIX + "/snapshot").json["items"][0]["title"])

    def test_poll_interval_and_slow_down_are_enforced_on_server(self):
        self.start()
        with patch.object(oauth, "post") as provider:
            early = self.post("/auth/poll")
        self.assertEqual("pending", early.json["status"])
        provider.assert_not_called()
        self.now += 5
        with patch.object(oauth, "post", return_value={"error": "slow_down"}) as provider:
            slow = self.post("/auth/poll")
            self.now += 6
            repeat = self.post("/auth/poll")
        self.assertEqual("slow_down", slow.json["status"])
        self.assertEqual(10, slow.json["interval"])
        self.assertEqual("pending", repeat.json["status"])
        provider.assert_called_once()

    def test_slow_down_respects_larger_provider_interval(self):
        self.start()
        self.now += 5
        with patch.object(oauth, "post", return_value={"error": "slow_down", "interval": 20}) as provider:
            response = self.post("/auth/poll")
            self.assertEqual(20, response.json["interval"])
            self.now += 19
            self.assertEqual("pending", self.post("/auth/poll").json["status"])
            provider.assert_called_once()
            self.now += 1
            self.post("/auth/poll")
            self.assertEqual(2, provider.call_count)

    def test_poll_reservation_excludes_concurrent_worker(self):
        self.start()
        row = self.row()
        self.now += 5
        with app.app_context():
            reserved, first = store.reserve_poll(row["session_key"])
            pending, second = store.reserve_poll(row["session_key"])
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(reserved["lease_id"], pending["lease_id"])

    def test_denied_and_expired_authorizations_are_discarded(self):
        for error, expected in (("access_denied", "denied"), ("expired_token", "expired")):
            with self.subTest(error=error):
                self.start()
                key = self.row()["session_key"]
                self.now += 5
                with patch.object(oauth, "post", return_value={"error": error}):
                    result = self.post("/auth/poll")
                self.assertEqual(expected, result.json["status"])
                with app.app_context():
                    self.assertIsNone(store.read(key))

    def test_device_token_exchange_is_scoped_to_simpleoffice_repository(self):
        self.start()
        self.now += 5
        with patch.object(oauth, "post", return_value={"error": "authorization_pending"}) as provider:
            result = self.post("/auth/poll")
        self.assertEqual("pending", result.json["status"])
        provider.assert_called_once_with(oauth.TOKEN_URL, {
            "client_id": CLIENT_ID,
            "device_code": DEVICE_CODE,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "repository_id": auth.REPOSITORY_ID,
        })

    def test_device_expiry_and_token_expiry_are_local_fail_closed(self):
        self.start()
        self.now += 901
        with patch.object(oauth, "post") as provider:
            self.assertEqual("expired", self.post("/auth/poll").json["status"])
        provider.assert_not_called()
        self.authenticate()
        self.now += 28801
        self.assertFalse(self.client.get(PREFIX + "/status").json["authenticated"])
        self.assertEqual("Public", self.client.get(PREFIX + "/snapshot").json["items"][0]["title"])

    def test_provider_401_purges_token_and_private_snapshot_without_public_fallback(self):
        self.authenticate()
        self.data.snapshot.side_effect = WorkspaceUnauthorized("GitHub-Sitzung abgelaufen.", status=401)
        response = self.client.get(PREFIX + "/snapshot")
        self.assertEqual(401, response.status_code)
        self.assertNotIn("items", response.json)
        self.assertFalse(response.json["auth"]["authenticated"])
        self.data.invalidate_cache.assert_called()
        self.assertEqual(1, self.data.snapshot.call_count)

    def test_logout_during_fetch_cannot_publish_private_result(self):
        self.authenticate()
        key = self.row()["session_key"]
        def complete_after_logout(*args, **kwargs):
            store.discard(key)
            return {"items": [{"title": "must-never-leave-server"}]}
        self.data.snapshot.side_effect = complete_after_logout
        response = self.client.get(PREFIX + "/snapshot")
        self.assertEqual(401, response.status_code)
        self.assertNotIn("must-never-leave-server", response.get_data(as_text=True))

    def test_late_oauth_completion_after_logout_is_not_persisted(self):
        self.start()
        key = self.row()["session_key"]
        self.now += 5
        def answer_after_logout(*args, **kwargs):
            store.discard(key)
            return dict(TOKEN)
        with patch.object(oauth, "post", side_effect=answer_after_logout), patch.object(auth, "GitHubReadClient") as read:
            read.return_value.get.return_value = {"login": "workspace-tester"}
            response = self.post("/auth/poll")
        self.assertEqual("expired", response.json["status"])
        self.assertFalse(response.json["auth"]["authenticated"])

    def test_logout_after_authorization_commit_cannot_be_undone_by_late_cookie(self):
        self.start()
        old_cookie = self.client.get_cookie(app.config["SESSION_COOKIE_NAME"])
        old_browser = app.test_client()
        old_browser.set_cookie(app.config["SESSION_COOKIE_NAME"], old_cookie.value)
        original_finish = store.finish_poll
        def logout_after_commit(*args, **kwargs):
            finished = original_finish(*args, **kwargs)
            self.assertTrue(finished)
            # The old capability must remain unable to READ the rotated session.
            self.assertFalse(old_browser.get(PREFIX + "/status").json["authenticated"])
            logged_out = self.post("/auth/logout", old_browser)
            self.assertEqual(200, logged_out.status_code)
            return finished
        self.now += 5
        with patch.object(oauth, "post", return_value=dict(TOKEN)), patch.object(auth, "GitHubReadClient") as read:
            read.return_value.get.return_value = {"login": "workspace-tester"}
            with patch.object(store, "finish_poll", side_effect=logout_after_commit):
                result = self.post("/auth/poll")
        self.assertFalse(result.json["auth"]["authenticated"])
        self.assertFalse(self.client.get(PREFIX + "/status").json["authenticated"])
        self.data.invalidate_cache.assert_called()

    def test_classic_oauth_config_or_token_is_rejected(self):
        app.config["GITHUB_WORKSPACE_CLIENT_ID"] = "Ov23.not-a-github-app"
        self.assertFalse(self.client.get(PREFIX + "/status").json["can_authenticate"])
        app.config["GITHUB_WORKSPACE_CLIENT_ID"] = CLIENT_ID
        self.start()
        self.now += 5
        with patch.object(oauth, "post", return_value={**TOKEN, "access_token": "gho_" + "x" * 40}):
            self.assertEqual(502, self.post("/auth/poll").status_code)
        self.assertFalse(self.client.get(PREFIX + "/status").json["authenticated"])

    def test_plaintext_database_token_is_rejected_not_migrated(self):
        self.authenticate()
        key = self.row()["session_key"]
        with app.app_context():
            db = get_db()
            db.execute("UPDATE repository_workspace_session SET protected=? WHERE session_key=?",
                       (json.dumps({"access_token": ACCESS_TOKEN}), key))
            db.commit()
        self.assertEqual(401, self.client.get(PREFIX + "/snapshot").status_code)
        self.data.snapshot.assert_not_called()

    def test_start_attempts_are_limited_across_new_browser_sessions(self):
        for _ in range(store.NETWORK_START_LIMIT):
            self.start(self.new_client())
        with patch.object(oauth, "post") as provider:
            response = self.post("/auth/start", self.new_client())
        self.assertEqual(429, response.status_code)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        provider.assert_not_called()

    def test_unexpected_failure_never_uses_automatic_issue_reporter(self):
        self.data.snapshot.side_effect = RuntimeError("sensitive-token-must-not-escape")
        with patch("app.github_error_reporter.report_error") as report:
            response = self.client.get(PREFIX + "/snapshot")
        self.assertEqual(500, response.status_code)
        self.assertNotIn("sensitive-token", response.get_data(as_text=True))
        report.assert_not_called()
        with app.test_request_context(PREFIX + "/snapshot"), patch("app.github_error_reporter.report_error") as report:
            result, code = unhandled_application_error(RuntimeError("hook-failure"))
            self.assertEqual(500, code)
        report.assert_not_called()

    def test_snapshot_does_not_inject_identity_into_shared_cached_object(self):
        cached = {"items": []}
        self.data.snapshot.side_effect = None
        self.data.snapshot.return_value = cached
        self.client.get(PREFIX + "/snapshot")
        self.assertNotIn("auth", cached)


class DeviceTransportTests(unittest.TestCase):
    def response(self, data, **headers):
        response = MagicMock()
        response.headers = headers
        response.read.return_value = json.dumps(data).encode()
        response.__enter__.return_value = response
        return response

    def test_only_fixed_device_oauth_posts_are_allowed(self):
        with patch.object(oauth._OPENER, "open", return_value=self.response(DEVICE)) as opened:
            result = oauth.post(oauth.DEVICE_URL, {"client_id": CLIENT_ID})
        self.assertEqual(DEVICE, result)
        request = opened.call_args.args[0]
        self.assertEqual("POST", request.method)
        self.assertEqual(oauth.DEVICE_URL, request.full_url)
        self.assertNotIn("Authorization", request.headers)
        with self.assertRaises(WorkspaceAuthError):
            oauth.post("https://evil.example/device", {"client_id": CLIENT_ID})
        self.assertIsNone(oauth._RejectRedirects().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_response_length_and_network_errors_are_sanitized(self):
        with patch.object(oauth._OPENER, "open", return_value=self.response({}, **{"Content-Length": str(oauth.MAX_RESPONSE_BYTES + 1)})):
            with self.assertRaises(WorkspaceAuthError):
                oauth.post(oauth.TOKEN_URL, {})
        with patch.object(oauth._OPENER, "open", side_effect=OSError("private-provider-detail")):
            with self.assertRaises(WorkspaceAuthError) as error:
                oauth.post(oauth.TOKEN_URL, {})
        self.assertNotIn("private-provider-detail", str(error.exception))

    def test_http_400_standard_oauth_error_is_retained(self):
        error = HTTPError(oauth.TOKEN_URL, 400, "bad", {}, io.BytesIO(b'{"error":"expired_token"}'))
        with patch.object(oauth._OPENER, "open", side_effect=error):
            self.assertEqual({"error": "expired_token"}, oauth.post(oauth.TOKEN_URL, {}))


if __name__ == "__main__":
    unittest.main()
