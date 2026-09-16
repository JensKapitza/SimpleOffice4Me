"""Windows system boundaries are mocked; no microphone or Windows host required."""
import subprocess
import io
import os
import shutil
import unittest
from unittest.mock import Mock, patch

from app.audio_output_discovery import discover_microphone_inputs
from app.audio_streamer import sender_command, receiver_playback_command, ReceiverSession, LiveAudioManager
from app.audio_streamer_config import validate_settings, default_settings


class WindowsAudioTests(unittest.TestCase):
    def test_windows_defaults_do_not_change_linux_or_overwrite_saved_configuration(self):
        with patch("app.audio_streamer_config.platform.system", return_value="Windows"):
            value = validate_settings("receiver", {})
            self.assertEqual(["default"], value["speaker_devices"])
            self.assertFalse(value["virtual_microphone"])
            self.assertFalse(value["autostart"])
            # Existing unsupported settings stay editable instead of breaking the page.
            self.assertTrue(validate_settings("receiver", {"virtual_microphone": True})["virtual_microphone"])
        with patch("app.audio_streamer_config.platform.system", return_value="Linux"):
            self.assertTrue(default_settings("receiver")["virtual_microphone"])
            self.assertEqual([], default_settings("receiver")["speaker_devices"])

    def test_windows_player_rejects_named_devices_and_missing_binary(self):
        with patch("app.audio_streamer.platform.system", return_value="Windows"):
            with self.assertRaises(ValueError):
                receiver_playback_command("speakers-123")
            with patch("app.audio_streamer.shutil.which", return_value=None), self.assertRaises(RuntimeError):
                receiver_playback_command("default")

    @unittest.skipUnless(shutil.which("ffplay"), "existing FFplay required")
    def test_real_ffplay_accepts_pcm_with_dummy_audio_driver(self):
        with patch("app.audio_streamer.platform.system", return_value="Windows"):
            command = receiver_playback_command("default")
        result = subprocess.run(command, input=b"\0" * 38400, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10,
                                env={**os.environ, "SDL_AUDIODRIVER": "dummy", "SDL_VIDEODRIVER": "dummy"})
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))

    def test_windows_receiver_lifecycle_preflight_and_cleanup(self):
        manager = LiveAudioManager()
        decoder = Mock(stdout=io.BytesIO(b"pcm-data"), stdin=None)
        player = Mock(stdin=io.BytesIO(), stdout=None)
        decoder.poll.return_value = player.poll.return_value = None
        next_decoder = Mock(stdout=io.BytesIO(), stdin=None)
        next_player = Mock(stdin=io.BytesIO(), stdout=None)
        next_decoder.poll.return_value = next_player.poll.return_value = None
        with patch("app.audio_streamer.platform.system", return_value="Windows"), patch("app.audio_streamer.shutil.which", side_effect=lambda name: name), patch("app.audio_streamer.subprocess.Popen", side_effect=[decoder, player, next_decoder, next_player]) as popen, patch("app.audio_streamer.threading.Thread"), patch.object(manager, "_ensure_monitor"):
            try:
                args = dict(port=5004, bind="127.0.0.1", speaker_devices=["default"], virtual_microphone=False)
                manager.start_receiver(**args)
                manager.start_receiver(**args)
                self.assertEqual(2, popen.call_count)
                self.assertEqual("ffplay", popen.call_args_list[1].args[0][0])
                session = manager.receiver
                directory = session.sdp_path.parent
                with self.assertRaises(ValueError):
                    manager.start_receiver(**{**args, "virtual_microphone": True})
                decoder.terminate.assert_not_called()
                with self.assertRaises(ValueError):
                    manager.start_receiver(**{**args, "speaker_devices": ["wrong-device"]})
                self.assertIs(session, manager.receiver)
                player.poll.return_value = 1
                manager.recover()
                self.assertEqual("failed", manager.states["receiver"].state)
                manager.states["receiver"].retry_at = 0.01
                manager.recover()
                self.assertEqual(4, popen.call_count)
                self.assertEqual("ffplay", popen.call_args.args[0][0])
                self.assertTrue(manager._receiver_alive())
                next_directory = manager.receiver.sdp_path.parent
                manager.stop_receiver()
                manager.stop_receiver()
                manager.recover()
                self.assertEqual(4, popen.call_count)
                self.assertFalse(directory.exists())
                self.assertFalse(next_directory.exists())
                self.assertTrue(player.stdin.closed)
                self.assertTrue(decoder.stdout.closed)
                decoder.terminate.assert_called()
                next_player.terminate.assert_called()
                self.assertTrue(next_player.stdin.closed)
            finally:
                manager.stop_all()

    def test_windows_partial_start_failure_closes_decoder_and_temporary_files(self):
        decoder = Mock(stdout=io.BytesIO(), stdin=None)
        decoder.poll.return_value = None
        with patch("app.audio_streamer.platform.system", return_value="Windows"), patch("app.audio_streamer.shutil.which", side_effect=lambda name: name), patch("app.audio_streamer.subprocess.Popen", side_effect=[decoder, OSError("player unavailable")]):
            session = ReceiverSession(5004, ["default"], bind="127.0.0.1")
            directory = session.sdp_path.parent
            with self.assertRaises(OSError):
                session.start()
            self.assertTrue(decoder.stdout.closed)
            self.assertFalse(directory.exists())
            self.assertIsNone(session.decoder)

    def scan(self, output):
        with patch("app.audio_output_discovery.platform.system", return_value="Windows"), patch("app.audio_output_discovery.shutil.which", return_value="C:/Program Files/FFmpeg/ffmpeg.exe"), patch("app.audio_output_discovery.subprocess.run", return_value=Mock(stderr=output, returncode=1)) as run:
            devices = discover_microphone_inputs()
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(5, run.call_args.kwargs["timeout"])
        self.assertEqual("dummy", run.call_args.args[0][-1])
        return devices

    def test_windows_scan_excludes_video_and_preserves_stable_unicode_ids(self):
        devices = self.scan('[dshow @ 1] "Camera" (video)\n[dshow @ 1] Alternative name "camera-id"\n[dshow @ 1] "Mikrofon ÜSB" (audio)\n[dshow @ 1] Alternative name "@device_cm_mic-id"\n')
        self.assertEqual(["@device_cm_mic-id"], [d["id"] for d in devices])
        self.assertEqual("Mikrofon ÜSB", devices[0]["label"])
        self.assertEqual("dshow", devices[0]["backend"])

    def test_legacy_sections_empty_inventory_and_unsupported_build(self):
        devices = self.scan('[dshow @ 1] DirectShow video devices\n[dshow @ 1] "Camera"\n[dshow @ 1] DirectShow audio devices\n[dshow @ 1] "Mic"\n')
        self.assertEqual(["Mic"], [d["id"] for d in devices])
        self.assertEqual([], self.scan('[dshow @ 1] Could not enumerate audio only devices (or none found).'))
        with self.assertRaises(RuntimeError):
            self.scan("Unknown input format: dshow")

    def test_duplicate_and_unsafe_names_are_not_offered(self):
        self.assertEqual([], self.scan('[dshow @ 1] "Mic" (audio)\n[dshow @ 1] "Mic" (audio)\n[dshow @ 1] "Mic:video=Camera" (audio)\n'))
        rows = ''.join(f'[dshow @ 1] "Mic-{n}" (audio)\n' for n in range(70))
        self.assertEqual(64, len(self.scan(rows)))
        devices = self.scan('[dshow @ 1] "Mic" (audio)\n[dshow @ 1] Alternative name "stable-id"\n[dshow @ 1] "Mic" (audio)\n')
        self.assertEqual(["stable-id"], [d["id"] for d in devices])

    def test_missing_ffmpeg_and_timeout_are_explicit_failures(self):
        with patch("app.audio_output_discovery.shutil.which", return_value=None):
            with self.assertRaises(RuntimeError):
                discover_microphone_inputs("dshow")
        with patch("app.audio_output_discovery.shutil.which", return_value="ffmpeg"), patch("app.audio_output_discovery.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 5)):
            with self.assertRaises(RuntimeError):
                discover_microphone_inputs("dshow")

    def test_command_passes_one_audio_input_without_shell_quoting_or_truncation(self):
        source = "@device_cm_" + "x" * 300 + " ÜSB & mic"
        clean = validate_settings("sender", {"backend": "dshow", "source": source})
        with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"):
            command = sender_command(source=clean["source"], backend="dshow", destinations=[("192.168.1.2", 5004)])
        self.assertEqual("audio=" + source, command[command.index("-i") + 1])
        self.assertEqual(1, command.count("-i"))
        self.assertIn("dshow", command)

    def test_invalid_device_is_rejected_by_settings_and_command(self):
        for source in ("default", "Mic:video=Camera", "Mic=other", "Mic\x00", "x" * 1025):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    validate_settings("sender", {"backend": "dshow", "source": source})
                with patch("app.audio_streamer.shutil.which", return_value="ffmpeg"), self.assertRaises(ValueError):
                    sender_command(source=source, backend="dshow", destinations=[])
