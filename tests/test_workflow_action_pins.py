"""Regression tests for immutable GitHub Action references."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile
import unittest

from tools.check_workflow_action_pins import unpinned_action_references


class WorkflowActionPinTests(unittest.TestCase):
    def test_repository_workflow_actions_are_sha_pinned(self):
        workflow_root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        self.assertEqual([], unpinned_action_references(workflow_root))

    def test_mutable_external_refs_are_reported_but_local_reusable_workflows_are_allowed(self):
        with tempfile.TemporaryDirectory() as temporary:
            workflow_root = Path(temporary)
            (workflow_root / "sample.yml").write_text(
                "steps:\n"
                "  - uses: vendor/action@v1\n"
                "  - uses: ./.github/workflows/reusable.yml\n"
                "  - uses: vendor/pinned@0123456789abcdef0123456789abcdef01234567\n",
                encoding="utf-8",
            )
            self.assertEqual(
                [f"{workflow_root / 'sample.yml'}:2: vendor/action@v1"],
                unpinned_action_references(workflow_root),
            )

    def test_scorecard_docker_base_images_are_digest_pinned(self):
        root = Path(__file__).resolve().parents[1]
        image_reference = re.compile(r"@sha256:[0-9a-f]{64}$")
        for relative_path in (Path("Dockerfile"), Path(".oss-scanner/Dockerfile")):
            with self.subTest(path=relative_path):
                lines = (root / relative_path).read_text(encoding="utf-8").splitlines()
                bases = [line.split()[1] for line in lines if line.strip().startswith("FROM ")]
                self.assertTrue(bases, f"No base image found in {relative_path}")
                self.assertTrue(all(image_reference.search(base) for base in bases), bases)

    def test_ci_build_helpers_use_shared_sha256_lock(self):
        root = Path(__file__).resolve().parents[1]
        lock_text = (root / "requirements-ci-tools.txt").read_text(encoding="utf-8")
        self.assertIn("packaging==26.3", lock_text)
        self.assertIn("defusedxml==0.7.1", lock_text)
        self.assertEqual(
            {
                "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
                "94edc256424af38762eb31306eed28beb9f0efc50a8837492c9d6fd6004aed79",
                "a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61",
                "1bb3032db185915b62d7c6209c5a8792be6a32ab2fedacc84e01b52c51aa3e69",
            },
            set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_text)),
        )
        workflow_root = root / ".github" / "workflows"
        lock_references = 0
        for path in workflow_root.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("packaging==26.3", text, path.name)
            self.assertNotIn("defusedxml==0.7.1", text, path.name)
            for line in text.splitlines():
                if "requirements-ci-tools.txt" in line and "install" in line:
                    lock_references += 1
                    self.assertIn("--require-hashes", line, f"{path}:{line}")
        self.assertGreater(lock_references, 0)

    def test_ci_pip_bootstrap_is_hash_locked_in_consuming_workflows(self):
        root = Path(__file__).resolve().parents[1]
        lock_text = (root / "requirements-ci-pip.txt").read_text(encoding="utf-8")
        self.assertIn("pip==26.3", lock_text)
        self.assertEqual(
            {
                "6c36ae7da8695be67e3cd9f7d87919667ced53f57ce2ede75d89b5912ed2bc4c",
                "f3b60b20ffaadd4415ed7957212a38ac63fa4c5fa375bd0f4a797777940fad13",
            },
            set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_text)),
        )

        workflow_root = root / ".github" / "workflows"
        scanner_ignore = (root / ".oss-scanner" / "Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        consumers = (
            "ci.yml",
            "manual-browser-screenshots.yml",
            "project-release.yml",
            "ubuntu-26-compat.yml",
        )
        for name in consumers:
            workflow = (workflow_root / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn(f"!.github/workflows/{name}", scanner_ignore)
                self.assertIn("requirements-ci-pip.txt", workflow)
                self.assertNotIn("pip install --upgrade pip", workflow)
                for line in workflow.splitlines():
                    if "requirements-ci-pip.txt" in line and "pip install" in line:
                        self.assertIn("--require-hashes", line)

        for name in ("manual-browser-screenshots.yml", "ubuntu-26-compat.yml"):
            workflow = (workflow_root / name).read_text(encoding="utf-8")
            pull_request_paths = workflow.split("  pull_request:", 1)[1].split(
                "  workflow_dispatch:", 1,
            )[0]
            with self.subTest(path_filter=name):
                self.assertIn("requirements-ci-pip.in", pull_request_paths)
                self.assertIn("requirements-ci-pip.txt", pull_request_paths)

    def test_ruff_static_analyzer_is_installed_from_hash_lock(self):
        root = Path(__file__).resolve().parents[1]
        lock_text = (root / "requirements-ci-ruff.txt").read_text(encoding="utf-8")
        self.assertIn("ruff==0.17.6", lock_text)
        self.assertEqual(
            {
                "0692088e6b33455572fb18465e891b443f196e98dc1986825685c77ff6f4c5ac",
                "084d9c8f5ec718e7eba007dba339e7cb4f7571d2f267558ae080bf428fb6a69b",
                "1939b3d701c7b6981b7cf93473de5bf88b129b9ba84efaed5154cc517c44634d",
                "1a0bea6aa1216e0ec4cd35d3dbccec0d772a7c2aad28ec9c6e82571dff2c20b0",
                "2b04276561331300325040bdca21460770029ec574d4c479099e39e8ad1ed43c",
                "3db61f5015600543a581b07fa7f6f64afa0eeedefe16034cadd88c4074087582",
                "435241c61af5909a022f76219d3d1bf24730dc1bf0acabb5ddf4b569ab3cbb30",
                "6a12c50a33936dc00db2be21ba25fb168a8b95c55eda2a3ac85005af69885e4b",
                "9cd2fd9923e9806b8b6b67a3469e2258e84bd4d21e45d5a1a690afdfb9c8e57d",
                "9d44ef2140402043fd6f689ef541858b80883a8b0a02e38539ef0614ebe57d0c",
                "9fbddc08577592e283c265d865e0182de6e33db185494db7b06290d989d888e5",
                "aa3b08254a732acba246891089a152c47c5733dc36b8ecb76ce8602f8326e5e4",
                "b5aba93b3db4b70c5697e4fee6e0d5b9c8452a9a3a03679611cadc25915a2173",
                "c2f4af6670b7a0501904e881bb3f1f24bd0d964dec1eedbd85f0eb73786e1128",
                "d006bbb2945edb17b0ce6a0c1b5f5a8d81ba11dbac294a0a925effa22ce34feb",
                "d8b14e1473dc86ab9069cf7a2ba5041dc012f59110201844ed04cfddf07055a0",
                "fb589e032806bb942e4fb526f30b80a1a64112147ed9f1d38cc3de2e255b6f45",
                "fd661702202a89c00a4c9d0339008805fa5876bfa1c57e165495c2e62230dfbf",
            },
            set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_text)),
        )

        workflow = (root / ".github/workflows/extended-quality.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("--require-hashes -r requirements-ci-ruff.txt", workflow)
        self.assertNotIn("ruff==", workflow)
        scanner_ignore = (root / ".oss-scanner/Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        self.assertIn("!.github/workflows/extended-quality.yml", scanner_ignore)

    def test_security_scanners_are_installed_from_hash_lock(self):
        root = Path(__file__).resolve().parents[1]
        source_text = (root / "requirements-ci-security-tools.in").read_text(
            encoding="utf-8",
        )
        self.assertEqual(
            {
                "bandit==1.9.4",
                "pip-audit==2.10.1",
                "typing-extensions==4.16.0",
            },
            {line.strip() for line in source_text.splitlines() if line.strip()},
        )

        lock_text = (root / "requirements-ci-security-tools.txt").read_text(
            encoding="utf-8",
        )
        self.assertIn("bandit==1.9.4", lock_text)
        self.assertIn("pip-audit==2.10.1", lock_text)
        package_stanza = []
        package_count = 0
        for line in lock_text.splitlines():
            if line and not line.startswith(("#", " ")) and "==" in line:
                if package_stanza:
                    self.assertTrue(
                        any("--hash=sha256:" in entry for entry in package_stanza),
                        package_stanza[0],
                    )
                package_stanza = [line]
                package_count += 1
            elif package_stanza:
                package_stanza.append(line)
        self.assertGreater(package_count, 2)
        self.assertTrue(
            any("--hash=sha256:" in entry for entry in package_stanza),
            package_stanza[0],
        )

        workflow = (root / ".github/workflows/security-quickwins.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("--require-hashes --only-binary=:all:", workflow)
        self.assertIn("requirements-ci-security-tools.txt", workflow)
        install_step = workflow.split("- name: Install security scanners", 1)[1].split(
            "- name: Audit Python dependencies", 1,
        )[0]
        self.assertIn("run: >-\n", install_step)
        self.assertNotIn(
            "pip install --disable-pip-version-check pip-audit bandit",
            workflow,
        )
        scanner_ignore = (root / ".oss-scanner/Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        self.assertIn("!.github/workflows/security-quickwins.yml", scanner_ignore)

    def test_electron_desktop_dependencies_are_audited_in_ci(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/desktop-build.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("desktop/electron/package-lock.json", workflow)
        audit_step = workflow.split(
            "- name: Audit Electron npm dependencies", 1,
        )[1].split("- name:", 1)[0]
        self.assertIn("if: matrix.os == 'ubuntu-24.04'", audit_step)
        self.assertIn("python ../../tools/electron_npm_audit.py", audit_step)
        audit_helper = (root / "tools/electron_npm_audit.py").read_text(
            encoding="utf-8",
        )
        self.assertIn('["npm", "audit", "--json"]', audit_helper)

    def test_browser_test_tools_are_hash_locked_in_consuming_workflows(self):
        root = Path(__file__).resolve().parents[1]
        source_text = (root / "requirements-ci-browser.in").read_text(encoding="utf-8")
        lock_text = (root / "requirements-ci-browser.txt").read_text(encoding="utf-8")
        packages = {
            "greenlet==3.5.6",
            "jinja2==3.1.6",
            "markupsafe==3.0.4",
            "playwright==1.65.2",
            "pyee==13.0.1",
            "typing-extensions==4.16.0",
        }
        self.assertEqual(
            packages,
            {
                line.strip()
                for line in source_text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            },
        )
        source_versions = {
            name.casefold().replace("_", "-"): version
            for name, version in (line.split("==", 1) for line in packages)
        }
        lock_stanzas = {}
        current_package = None
        for line in lock_text.splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)==([0-9][A-Za-z0-9.+!_-]*)", line)
            if match:
                current_package = match.group(1).casefold().replace("_", "-")
                lock_stanzas[current_package] = {
                    "version": match.group(2),
                    "hashes": [],
                }
            elif current_package:
                lock_stanzas[current_package]["hashes"].extend(
                    re.findall(r"--hash=sha256:([0-9a-f]{64})", line),
                )
                if line and not line.startswith((" ", "#")):
                    current_package = None
        self.assertEqual(set(source_versions), set(lock_stanzas))
        for name, version in source_versions.items():
            with self.subTest(package=name):
                self.assertEqual(version, lock_stanzas[name]["version"])
                self.assertTrue(lock_stanzas[name]["hashes"])

        workflow_root = root / ".github" / "workflows"
        consumers = ("manual-browser-screenshots.yml", "ubuntu-26-compat.yml")
        scanner_ignore = (root / ".oss-scanner" / "Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        for name in consumers:
            workflow = (workflow_root / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("requirements-ci-browser.txt", workflow)
                self.assertNotIn("playwright==", workflow)
                self.assertNotIn("Jinja2>=3.1.2,<4", workflow)
                self.assertIn("--require-hashes --only-binary=:all:", workflow)
                self.assertIn(f"!.github/workflows/{name}", scanner_ignore)
                pull_request_paths = workflow.split("  pull_request:", 1)[1].split(
                    "  workflow_dispatch:", 1,
                )[0]
                self.assertIn("requirements-ci-browser.in", pull_request_paths)
                self.assertIn("requirements-ci-browser.txt", pull_request_paths)

    def test_aws_cli_s3_smoke_uses_hash_locked_patched_version(self):
        root = Path(__file__).resolve().parents[1]
        source_text = (root / "requirements-ci-awscli.in").read_text(encoding="utf-8")
        lock_text = (root / "requirements-ci-awscli.txt").read_text(encoding="utf-8")
        self.assertIn("awscli==1.46.3", source_text)
        self.assertIn("awscli==1.46.3", lock_text)
        self.assertNotIn("awscli==1.42.59", lock_text)
        package_stanza = []
        package_count = 0
        for line in lock_text.splitlines():
            if line and not line.startswith(("#", " ")) and "==" in line:
                if package_stanza:
                    self.assertTrue(
                        any("--hash=sha256:" in stanza_line for stanza_line in package_stanza),
                        package_stanza[0],
                    )
                package_stanza = [line]
                package_count += 1
            elif package_stanza:
                package_stanza.append(line)
        self.assertGreater(package_count, 1)
        self.assertTrue(
            any("--hash=sha256:" in stanza_line for stanza_line in package_stanza),
            package_stanza[0],
        )

        workflow = (
            root / ".github" / "workflows" / "manual-browser-screenshots.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "python -m pip install --require-hashes --only-binary=:all: "
            "-r requirements-ci-awscli.txt",
            workflow,
        )
        install_step = workflow.split(
            "- name: Install hash-locked AWS CLI S3 test tool", 1,
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("aws --version", install_step)
        self.assertIn("if: github.event_name == 'pull_request'", workflow)
        self.assertNotIn("awscli==1.42.59", workflow)
        pull_request_paths = workflow.split("  pull_request:", 1)[1].split(
            "  workflow_dispatch:", 1,
        )[0]
        self.assertIn("requirements-ci-awscli.in", pull_request_paths)
        self.assertIn("requirements-ci-awscli.txt", pull_request_paths)

    def test_android_build_tools_are_hash_locked_and_lock_changes_trigger_ci(self):
        root = Path(__file__).resolve().parents[1]
        wheel_lock = (root / "requirements-ci-android-wheels.txt").read_text(
            encoding="utf-8",
        )
        apk_lock = (root / "requirements-ci-android-apk.txt").read_text(
            encoding="utf-8",
        )
        self.assertIn("pip==26.3", wheel_lock)
        self.assertNotIn("pip==26.1.2", wheel_lock)
        for package in ("pip==26.3", "cibuildwheel==4.3.5", "wheel==0.48.0"):
            with self.subTest(package=package):
                lines = wheel_lock.splitlines()
                start = next(
                    (index for index, line in enumerate(lines)
                     if line.startswith(f"{package} \\")),
                    None,
                )
                self.assertIsNotNone(start, package)
                hashes = []
                for line in lines[start + 1:]:
                    if not line.startswith("    --hash=sha256:"):
                        break
                    hashes.extend(re.findall(r"sha256:([0-9a-f]{64})", line))
                self.assertTrue(hashes, package)
        self.assertIn("pillow==13.0.0", apk_lock)
        self.assertRegex(apk_lock, r"--hash=sha256:[0-9a-f]{64}")

        native_workflow = (root / ".github/workflows/android-native-wheels.yml").read_text(
            encoding="utf-8",
        )
        apk_workflow = (root / ".github/workflows/android-apk-build.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("--require-hashes", native_workflow)
        self.assertIn("requirements-ci-android-wheels.txt", native_workflow)
        self.assertNotIn("pip install --upgrade pip", native_workflow)
        self.assertNotIn("pip install 'cibuildwheel==4.3.5'", native_workflow)
        self.assertIn("requirements-ci-android-apk.txt", apk_workflow)
        self.assertNotIn("pip install 'Pillow==13.0.0'", apk_workflow)
        self.assertNotIn("pip install 'wheel==0.48.0'", native_workflow)

        path_filtered_workflows = (
            "android-apk-build.yml", "desktop-build.yml", "docker-build.yml",
            "ubuntu-26-compat.yml", "tauri-build.yml",
            "tauri-desktop-crossplatform.yml",
        )
        for name in path_filtered_workflows:
            text = (root / ".github/workflows" / name).read_text(encoding="utf-8")
            pull_request = text.split("  pull_request:", 1)[1]
            pull_request_paths = re.split(
                r"(?m)^(?:  [A-Za-z_-]+:|[A-Za-z_-]+:)", pull_request, maxsplit=1,
            )[0]
            with self.subTest(workflow=name):
                self.assertIn("requirements-ci-tools.txt", pull_request_paths)
        for lock_name in (
            "requirements-ci-android-apk.in", "requirements-ci-android-apk.txt",
            "requirements-ci-android-wheels.in", "requirements-ci-android-wheels.txt",
        ):
            with self.subTest(lock=lock_name):
                pull_request = apk_workflow.split("  pull_request:", 1)[1]
                pull_request_paths = re.split(
                    r"(?m)^(?:  [A-Za-z_-]+:|[A-Za-z_-]+:)",
                    pull_request, maxsplit=1,
                )[0]
                self.assertIn(lock_name, pull_request_paths)

    def test_scorecard_upload_workflow_has_only_step_scoped_environment(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (
            root
            / ".github"
            / "workflows"
            / "scorecard.yml"
        ).read_text(encoding="utf-8")
        scanner_ignore = (root / ".oss-scanner" / "Dockerfile.dockerignore").read_text(
            encoding="utf-8"
        )
        self.assertIn("!.github/workflows/scorecard.yml", scanner_ignore)
        self.assertNotRegex(workflow, r"(?m)^(?:env|defaults):")
        self.assertNotRegex(workflow, r"(?m)^  (?:env|defaults):")
        lines = workflow.splitlines()
        runtime_settings = [
            line for line in lines if "FORCE_JAVASCRIPT_ACTIONS_TO_NODE24:" in line
        ]
        action_steps = [line for line in lines if re.match(r"^\s+uses:", line)]
        self.assertTrue(action_steps)
        self.assertEqual(len(action_steps), len(runtime_settings))
        self.assertTrue(all(len(line) - len(line.lstrip()) >= 10 for line in runtime_settings))


if __name__ == "__main__":
    unittest.main()
