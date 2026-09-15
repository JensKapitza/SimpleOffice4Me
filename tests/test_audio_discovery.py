import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.audio_output_discovery import discover_microphone_inputs, discover_speaker_outputs
from app.audio_output_store import AudioOutputStore


class AudioDiscoveryTests(unittest.TestCase):
    def test_alsa_capture_fallback_uses_card_id_and_excludes_playback_only_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pcm").write_text("00-00: USB Mic : USB Audio : capture 1\n01-00: Speakers : playback 1\n02-00: Removed : capture 1\n")
            (root / "card0").mkdir()
            (root / "card0/id").write_text("USBMic\n")
            with patch("app.audio_output_discovery._ALSA_ROOT", root), patch("app.audio_output_discovery.shutil.which", return_value=None):
                devices = discover_microphone_inputs()
            self.assertEqual(1, len(devices))
            self.assertEqual("plughw:CARD=USBMic,DEV=0", devices[0]["id"])
            self.assertEqual("alsa", devices[0]["backend"])
            self.assertFalse(devices[0]["default"])

    def test_explicit_alsa_scan_never_invokes_pactl_and_card_reordering_keeps_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pcm").write_text("05-02: USB Mic : capture 1\n")
            (root / "card5").mkdir()
            (root / "card5/id").write_text("USBMic")
            with patch("app.audio_output_discovery._ALSA_ROOT", root), patch("app.audio_output_discovery._run_pactl") as pactl:
                devices = discover_microphone_inputs("alsa")
            pactl.assert_not_called()
            self.assertEqual("plughw:CARD=USBMic,DEV=2", devices[0]["id"])
            with self.assertRaises(ValueError):
                discover_microphone_inputs("unknown")

    def test_alsa_missing_inventory_returns_empty_and_invalid_card_id_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("app.audio_output_discovery._ALSA_ROOT", root):
                self.assertEqual([], discover_microphone_inputs("alsa"))
                (root / "pcm").write_text("00-00: USB Mic : capture 1\n")
                (root / "card0").mkdir()
                (root / "card0/id").write_text("bad/../../card")
                self.assertEqual([], discover_microphone_inputs("alsa"))

    def test_inputs_exclude_monitors_and_prefer_default(self):
        rows = "1\tmic-b\talsa\ts16le 1ch 48000Hz\tSUSPENDED\n2\tmic-a\talsa\ts16le 1ch 48000Hz\tRUNNING\n3\tspeaker.monitor\talsa\ts16le 1ch 48000Hz\tIDLE\n"
        with patch("app.audio_output_discovery.shutil.which", return_value="/usr/bin/pactl"), patch("app.audio_output_discovery.subprocess.run", side_effect=[Mock(stdout=rows), Mock(stdout="mic-a\n")]) as run:
            devices = discover_microphone_inputs()
        self.assertEqual(["mic-a", "mic-b"], [device["id"] for device in devices])
        self.assertEqual(["/usr/bin/pactl", "list", "short", "sources"], run.call_args_list[0].args[0])
        self.assertEqual(5, run.call_args.kwargs["timeout"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_missing_audio_server_is_actionable(self):
        with patch("app.audio_output_discovery.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "pactl"):
                discover_speaker_outputs()
        with patch("app.audio_output_discovery.shutil.which", return_value="pactl"), patch("app.audio_output_discovery.subprocess.run", side_effect=subprocess.TimeoutExpired("pactl", 5)):
            with self.assertRaises(RuntimeError):
                discover_speaker_outputs()

    def test_empty_devices_duplicates_and_limit(self):
        rows = "1\tsink-a\tdriver\tformat\tIDLE\n" * 4
        rows += "".join(f"{i}\tsink-{i}\tdriver\tformat\tIDLE\n" for i in range(100))
        with patch("app.audio_output_discovery.shutil.which", return_value="pactl"), patch("app.audio_output_discovery._run_pactl", side_effect=[rows, ""]):
            self.assertEqual(64, len(discover_speaker_outputs()))
        with patch("app.audio_output_discovery.shutil.which", return_value="pactl"), patch("app.audio_output_discovery._run_pactl", return_value=""):
            self.assertEqual([], discover_speaker_outputs())

    def test_rescan_preserves_preferences_remote_outputs_and_removed_device(self):
        with tempfile.TemporaryDirectory() as temp:
            store = AudioOutputStore(temp)
            store.register_output("remote", "speaker", "Remote")
            first = store.sync_local_outputs([{"id": "sink:long/id"}])[0]
            store.register_output("local", first["output_id"], "Küche", device=first["device"], volume=35)
            second = store.sync_local_outputs([{"id": "sink:long/id"}])[0]
            self.assertEqual(first["output_id"], second["output_id"])
            self.assertEqual("Küche", second["name"])
            self.assertEqual(35, second["volume"])
            self.assertFalse(store.sync_local_outputs([])[0]["online"])
            self.assertTrue(store.output("remote", "speaker")["online"])


if __name__ == "__main__":
    unittest.main()
