from __future__ import annotations

import tempfile
import unittest
import wave
import threading
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.audio_calendar import announcement_text, queue_due_calendar_events
from app.audio_output_engine import render_preset, attenuated_audio, playback_command
from app.audio_output_store import AudioOutputStore


class AudioOutputTests(unittest.TestCase):
    def test_ffplay_never_silently_ignores_selected_output(self):
        with patch("app.audio_output_engine.shutil.which", side_effect=lambda name: "/bin/ffplay" if name == "ffplay" else None):
            self.assertIn("ffplay", playback_command("audio.wav", "default"))
            with self.assertRaises(RuntimeError):
                playback_command("audio.wav", "specific-speaker")

    def test_pcm_gain_preserves_format_source_and_cleans_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.wav"
            for width in (1, 2, 3, 4):
                with self.subTest(width=width):
                    limit = 2 ** (width * 8 - 1)
                    samples = [-limit, -3, 0, 3, limit - 1, 0]
                    encoded = b"".join((value + (128 if width == 1 else 0)).to_bytes(width, "little", signed=width > 1) for value in samples)
                    with wave.open(str(source), "wb") as out:
                        out.setparams((2, width, 48000, 0, "NONE", "not compressed"))
                        out.writeframes(encoded)
                    original = source.read_bytes()
                    for volume in (0, 50, 100):
                        with attenuated_audio(source, volume) as adjusted:
                            with wave.open(str(adjusted), "rb") as result:
                                self.assertEqual((2, width, 48000), (result.getnchannels(), result.getsampwidth(), result.getframerate()))
                                data = result.readframes(10)
                            decoded = [int.from_bytes(data[i:i+width], "little", signed=width > 1) - (128 if width == 1 else 0) for i in range(0, len(data), width)]
                            expected = [int(value * volume / 100) for value in samples]
                            self.assertEqual(expected, decoded)
                        self.assertEqual(original, source.read_bytes())
                        if volume != 100:
                            self.assertFalse(adjusted.exists())
                    self.assertEqual([source], list(Path(directory).iterdir()))

    def test_gain_cancellation_and_invalid_volume_leave_no_files(self):
        with tempfile.TemporaryDirectory() as directory:
            source = render_preset("gong", directory)
            event = threading.Event(); event.set()
            with self.assertRaises(RuntimeError):
                with attenuated_audio(source, 50, cancel_event=event):
                    self.fail("Cancelled transformation yielded audio")
            for value in (-1, 101, True, "50"):
                with self.assertRaises(ValueError):
                    with attenuated_audio(source, value):
                        self.fail("Invalid volume accepted")
            self.assertEqual([source], list(Path(directory).iterdir()))

    def test_outputs_groups_and_priority_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AudioOutputStore(directory)
            store.register_output("client-1", "a", "Wohnzimmer", device="sink-a")
            store.register_output("client-1", "b", "Kueche", device="sink-b")
            store.set_group("all", "Alle", ["a", "b", "a"])
            low = store.queue_tts("Essen ist fertig", ["all"], priority=50)
            high = store.queue_sound("alarm.fire", ["all"])
            self.assertEqual(len(store.outputs()), 2)
            self.assertEqual(store.groups()[0]["members"], ["a", "b"])
            self.assertEqual(store.pending()[0]["id"], high["id"])
            self.assertEqual(store.pending()[1]["id"], low["id"])

    def test_calendar_event_queues_tts_at_trigger_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AudioOutputStore(directory)
            now = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
            event = {
                "event_id": "meeting-1",
                "title": "Teamrunde",
                "start": (now + timedelta(minutes=5)).isoformat(),
                "audio_announcement": {"enabled": True, "targets": ["all"], "minutes_before": 5},
            }
            queued = queue_due_calendar_events(store, [event], now=now)
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]["source"], "calendar")
            self.assertIn("Teamrunde", queued[0]["payload"]["text"])

    def test_calendar_default_text(self) -> None:
        self.assertEqual(announcement_text({"title": "Arzt"}, 1), "Der Termin Arzt beginnt in einer Minute.")

    def test_generated_preset_is_valid_wav(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = render_preset("gong", directory)
            self.assertTrue(Path(path).exists())
            self.assertGreater(Path(path).stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
