"""Regression coverage for camera permission scope (issue #736)."""
import shutil
import subprocess
import unittest
from pathlib import Path

from flask import Blueprint, Flask

from app import add_header

ROOT = Path(__file__).resolve().parents[1]


class CameraPolicyHeaderTests(unittest.TestCase):
    def test_camera_permissions_policy_is_scoped_to_federation_dashboard(self):
        app = Flask(__name__)
        federation = Blueprint("federation_peer_admin", __name__)
        federation.add_url_rule("/dashboard", "dashboard", lambda: "dashboard")
        federation.add_url_rule("/other", "other", lambda: "other")
        app.register_blueprint(federation)
        app.after_request(add_header)
        client = app.test_client()

        dashboard = client.get("/dashboard")
        other_admin = client.get("/other")

        self.assertTrue(
            dashboard.headers["Permissions-Policy"].startswith("camera=(self),")
        )
        self.assertTrue(
            other_admin.headers["Permissions-Policy"].startswith("camera=(),")
        )

    def test_camera_permissions_policy_is_scoped_to_shopping_scanner_page(self):
        app = Flask(__name__)
        shopping = Blueprint("shopping", __name__)
        shopping.add_url_rule("/", "index", lambda: "index")
        shopping.add_url_rule("/lists", "create_list", lambda: "lists")
        app.register_blueprint(shopping)
        app.after_request(add_header)
        client = app.test_client()

        index = client.get("/")
        other = client.get("/lists")

        self.assertTrue(
            index.headers["Permissions-Policy"].startswith("camera=(self),")
        )
        self.assertTrue(
            other.headers["Permissions-Policy"].startswith("camera=(),")
        )

    def test_camera_policy_limits_federation_to_scanner_endpoint(self):
        source = (ROOT / "app" / "__init__.py").read_text()
        self.assertIn('request.blueprint == "federation_peer_admin"', source)
        self.assertIn('request.endpoint == "federation_peer_admin.dashboard"', source)
        self.assertIn('request.blueprint == "shopping"', source)
        self.assertIn('request.endpoint == "shopping.index"', source)
        self.assertIn("camera=()", source)
        federation = (ROOT / "app" / "federation_peer_admin.py").read_text()
        self.assertIn('@bp.get("")', federation)
        self.assertIn("def dashboard():", federation)


class CameraFallbackStaticTests(unittest.TestCase):
    def test_qr_fallback_is_loaded_on_inventory_and_library(self):
        for template in (
            "templates/inventory/index.html",
            "templates/library/index.html",
            "templates/admin/federation_peer_discovery.html",
        ):
            source = (ROOT / template).read_text()
            self.assertIn("vendor/jsqr/jsQR.js", source)
            self.assertIn("js/camera_capture.js", source)
        self.assertIn("window.jsQR", (ROOT / "static/js/inventory.js").read_text())
        self.assertIn("window.jsQR", (ROOT / "templates/library/index.html").read_text())

    def test_local_qr_fallback_runs_even_when_native_detector_exists(self):
        inventory = (ROOT / "static/js/inventory.js").read_text()
        library = (ROOT / "templates/library/index.html").read_text()
        self.assertIn("if (!codes.length && typeof window.jsQR === 'function')", inventory)
        self.assertIn(
            "const canDecodeQr = !isLabelScanner && typeof window.jsQR === 'function'",
            library,
        )
        self.assertIn("if (!found.length && canDecodeQr)", library)
        self.assertIn("1200 / Math.max(width, height)", inventory)
        self.assertIn("1200 / Math.max(width, height)", library)

    def test_camera_flows_use_the_shared_user_selected_stream_helper(self):
        inventory = (ROOT / "static/js/inventory.js").read_text()
        library = (ROOT / "templates/library/index.html").read_text()
        federation = (ROOT / "static/js/federation_qr_scan.js").read_text()
        shopping = (ROOT / "static/js/shopping.js").read_text()
        for source in (inventory, library, federation, shopping):
            self.assertIn("SimpleOfficeCamera.openCameraStream", source)
        self.assertIn("() => generation === cameraGeneration", federation)
        self.assertIn("cameraVideo.srcObject = stream", library)
        self.assertIn("video.srcObject = stream", inventory)
        self.assertIn(
            "js/camera_capture.js",
            (ROOT / "templates/shopping/index.html").read_text(),
        )

    def test_shopping_scanner_guards_restarts_and_denial(self):
        shopping = (ROOT / "static/js/shopping.js").read_text()
        self.assertIn("let cameraGeneration = 0", shopping)
        self.assertIn("cameraGeneration += 1", shopping)
        self.assertIn("generation !== cameraGeneration", shopping)
        self.assertIn(
            "requestedStream?.getTracks().forEach((track) => track.stop())", shopping
        )
        self.assertIn("error?.name === 'NotAllowedError'", shopping)
        self.assertIn("Kameraberechtigung im Browser freigeben", shopping)
        self.assertIn("HTTPS kann erforderlich sein", shopping)
        self.assertIn("window.addEventListener('pagehide', stopCamera)", shopping)


class CameraCaptureHelperTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_camera_capture_permission_and_facing_mode_fallbacks(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "--test", str(root / "tests" / "camera_capture.test.cjs")],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_federation_scanner_stops_pending_camera_requests(self):
        source = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "federation_qr_scan.js"
        ).read_text()
        self.assertIn("let cameraGeneration = 0", source)
        self.assertIn("if (generation !== cameraGeneration)", source)
        self.assertIn("requestedStream.getTracks().forEach(track => track.stop())", source)
