import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.audio_streamer import LiveAudioManager, decoder_command
from app.audio_streamer_config import validate_settings, settings
from flask import Flask, g
from app.audio_streamer_admin import bp
from app.security_controls import protect_browser_mutation


class AudioSettingsTests(unittest.TestCase):
    def test_storage_failure_does_not_abort_other_audio_autostarts(self):
        manager = LiveAudioManager()
        with patch.object(manager, "_ensure_monitor"), patch("app.audio_streamer_config.settings", side_effect=[sqlite3.OperationalError("locked"), {"enabled": False}]) as read:
            manager.start_background()
        self.assertEqual(2, read.call_count)
        self.assertEqual("failed", manager.states["sender"].state)
        self.assertEqual("stopped", manager.states["receiver"].state)

    def test_defaults_do_not_capture_microphone_on_start(self):
        for service in ("sender", "receiver"):
            self.assertFalse(validate_settings(service, {})["autostart"])

    def test_invalid_types_addresses_and_capture_options(self):
        for service, value in (
            ("sender", {"autostart": "false"}), ("sender", {"source": "bad\x00source"}),
            ("sender", {"bitrate_kbps": 1000}), ("sender", {"destinations": "host"}),
            ("receiver", {"port": 1}), ("receiver", {"bind": "0.0.0.0"}),
            ("receiver", {"bind": "239.0.0.1"}), ("receiver", {"virtual_microphone": "false"}),
            ("sender", {"retry_limit": 100}), ("sender", {"autostart": True}),
        ):
            with self.subTest(service=service, value=value), self.assertRaises(ValueError):
                validate_settings(service, value)

    def test_settings_persist_in_existing_audio_database(self):
        with tempfile.TemporaryDirectory() as temp, patch("app.audio_streamer_config.default_config_path", return_value=Path(temp) / "mini.json"):
            first = settings("sender", {"source": "usb-mic", "destinations": [{"host": "192.168.1.30", "port": 5004}]})
            self.assertEqual(first, settings("sender"))
            self.assertTrue((Path(temp) / "audio/audio-output.sqlite3").is_file())

    def test_decoder_binds_only_requested_interface(self):
        with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"):
            command = decoder_command("stream.sdp", "127.0.0.1")
        self.assertEqual("127.0.0.1", command[command.index("-localaddr") + 1])


class AudioLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.manager = LiveAudioManager()
        self.addCleanup(self.manager.stop_all)
        patcher = patch.object(self.manager, "_ensure_monitor")
        patcher.start(); self.addCleanup(patcher.stop)
        self.process = Mock(pid=123, stdin=None, stdout=None)
        self.process.poll.return_value = None
        self.targets = [{"host": "127.0.0.1", "port": 5004}]

    def start(self):
        return self.manager.start_sender(source="default", backend="pulse", destinations=self.targets)

    def test_repeated_start_and_stop_are_idempotent(self):
        with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"), patch("app.audio_streamer.subprocess.Popen", return_value=self.process) as popen:
            self.start(); self.start()
            self.assertEqual(1, popen.call_count)
            self.manager.stop_sender(); self.manager.stop_sender()
            self.assertEqual(1, self.process.terminate.call_count)
            self.start()
            self.assertEqual(2, popen.call_count)

    def test_invalid_config_does_not_stop_active_stream(self):
        with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"), patch("app.audio_streamer.subprocess.Popen", return_value=self.process):
            self.start()
            with self.assertRaises(ValueError):
                self.manager.start_sender(source="default", backend="invalid", destinations=self.targets)
            self.process.terminate.assert_not_called()
            self.assertTrue(self.manager.status()["sender"]["running"])

    def test_windows_sender_uses_existing_idempotent_lifecycle_and_recovery(self):
        with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"), patch("app.audio_streamer.subprocess.Popen", return_value=self.process) as popen:
            for _ in range(2):
                self.manager.start_sender(source="USB Mic", backend="dshow", destinations=self.targets)
            self.assertEqual(1, popen.call_count)
            self.assertIn("audio=USB Mic", popen.call_args.args[0])
            self.process.poll.return_value = 1
            self.manager.recover()
            self.assertEqual("failed", self.manager.states["sender"].state)
            self.manager.states["sender"].retry_at = 0.01
            self.process.poll.return_value = None
            self.manager.recover()
            self.assertEqual(2, popen.call_count)
            self.assertIn("audio=USB Mic", popen.call_args.args[0])
            self.manager.stop_sender()
            self.manager.stop_sender()
            self.assertFalse(self.manager.status()["sender"]["running"])

    def test_crash_has_bounded_recovery_and_explicit_stop_cancels_it(self):
        with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"), patch("app.audio_streamer.subprocess.Popen", return_value=self.process) as popen:
            self.start()
            self.process.poll.return_value = 1
            self.manager.recover()
            state = self.manager.states["sender"]
            self.assertEqual("failed", state.state)
            self.assertEqual(1, state.retry_count)
            state.retry_at = 0.01
            self.process.poll.return_value = None
            self.manager.recover()
            self.assertEqual(2, popen.call_count)
            self.assertEqual("running", self.manager.states["sender"].state)
            self.manager.stop_sender()
            self.manager.recover()
            self.assertEqual(2, popen.call_count)

    def test_missing_dependency_can_recover_after_reappearing(self):
        with tempfile.TemporaryDirectory() as temp, patch("app.audio_streamer_config.default_config_path", return_value=Path(temp) / "mini.json"):
            with patch("app.audio_streamer.shutil.which", return_value=None):
                with self.assertRaises(RuntimeError):
                    self.manager.configured_start("sender", {"destinations": self.targets})
            self.assertEqual("failed", self.manager.states["sender"].state)
            self.manager.states["sender"].retry_at = 0.01
            with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"), patch("app.audio_streamer.subprocess.Popen", return_value=self.process):
                self.manager.recover()
            self.assertTrue(self.manager.status()["sender"]["running"])

    def test_receiver_needs_live_fanout_and_playback_in_addition_to_decoder(self):
        decoder = Mock(); decoder.poll.return_value = None
        player = Mock(); player.poll.return_value = 1
        thread = Mock(); thread.is_alive.return_value = True
        self.manager.receiver = Mock(decoder=decoder, players=[player], thread=thread)
        self.assertFalse(self.manager._receiver_alive())
        player.poll.return_value = None
        self.assertTrue(self.manager._receiver_alive())


class AudioApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test")
        self.app.register_blueprint(bp)
        self.user = {"is_admin": True, "is_disabled": False}
        self.app.before_request(lambda: setattr(g, "user", self.user))
        self.app.before_request(protect_browser_mutation)
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        self.headers = {"X-CSRF-Token": "x" * 40}
        for target, value in (("app.audio_streamer_config.default_config_path", Path(self.temp.name) / "mini.json"), ("app.audio_streamer_admin.audit", None)):
            patcher = patch(target, return_value=value)
            patcher.start(); self.addCleanup(patcher.stop)

    def test_settings_api_rejects_forgery_and_invalid_bool(self):
        url = "/admin/mini-services/audio/streamer/sender/settings"
        self.assertEqual(403, self.client.post(url, json={"enabled": False}).status_code)
        self.assertEqual(400, self.client.post(url, json={"autostart": "true"}, headers=self.headers).status_code)
        self.assertEqual(200, self.client.post(url, json={"source": "usb-microphone"}, headers=self.headers).status_code)
        self.assertEqual("usb-microphone", self.client.get("/admin/mini-services/audio/streamer/settings").json["sender"]["source"])
        self.user["is_admin"] = False
        self.assertEqual(403, self.client.get("/admin/mini-services/audio/streamer/settings").status_code)

    def test_windows_receiver_scan_reset_and_old_settings_remain_editable(self):
        base = "/admin/mini-services/audio/streamer"
        with patch("app.audio_streamer_admin.platform.system", return_value="Windows"), patch("app.audio_output_discovery.shutil.which", return_value="ffplay"), patch("app.audio_streamer_admin.manager.stop_receiver"):
            result = self.client.get(base + "/outputs")
            self.assertEqual(200, result.status_code)
            self.assertEqual("unknown", result.json["outputs"][0]["state"])
            self.assertIn("keine Hardwareprüfung", result.json["message"])
            self.assertEqual(403, self.client.post(base + "/receiver/reset").status_code)
            old = {"virtual_microphone": True, "speaker_devices": ["old-pulse-sink"]}
            self.assertEqual(200, self.client.post(base + "/receiver/settings", json=old, headers=self.headers).status_code)
            self.assertEqual(200, self.client.get(base + "/settings").status_code)
            reset = self.client.post(base + "/receiver/reset", headers=self.headers)
            self.assertEqual(200, reset.status_code)
            self.assertFalse(reset.json["virtual_microphone"])
            self.assertEqual(["default"], reset.json["speaker_devices"])
            self.user["is_admin"] = False
            self.assertEqual(403, self.client.get(base + "/outputs").status_code)

    def test_missing_dependency_error_does_not_expose_exception(self):
        with patch("app.audio_streamer_admin.manager.configured_start", side_effect=RuntimeError("token=secret")):
            result = self.client.post("/admin/mini-services/audio/streamer/sender/start", json={}, headers=self.headers)
        self.assertEqual(503, result.status_code)
        self.assertNotIn("secret", result.get_data(as_text=True))

    def test_windows_scan_and_persistent_selection_are_admin_only(self):
        url = "/admin/mini-services/audio/streamer/inputs?backend=dshow"
        with patch("app.audio_streamer_admin.discover_microphone_inputs", return_value=[{"id": "USB Mic", "backend": "dshow"}]) as scan:
            self.assertEqual(200, self.client.get(url).status_code)
            scan.assert_called_once_with("dshow")
            self.user["is_admin"] = False
            self.assertEqual(403, self.client.get(url).status_code)
            self.assertEqual(1, scan.call_count)
        self.user["is_admin"] = True
        data = {"backend": "dshow", "source": "USB Mic"}
        url = "/admin/mini-services/audio/streamer/sender/settings"
        self.assertEqual(403, self.client.post(url, json=data).status_code)
        self.assertEqual(200, self.client.post(url, json=data, headers=self.headers).status_code)
        stored = self.client.get("/admin/mini-services/audio/streamer/settings").json["sender"]
        self.assertEqual("dshow", stored["backend"])
        self.assertEqual("USB Mic", stored["source"])


if __name__ == "__main__":
    unittest.main()
