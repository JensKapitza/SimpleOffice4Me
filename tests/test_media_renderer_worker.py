import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simpleoffice_media_renderer import save_media_renderer_settings
from simpleoffice_mini_control import ControlStore
from tools.mini_services import Worker


class MediaRendererWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "mini-services.json"

    def test_control_store_exposes_renderer_preferences_and_commands(self):
        store = ControlStore(self.path)
        self.assertEqual(
            {"enabled": True, "autostart": True},
            store.preferences()["media-renderer"],
        )
        store.save_preferences(
            "media-renderer", {"enabled": True, "autostart": False}
        )
        command = store.enqueue("media-renderer", "start")
        self.assertEqual("media-renderer", command["service"])
        self.assertFalse(
            ControlStore(self.path).preferences()["media-renderer"]["autostart"]
        )

    def test_worker_registers_renderer_but_default_config_does_not_start_it(self):
        worker = Worker(self.path)
        worker.control.save_preferences("sip", {"enabled": False, "autostart": False})
        self.assertIn("media-renderer", worker.states)
        with patch("tools.mini_services.MediaRendererService") as renderer:
            worker._load_network_services()
        renderer.assert_not_called()
        self.assertEqual("disabled", worker.states["media-renderer"].state)

    def test_worker_starts_renderer_only_when_domain_and_control_enable_it(self):
        save_media_renderer_settings(
            {
                "enabled": True,
                "bind": "127.0.0.1",
                "port": 18200,
                "friendly_name": "Test Renderer",
            },
            self.path,
        )
        worker = Worker(self.path)
        worker.control.save_preferences("sip", {"enabled": False, "autostart": False})
        worker.preferences = worker.control.preferences()
        fake = Mock()
        fake.stop_event.is_set.return_value = False
        fake.socket.fileno.return_value = 4
        fake.thread.is_alive.return_value = True
        with patch(
            "tools.mini_services.MediaRendererService", return_value=fake
        ) as renderer:
            worker._load_network_services()
        renderer.assert_called_once()
        fake.start.assert_called_once()
        self.assertEqual("running", worker.states["media-renderer"].state)

    def test_hub_keeps_dlna_separate_from_live_rtp(self):
        root = Path(__file__).resolve().parents[1]
        hub = (root / "templates" / "admin" / "mini_services_hub.html").read_text(
            encoding="utf-8"
        )
        page = (root / "templates" / "admin" / "media_renderer.html").read_text(
            encoding="utf-8"
        )
        admin = (root / "app" / "mini_services_admin.py").read_text(encoding="utf-8")
        self.assertIn("'media-renderer': url_for('media_renderer_admin.index')", hub)
        self.assertIn("Media / DLNA", hub)
        self.assertIn("getrennt vom Live-RTP-Streamer", hub)
        self.assertIn("_media_renderer_admin_bp", admin)
        self.assertIn('id="media-bind-help"', page)
        self.assertIn('aria-describedby="media-bind-help"', page)
        self.assertIn("kein Interface automatisch geraten", page)


if __name__ == "__main__":
    unittest.main()
