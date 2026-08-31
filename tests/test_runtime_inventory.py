import os
import subprocess
import unittest
from unittest.mock import patch

from app import app
from app.runtime_inventory import _tool_version, clear_runtime_inventory, runtime_inventory


class RuntimeInventoryTest(unittest.TestCase):
    def tearDown(self):
        clear_runtime_inventory()

    def test_version_probe_is_allowlisted_bounded_and_secret_free(self):
        completed = subprocess.CompletedProcess(["/usr/bin/gs", "--version"], 0, stdout="10.05.1\n", stderr="")
        with app.app_context(), patch("app.runtime_inventory.shutil.which", return_value="/usr/bin/gs"), patch(
            "app.runtime_inventory.subprocess.run", return_value=completed,
        ) as run, patch.dict(os.environ, {"SECRET_TOKEN": "must-not-leak"}, clear=False):
            result = _tool_version("Ghostscript", "Ghostscript", ("gs", "--version"))
        self.assertEqual("available", result["status"])
        self.assertEqual("10.05.1", result["version"])
        arguments, options = run.call_args
        self.assertEqual(("/usr/bin/gs", "--version"), arguments[0])
        self.assertFalse(options["check"])
        self.assertEqual(4, options["timeout"])
        self.assertNotIn("SECRET_TOKEN", options["env"])

    def test_inventory_lists_registered_modules_and_missing_tools(self):
        clear_runtime_inventory()
        with app.app_context(), patch("app.runtime_inventory.shutil.which", return_value=None):
            inventory = runtime_inventory()
        names = {row["name"] for row in inventory["modules"]}
        self.assertIn("documents", names)
        self.assertIn("admin", names)
        self.assertTrue(all(tool["status"] == "missing" for tool in inventory["tools"]))
        self.assertEqual("SimpleOffice4Me", inventory["system"]["application"])


if __name__ == "__main__":
    unittest.main()
