"""Verify download retry display using the real catalog and Flask interface."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from bs4 import BeautifulSoup

from app.federation_catalog import FederationCatalog
from tests.federation_admin_ui_fixture import FederationAdminUiFixture


class FederationAdminDownloadRetryTests(FederationAdminUiFixture, unittest.TestCase):
    now = 1_800_000_000
    transfer_path = "/admin/federation?view=transfers"

    def setUp(self):
        super().setUp()
        self.catalog = FederationCatalog(self.root)
        self.peer_id = "office-b"
        self.catalog.begin_index(self.peer_id, "gen-retries")
        self.statuses = {
            "future-retry": ("retry", self.now + 90),
            "future-wait": ("waiting_peer", self.now + 120),
            "due": ("retry", self.now),
            "past": ("waiting_peer", self.now - 1),
            "zero": ("retry", 0),
            "queued": ("queued", 0),
            "running": ("running", self.now + 90),
            "complete": ("complete", self.now + 90),
            "overflow": ("retry", 2**63 - 1),
        }
        self.catalog.ingest(self.peer_id, "gen-retries", [
            {"document_id": name, "path": f"{name}.pdf", "blob_hash": f"{number:064x}", "size": 10}
            for number, name in enumerate(self.statuses, 1)
        ])
        self.catalog.finish_index(self.peer_id, "gen-retries")
        self.requests = {}
        for name, (status, next_attempt) in self.statuses.items():
            row = self.catalog.request_download(self.peer_id, name, requested_by="federation-ui-admin")
            self.requests[name] = row["request_id"]
            self.catalog.update_request(row["request_id"], status=status, next_attempt_at=next_attempt)

    def test_transfers_shows_eligibility_and_absolute_retry_time(self):
        snapshot = self.catalog.queue()
        for language, scheduled, ready in (
            ("de", "Frühester nächster Versuch:", "Wartezeit abgelaufen"),
            ("en", "Earliest retry:", "Retry delay elapsed"),
        ):
            with self.subTest(language=language):
                with self.client.session_transaction() as session:
                    session["simpleoffice_language"] = language
                with patch("app.federation_admin.time") as clock, \
                        patch("app.federation_catalog._now", return_value=self.now):
                    clock.time.return_value = self.now
                    eligible = {row["request_id"] for row in self.catalog.next_requests(25)}
                    result = self.client.get(self.transfer_path)
                self.assertEqual(200, result.status_code)
                page = BeautifulSoup(result.get_data(as_text=True), "html.parser")
                for name, (status, timestamp) in self.statuses.items():
                    with self.subTest(name=name):
                        form = page.find("form", action=f'/admin/federation/queue/{self.requests[name]}/priority')
                        self.assertIsNotNone(form)
                        cell = form.find_parent("tr").find("td")
                        self.assertEqual(status, cell.select_one(".badge").get_text())
                        time = cell.find("time")
                        if name.startswith("future-"):
                            self.assertNotIn(self.requests[name], eligible)
                            self.assertIn(scheduled, cell.get_text())
                            self.assertIsNotNone(time)
                            self.assertEqual(datetime.fromtimestamp(timestamp, timezone.utc),
                                             datetime.fromisoformat(time["datetime"]))
                            self.assertTrue(time.get_text().endswith(" UTC"))
                        else:
                            self.assertIsNone(time)
                            if name in {"due", "past", "zero"}:
                                self.assertIn(self.requests[name], eligible)
                                self.assertIn(ready, cell.get_text())
                            else:
                                self.assertNotIn(ready, cell.get_text())
        self.assertEqual(snapshot, self.catalog.queue())
