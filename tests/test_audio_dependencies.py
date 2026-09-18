"""Program checks must not pretend to verify audio hardware."""
import unittest
from unittest.mock import Mock, patch

from app.audio_dependencies import audio_dependencies
from app.audio_streamer import LiveAudioManager


class AudioDependencyTests(unittest.TestCase):
    def inventory(self, service, settings, available=(), system="Linux"):
        with patch("app.audio_dependencies.platform.system", return_value=system), patch("app.audio_dependencies.shutil.which", side_effect=lambda name: name if name in available else None):
            return audio_dependencies(service, settings)

    def test_virtual_microphone_requires_both_pulse_tools_without_speakers(self):
        rows = self.inventory("receiver", {"virtual_microphone": True, "speaker_devices": []})
        self.assertEqual({"ffmpeg", "paplay", "pactl"}, {r["programs"][0] for r in rows if r["required"]})
        self.assertTrue(all(not r["available"] for r in rows))

    def test_windows_receiver_needs_no_pulse_tools(self):
        rows = self.inventory("receiver", {"speaker_devices": ["default"]}, ("ffmpeg", "ffplay"), "Windows")
        self.assertEqual(["ffmpeg", "ffplay"], [r["programs"][0] for r in rows])
        self.assertTrue(all(r["available"] and r["scope"] == "executable-only" for r in rows))

    def test_announcement_tools_are_conditional_and_players_are_alternatives(self):
        rows = self.inventory("output", {}, ("aplay",))
        self.assertFalse(any(r["required"] for r in rows))
        self.assertEqual(1, sum(r["available"] for r in rows))
        self.assertIn("aplay", next(r for r in rows if r["available"])["programs"])

    def test_sender_discovery_tool_is_optional(self):
        rows = self.inventory("sender", {"backend": "pulse"}, ("ffmpeg",))
        self.assertTrue(rows[0]["available"])
        self.assertFalse(rows[1]["required"])
        self.assertEqual(1, len(self.inventory("sender", {"backend": "dshow"})))

    def test_missing_paplay_does_not_stop_existing_receiver_or_spawn(self):
        manager = LiveAudioManager()
        old = Mock()
        manager.receiver = old
        with patch("app.audio_streamer.platform.system", return_value="Linux"), patch("app.audio_streamer.shutil.which", side_effect=lambda name: None if name == "paplay" else name), patch("app.audio_streamer.subprocess.Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "paplay"):
                manager.start_receiver(port=5004, bind="127.0.0.1", speaker_devices=[], virtual_microphone=True, _restart=True)
        self.assertIs(old, manager.receiver)
        old.stop.assert_not_called()
        popen.assert_not_called()
