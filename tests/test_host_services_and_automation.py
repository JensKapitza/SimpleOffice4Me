from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import host_automation, host_services


class HostServicesTests(unittest.TestCase):
    def test_vm_autostart_rejects_shell_metacharacters(self):
        with self.assertRaises(ValueError):
            host_services.set_vm_autostart("demo;shutdown", True)

    def test_vm_autostart_uses_virsh_without_shell(self):
        with patch.object(host_services, "_run", return_value={"ok": True, "stderr": "", "stdout": "", "returncode": 0}) as run:
            result = host_services.set_vm_autostart("demo-vm", True)
        self.assertEqual(result, {"name": "demo-vm", "autostart": True})
        run.assert_called_once_with(["virsh", "autostart", "demo-vm"])

    def test_registry_writer_uses_managed_dropin_only(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"XDG_CONFIG_HOME": temp}, clear=False):
            value = host_services.write_registry_config([
                {"location": "registry.example.test:5000", "insecure": False, "blocked": False},
                {"location": "blocked.example.test", "blocked": True},
            ])
            path = Path(value["path"])
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn('location = "registry.example.test:5000"', text)
            self.assertIn("blocked = true", text)
            self.assertIn("90-simpleoffice.conf", str(path))

    def test_registry_writer_rejects_injection(self):
        with self.assertRaises(ValueError):
            host_services.write_registry_config([{"location": 'evil.test"\nblocked = false'}])


class HostAutomationTests(unittest.TestCase):
    def test_idle_power_action_defaults_to_two_safety_guards(self):
        rule = host_automation.validate_rule({
            "id": "0123456789abcdef",
            "name": "Standby nachts",
            "trigger": "idle",
            "action": "suspend",
            "idle_seconds": 1800,
        })
        self.assertTrue(rule["require_no_active_users"])
        self.assertTrue(rule["require_network_idle"])

    def test_interval_is_bounded(self):
        with self.assertRaises(ValueError):
            host_automation.validate_rule({
                "id": "0123456789abcdef",
                "name": "Zu schnell",
                "trigger": "interval",
                "action": "script",
                "interval_seconds": 1,
            })

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            store = host_automation.HostAutomationStore(temp)
            saved = store.save({
                "id": "0123456789abcdef",
                "name": "USB Backup",
                "trigger": "usb",
                "action": "script",
                "target": "backup.py",
                "usb_vendor": "abcd",
                "usb_product": "1234",
            })
            self.assertEqual(saved["name"], "USB Backup")
            self.assertEqual(len(store.all()), 1)
            store.delete("0123456789abcdef")
            self.assertEqual(store.all(), [])


if __name__ == "__main__":
    unittest.main()
