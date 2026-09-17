"""Installed root-module imports must work independently of the checkout."""
import ast
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class MiniPackagingTests(unittest.TestCase):
    def test_modern_and_legacy_packages_include_all_mini_service_modules(self):
        tree = ast.parse((ROOT / "setup.py").read_text(encoding="utf-8"))
        setup = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "setup")
        legacy = ast.literal_eval(next(key.value for key in setup.keywords if key.arg == "py_modules"))
        # The metadata uses only a literal string array: no TOML dependency on Python 3.10.
        modern = ast.literal_eval(re.search(r"(?ms)^py-modules\s*=\s*(\[.*?\])", (ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1))
        required = {path.stem for path in ROOT.glob("simpleoffice_*.py")}
        for label, modules in (("modern", modern), ("legacy", legacy)):
            with self.subTest(build=label):
                self.assertEqual(required, set(modules))
                self.assertEqual(len(modules), len(set(modules)))
                with tempfile.TemporaryDirectory() as folder:
                    for name in modules:
                        shutil.copyfile(ROOT / (name + ".py"), Path(folder) / (name + ".py"))
                    result = subprocess.run([sys.executable, "-I", "-c",
                        "import sys; sys.path.insert(0, sys.argv[1]); import simpleoffice_network_gateway_runtime; import simpleoffice_mini_control; import simpleoffice_service_lifecycle; import simpleoffice_sip_runtime", folder],
                        cwd=folder, capture_output=True, text=True, timeout=20)
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
