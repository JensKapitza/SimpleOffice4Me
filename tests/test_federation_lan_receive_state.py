import tempfile
import unittest
from pathlib import Path

from app.federation_lan_receive_state import LanReceiveState, MAX_RECEIVE_WINDOW_SECONDS


class LanReceiveStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = LanReceiveState(self.root)

    def test_receive_window_starts_and_expires(self):
        started = self.store.start(now=1000, duration_seconds=120)
        self.assertTrue(started["active"])
        self.assertEqual(1120, started["expires_at"])
        self.assertTrue(self.store.status(now=1119)["active"])
        self.assertFalse(self.store.status(now=1120)["active"])

    def test_receive_window_is_bounded(self):
        started = self.store.start(now=1000, duration_seconds=99999)
        self.assertEqual(1000 + MAX_RECEIVE_WINDOW_SECONDS, started["expires_at"])

    def test_receive_window_can_be_stopped(self):
        self.store.start(now=1000)
        stopped = self.store.stop()
        self.assertFalse(stopped["active"])
        self.assertFalse(self.store.status(now=1001)["active"])


if __name__ == "__main__":
    unittest.main()
