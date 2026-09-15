from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simpleoffice_network_gateway_runtime import (
    gateway_ownership_path,
    load_gateway_ownership,
    remember_gateway_ownership,
)
from tools.mini_services import Worker


_GATEWAY = {
    "version": 1,
    "enabled": True,
    "mode": "nat",
    "auto_detect": False,
    "internal_interface": "lan0",
    "external_interface": "wan0",
    "internal_network": "192.168.178.0/24",
    "forward_ipv4": True,
    "allow_established": True,
    "allow_lan_to_wan": True,
    "allow_wan_to_lan": False,
    "nat_name": "SimpleOfficeMiniNat",
}


class GatewayOwnershipRecoveryTests(unittest.TestCase):
    def test_marker_roundtrip_uses_validated_gateway_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "mini-services.json"
            saved = remember_gateway_ownership(_GATEWAY, config)
            loaded = load_gateway_ownership(config)

            self.assertEqual(saved, loaded)
            self.assertTrue(gateway_ownership_path(config).is_file())

    def test_fresh_worker_cleans_marker_owned_gateway_without_in_memory_state(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "mini-services.json"
            remember_gateway_ownership(_GATEWAY, config)
            worker = Worker(config)

            with patch("tools.mini_services.disable_gateway", return_value={"ok": True, "mode": "off"}) as disable:
                self.assertTrue(worker._stop_one("gateway"))

            disable.assert_called_once()
            self.assertFalse(gateway_ownership_path(config).exists())
            self.assertFalse(worker.gateway_active)

    def test_failed_cleanup_keeps_marker_for_next_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "mini-services.json"
            remember_gateway_ownership(_GATEWAY, config)
            worker = Worker(config)

            with patch("tools.mini_services.disable_gateway", side_effect=PermissionError("denied")):
                self.assertFalse(worker._stop_one("gateway"))

            self.assertTrue(gateway_ownership_path(config).is_file())
            self.assertEqual("failed", worker.states["gateway"].state)


if __name__ == "__main__":
    unittest.main()
