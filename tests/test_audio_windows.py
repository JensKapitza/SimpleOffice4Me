"""Windows system boundaries are mocked; no microphone or Windows host required."""
import subprocess
import unittest
from unittest.mock import Mock, patch

from app.audio_output_discovery import discover_microphone_inputs
from app.audio_streamer import sender_command
from app.audio_streamer_config import validate_settings


class WindowsAudioTests(unittest.TestCase):
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
