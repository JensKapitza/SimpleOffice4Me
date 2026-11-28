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


class _WorkflowPeerNoticeFixture(PublicHttpTestCase):
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


class WorkflowPeerNoticeTests(_WorkflowPeerNoticeFixture):
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

    def test_read_receipts_are_explicit_atomic_private_and_do_not_remove_notices(self):
        store = ActivityStore(self.root)
        first = store.record(PEER_NOTICE, "owner", event_id="1" * 64)
        late = store.record(PEER_NOTICE, "owner", event_id="2" * 64)
        foreign = store.record(PEER_NOTICE, "other", event_id="3" * 64)
        unrelated = store.record("unrelated", "owner", event_id="4" * 64)
        url = "/automation-v3/copy-notices/read"
        feed = "/automation-v3/copy-notices"
        self.assertEqual(403, self.client.post(url, json={"ids": [first.event_id]}).status_code)
        for invalid in (foreign, unrelated):
            failed = self.client.post(url, headers=self.csrf, json={"ids": [first.event_id, invalid.event_id]})
            self.assertEqual(404, failed.status_code)
            self.assertEqual([], self.client.get(feed).json.get("seen", []))
        for _ in range(2):
            seen = self.client.post(url, headers=self.csrf, json={"ids": [first.event_id]})
            self.assertEqual(204, seen.status_code)
            self.assertEqual("private, no-store", seen.headers["Cache-Control"])
        updated = self.client.get(feed).json
        self.assertEqual([first.event_id], updated["seen"])
        self.assertEqual({first.event_id, late.event_id}, {row["id"] for row in updated["notices"]})
        self.assertEqual(2, len(store.list(actor="owner", event_type=PEER_NOTICE)))

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

    def test_local_midnight_is_accepted_across_dst_but_late_copy_is_not(self):
        from unittest.mock import patch
        from app.v3_automation_timing import utc_instant

        grant = self._grant()
        start = datetime.fromisoformat("2026-10-24T22:05:00+00:00")  # 00:05 before Berlin's fall-back.
        timing = {"end_of_day": True, "timezone": "Europe/Berlin"}
        expected = datetime.fromisoformat("2026-10-25T23:00:00+00:00")
        self.assertEqual(expected, utc_instant(timing_envelope(timing, start)["expires_at"]))
        with patch("app.workflow_peer_notices._now", return_value=start + timedelta(hours=9)):
            self.assertEqual(400, self._send(grant, at=start, timing={"within_seconds": 86400}).status_code)
            self.assertEqual("delivered", self._send(grant, at=start, timing=timing).json["status"])
        with patch("app.workflow_peer_notices._now", return_value=expected):
            self.assertEqual("window_missed", self._send(grant, at=start, timing=timing).json["status"])
        self.assertEqual(1, len(self._notice_rows()))

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


