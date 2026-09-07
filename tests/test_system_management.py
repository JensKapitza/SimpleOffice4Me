import tempfile
import unittest
from pathlib import Path

from app.system_management import (
    StorageRoleStore,
    health_checks,
    storage_plan,
    validate_device_path,
)


class SystemManagementTests(unittest.TestCase):
    def test_beginner_raid1_plan_layers_raid_before_lvm(self):
        plan = storage_plan(
            "raid1-lvm",
            ["/dev/sdb", "/dev/sdc"],
            name="daten",
            encrypted=True,
        )
        self.assertEqual(["RAID1", "LUKS2", "LVM", "ext4"], plan["layers"])
        self.assertFalse(plan["executable"])
        self.assertEqual("mdadm", plan["commands"][0][0])
        self.assertIn("cryptsetup", [command[0] for command in plan["commands"]])
        self.assertIn("pvcreate", [command[0] for command in plan["commands"]])

    def test_linear_lvm_is_high_risk_and_not_executable(self):
        plan = storage_plan("lvm-linear", ["/dev/sdb", "/dev/sdc"], name="pool")
        self.assertEqual("high", plan["risk"])
        self.assertFalse(plan["executable"])
        self.assertTrue(any("gesamte" in warning for warning in plan["warnings"]))

    def test_asymmetric_aggregate_mirror_stays_expert_only(self):
        plan = storage_plan(
            "aggregate-mirror",
            ["/dev/sdb", "/dev/sdc", "/dev/sdd"],
            name="backup",
        )
        self.assertEqual("expert", plan["risk"])
        self.assertEqual([], plan["commands"])
        self.assertFalse(plan["executable"])

    def test_device_path_validation_rejects_shell_metacharacters(self):
        self.assertEqual("/dev/sdb", validate_device_path("/dev/sdb"))
        for value in ("sdb", "/tmp/sdb", "/dev/sdb;reboot", "/dev/../etc/passwd"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_device_path(value)

    def test_storage_roles_are_persisted_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            store = StorageRoleStore(Path(temp))
            store.set("/srv/data", "work", "office", "Arbeitsdaten")
            store.set("/srv/backup", "backup", "office", "Backup")
            rows = store.all()
            self.assertEqual(2, len(rows))
            self.assertEqual({"work", "backup"}, {row["role"] for row in rows})
            text = store.path.read_text(encoding="utf-8")
            self.assertNotIn("password", text.casefold())
            self.assertNotIn("secret", text.casefold())

    def test_health_warns_when_work_storage_has_no_backup(self):
        snapshot = {
            "document_usage": {"used_percent": 50},
            "blocks": [{"path": "/dev/sdb", "name": "sdb", "mountpoints": ["/srv/data"]}],
            "mounts": [{"target": "/srv/data", "source": "/dev/sdb"}],
            "mdraid": {"degraded": False},
        }
        checks = health_checks(
            snapshot,
            [{"identifier": "/srv/data", "role": "work", "group": "office", "label": "Arbeitsdaten"}],
        )
        self.assertTrue(any(check["code"] == "work-without-backup" for check in checks))

    def test_health_warns_when_backup_target_disappears(self):
        snapshot = {
            "document_usage": {"used_percent": 50},
            "blocks": [],
            "mounts": [],
            "mdraid": {"degraded": False},
        }
        checks = health_checks(
            snapshot,
            [{"identifier": "/srv/backup", "role": "backup", "group": "office", "label": "Backup"}],
        )
        self.assertTrue(any(check["code"] == "storage-role-missing" and check["severity"] == "critical" for check in checks))

    def test_health_warns_when_document_storage_is_nearly_full(self):
        snapshot = {
            "document_usage": {"used_percent": 92},
            "blocks": [],
            "mounts": [],
            "mdraid": {"degraded": False},
        }
        checks = health_checks(snapshot, [])
        self.assertTrue(any(check["code"] == "document-storage-low" for check in checks))

    def test_health_marks_degraded_raid_critical(self):
        snapshot = {
            "document_usage": {"used_percent": 20},
            "blocks": [],
            "mounts": [],
            "mdraid": {"degraded": True},
        }
        checks = health_checks(snapshot, [])
        self.assertTrue(any(check["code"] == "raid-degraded" and check["severity"] == "critical" for check in checks))


if __name__ == "__main__":
    unittest.main()
