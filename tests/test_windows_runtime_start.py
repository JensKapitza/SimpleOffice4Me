"""Exercise the actual batch starter with a valid venv and a rejected launcher."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "win32", "requires cmd.exe and a Windows venv")
class WindowsRuntimeStartTests(unittest.TestCase):
    def run_starter(self, reject_venv=False, check_system=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "start.bat", root / "start.bat")
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / ".venv")],
                           check=True, capture_output=True, timeout=30)
            (root / "py.bat").write_text('@echo launcher-used> "%~dp0launcher-used"\n@exit /b 1\n')
            (root / "simpleoffice_runtime_support.py").write_text(
                "import os, sys\nfrom pathlib import Path\n"
                "Path('checked-runtime').write_text(sys.prefix)\n"
                "sys.exit(1 if os.environ.get('REJECT_TEST_VENV') == '1' else 0)\n")
            (root / "pip.py").write_text("from pathlib import Path\nPath('pip-used').touch()\n")
            tools = root / "tools"
            tools.mkdir()
            (tools / "__init__.py").touch()
            for filename in ("install_invoice_validator.py", "install_xrechnung_validator.py", "launcher.py", "system_requirements.py"):
                (tools / filename).write_text("pass\n")
            result = subprocess.run(["cmd.exe", "/d", "/c", "start.bat", *(["--check-system"] if check_system else [])], cwd=root,
                                    env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                                         "PYTHONPATH": str(root), "REJECT_TEST_VENV": str(int(reject_venv))},
                                    capture_output=True, text=True, timeout=30)
            self.assertFalse((root / "launcher-used").exists(), result.stdout + result.stderr)
            self.assertEqual(root / ".venv", Path((root / "checked-runtime").read_text()))
            self.assertEqual(not reject_venv and not check_system, (root / "pip-used").exists())
            return result

    def test_valid_existing_venv_precedes_newest_launcher_runtime(self):
        result = self.run_starter()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_invalid_existing_venv_stops_before_installation(self):
        self.assertEqual(1, self.run_starter(reject_venv=True).returncode)

    def test_system_check_uses_existing_venv_without_installing(self):
        result = self.run_starter(check_system=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