class WorkflowPeerNoticeRuleTests(_WorkflowPeerNoticeFixture):
    def setUp(self):
        super().setUp()
        flags = {"v3.activity": True, "v3.automation": True, "v3.federation": True, "v3.jobs": True}
        SettingsStore(self.root).save({"v3": flags}, "owner")
        with app.app_context():
            FederationStore(self.root).save_peer(
                "peer-a", "test peer", "https://example.invalid", self.key,
                policy={"data_classes": {"workflow_notices": {"send": True}}},
            )
        self.form = {"peer": "peer-a", "destination": "b" * 32, "source": "browser",
                     "delay": "0", "deadline": "120", "zone": "Europe/Berlin"}

    def _create(self):
        path = "/automation-v3/peer-copy-rule"
        self.assertEqual(403, self.client.post(path, data=self.form).status_code)
        self.assertEqual(303, self.client.post(path, headers=self.csrf, data=self.form).status_code)

    def _queue(self):
        from app.document_copy_events import capture_copy_completed
        from app.v3_jobs import JobStore
        from app.workflow_peer_notice_jobs import KIND
        with app.app_context():
            for _ in range(2):
                capture_copy_completed(self.root, "owner", "synthetic-copy")
        job, = JobStore(self.root).list(kind=KIND)
        return job

    def test_http_preset_is_idempotent_and_retries_keep_the_original_window(self):
        from unittest.mock import patch
        from app.v3_automation import AutomationStore
        from app.v3_automation_timing import utc_instant
        from app.v3_jobs import RetryableJobError
        from app.workflow_peer_notice_jobs import job_handler

        self._create()
        self._create()
        self.assertEqual(1, len(AutomationStore(self.root).list("owner")))
        job = self._queue()
        window = job.payload["window"]
        self.assertEqual("Europe/Berlin", window["origin_timezone"])
        self.assertEqual(120, (utc_instant(window["expires_at"]) - utc_instant(window["requested_at"])).seconds)
        with patch("app.workflow_peer_notice_jobs.send_notice", side_effect=[ConnectionError, "delivered"]) as send:
            with self.assertRaisesRegex(RetryableJobError, "peer_unreachable"):
                job_handler(self.root)(job)
            job_handler(self.root)(job)
            self.assertEqual([window, window], [call.kwargs["window"] for call in send.call_args_list])
        states = {row["status"] for row in AutomationStore(self.root).logs("owner")}
        self.assertEqual({"peer_queued", "peer_unreachable", "delivered"}, states)

    def test_local_delivery_preview_preserves_form_without_saving_or_contacting_peer(self):
        from unittest.mock import patch
        from bs4 import BeautifulSoup
        from app.v3_automation import AutomationStore
        from app.v3_jobs import JobStore

        utc_near_midnight = datetime(2026, 11, 28, 22, 59, 45, tzinfo=timezone.utc)
        fields = {**self.form, "source": "job", "delay": "30", "deadline": "end_of_day", "operation": "preview"}
        url = "/automation-v3/peer-copy-rule"
        self.assertEqual(403, self.client.post(url, data=fields).status_code)
        with patch("app.v3_automation_routes.datetime") as clock, patch(
            "app.workflow_peer_notice_jobs.send_notice", side_effect=AssertionError("unexpected network"),
        ) as delivery:
            clock.now.return_value = utc_near_midnight
            response = self.client.post(url, headers=self.csrf, data=fields)
            delivery.assert_not_called()
        self.assertEqual(200, response.status_code)
        self.assertEqual("private, no-store", response.headers["Cache-Control"])
        self.assertEqual("no-referrer", response.headers["Referrer-Policy"])
        soup = BeautifulSoup(response.data, "html.parser")
        sample = soup.select_one("[data-peer-rule-preview]")
        self.assertIsNotNone(sample)
        self.assertEqual(
            ["2026-11-28T23:59:45+01:00", "2026-11-29T00:00:15+01:00", "2026-11-29T00:00:00+01:00"],
            [tag["datetime"] for tag in sample.select("time")],
        )
        self.assertIsNotNone(sample.select_one(".fw-bold"))
        self.assertEqual("30", soup.select_one("#copy-send-delay option[selected]")["value"])
        self.assertEqual("end_of_day", soup.select_one("#copy-send-deadline option[selected]")["value"])
        self.assertEqual("job", soup.select_one("#copy-send-source option[selected]")["value"])
        self.assertEqual(self.form["destination"], soup.select_one("#copy-send-destination")["value"])
        self.assertEqual([], AutomationStore(self.root).list("owner"))
        self.assertEqual([], JobStore(self.root).list())
        self.assertEqual([], ActivityStore(self.root).list())
        self.assertEqual(403, self.client.post(url, headers=self.csrf,
                                               data={**fields, "peer": "peer-b"}).status_code)
        for bad in ({"zone": "Invalid/Zone"}, {"deadline": "10"}, {"destination": "bad"}, {"source": "bad"}):
            checked = self.client.post(url, headers=self.csrf, data={**fields, **bad})
            self.assertEqual(303, checked.status_code)
        self.assertEqual([], AutomationStore(self.root).list("owner"))
        self.assertEqual(303, self.client.post(url, headers=self.csrf,
                                               data={**fields, "operation": "save"}).status_code)
        self.assertEqual(1, len(AutomationStore(self.root).list("owner")))
        self.assertEqual([], JobStore(self.root).list())

    def test_expired_window_and_changed_rule_do_not_contact_peer(self):
        from unittest.mock import patch
        from app.v3_automation import AutomationStore
        from app.v3_automation_timing import utc_instant
        from app.v3_jobs import PermanentJobError
        from app.workflow_peer_notice_jobs import job_handler

        self._create()
        job = self._queue()
        store = AutomationStore(self.root)
        with patch("app.workflow_peer_notice_jobs.send_notice") as send:
            with patch("app.workflow_peer_notice_jobs._now", return_value=utc_instant(job.payload["window"]["expires_at"])):
                job_handler(self.root)(job)
            self.assertIn("window_missed", {row["status"] for row in store.logs("owner")})
            store.update(job.payload["rule_id"], "owner", {"enabled": False})
            with self.assertRaisesRegex(PermanentJobError, "rule has changed"):
                job_handler(self.root)(job)
            send.assert_not_called()

    def test_receipts_group_active_targets_by_copy_and_do_not_expose_grants_or_other_accounts(self):
        from bs4 import BeautifulSoup
        from app.document_copy_events import capture_copy_completed

        self._create()
        self.form["destination"] = "c" * 32
        self._create()
        with app.app_context():
            capture_copy_completed(self.root, "owner", "receipt-synthetic-copy")
        page = self.client.get("/automation-v3")
        box = BeautifulSoup(page.data, "html.parser").select_one("section[aria-labelledby=peer-copy-receipts-title]")
        self.assertIsNotNone(box)
        self.assertEqual(1, len(box.select("h3")))
        self.assertEqual(2, len(box.select('[data-delivery-state="peer_queued"]')))
        self.assertNotIn("b" * 32, page.get_data(as_text=True))
        self.assertNotIn("c" * 32, page.get_data(as_text=True))
        self._register(self.client, "other")
        other = app.test_client()
        self._login(other, "other")
        self.assertNotIn(b"peer-copy-receipts-title", other.get("/automation-v3").data)

    def test_receipt_stops_promising_retries_past_the_original_deadline(self):
        from app.v3_automation import AutomationStore
        from app.v3_automation_timing import utc_instant
        from app.v3_jobs import JobStore
        from app.workflow_peer_notice_jobs import receipts
        self._create()
        job = self._queue()
        deadline = utc_instant(job.payload["window"]["expires_at"])
        jobs = JobStore(self.root)
        now = int(deadline.timestamp()) - 3
        jobs.claim("offline-worker", now=now)
        jobs.fail(job.job_id, "peer_unreachable", retryable=True, now=now)
        store = AutomationStore(self.root)
        with app.app_context():
            rules = store.list("owner")
            waiting, = receipts(self.root, "owner", store, rules, clock=lambda: deadline - timedelta(seconds=2))
            expired, = receipts(self.root, "owner", store, rules, clock=lambda: deadline)
        self.assertEqual("retry_after_deadline", waiting["targets"][0]["state"])
        self.assertEqual("window_missed", expired["targets"][0]["state"])
        self.assertIsNone(waiting["targets"][0]["next_attempt"])
        self.assertIsNone(expired["targets"][0]["next_attempt"])

    def test_receipts_show_retry_eligibility_in_sender_zone_without_promising_delivery(self):
        from unittest.mock import patch
        from bs4 import BeautifulSoup
        from app.v3_automation import AutomationStore
        from app.v3_automation_timing import utc_instant
        from app.v3_jobs import JobStore
        from app.workflow_peer_notice_jobs import receipts

        self.form["zone"] = "Asia/Kathmandu"
        self._create()
        job = self._queue()
        deadline = utc_instant(job.payload["window"]["expires_at"])
        jobs = JobStore(self.root)
        now = int(deadline.timestamp()) - 35
        jobs.claim("test-worker", now=now)
        jobs.fail(job.job_id, "recipient_busy", retryable=True, now=now)
        clock = lambda: datetime.fromtimestamp(now, timezone.utc)
        with patch("app.v3_automation_routes.peer_jobs.receipts", wraps=receipts) as read:
            read.side_effect = lambda *args: receipts(*args, clock=clock)
            page = self.client.get("/automation-v3")
        row = BeautifulSoup(page.data, "html.parser").select_one('[data-delivery-state="recipient_busy"]')
        self.assertIsNotNone(row)
        expected = datetime.fromtimestamp(now + 5, timezone.utc)
        self.assertEqual(expected.isoformat(), row.select_one("[data-next-attempt]")["datetime"])
        self.assertIn("Asia/Kathmandu", row.get_text())
        self.assertNotIn("b" * 32, row.get_text())

        store = AutomationStore(self.root)
        with app.app_context():
            rules = store.list("owner")
            due, = receipts(self.root, "owner", store, rules, clock=lambda: expected)
        self.assertEqual("retry_due", due["targets"][0]["state"])
        self.assertIsNone(due["targets"][0]["next_attempt"])
        self.assertEqual("retry_scheduled", jobs.get(job.job_id).state)

        jobs.claim("test-worker", now=now + 5)
        jobs.fail(job.job_id, "peer_unreachable", retryable=False, now=now + 5)
        with app.app_context():
            failed, = receipts(self.root, "owner", store, rules, clock=lambda: expected)
        self.assertEqual("peer_unreachable_final", failed["targets"][0]["state"])
        self.assertIsNone(failed["targets"][0]["next_attempt"])

    def test_real_queue_limit_surfaces_an_account_scoped_receipt_without_duplicate_on_replay(self):
        from bs4 import BeautifulSoup
        from app.db import get_db
        from app.document_copy_events import capture_copy_completed
        from app.v3_automation import AutomationStore
        from app.v3_jobs import JobStore
        from app.workflow_peer_notice_jobs import KIND, receipts

        self._create()
        jobs = JobStore(self.root)
        filled = [jobs.enqueue(KIND, {}, "owner", idempotency_key=f"fixture-{i}") for i in range(50)]
        with app.app_context():
            event_id = capture_copy_completed(self.root, "owner", "real-capacity-copy")
            self.assertIsNotNone(event_id)
            self.assertEqual(50, len(jobs.list(kind=KIND)))
        page = self.client.get("/automation-v3")
        receipt = BeautifulSoup(page.data, "html.parser").select_one('[data-delivery-state="peer_queue_unavailable"]')
        self.assertIsNotNone(receipt)
        self.assertIn(": 0", receipt.get_text())
        self.assertIsNone(receipt.select_one("[data-next-attempt]"))
        self.assertNotIn("b" * 32, receipt.get_text())
        self._register(self.client, "other")
        other = app.test_client()
        self._login(other, "other")
        self.assertNotIn(b"peer-copy-receipts-title", other.get("/automation-v3").data)

        with app.app_context():
            store = AutomationStore(self.root)
            db = get_db()
            version = db.execute("SELECT auth_version FROM user WHERE username=?", ("owner",)).fetchone()[0]
            db.execute("UPDATE user SET auth_version=? WHERE username=?", (version + 1, "owner"))
            db.commit()
            self.assertEqual([], receipts(self.root, "owner", store, store.list("owner")))
            db.execute("UPDATE user SET auth_version=? WHERE username=?", (version, "owner"))
            db.commit()

            jobs.cancel(filled[0].job_id)
            self.assertEqual(event_id, capture_copy_completed(self.root, "owner", "real-capacity-copy"))
            self.assertEqual(event_id, capture_copy_completed(self.root, "owner", "real-capacity-copy"))
            real_jobs = [job for job in jobs.list(kind=KIND) if job.payload.get("event_id") == event_id]
            self.assertEqual(1, len(real_jobs))
            entry, = receipts(self.root, "owner", store, store.list("owner"))
            target, = entry["targets"]
            self.assertEqual("peer_queued", target["state"])

    def test_day_end_rule_persists_bounded_long_retry_and_original_zone(self):
        from app.v3_automation_timing import action_window, utc_instant

        self.form.update({"deadline": "end_of_day", "zone": "Pacific/Honolulu"})
        self._create()
        job = self._queue()
        self.assertEqual(40, job.max_attempts)
        window = job.payload["window"]
        origin = utc_instant(window["requested_at"])
        self.assertEqual("Pacific/Honolulu", window["origin_timezone"])
        _, deadline = action_window({"end_of_day": True}, origin, "Pacific/Honolulu")
        self.assertEqual(deadline, utc_instant(window["expires_at"]))
