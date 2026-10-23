"""Regression tests for the supported CPython runtime gate and installers."""
import ast
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

import simpleoffice_runtime_support as policy


ROOT = Path(__file__).resolve().parents[1]


class Version(tuple):
    @property
    def releaselevel(self):
        return self[3]

    @property
    def serial(self):
        return self[4]


class RuntimeSupportTests(unittest.TestCase):
    def setUp(self):
        for context in (
            patch.object(policy.platform, "python_implementation", return_value="CPython"),
            patch.dict(os.environ, {"SIMPLEOFFICE_ALLOW_PRERELEASE": "0"}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def problem(self, minor, level="final", micro=22, serial=0):
        with patch.object(policy.sys, "version_info", Version((3, minor, micro, level, serial))):
            return policy.runtime_problem()

    def test_supported_stable_series(self):
        for minor in (11, 12, 13, 14, 15):
            with self.subTest(minor=minor):
                self.assertIsNone(self.problem(minor))
        for minor in (9, 10, 16):
            with self.subTest(minor=minor):
                self.assertIn("Freigegeben sind CPython 3.11–3.15", self.problem(minor))

    def test_prereleases_remain_rejected_after_final_approval(self):
        for minor, level, serial in (
            (15, "candidate", 3), (15, "candidate", 4),
            (16, "alpha", 1), (14, "candidate", 1), (11, "candidate", 1),
        ):
            with self.subTest(minor=minor, level=level, serial=serial):
                self.assertIn("Vorabversionen", self.problem(minor, level, micro=0, serial=serial))
        with patch.dict(os.environ, {"SIMPLEOFFICE_ALLOW_PRERELEASE": "1"}):
            self.assertIn("Vorabversionen", self.problem(15, "candidate", micro=0, serial=3))

    def test_non_cpython_is_rejected(self):
        with patch.object(policy.platform, "python_implementation", return_value="PyPy"):
            self.assertIn("Unterstützt wird CPython", self.problem(14))

    def test_runtime_gate_caches_success_for_process_lifetime(self):
        with patch.object(policy, "_RUNTIME_PROBLEM_CACHE", policy._RUNTIME_PROBLEM_UNSET), \
                patch.object(policy, "runtime_problem", return_value=None) as problem:
            policy.require_supported_runtime()
            policy.require_supported_runtime()
        problem.assert_called_once()

    def test_setup_checks_policy_before_invoking_setuptools(self):
        setuptools = ModuleType("setuptools")
        setup = setuptools.setup = Mock()
        with patch.dict(sys.modules, {"setuptools": setuptools}), \
                patch.object(policy.sys, "version_info", Version((3, 10, 0, "final", 0))):
            with self.assertRaisesRegex(RuntimeError, "Freigegeben"):
                runpy.run_path(str(ROOT / "setup.py"))
            setup.assert_not_called()

    def test_setup_installer_matches_project_metadata(self):
        setuptools = ModuleType("setuptools")
        setup = setuptools.setup = Mock()
        with patch.dict(sys.modules, {"setuptools": setuptools}), \
                patch.object(policy.sys, "version_info", Version((3, 12, 0, "final", 0))):
            runpy.run_path(str(ROOT / "setup.py"))
        legacy = setup.call_args.kwargs
        modern = (ROOT / "pyproject.toml").read_text()
        dependencies = ast.literal_eval(re.search(r"(?ms)^dependencies = (\[.*?\])", modern).group(1))
        classifiers = ast.literal_eval(re.search(r"(?ms)^classifiers = (\[.*?\])", modern).group(1))
        self.assertEqual(dependencies, legacy["install_requires"])
        self.assertEqual(classifiers, legacy["classifiers"])
        self.assertEqual(">=3.11", legacy["python_requires"])
        self.assertEqual(["packaging>=26.3,<27"], legacy["extras_require"]["sbom"])
        self.assertIn('sbom = [\n  "packaging>=26.3,<27",\n]', modern)

    def test_standalone_option_keeps_system_python_and_default_venv_untouched(self):
        starter = (ROOT / "start.sh").read_text()
        self.assertIn("--standalone)", starter)
        self.assertIn('VENV="$ROOT/.venv-standalone"', starter)
        self.assertIn('UV_PYTHON_INSTALL_DIR="$tools_dir/python"', starter)
        self.assertIn('"$uv_cmd" python install 3.12', starter)
        self.assertIn('"$uv_cmd" python find --managed-python 3.12', starter)
        self.assertIn("SIMPLEOFFICE_NATIVE_PACKAGES=0", starter)
        self.assertIn('PYTHON="$VENV/bin/python"', starter)

    def test_standalone_rejects_mixed_readonly_system_check(self):
        starter = (ROOT / "start.sh").read_text()
        self.assertIn('if [ "$CHECK_SYSTEM" -eq 1 ]; then', starter)
        self.assertIn("--standalone und --check-system", starter)
        self.assertIn('if [ -e "$VENV" ]; then', starter)

    def test_starters_preserve_an_existing_unsupported_venv(self):
        for starter, shell in (("start.sh", "bash"), ("start-sftp.sh", "sh")):
            with self.subTest(starter=starter), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copyfile(ROOT / starter, root / starter)
                shutil.copyfile(ROOT / "simpleoffice_runtime_support.py", root / "simpleoffice_runtime_support.py")
                venv = root / ".venv" / "bin"
                venv.mkdir(parents=True)
                python = venv / "python"
                python.write_text("#!/bin/sh\necho unsupported-venv >&2\nexit 1\n")
                python.chmod(0o755)
                result = subprocess.run(
                    [shell, str(root / starter)], cwd=root,
                    env={**os.environ, "PYTHON": sys.executable,
                         "SIMPLEOFFICE_ALLOW_PRERELEASE": "1", "SIMPLEOFFICE_NATIVE_PACKAGES": "0"},
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(1, result.returncode)
                self.assertIn("unsupported-venv", result.stderr)
                self.assertTrue(python.exists())

    def test_unix_starters_prefer_supported_venv_to_rejected_bootstrap(self):
        cases = (("start.sh", "bash", []), ("start.sh", "bash", ["--check-system"]),
                 ("start-sftp.sh", "sh", []))
        for starter, shell, args in cases:
            with self.subTest(starter=starter, args=args), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copyfile(ROOT / starter, root / starter)
                shutil.copyfile(ROOT / "simpleoffice_runtime_support.py", root / "simpleoffice_runtime_support.py")
                venv = root / ".venv" / "bin"
                venv.mkdir(parents=True)
                python = venv / "python"
                python.write_text(
                    '#!/bin/sh\ncase "$1" in\n*/simpleoffice_runtime_support.py) exec "'
                    + sys.executable + '" "$@" ;;\n*) echo selected-venv; exit 0 ;;\nesac\n'
                )
                python.chmod(0o755)
                bootstrap = root / "unsupported-python"
                bootstrap.write_text('#!/bin/sh\ntouch "' + str(root / "bootstrap-used") + '"\nexit 1\n')
                bootstrap.chmod(0o755)
                result = subprocess.run(
                    [shell, str(root / starter), *args], cwd=root,
                    env={**os.environ, "PYTHON": str(bootstrap), "SIMPLEOFFICE_NATIVE_PACKAGES": "0",
                         "SIMPLEOFFICE_ALLOW_PRERELEASE": "1"},
                    capture_output=True, text=True, timeout=20,
                )
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertIn("selected-venv", result.stdout)
                self.assertFalse((root / "bootstrap-used").exists())
                self.assertTrue(python.exists())

    def test_rejected_termux_venv_requires_controlled_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "start-sftp.sh", root / "start-sftp.sh")
            shutil.copyfile(ROOT / "simpleoffice_runtime_support.py", root / "simpleoffice_runtime_support.py")
            venv = root / ".venv-android" / "bin"
            venv.mkdir(parents=True)
            python = venv / "python"
            python.write_text("#!/bin/sh\necho rejected-termux-runtime >&2\nexit 1\n")
            python.chmod(0o755)
            pkg = root / "pkg"
            pkg.write_text("#!/bin/sh\nexit 0\n")
            pkg.chmod(0o755)
            result = subprocess.run(
                ["sh", str(root / "start-sftp.sh")], cwd=root,
                env={**os.environ, "PYTHON": sys.executable, "TERMUX_VERSION": "test",
                     "PATH": str(root) + os.pathsep + os.environ["PATH"]},
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("rejected-termux-runtime", result.stderr)
            self.assertTrue(python.exists())

    def test_check_system_rejects_unsupported_runtime_without_installs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copyfile(ROOT / "start.sh", root / "start.sh")
            shutil.copyfile(ROOT / "simpleoffice_runtime_support.py", root / "simpleoffice_runtime_support.py")
            wrapper = root / "python"
            wrapper.write_text(
                '#!/bin/sh\nexec "' + sys.executable + '" -c \'import runpy, sys; '
                'V=type("V",(tuple,),{"releaselevel":"final"}); '
                'p=__import__("unittest.mock",fromlist=["patch"]).patch.object(sys,"version_info",V((3,10,0,"final",0))); '
                'p.start(); runpy.run_path(sys.argv[1],run_name="__main__")\' "$1"\n'
            )
            wrapper.chmod(0o755)
            result = subprocess.run(
                ["bash", str(root / "start.sh"), "--check-system"],
                env={**os.environ, "PYTHON": str(wrapper)}, cwd=root,
                capture_output=True, text=True, timeout=20,
            )
        self.assertEqual(1, result.returncode)
        self.assertIn("verändert das System nicht", result.stderr)
        self.assertNotIn("installiere", result.stdout)


if __name__ == "__main__":
    unittest.main()
