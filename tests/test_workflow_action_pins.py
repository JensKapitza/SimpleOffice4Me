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
        self.assertIn("ruff==0.17.5", lock_text)
        self.assertEqual(
            {
                "0e5c125c5b31dc8efc948d41357496a4c4a312977565ccba740030efdff83580",
                "40ca8afaac309b41a5b2e09e4e7beb1f824826b93c25697c4d74f573324fba4e",
                "50dfc3c6b3ebd0a932f11dfd579bd42234b0b7137e68940eb6abdbfdb9d69697",
                "54c0f67d1be11b55d12c184a819fc229f5eee3ef45c16c4fcad41258e83b4882",
                "625e57d80cd43352d12ea81690f3eac46cddc8c0f35026d137fb0f157f94592e",
                "64a6fd668a336df959c522991de33e9e3c8be463afc0c9ac8f029f9d19ef756e",
                "6f0db748508f04a04e3fbebcc636a5c8b3bd76274f8ed28f497c44d75c2ea2d7",
                "6f6a7b138edf9cc402ca736a59d9525e6d766e5395760b72e7e146e6251ac963",
                "86bd1f3b83c25f9b8055c6e796f0685b39d3127ce947322098c3108cf82fd586",
                "8e85601bd6d9b6c3e3b95c0e0dccdbac1d23c6bcef006d187fbcddaf8225a5df",
                "aa415d57c244299279901d776607fee3df7831c2a7435433fbfacde8632c6550",
                "b72fbc232e328bdbb9cdddf4f1aedcd61bd1aa64d75fc012114b4add0bd7f26b",
                "d511dd302982e46a285528942fa6557d57730edc4bdb9def95e86a2603d77396",
                "dd0dfe7c6fe5a447fce467c58e69893be1d9a7e4378f5b9906432da33d94a97f",
                "e1546609b2ad73f1d6c5c076881b788672b7b3cc3283981182cbdbb6524ed88a",
                "e52622ecf3a0df9d03e796fe3eed4aa085adda65520db7e0619cbd2192a97342",
                "ec4ecf40fb4d6e1265b009dbe551727920926fc0f769070c452d6d84b422a74d",
                "f93d2b7d449ed9545304f6923699ca20d13c675b69f258481709a08ae61076db",
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
        self.assertIn("npm audit --audit-level=moderate", audit_step)

    def test_browser_test_tools_are_hash_locked_in_consuming_workflows(self):
        root = Path(__file__).resolve().parents[1]
        source_text = (root / "requirements-ci-browser.in").read_text(encoding="utf-8")
        lock_text = (root / "requirements-ci-browser.txt").read_text(encoding="utf-8")
        packages = {
            "greenlet==3.5.6",
            "jinja2==3.1.6",
            "markupsafe==3.0.4",
            "playwright==1.65.1",
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
