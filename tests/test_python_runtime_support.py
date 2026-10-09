"""Regression tests for security support, package provenance and installer gates."""
import datetime
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
        for context in (patch.object(policy.platform, "python_implementation", return_value="CPython"),
                        patch.dict(os.environ, {"SIMPLEOFFICE_ALLOW_PRERELEASE": "0"})):
            context.start()
            self.addCleanup(context.stop)

    def problem(self, minor, level="final", micro=22, serial=0):
        with patch.object(policy.sys, "version_info", Version((3, minor, micro, level, serial))):
            return policy.runtime_problem()

    def test_supported_stable_series(self):
        for minor in (11, 12, 13, 14):
            self.assertIsNone(self.problem(minor))
        for minor in (9, 15, 16):
            self.assertIsNotNone(self.problem(minor))

    def test_prerelease_switch_is_narrow_and_never_approves_final(self):
        self.assertIsNotNone(self.problem(15, "candidate"))
        with patch.dict(os.environ, {"SIMPLEOFFICE_ALLOW_PRERELEASE": "1"}):
            self.assertIsNone(self.problem(15, "candidate", micro=0, serial=3))
            self.assertIsNotNone(self.problem(15, "candidate", micro=0, serial=2))
            self.assertIsNotNone(self.problem(15, "candidate", micro=0, serial=4))
            self.assertIsNotNone(self.problem(15, "candidate", micro=1, serial=3))
            for minor, level in ((15, "final"), (14, "candidate"), (16, "alpha"), (10, "candidate")):
                self.assertIsNotNone(self.problem(minor, level))
            with patch.object(policy, "_jammy_python", return_value=False):
                self.assertIn("upstream EOL", self.problem(10))

    def test_jammy_exception_expires_without_implicit_esm_extension(self):
        end = datetime.date(2027, 5, 31)
        expired = datetime.date(2027, 6, 1)
        with patch.object(policy, "_jammy_python", return_value=True), patch.object(policy.datetime, "date") as date:
            date.today.return_value = end
            self.assertIsNone(self.problem(10))
            date.today.return_value = expired
            self.assertIn("upstream EOL", self.problem(10))

    def apt_policies(self, version="3.10.12-1~22.04.13", candidate=None, source="http://azure.archive.ubuntu.com/ubuntu", release=None):
        candidate = candidate or version
        index = f"500 {source} jammy-security/main amd64 Packages"
        release = release or "v=22.04,o=Ubuntu,a=jammy-security,n=jammy,l=Ubuntu,c=main,b=amd64"
        package = f"  Candidate: {candidate}\n  *** {version} 500\n    {index}\n    100 /var/lib/dpkg/status"
        origins = index + "\n release " + release
        origins += f"\n500 {source} jammy-updates/main amd64 Packages\n release v=22.04,o=Ubuntu,a=jammy-updates,n=jammy,l=Ubuntu,c=main,b=amd64"
        return package, origins

    def provenance(self, release='ID=ubuntu\nVERSION_ID="22.04"', base="/usr/bin/python3.10", overrides=None, canonical_bytes=True):
        version = "3.10.12-1~22.04.13"
        package_policy, origins = self.apt_policies(version)
        overrides = overrides or {}

        def command(*args):
            if args in overrides:
                result = overrides[args]
                if isinstance(result, Exception):
                    raise result
                return result
            if args[:2] == ("dpkg-query", "-S"):
                return "python3.10-minimal: /usr/bin/python3.10"
            if args[:2] == ("dpkg-query", "-W"):
                self.assertIn(args[-1], policy.JAMMY_PACKAGES)
                return "install ok installed\n" + version
            if args[:2] == ("dpkg", "--verify"):
                self.assertIn(args[-1], policy.JAMMY_PACKAGES)
                return ""
            return origins if len(args) == 2 else package_policy

        with patch.object(policy.Path, "read_text", return_value=release), \
                patch.object(policy, "_canonical_package_bytes", return_value=canonical_bytes), \
                patch.object(policy, "_local_runtime_proof", return_value=canonical_bytes), \
                patch.object(policy.sys, "_base_executable", base), \
                patch.object(policy.Path, "resolve", return_value=Path(base)), \
                patch.object(policy, "_command", side_effect=command) as commands:
            result = policy._jammy_python(allow_network=True)
            if result:
                for package in policy.JAMMY_PACKAGES:
                    self.assertIn(unittest.mock.call("dpkg", "--verify", package), commands.call_args_list)
            return result

    def test_distro_base_interpreter_and_venv_provenance(self):
        self.assertTrue(self.provenance())
        with patch.object(policy.sys, "executable", "/tmp/venv/bin/python"):
            self.assertTrue(self.provenance())
        for release, base in (("ID=debian\nVERSION_ID=12", "/usr/bin/python3.10"),
                              ("ID=ubuntu\nVERSION_ID=24.04", "/usr/bin/python3.10"),
                              ("ID=ubuntu\nVERSION_ID=22.04", "/usr/local/bin/python3.10")):
            self.assertFalse(self.provenance(release=release, base=base))

    def test_every_interpreter_package_requires_provenance_and_integrity(self):
        for package in policy.JAMMY_PACKAGES:
            with self.subTest(package=package):
                self.assertFalse(self.provenance(overrides={
                    ("dpkg", "--verify", package): "??5?????? /usr/lib/python3.10/os.py"}))
                self.assertFalse(self.provenance(overrides={
                    ("dpkg-query", "-W", "-f=${Status}\n${Version}", package): "deinstall ok config-files\n3.10.12"}))
                ppa, _ = self.apt_policies(source="https://ppa.launchpad.net/python")
                self.assertFalse(self.provenance(overrides={("apt-cache", "policy", package): ppa}))
                self.assertFalse(self.provenance(overrides={
                    ("apt-cache", "policy", package): self.apt_policies(candidate="3.10.12-1~22.04.14")[0]}))
        for result in ("unowned", OSError("dpkg unavailable"), subprocess.TimeoutExpired("dpkg", 10)):
            self.assertFalse(self.provenance(overrides={("dpkg-query", "-S", "/usr/bin/python3.10"): result}))

    def test_jammy_rejects_installed_bytes_that_do_not_match_canonical_archive(self):
        self.assertFalse(self.provenance(canonical_bytes=False))

    def test_apt_provenance_requires_current_candidate_and_both_update_sources(self):
        for source in ("http://archive.ubuntu.com/ubuntu", "mirror+file:/etc/apt/apt-mirrors.txt",
                       "https://internal.example/ubuntu"):
            for release, expected in (
                ("v=22.04,o=Ubuntu,a=jammy-security,n=jammy,l=Ubuntu,c=main", True),
                ("v=22.04,o=LP-PPA-python,a=jammy-security,n=jammy,l=Ubuntu,c=main", False),
                ("v=24.04,o=Ubuntu,a=noble-security,n=noble,l=Ubuntu,c=main", False),
                ("v=22.04,o=Ubuntu,a=jammy-security,n=jammy,l=Ubuntu,c=universe", False),
            ):
                package, origins = self.apt_policies(source=source, release=release)
                with patch.object(policy, "_command", side_effect=[package, origins]):
                    self.assertEqual(expected, policy._canonical_package_version("3.10.12-1~22.04.13"))
        version = "3.10.12-1~22.04.13"
        package, origins = self.apt_policies()
        for missing in ("jammy-security", "jammy-updates"):
            with patch.object(policy, "_command", side_effect=[package, origins.replace(missing, "disabled")]):
                self.assertFalse(policy._canonical_package_version(version))

        for package in (self.apt_policies(candidate="3.10.12-1~22.04.14")[0],
                        "*** " + version + " 100\n100 /var/lib/dpkg/status"):
            with patch.object(policy, "_command", side_effect=[package, origins]):
                self.assertFalse(policy._canonical_package_version(version))

    def test_same_version_from_official_and_foreign_origins_is_ambiguous(self):
        version = "3.10.12-1~22.04.13"
        package, origins = self.apt_policies()
        foreign = "900 https://ppa.launchpad.net/python jammy/main amd64 Packages"
        package += "\n" + foreign
        for metadata in ("", "\n release o=LP-PPA-python,a=jammy,n=jammy,l=Ubuntu,c=main"):
            with patch.object(policy, "_command", side_effect=[package, origins + "\n" + foreign + metadata]):
                self.assertFalse(policy._canonical_package_version(version))

    def test_pinning_cannot_hide_newer_canonical_versions(self):
        installed = "3.10.12-1~22.04.13"
        run_command = policy._command
        for advertised, accepted in (("3.10.12-1~22.04.9", True),
                                     ("3.10.12-1~22.04.14", False), ("1:3.10.12-1", False)):
            package, origins = self.apt_policies(installed)
            package += f"\n {advertised} -1\n  500 http://azure.archive.ubuntu.com/ubuntu jammy-security/main amd64 Packages"

            def command(*args):
                if args[0] == "dpkg":
                    return run_command(*args)
                return package if len(args) == 3 else origins

            with self.subTest(advertised=advertised), patch.object(policy, "_command", side_effect=command):
                self.assertEqual(accepted, policy._canonical_package_version(installed))

    def test_runtime_gate_caches_success_for_process_lifetime(self):
        with patch.object(policy, "_RUNTIME_PROBLEM_CACHE", policy._RUNTIME_PROBLEM_UNSET), \
                patch.object(policy, "runtime_problem", return_value=None) as problem:
            policy.require_supported_runtime()
            policy.require_supported_runtime()
        problem.assert_called_once()

    def test_setup_checks_policy_before_invoking_setuptools(self):
        setuptools = ModuleType("setuptools")
        setup = setuptools.setup = Mock()
        with patch.dict(sys.modules, {"setuptools": setuptools}), patch.object(policy.sys, "version_info", Version((3, 9, 0, "final", 0))):
            with self.assertRaisesRegex(RuntimeError, "Freigegeben"):
                runpy.run_path(str(ROOT / "setup.py"))
            setup.assert_not_called()

    def test_legacy_installer_keeps_runtime_dependencies_and_classifiers_aligned(self):
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
        self.assertEqual(">=3.10", legacy["python_requires"])

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
                result = subprocess.run([shell, str(root / starter)], cwd=root,
                                        env={**os.environ, "PYTHON": sys.executable,
                                             "SIMPLEOFFICE_ALLOW_PRERELEASE": "1"},
                                        capture_output=True, text=True, timeout=20)
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
                python.write_text('#!/bin/sh\ncase "$1" in\n*/simpleoffice_runtime_support.py) exec "'
                                  + sys.executable + '" "$@" ;;\n*) echo selected-venv; exit 0 ;;\nesac\n')
                python.chmod(0o755)
                bootstrap = root / "unsupported-python"
                bootstrap.write_text('#!/bin/sh\ntouch "' + str(root / "bootstrap-used") + '"\nexit 1\n')
                bootstrap.chmod(0o755)
                result = subprocess.run([shell, str(root / starter), *args], cwd=root,
                                        env={**os.environ, "PYTHON": str(bootstrap),
                                             "SIMPLEOFFICE_NATIVE_PACKAGES": "0", "SIMPLEOFFICE_ALLOW_PRERELEASE": "1"},
                                        capture_output=True, text=True, timeout=20)
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
            result = subprocess.run(["sh", str(root / "start-sftp.sh")], cwd=root,
                                    env={**os.environ, "PYTHON": sys.executable, "TERMUX_VERSION": "test",
                                         "PATH": str(root) + os.pathsep + os.environ["PATH"]},
                                    capture_output=True, text=True, timeout=20)
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
                'from unittest.mock import Mock, patch; '
                'V=type("V",(tuple,),{"releaselevel":"final"}); '
                'p=patch.object(sys,"version_info",V((3,9,0,"final",0))); p.start(); '
                'runpy.run_path(sys.argv[1],run_name="__main__")\' "$1"\n'
            )
            wrapper.chmod(0o755)
            result = subprocess.run(["bash", str(root / "start.sh"), "--check-system"],
                                    env={**os.environ, "PYTHON": str(wrapper)}, cwd=root,
                                    capture_output=True, text=True, timeout=20)
        self.assertEqual(1, result.returncode)
        self.assertIn("verändert das System nicht", result.stderr)
        self.assertNotIn("installiere", result.stdout)


if __name__ == "__main__":
    unittest.main()
