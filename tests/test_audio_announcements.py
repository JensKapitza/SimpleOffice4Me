import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.audio_output_store import AudioOutputStore
from app.audio_output_worker import AudioOutputWorker
from app.audio_calendar import queue_due_calendar_events
from datetime import datetime, timezone


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AudioOutputStore(self.temp.name)
        self.worker = AudioOutputWorker(Path(self.temp.name))
        self.addCleanup(self.worker.stop)
        self.store.register_output("local", "speaker", "Local speaker", device="default")

    def test_two_consumers_cannot_claim_same_job(self):
        job = self.store.queue_sound("gong", ["speaker"])
        barrier = threading.Barrier(2)
        results = []
        def claim():
            barrier.wait()
            results.append(self.store.claim())
        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        claimed = [item for item in results if item is not None]
        self.assertEqual([job["id"]], [item["id"] for item in claimed])

    def test_local_playback_completes_and_uses_no_shell(self):
        job = self.store.queue_sound("gong", ["speaker"])
        player = Mock(returncode=0); player.poll.return_value = 0
        with patch("app.audio_output_worker.playback_command", return_value=["player", "file.wav"]), patch("app.audio_output_worker.subprocess.Popen", return_value=player) as popen:
            self.worker.process_job(self.store, self.store.claim())
        self.assertEqual("done", self.store.announcement(job["id"])["state"])
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual([], self.worker.processes)

    def test_unsupported_remote_target_fails_without_lingering(self):
        self.store.register_output("remote", "remote-speaker", "Remote")
        job = self.store.queue_sound("gong", ["remote-speaker"])
        self.worker.process_job(self.store, self.store.claim())
        result = self.store.announcement(job["id"])
        self.assertEqual("failed", result["state"])
        self.assertIn("externe Knoten", result["error"])

    def test_missing_hardware_retries_before_playback_only(self):
        self.store.register_output("local", "speaker", "Offline", online=False)
        job = self.store.queue_sound("gong", ["speaker"])
        self.worker.process_job(self.store, self.store.claim())
        result = self.store.announcement(job["id"])
        self.assertEqual("queued", result["state"])
        self.assertEqual(1, result["attempts"])
        self.assertIsNone(self.store.claim())

    def test_partial_playback_failure_is_not_replayed(self):
        job = self.store.queue_sound("gong", ["speaker"])
        player = Mock(returncode=1); player.poll.return_value = 1
        with patch("app.audio_output_worker.playback_command", return_value=["player", "file.wav"]), patch("app.audio_output_worker.subprocess.Popen", return_value=player):
            self.worker.process_job(self.store, self.store.claim())
        self.assertEqual("failed", self.store.announcement(job["id"])["state"])

    def test_cycles_and_ambiguous_output_ids_are_rejected(self):
        self.store.set_group("first", "First", ["second"])
        self.store.set_group("second", "Second", ["first"])
        with self.assertRaises(ValueError): self.store.local_targets(["first"])
        self.store.register_output("remote", "speaker", "Remote")
        with self.assertRaises(ValueError): self.store.local_targets(["speaker"])

    def test_pending_cancel_and_crash_recovery(self):
        job = self.store.queue_sound("gong", ["speaker"])
        self.assertTrue(self.store.cancel(job["id"]))
        self.assertIsNone(self.store.claim())
        second = self.store.queue_sound("gong", ["speaker"])
        self.store.claim()
        self.store.recover_interrupted()
        self.assertEqual("failed", self.store.announcement(second["id"])["state"])

    def test_calendar_poll_does_not_duplicate_an_occurrence(self):
        now = datetime(2026, 9, 14, tzinfo=timezone.utc)
        events = [{"event_id": "meeting", "start": now.isoformat(), "audio_announcement": {"enabled": True, "targets": ["speaker"], "minutes_before": 0}}]
        queue_due_calendar_events(self.store, events, now=now)
        queue_due_calendar_events(self.store, events, now=now)
        self.assertEqual(1, len(self.store.pending()))

    def test_invalid_settings_and_targets_are_rejected(self):
        with self.assertRaises(ValueError): self.worker.save_settings({"enabled": "true", "autostart": True, "retry_limit": 3})
        with self.assertRaises(ValueError): self.store.queue_sound("gong", "speaker")


if __name__ == "__main__": unittest.main()
