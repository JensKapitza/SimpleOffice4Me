import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.remote_hub_store import RemoteHubStore, SESSION_STATES


class RemoteHubStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RemoteHubStore(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def _device(self):
        return self.store.upsert_device(
            label="Testgerät",
            platform="linux",
            mode="consent",
            tags=["test"],
            enabled=True,
        )

    def test_new_device_is_default_deny(self):
        device = self._device()
        self.assertEqual([], device["allowed_user_ids"])
        self.assertFalse(self.store.user_can_access(device["device_id"], 1))
        with self.assertRaises(PermissionError):
            self.store.create_session(
                device_id=device["device_id"],
                actor_id=1,
                actor_name="Test User",
                session_type="desktop",
            )

    def test_explicit_device_user_permission_allows_session_metadata(self):
        device = self._device()
        self.store.set_device_users(device["device_id"], [7])
        self.assertTrue(self.store.user_can_access(device["device_id"], 7))
        self.assertFalse(self.store.user_can_access(device["device_id"], 8))
        session = self.store.create_session(
            device_id=device["device_id"],
            actor_id=7,
            actor_name="Erlaubter User",
            session_type="support",
        )
        self.assertEqual("requested", session["state"])

    def test_heartbeat_online_state_expires(self):
        device = self._device()
        heartbeat = self.store.heartbeat(device["device_id"], agent_version="1.0", platform="linux")
        self.assertTrue(heartbeat["online"])

        stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(microsecond=0).isoformat()
        with patch.object(self.store, "_read", wraps=self.store._read) as wrapped:
            data = self.store._read()
            row = next(item for item in data["devices"] if item["device_id"] == device["device_id"])
            row["last_seen_at"] = stale
            self.store._save(data)
            wrapped.reset_mock()
            result = self.store.get_device(device["device_id"])
        self.assertIsNotNone(result)
        self.assertFalse(result["online"])

    def test_expected_session_states_are_available(self):
        expected = {
            "requested",
            "accepted",
            "connecting",
            "active",
            "disconnected",
            "suspended",
            "resuming",
            "ended",
            "failed",
            "rejected",
        }
        self.assertEqual(expected, SESSION_STATES)

    def test_session_state_history_can_progress_without_transport_data(self):
        device = self._device()
        self.store.set_device_users(device["device_id"], [9])
        session = self.store.create_session(
            device_id=device["device_id"],
            actor_id=9,
            actor_name="Review User",
            session_type="desktop",
        )
        for state in ("connecting", "active", "disconnected", "resuming", "active", "ended"):
            session = self.store.set_session_state(session["session_id"], state)
        self.assertEqual("ended", session["state"])
        self.assertTrue(session["ended_at"])


if __name__ == "__main__":
    unittest.main()
