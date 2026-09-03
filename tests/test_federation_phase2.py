from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from app.federation_core import build_manifest
from app.federation_phase2 import _availability_set, bp, discover_blob_sources
from app.federation_store import FederationStore
from app.todo_store import TodoStore


class FederationPhase2Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "federation-phase2-test-secret"
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        self.temp.cleanup()

    def _peer(self, peer_id: str) -> None:
        FederationStore(self.root).save_peer(
            peer_id,
            peer_id,
            f"http://{peer_id}.example.test",
            "secret",
            {"resources": {"documents": {"receive": True}}},
            True,
        )

    def test_availability_ranges_are_bounded(self):
        self.assertEqual({0, 1, 2, 4}, _availability_set({"available": [[0, 2], [4, 99], [-1, 2], [8, 7]]}, 5))

    def test_discovery_combines_partial_sources(self):
        source = self.root / "blob.bin"
        source.write_bytes(b"A" * (4 * 1024 * 1024) + b"B" * (4 * 1024 * 1024) + b"tail")
        manifest = build_manifest(source, 4 * 1024 * 1024)
        digest = manifest["blob_hash"]
        self._peer("peer-a")
        self._peer("peer-b")

        def availability(_root, peer_id, _digest):
            return {"chunk_count": manifest["chunk_count"], "available": [[0, 0]] if peer_id == "peer-a" else [[1, manifest["chunk_count"] - 1]]}

        with patch("app.federation_phase2.remote_blob_manifest", return_value=manifest), patch(
            "app.federation_phase2.remote_availability", side_effect=availability
        ):
            result = discover_blob_sources(self.root, digest)
        self.assertEqual({0}, result["sources"]["peer-a"])
        self.assertEqual(set(range(1, manifest["chunk_count"])), result["sources"]["peer-b"])

    def test_discovery_rejects_uncovered_chunks(self):
        source = self.root / "blob.bin"
        source.write_bytes(b"x" * (5 * 1024 * 1024))
        manifest = build_manifest(source, 1024 * 1024)
        self._peer("peer-a")
        with patch("app.federation_phase2.remote_blob_manifest", return_value=manifest), patch(
            "app.federation_phase2.remote_availability",
            return_value={"chunk_count": manifest["chunk_count"], "available": [[0, 0]]},
        ):
            with self.assertRaisesRegex(ValueError, "cannot provide all chunks"):
                discover_blob_sources(self.root, manifest["blob_hash"])


class FederationResourceEndpointTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = Flask(__name__)
        self.app.testing = True
        self.app.config["DOCUMENT_ROOT"] = str(self.root)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def test_task_export_and_import_keep_uid_and_newer_sequence(self):
        store = TodoStore(self.root)
        created = store.add("Pruefen", "alice", {"uid": "task-1@example.test", "sequence": 1, "description": "alt"})
        exported = self.client.get("/federation/v1/resources/tasks/alice/export.json")
        self.assertEqual(200, exported.status_code)
        payload = exported.get_json()
        self.assertEqual("sofp-tasks/v1", payload["schema"])
        self.assertEqual("task-1@example.test", payload["tasks"][0]["uid"])

        payload["tasks"][0]["sequence"] = 2
        payload["tasks"][0]["description"] = "neu"
        imported = self.client.post(
            "/federation/v1/resources/tasks/alice/import.json",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(200, imported.status_code)
        self.assertEqual(1, imported.get_json()["updated"])
        current = next(item for item in TodoStore(self.root).items("alice") if item["id"] == created["id"])
        self.assertEqual("neu", current["description"])

    def test_calendar_roundtrip_endpoint(self):
        body = "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN", "BEGIN:VEVENT",
            "UID:event-1@example.test", "DTSTART:20260904T100000Z", "DTEND:20260904T110000Z",
            "SUMMARY:Test", "END:VEVENT", "END:VCALENDAR", "",
        ])
        response = self.client.post(
            "/federation/v1/resources/calendars/alice/import.ics",
            data=body.encode("utf-8"), content_type="text/calendar",
        )
        self.assertEqual(200, response.status_code)
        exported = self.client.get("/federation/v1/resources/calendars/alice/export.ics")
        self.assertEqual(200, exported.status_code)
        self.assertIn(b"UID:event-1@example.test", exported.data)


if __name__ == "__main__":
    unittest.main()
