"""RTP announcement boundaries and the existing queue/process lifecycle."""
from app.sqlite_utils import connect as sqlite_connect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.audio_output_store import AudioOutputStore
from app.audio_output_transport import announcement_command, validate_transport
from app.audio_output_worker import AudioOutputWorker


class AudioTransportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = AudioOutputStore(temp.name)
        self.target = {"kind": "rtp-udp", "host": "127.0.0.1", "port": 5004}
        self.worker = AudioOutputWorker(Path(temp.name))
        self.addCleanup(self.worker.stop)

    @unittest.skipUnless(shutil.which("node"), "Node.js required for frontend regression tests")
    def test_remote_registration_frontend(self):
        script = Path(__file__).with_name("audio_output_remote.test.cjs")
        result = subprocess.run([shutil.which("node"), "--test", str(script)], capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def register(self):
        return self.store.register_output("remote", "speaker", "Remote", volume=40, transport=self.target)

    def test_transport_rejects_unsafe_or_unsupported_targets_before_persistence(self):
        original = self.register()
        for change in ({"host": "https://host"}, {"host": "8.8.8.8"}, {"host": "224.0.0.1"},
                       {"host": "0.0.0.0"}, {"host": "169.254.169.254"}, {"host": "::1"},
                       {"host": 2130706433}, {"port": True}, {"port": 65535}, {"port": "5004"},
                       {"kind": "shell"}, {"extra": "value"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.store.register_output("remote", "speaker", "Changed", transport={**self.target, **change})
            self.assertEqual(original, self.store.output("remote", "speaker"))
        with self.assertRaises(ValueError):
            validate_transport(self.target, "local")

    def test_mixed_groups_resolve_and_local_only_contract_is_preserved(self):
        self.register()
        self.store.register_output("local", "desk", "Desk")
        self.store.set_group("all", "All", ["desk", "speaker"])
        self.assertEqual(2, len(self.store.playback_targets(["all"])))
        with self.assertRaises(ValueError):
            self.store.local_targets(["all"])
        self.assertEqual(self.target, AudioOutputStore(self.store.root).output("remote", "speaker")["transport"])

    def test_duplicate_remote_receiver_is_rejected_before_spawning(self):
        self.register()
        self.store.register_output("another", "duplicate", "Duplicate", transport=self.target)
        self.store.queue_sound("gong", ["speaker", "duplicate"])
        with patch("app.audio_output_worker.subprocess.Popen") as popen:
            self.worker.process_job(self.store, self.store.claim())
        popen.assert_not_called()
        self.assertEqual("failed", self.store.history()[0]["state"])

    def test_transport_command_is_fixed_opus_and_requires_existing_ffmpeg(self):
        with patch("app.audio_output_transport.shutil.which", return_value="ffmpeg"):
            command = announcement_command("file.wav", self.target)
        self.assertIn("libopus", command)
        self.assertIn("111", command)
        self.assertIn("-re", command)
        self.assertTrue(Path(command[command.index("-i") + 1]).is_absolute())
        self.assertEqual("rtp://127.0.0.1:5004?pkt_size=1200", command[-1])
        with patch("app.audio_output_transport.shutil.which", return_value=None), self.assertRaises(RuntimeError):
            announcement_command("file.wav", self.target)

    def test_remote_completion_is_sent_unconfirmed_and_cleans_volume_file(self):
        self.register()
        job = self.store.queue_sound("gong", ["speaker"])
        process = Mock(returncode=0); process.poll.return_value = 0
        with patch("app.audio_output_transport.shutil.which", return_value="ffmpeg"), patch("app.audio_output_worker.subprocess.Popen", return_value=process) as popen:
            self.worker.process_job(self.store, self.store.claim())
        result = self.store.announcement(job["id"])
        self.assertEqual(("done", "sent-unconfirmed"), (result["state"], result["delivery"]))
        command = popen.call_args.args[0]
        self.assertEqual("rtp://127.0.0.1:5004?pkt_size=1200", command[-1])
        self.assertFalse(Path(command[command.index("-i") + 1]).exists())
        self.assertFalse(popen.call_args.kwargs.get("shell", False))
        self.assertEqual([], self.worker.processes)

    def test_missing_program_retries_before_sending_but_failed_send_never_replays(self):
        self.register()
        job = self.store.queue_sound("gong", ["speaker"])
        with patch("app.audio_output_transport.shutil.which", return_value=None), patch("app.audio_output_worker.subprocess.Popen") as popen:
            self.worker.process_job(self.store, self.store.claim())
        popen.assert_not_called()
        self.assertEqual("queued", self.store.announcement(job["id"])["state"])
        self.store.cancel(job["id"])
        job = self.store.queue_sound("gong", ["speaker"])
        process = Mock(returncode=1); process.poll.return_value = 1
        with patch("app.audio_output_transport.shutil.which", return_value="ffmpeg"), patch("app.audio_output_worker.subprocess.Popen", return_value=process):
            self.worker.process_job(self.store, self.store.claim())
        self.assertEqual("failed", self.store.announcement(job["id"])["state"])
        self.assertEqual([], self.store.pending())

    def test_stop_during_transmission_cancels_and_terminates_sender(self):
        self.register()
        job = self.store.queue_sound("gong", ["speaker"])
        process = Mock(returncode=0)
        def poll():
            self.worker.stop_event.set()
            return None
        process.poll.side_effect = poll
        with patch("app.audio_output_transport.shutil.which", return_value="ffmpeg"), patch("app.audio_output_worker.subprocess.Popen", return_value=process):
            self.worker.process_job(self.store, self.store.claim())
        self.assertEqual("cancelled", self.store.announcement(job["id"])["state"])
        process.terminate.assert_called()

    def test_old_registry_migrates_without_claiming_remote_support(self):
        with tempfile.TemporaryDirectory() as temp:
            with sqlite_connect(Path(temp) / "audio-output.sqlite3") as db:
                db.execute("CREATE TABLE audio_output_node(node_id TEXT, output_id TEXT, name TEXT, device TEXT, channels INTEGER, online INTEGER, volume INTEGER, updated_at INTEGER, PRIMARY KEY(node_id,output_id))")
                db.execute("INSERT INTO audio_output_node VALUES('remote','old','Old','',2,1,100,1)")
            store = AudioOutputStore(temp)
            self.assertEqual({}, store.output("remote", "old")["transport"])
            with self.assertRaises(ValueError):
                store.playback_targets(["old"])
