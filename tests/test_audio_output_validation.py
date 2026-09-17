"""Reject invalid output updates before changing persistent configuration."""
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, g

from app.audio_output_admin import bp
from app.audio_output_store import AudioOutputStore
from app.security_controls import protect_browser_mutation


class AudioOutputValidationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = AudioOutputStore(directory.name)
        self.original = self.store.register_output("local", "speaker", "Lautsprecher", volume=35)

    def test_invalid_updates_preserve_saved_output(self):
        for field, values in {
            "channels": [0, 17, True, 2.5, "2", None],
            "volume": [-1, 101, False, 30.5, "30", None],
            "online": ["false", "true", 0, 1, None],
            "device": [None, {}, "x" * 501, "sink\x00other", "sink\nother", "sink\x7f"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.store.register_output("local", "speaker", "Changed", **{field: value})
                self.assertEqual(self.original, self.store.output("local", "speaker"))

    def test_valid_boundaries_and_offline_persist(self):
        for channels, volume, online in ((1, 0, False), (16, 100, True)):
            result = self.store.register_output("local", "speaker", "USB", channels=channels,
                                                volume=volume, online=online, device="USB Audio: 1")
            self.assertEqual((channels, volume, online), (result["channels"], result["volume"], result["online"]))
            self.assertEqual(result, AudioOutputStore(self.store.root).output("local", "speaker"))

    def test_invalid_priority_does_not_enqueue(self):
        for priority in (-1, 101, True, 2.5, "50", float("inf")):
            for kind in ("sound", "tts"):
                with self.subTest(priority=priority, kind=kind), self.assertRaises(ValueError):
                    if kind == "sound":
                        self.store.queue_sound("gong", ["speaker"], priority=priority)
                    else:
                        self.store.queue_tts("Test", ["speaker"], priority=priority)
        self.assertEqual([], self.store.pending())

    def test_admin_api_validates_and_enforces_csrf_and_role(self):
        app = Flask(__name__)
        app.config.update(TESTING=True, TEST_CSRF_PROTECTION=True, SECRET_KEY="test")
        app.register_blueprint(bp)
        user = {"is_admin": True, "is_disabled": False}
        app.before_request(lambda: setattr(g, "user", user))
        app.before_request(protect_browser_mutation)
        client = app.test_client()
        with client.session_transaction() as session:
            session["_csrf_token"] = "x" * 40
        headers = {"X-CSRF-Token": "x" * 40}
        url = "/admin/mini-services/audio/outputs"
        payload = {"node_id": "local", "output_id": "speaker", "online": "false"}
        with patch("app.audio_output_admin._store", return_value=self.store), patch("app.audio_output_admin.audit"):
            self.assertEqual(403, client.post(url, json=payload).status_code)
            result = client.post(url, json=payload, headers=headers)
            self.assertEqual(400, result.status_code)
            self.assertIn("Online", result.json["error"])
            self.assertEqual(self.original, self.store.output("local", "speaker"))
            payload["online"] = False
            self.assertEqual(201, client.post(url, json=payload, headers=headers).status_code)
            self.assertFalse(self.store.output("local", "speaker")["online"])
            for path, data in (("sound", {"preset": "gong"}), ("say", {"text": "Test"})):
                result = client.post("/admin/mini-services/audio/" + path,
                                     json={**data, "targets": ["speaker"], "priority": "50"}, headers=headers)
                self.assertEqual(400, result.status_code)
            user["is_admin"] = False
            self.assertEqual(403, client.post(url, json=payload, headers=headers).status_code)
