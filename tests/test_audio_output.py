from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.audio_calendar import announcement_text, queue_due_calendar_events
from app.audio_output_engine import render_preset
from app.audio_output_store import AudioOutputStore


class AudioOutputTests(unittest.TestCase):
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
