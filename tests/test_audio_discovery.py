import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.audio_output_discovery import discover_microphone_inputs, discover_speaker_outputs
from app.audio_output_store import AudioOutputStore


class AudioDiscoveryTests(unittest.TestCase):
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
