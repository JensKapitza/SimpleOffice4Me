"""Regression coverage for camera permission scope (issue #736)."""
from pathlib import Path


def test_camera_policy_limits_federation_to_scanner_endpoint():
    source = (Path(__file__).resolve().parents[1] / "app" / "__init__.py").read_text()
    assert 'request.blueprint == "federation_peer_admin"' in source
    assert 'request.endpoint == "federation_peer_admin.index"' in source
    assert 'camera=()' in source


def test_qr_fallback_is_loaded_on_inventory_and_library():
    root = Path(__file__).resolve().parents[1]
    for template in ("templates/inventory/index.html", "templates/library/index.html"):
        source = (root / template).read_text()
        assert "vendor/jsqr/jsQR.js" in source
    assert "window.jsQR" in (root / "static/js/inventory.js").read_text()
    assert "window.jsQR" in (root / "templates/library/index.html").read_text()

def test_local_qr_fallback_runs_even_when_native_detector_exists():
    root = Path(__file__).resolve().parents[1]
    inventory = (root / "static/js/inventory.js").read_text()
    library = (root / "templates/library/index.html").read_text()
    assert "if (!codes.length && typeof window.jsQR === 'function')" in inventory
    assert "if (!found.length && typeof window.jsQR === 'function')" in library
    assert "1200 / Math.max(width, height)" in inventory
    assert "1200 / Math.max(width, height)" in library
