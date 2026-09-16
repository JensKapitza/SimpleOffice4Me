import tempfile
import sqlite3
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.audio_output_store import AudioOutputStore
from app.audio_output_worker import AudioOutputWorker
from app.audio_calendar import queue_due_calendar_events
from datetime import datetime, timezone
from simpleoffice_service_lifecycle import log_service_event


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

    def test_manual_output_volume_file_exists_until_player_finishes(self):
        self.store.register_output("local", "speaker", "Quiet", device="default", volume=50)
        job = self.store.queue_sound("gong", ["speaker"])
        files = []
        def command(path, device):
            files.append(Path(path))
            self.assertTrue(Path(path).is_file())
            self.assertEqual("default", device)
            return ["player", str(path)]
        player = Mock(returncode=0)
        def poll():
            self.assertTrue(files[0].is_file())
            return 0
        player.poll.side_effect = poll
        with patch("app.audio_output_worker.playback_command", side_effect=command), patch("app.audio_output_worker.subprocess.Popen", return_value=player):
            self.worker.process_job(self.store, self.store.claim())
        self.assertEqual("done", self.store.announcement(job["id"])["state"])
        self.assertFalse(files[0].exists())
        self.assertTrue(list((self.store.root / "rendered").glob("preset-*.wav")))

    def test_unsupported_remote_target_fails_without_lingering(self):
        self.store.register_output("remote", "remote-speaker", "Remote")
        job = self.store.queue_sound("gong", ["remote-speaker"])
        self.worker.process_job(self.store, self.store.claim())
        result = self.store.announcement(job["id"])
        self.assertEqual("failed", result["state"])
        self.assertIn("externe Knoten", result["error"])

    def test_player_start_failure_cleans_attenuated_audio(self):
        self.store.register_output("local", "speaker", "Quiet", volume=40)
        job = self.store.queue_sound("gong", ["speaker"])
        with patch("app.audio_output_worker.playback_command", return_value=["player"]), patch("app.audio_output_worker.subprocess.Popen", side_effect=OSError("not available")):
            self.worker.process_job(self.store, self.store.claim())
        self.assertEqual("queued", self.store.announcement(job["id"])["state"])
        self.assertEqual([], list((self.store.root / "rendered").glob("playback-*")))

    def test_cleanup_failure_does_not_skip_other_players_and_keeps_retryable_handles(self):
        blocked = Mock()
        blocked.poll.return_value = None
        blocked.terminate.side_effect = PermissionError("private-context")
        blocked.kill.side_effect = PermissionError("private-context")
        healthy = Mock()
        healthy.poll.return_value = None
        self.worker.processes = [blocked, healthy]
        with self.assertLogs("simpleoffice.mini_services", level="ERROR") as logs:
            self.worker._cleanup_players()
        self.assertNotIn("private-context", "\n".join(logs.output))
        healthy.terminate.assert_called_once()
        healthy.wait.assert_called_once_with(timeout=2)
        self.assertEqual([blocked], self.worker.processes)
        self.assertTrue(self.worker.stop_event.is_set())
        with self.assertRaises(RuntimeError):
            self.worker.start()
        blocked.poll.return_value = 0
        self.worker.stop()
        self.assertEqual([], self.worker.processes)
        self.assertEqual("stopped", self.worker.state.state)

    def test_player_wait_timeout_uses_bounded_kill_fallback(self):
        player = Mock()
        player.poll.return_value = None
        player.wait.side_effect = [subprocess.TimeoutExpired("player", 2), 0]
        self.worker.processes = [player]
        self.worker._cleanup_players()
        player.kill.assert_called_once()
        self.assertEqual(2, player.wait.call_count)
        self.assertEqual([], self.worker.processes)

    def test_cleanup_os_error_still_removes_volume_files_and_active_job(self):
        self.store.register_output("local", "speaker", "Quiet", volume=40)
        self.store.queue_sound("gong", ["speaker"])
        files = []
        def command(path, device):
            files.append(Path(path))
            return ["player", str(path)]
        player = Mock(returncode=0)
        player.poll.side_effect = [0, OSError("gone")]
        with patch("app.audio_output_worker.playback_command", side_effect=command), patch("app.audio_output_worker.subprocess.Popen", return_value=player):
            self.worker.process_job(self.store, self.store.claim())
        self.assertTrue(files)
        self.assertFalse(files[0].exists())
        self.assertIsNone(self.worker.active_job)
        self.assertEqual([], self.worker.processes)
        player.kill.assert_called_once()

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

    def test_unreadable_autostart_settings_do_not_abort_web_start(self):
        with patch.object(self.worker, "settings", side_effect=sqlite3.OperationalError("private detail")):
            self.worker.start_background()
        self.assertEqual("failed", self.worker.state.state)
        self.assertIsNone(self.worker.thread)
        self.assertIsNone(self.worker.state.retry_at)

    def test_worker_storage_open_failures_have_bounded_cancellable_recovery(self):
        with patch.object(self.worker, "store", side_effect=sqlite3.OperationalError("locked")) as store, patch.object(self.worker.stop_event, "wait", return_value=False) as wait:
            self.worker._run()
        self.assertEqual(4, store.call_count)
        self.assertEqual([2, 4, 8], [call.args[0] for call in wait.call_args_list])
        self.assertEqual("failed", self.worker.state.state)
        self.assertIsNone(self.worker.state.retry_at)
        self.worker.state.retry_count = 0
        def cancel(_delay):
            self.worker.stop_event.set()
            return True
        with patch.object(self.worker, "store", side_effect=PermissionError) as store, patch.object(self.worker.stop_event, "wait", side_effect=cancel):
            self.worker._run()
        self.assertEqual(1, store.call_count)
        self.assertEqual("stopped", self.worker.state.state)

    def test_worker_recovers_after_transient_storage_failure(self):
        calls = []
        def attempt():
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("locked")
            self.worker.state.running()
        with patch.object(self.worker, "_run_once", side_effect=attempt), patch.object(self.worker.stop_event, "wait", return_value=False):
            self.worker._run()
        self.assertEqual(2, len(calls))
        self.assertEqual("running", self.worker.state.state)

    def test_shared_diagnostics_include_frames_but_no_exception_payload(self):
        try:
            raise ValueError("secret-password-do-not-log")
        except ValueError as exc:
            with self.assertLogs("simpleoffice.mini_services", level="ERROR") as captured:
                log_service_event("audio-output", "failed", exc=exc, operation_id="operation-1")
        message = captured.output[0]
        self.assertNotIn("secret-password-do-not-log", message)
        for value in ("timestamp", "severity", "trace", "test_audio_announcements.py", "operation-1"):
            self.assertIn(value, message)


if __name__ == "__main__": unittest.main()
