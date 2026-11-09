"""Read-only transport, evidence semantics and account/public cache separation."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from app import app
from app.db import ensure_auth_database, get_db
from app import repository_workspace_data as data
from app import repository_workspace_model as model
from app.repository_workspace_github import (
    API_BASE, GitHubReadClient, MAX_RESPONSE_BYTES, WorkspaceError, WorkspaceUnauthorized,
)

SHA = "a" * 40
OLD_SHA = "b" * 40


def response(payload, headers=None):
    result = io.BytesIO(json.dumps(payload).encode())
    result.headers = headers or {}
    return result


def pull(**overrides):
    return {"number": 12, "title": "Desktop smoke test", "state": "open", "draft": False,
            "head": {"sha": SHA}, "labels": [], **overrides}


def run(**overrides):
    return {"id": 100, "workflow_id": 9, "name": "Desktop", "head_sha": SHA,
            "event": "pull_request", "status": "completed", "conclusion": "failure",
            "created_at": "2026-11-09T10:00:00Z", "run_attempt": 1, **overrides}


class GitHubReadTransportTest(unittest.TestCase):
    def client(self, payload=None):
        client = GitHubReadClient("synthetic-token")
        client._opener = Mock()
        client._opener.open.return_value = response(payload if payload is not None else [])
        return client

    def test_only_fixed_get_repository_requests_and_user_identity_are_available(self):
        client = self.client()
        client.get("pulls", {"state": "open"})
        request = client._opener.open.call_args.args[0]
        self.assertEqual("GET", request.method)
        self.assertEqual(API_BASE + "/pulls?state=open", request.full_url)
        self.assertEqual("Bearer synthetic-token", request.get_header("Authorization"))
        self.assertIsNone(request.data)

    def test_arbitrary_resources_and_query_keys_are_rejected_before_network(self):
        for resource in ("https://evil.example", "../user", "pulls/1/merge", "issues/1", "actions/runs/1/rerun", "user?token=x"):
            client = self.client()
            with self.subTest(resource=resource), self.assertRaises(WorkspaceError):
                client.get(resource)
            client._opener.open.assert_not_called()
        client = self.client()
        with self.assertRaises(WorkspaceError):
            client.get("pulls", {"url": "https://evil.example"})
        client._opener.open.assert_not_called()

    def test_anonymous_client_does_not_load_credentials(self):
        client = GitHubReadClient()
        client._opener = Mock()
        client._opener.open.return_value = response([])
        client.get("issues")
        self.assertIsNone(client._opener.open.call_args.args[0].get_header("Authorization"))

    def test_redirect_is_not_followed_with_credentials(self):
        client = GitHubReadClient("synthetic-token")
        handlers = [h for h in client._opener.handlers if type(h).__name__ == "_NoRedirects"]
        self.assertEqual(1, len(handlers))
        self.assertIsNone(handlers[0].redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_network_and_format_errors_do_not_echo_provider_data(self):
        client = self.client()
        client._opener.open.side_effect = URLError("sensitive-provider-token")
        with self.assertRaises(WorkspaceError) as caught:
            client.get("issues")
        self.assertNotIn("sensitive-provider-token", str(caught.exception))
        client = self.client()
        client._opener.open.return_value = response("unexpected scalar")
        with self.assertRaises(WorkspaceError):
            client.get("issues")

    def test_response_size_is_bounded(self):
        client = self.client()
        raw = io.BytesIO(b"x" * (MAX_RESPONSE_BYTES + 1))
        raw.headers = {}
        client._opener.open.return_value = raw
        with self.assertRaises(WorkspaceError):
            client.get("issues")

    def test_unauthorized_token_is_distinct_from_unavailable_source(self):
        client = self.client()
        client._opener.open.side_effect = HTTPError(API_BASE, 401, "token-detail", {}, io.BytesIO(b"secret"))
        with self.assertRaises(WorkspaceUnauthorized):
            client.get("issues")

    def test_rate_limit_halts_remaining_requests(self):
        client = self.client()
        client._opener.open.side_effect = HTTPError(API_BASE, 429, "", {"Retry-After": "90"}, io.BytesIO())
        with self.assertRaises(WorkspaceError) as caught:
            client.get("issues")
        self.assertEqual(90, caught.exception.retry_after)
        with self.assertRaises(WorkspaceError):
            client.get("pulls")
        self.assertEqual(1, client._opener.open.call_count)

    def test_pagination_is_explicitly_incomplete_at_limit(self):
        client = self.client()
        client._opener.open.return_value = response([{"id": 1}], {"Link": '<https://api.github.com/next>; rel="next"'})
        records, complete = client.collection("issues", max_pages=1)
        self.assertEqual([{"id": 1}], records)
        self.assertFalse(complete)

    def test_secondary_limit_without_retry_header_halts_collection(self):
        client = self.client()
        client._opener.open.side_effect = HTTPError(API_BASE, 403, "", {},
            io.BytesIO(b'{"message":"You have exceeded a secondary rate limit."}'))
        with self.assertRaises(WorkspaceError) as caught:
            client.get("issues")
        self.assertEqual(60, caught.exception.retry_after)
        with self.assertRaises(WorkspaceError):
            client.get("pulls")
        self.assertEqual(1, client._opener.open.call_count)

    def test_failed_later_page_preserves_known_findings(self):
        client = self.client()
        client._opener.open.side_effect = [
            response([{"id": 1}], {"Link": '<https://api.github.com/next>; rel="next"'}),
            URLError("Network failed"),
        ]
        records, complete = client.collection("issues")
        self.assertEqual([{"id": 1}], records)
        self.assertFalse(complete)
        self.assertIn("Teilweise", client.partial_message)

    def test_pagination_constructs_only_allowlisted_urls(self):
        client = self.client()
        client._opener.open.side_effect = [
            response([{"id": 1}], {"Link": '<https://evil.example/>; rel="next"'}),
            response([{"id": 2}]),
        ]
        records, complete = client.collection("issues")
        self.assertEqual(2, len(records))
        self.assertTrue(complete)
        self.assertTrue(all(call.args[0].full_url.startswith(API_BASE + "/issues?")
                            for call in client._opener.open.call_args_list))


class RepositoryAssessmentTest(unittest.TestCase):
    def test_old_commit_and_push_runs_cannot_block_current_pr(self):
        item, _ = model.pull_request(pull(), {}, [run(head_sha=OLD_SHA), run(event="push")])
        self.assertEqual("review", item["status"])
        self.assertEqual("unknown", item["test_status"]["state"])
        self.assertEqual([], model.ci_findings([run(head_sha=OLD_SHA), run(event="push")], [pull()], ""))

    def test_latest_rerun_replaces_older_failure(self):
        rows = [run(), run(id=101, conclusion="success", created_at="2026-11-09T11:00:00Z")]
        item, _ = model.pull_request(pull(), {}, rows)
        self.assertEqual("success", item["test_status"]["state"])
        self.assertEqual([], model.ci_findings(rows, [pull()], ""))

    def test_cancelled_and_skipped_are_not_passed_tests(self):
        cancelled, _ = model.pull_request(pull(), {}, [run(conclusion="cancelled")])
        self.assertEqual("cancelled", cancelled["test_status"]["state"])
        self.assertEqual("P2", cancelled["priority"])
        self.assertEqual("blocked", cancelled["status"])
        skipped, _ = model.pull_request(pull(), {}, [run(conclusion="skipped")])
        self.assertEqual("unknown", skipped["test_status"]["state"])

    def test_green_checks_never_imply_merge_ready(self):
        item, _ = model.pull_request(pull(), {"complete": True}, [run(conclusion="success")])
        self.assertEqual("review", item["status"])
        self.assertTrue(any("Branch-Regeln" in b for b in item["blockers"]))

    def test_failed_checks_are_bound_to_head_sha(self):
        detail = {"checks": [{"head_sha": OLD_SHA, "conclusion": "failure", "status": "completed"}]}
        item, _ = model.pull_request(pull(), detail, [])
        self.assertEqual("unknown", item["test_status"]["state"])
        detail["checks"][0]["head_sha"] = SHA
        item, _ = model.pull_request(pull(), detail, [])
        self.assertEqual("failed", item["test_status"]["state"])
        self.assertEqual("P1", item["priority"])

    def test_review_request_at_old_commit_is_not_current_blocker(self):
        review = {"id": 5, "state": "CHANGES_REQUESTED", "commit_id": OLD_SHA, "user": {"id": 2},
                  "submitted_at": "2026-11-09T10:00:00Z", "body": "Check this"}
        item, findings = model.pull_request(pull(), {"reviews": [review]}, [])
        self.assertEqual("review", item["status"])
        self.assertIn("ungeprüft", findings[0]["assessment"])
        review["commit_id"] = SHA
        item, _ = model.pull_request(pull(), {"reviews": [review]}, [])
        self.assertEqual("blocked", item["status"])

    def test_later_approval_supersedes_same_reviewers_change_request(self):
        reviews = [{"id": 1, "state": "CHANGES_REQUESTED", "commit_id": SHA, "user": {"id": 2},
                    "submitted_at": "2026-11-09T10:00:00Z"},
                   {"id": 2, "state": "APPROVED", "commit_id": SHA, "user": {"id": 2},
                    "submitted_at": "2026-11-09T11:00:00Z"}]
        item, findings = model.pull_request(pull(), {"reviews": reviews}, [])
        self.assertEqual([], findings)
        self.assertEqual("review", item["status"])

    def test_automated_review_findings_remain_visible(self):
        review = {"id": 1, "state": "CHANGES_REQUESTED", "commit_id": SHA,
                  "user": {"id": 2, "type": "Bot"}, "submitted_at": "2026-11-09T10:00:00Z"}
        item, findings = model.pull_request(pull(), {"reviews": [review]}, [])
        self.assertEqual("blocked", item["status"])
        self.assertIn("Automatisiertes Review", findings[0]["assessment"])

    def test_closed_issue_is_not_claimed_as_tested_or_fixed(self):
        item = model.issue({"number": 1, "state": "closed", "state_reason": "not_planned"})
        self.assertEqual("done", item["status"])
        self.assertIn("nicht geplant", item["cause"])
        self.assertEqual("unknown", item["test_status"]["state"])

    def test_closed_unmerged_pull_request_is_not_claimed_as_merged(self):
        item = model.completed({"number": 1, "state": "closed", "pull_request": {"merged_at": None}})
        self.assertIn("Merge nicht belegt", item["cause"])

    def test_security_view_selects_metadata_and_excludes_secret_payload(self):
        alerts = [{"number": 4, "state": "open", "secret": "do-not-expose", "rule": {
            "description": "Potential path traversal", "security_severity_level": "high"}}]
        items = model.security_findings(alerts, "code-scanning")
        self.assertEqual("P1", items[0]["priority"])
        self.assertNotIn("do-not-expose", json.dumps(items))
        self.assertIn("ungeprüft", items[0]["cause"])


class WorkspaceSnapshotCacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {key: app.config.get(key) for key in ("DATABASE", "DOCUMENT_ROOT", "TESTING")}
        app.config.update(DATABASE=str(Path(self.temp.name) / "auth.sqlite"),
                          DOCUMENT_ROOT=str(Path(self.temp.name) / "documents"), TESTING=True)
        self.context = app.app_context()
        self.context.push()
        ensure_auth_database()

    def tearDown(self):
        self.context.pop()
        app.config.update(self.saved)
        self.temp.cleanup()

    def collected(self, private=False, title="Public title"):
        result = data._empty(private)
        result.update(fetched_at="2026-11-09T10:00:00Z", items=[{"title": title}])
        return result, 0

    def test_public_refresh_respects_shared_anonymous_budget(self):
        with patch.object(data, "_collect", return_value=self.collected()) as collect:
            self.assertFalse(data.snapshot()["cached"])
            self.assertTrue(data.snapshot(refresh=True)["cached"])
            self.assertTrue(data.snapshot()["cached"])
            collect.assert_called_once_with("")

    def test_private_snapshot_cannot_use_public_cache_namespace(self):
        for token, key in (("synthetic", "public"), ("", "private:someone")):
            with self.subTest(token=bool(token)), self.assertRaises(WorkspaceError):
                data.snapshot(token, cache_key=key)

    def test_account_snapshots_are_separate_and_encrypted(self):
        with patch.object(data, "_collect", side_effect=[self.collected(True, "private-account-A"),
                                                        self.collected(True, "private-account-B"), self.collected()]):
            data.snapshot("token-A", cache_key="account-A")
            data.snapshot("token-B", cache_key="account-B")
            public = data.snapshot()
        self.assertEqual("Public title", public["items"][0]["title"])
        self.assertEqual("private-account-A", data.snapshot("token-A", cache_key="account-A")["items"][0]["title"])
        raw = " ".join(row[0] for row in get_db().execute("SELECT payload FROM repository_workspace_cache"))
        self.assertNotIn("private-account-A", raw)
        self.assertNotIn("private-account-B", raw)
        self.assertNotIn("token-A", raw)

    def test_account_cache_invalidation_cannot_resurrect_from_late_fetch(self):
        def fetch(_token):
            data.invalidate_cache("account-A")
            return self.collected(True, "private-A")
        with patch.object(data, "_collect", side_effect=fetch):
            data.snapshot("token-A", cache_key="account-A")
        self.assertEqual(0, get_db().execute("SELECT COUNT(*) FROM repository_workspace_cache").fetchone()[0])

    def test_second_request_does_not_start_concurrent_collection(self):
        observed = []
        def fetch(_token):
            observed.append(data.snapshot())
            return self.collected()
        with patch.object(data, "_collect", side_effect=fetch) as collect:
            data.snapshot()
        self.assertEqual(1, collect.call_count)
        self.assertEqual("refresh-pending", observed[0]["sources"][0]["id"])

    def test_provider_401_propagates_and_releases_refresh_lease(self):
        with patch.object(data, "_collect", side_effect=WorkspaceUnauthorized("Session expired", status=401)):
            with self.assertRaises(WorkspaceUnauthorized):
                data.snapshot("token-A", cache_key="account-A")
        row = get_db().execute("SELECT * FROM repository_workspace_cache").fetchone()
        self.assertEqual(0, row["busy_until"])
        self.assertEqual("", row["payload"])

    def test_collector_marks_missing_security_as_unknown_not_zero(self):
        with patch.object(data, "GitHubReadClient") as reader:
            reader.return_value.get.return_value = {"default_branch": "main", "commit": {"sha": SHA}}
            reader.return_value.collection.return_value = ([], True)
            reader.return_value.retry_after = 0
            snapshot, _ = data._collect("")
        security = [s for s in snapshot["sources"] if s["id"] in {"code-scanning", "dependabot"}]
        self.assertTrue(all(s["state"] == "unavailable" for s in security))
        self.assertFalse(any(call.args[0].endswith("/alerts") for call in reader.return_value.collection.call_args_list))

    def test_partial_sources_do_not_discard_successful_issues(self):
        with patch.object(data, "GitHubReadClient") as reader:
            client = reader.return_value
            client.get.return_value = {"default_branch": "main", "commit": {"sha": SHA}}
            client.retry_after = 0
            def records(resource, *args, **kwargs):
                if resource == "issues" and args[0]["state"] == "open":
                    return [{"number": 42, "title": "A documented issue"}], True
                raise WorkspaceError("Unavailable")
            client.collection.side_effect = records
            snapshot, _ = data._collect("")
        self.assertEqual("issue-42", snapshot["items"][0]["id"])
        self.assertTrue(any(s["state"] == "error" for s in snapshot["sources"]))

    def test_partial_commit_statuses_keep_known_failure_even_with_green_checks(self):
        with patch.object(data, "GitHubReadClient") as reader:
            client = reader.return_value
            client.retry_after = 0
            def get(resource, *args):
                if resource.endswith("/status"):
                    return {"sha": SHA, "statuses": [{"state": "failure"}], "total_count": 101}
                return {"default_branch": "main", "commit": {"sha": OLD_SHA}}
            def records(resource, *args, **kwargs):
                if resource == "pulls":
                    return [pull()], True
                if resource.endswith("/check-runs"):
                    return [{"head_sha": SHA, "conclusion": "success", "status": "completed"}], True
                return [], True
            client.get.side_effect = get
            client.collection.side_effect = records
            snapshot, _ = data._collect("")
        item = next(item for item in snapshot["items"] if item["id"] == "pr-12")
        self.assertEqual("blocked", item["status"])
        self.assertEqual("failed", item["test_status"]["state"])
        self.assertTrue(any(s["id"] == "status-12" and s["state"] == "partial" for s in snapshot["sources"]))

    def test_malformed_commit_status_is_isolated_as_source_error(self):
        with patch.object(data, "GitHubReadClient") as reader:
            client = reader.return_value
            client.retry_after = 0
            client.collection.side_effect = lambda resource, *args, **kwargs: ([pull()] if resource == "pulls" else [], True)
            client.get.side_effect = lambda resource, *args: (
                {"sha": SHA, "statuses": [None]} if resource.endswith("/status") else {"default_branch": "main"})
            snapshot, _ = data._collect("")
        self.assertTrue(any(s["id"] == "status-12" and s["state"] == "error" for s in snapshot["sources"]))
        self.assertEqual("unknown", snapshot["items"][0]["test_status"]["state"])


if __name__ == "__main__":
    unittest.main()
