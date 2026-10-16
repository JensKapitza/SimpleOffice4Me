import tempfile
import unittest

from app.library.scan_sessions import MAX_RECENT_SCANS, ScanSessionStore


class ScanSessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ScanSessionStore(self.temp.name)

    def test_session_resumes_after_new_store_instance_and_is_user_bound(self):
        session = self.store.start("alice", "shelf-a", "Room / Shelf A")
        self.store.record(
            "alice", session_id=session["session_id"], scan_id="request-1",
            location_id="shelf-a", outcome="assigned", display_id="12", name="Book",
        )
        restored = ScanSessionStore(self.temp.name).get("alice")
        self.assertEqual(1, restored["assigned"])
        self.assertEqual("Room / Shelf A", restored["location_path"])
        self.assertIsNone(self.store.get("bob"))

    def test_pause_resume_and_finish_are_explicit(self):
        session = self.store.start("alice", "shelf-a", "Shelf A")
        paused = self.store.transition("alice", "pause")
        self.assertEqual("paused", paused["status"])
        with self.assertRaisesRegex(ValueError, "pausiert"):
            self.store.record(
                "alice", session_id=session["session_id"], scan_id="request-1",
                location_id="shelf-a", outcome="assigned",
            )
        self.assertEqual("active", self.store.transition("alice", "resume")["status"])
        self.assertIsNone(self.store.transition("alice", "finish"))
        self.assertIsNone(self.store.get("alice"))

    def test_scan_id_is_idempotent_and_location_is_bound(self):
        session = self.store.start("alice", "shelf-a", "Shelf A")
        first = self.store.record(
            "alice", session_id=session["session_id"], scan_id="request-1",
            location_id="shelf-a", outcome="duplicate", name="Existing",
        )
        repeated = self.store.record(
            "alice", session_id=session["session_id"], scan_id="request-1",
            location_id="shelf-a", outcome="duplicate", name="Existing",
        )
        self.assertEqual(1, first["total"])
        self.assertEqual(1, repeated["duplicates"])
        with self.assertRaisesRegex(ValueError, "anderen Standort"):
            self.store.record(
                "alice", session_id=session["session_id"], scan_id="request-2",
                location_id="shelf-b", outcome="assigned",
            )

    def test_recent_history_is_bounded(self):
        session = self.store.start("alice", "shelf-a", "Shelf A")
        for index in range(MAX_RECENT_SCANS + 4):
            self.store.record(
                "alice", session_id=session["session_id"], scan_id=f"request-{index}",
                location_id="shelf-a", outcome="missing", name="x" * 500,
            )
        restored = self.store.get("alice")
        self.assertEqual(MAX_RECENT_SCANS + 4, restored["total"])
        self.assertEqual(MAX_RECENT_SCANS, len(restored["recent"]))
        self.assertEqual("request-4", restored["recent"][0]["scan_id"])
        self.assertEqual(200, len(restored["recent"][-1]["name"]))


if __name__ == "__main__":
    unittest.main()
