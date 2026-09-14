import unittest
from pathlib import Path
from unittest import mock

from app import app
from app import screen_share


ROOT = Path(__file__).resolve().parents[1]


class ScreenShareTests(unittest.TestCase):
    def test_blueprint_and_navigation_are_registered(self):
        self.assertIn("screen", app.blueprints)
        self.assertIn('url_for(\'screen.index\')', (ROOT / "templates/documents/nav.html").read_text(encoding="utf-8"))

    def test_screen_capture_policy_is_scoped_to_screen_blueprint(self):
        response = app.test_client().get("/screen")
        self.assertIn("display-capture=(self)", response.headers["Permissions-Policy"])
        response = app.test_client().get("/")
        self.assertIn("display-capture=()", response.headers["Permissions-Policy"])

    def test_linux_launchers_use_argument_arrays_without_shell_or_root(self):
        with mock.patch.object(screen_share, "_system", return_value="linux"), \
                mock.patch.object(screen_share.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), \
                mock.patch.object(screen_share.subprocess, "Popen") as popen:
            screen_share._perform_action("linux-send")
            self.assertEqual(["/usr/bin/gnome-network-displays"], popen.call_args.args[0])
            self.assertNotIn("shell", popen.call_args.kwargs)
            screen_share._perform_action("linux-receive")
            command = popen.call_args.args[0]
            self.assertIn("/usr/bin/miracle-sinkctl", command)
            self.assertNotIn("sudo", command)

    def test_frontend_has_native_and_browser_capture_paths(self):
        source = (ROOT / "static/js/screen_share.js").read_text(encoding="utf-8")
        self.assertIn("SimpleOfficeNativeScreen?.startShare", source)
        self.assertIn("navigator.mediaDevices?.getDisplayMedia", source)
        self.assertIn("pendingCandidates", source)

    def test_electron_ipc_handlers_are_present_and_validated(self):
        source = (ROOT / "desktop/electron/main.js").read_text(encoding="utf-8")
        self.assertIn("ipcMain.handle('screen:status'", source)
        self.assertIn("ipcMain.handle('screen:action'", source)
        self.assertIn("trustedIpcSender", source)
        self.assertIn("shell: false", source)


if __name__ == "__main__":
    unittest.main()
