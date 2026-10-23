"""Regression coverage for camera permission scope (issue #736)."""
import shutil
import subprocess
import unittest
from pathlib import Path

from flask import Blueprint, Flask

from app import add_header


def test_camera_permissions_policy_is_scoped_to_federation_dashboard():
    app = Flask(__name__)
    federation = Blueprint("federation_peer_admin", __name__)
    federation.add_url_rule("/dashboard", "dashboard", lambda: "dashboard")
    federation.add_url_rule("/other", "other", lambda: "other")
    app.register_blueprint(federation)
    app.after_request(add_header)
    client = app.test_client()

    dashboard = client.get("/dashboard")
    other_admin = client.get("/other")

    assert dashboard.headers["Permissions-Policy"].startswith("camera=(self),")
    assert other_admin.headers["Permissions-Policy"].startswith("camera=(),")


def test_camera_policy_limits_federation_to_scanner_endpoint():
    source = (Path(__file__).resolve().parents[1] / "app" / "__init__.py").read_text()
    assert 'request.blueprint == "federation_peer_admin"' in source
    assert 'request.endpoint == "federation_peer_admin.dashboard"' in source
    assert 'camera=()' in source
    federation = (Path(__file__).resolve().parents[1] / "app" / "federation_peer_admin.py").read_text()
    assert '@bp.get("")' in federation
    assert "def dashboard():" in federation


def test_qr_fallback_is_loaded_on_inventory_and_library():
    root = Path(__file__).resolve().parents[1]
    for template in (
        "templates/inventory/index.html",
        "templates/library/index.html",
        "templates/admin/federation_peer_discovery.html",
    ):
        source = (root / template).read_text()
        assert "vendor/jsqr/jsQR.js" in source
        assert "js/camera_capture.js" in source
    assert "window.jsQR" in (root / "static/js/inventory.js").read_text()
    assert "window.jsQR" in (root / "templates/library/index.html").read_text()

def test_local_qr_fallback_runs_even_when_native_detector_exists():
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "static/js/inventory.js").read_text()
    library = (root / "templates/library/index.html").read_text()
    assert "if (!codes.length && typeof window.jsQR === 'function')" in inventory
    assert "const canDecodeQr = !isLabelScanner && typeof window.jsQR === 'function'" in library
    assert "if (!found.length && canDecodeQr)" in library
    assert "1200 / Math.max(width, height)" in inventory
    assert "1200 / Math.max(width, height)" in library


def test_camera_flows_use_the_shared_user_selected_stream_helper():
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "static/js/inventory.js").read_text()
    library = (root / "templates/library/index.html").read_text()
    federation = (root / "static/js/federation_qr_scan.js").read_text()
    for source in (inventory, library, federation):
        assert "SimpleOfficeCamera.openCameraStream" in source
    assert "() => generation === cameraGeneration" in federation
    assert "cameraVideo.srcObject = stream" in library
    assert "video.srcObject = stream" in inventory


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
