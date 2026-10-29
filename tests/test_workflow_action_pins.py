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
        self.assertIn("pip==26.2.2", lock_text)
        self.assertEqual(
            {
                "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70",
                "3a8e5c800492787f33a4114ef54444546c340df428974ebba17e0fd47e11af2f",
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
        self.assertIn("ruff==0.16.9", lock_text)
        self.assertEqual(
            {
                "95e6f022090368ab3b824c36276839c53b2adf1a3f4c09fefc33dfc400f6da96",
                "a5f27be168556594a86d2f415db0cf43f5291917849318f873c7e2791f7a8c67",
                "1632eb1d6197f33bd00b1acbc5b71009e89a8895c158e2d2b03a834fac964ab6",
                "b3f951b14d865d5952c89d40a5ca07e87abe24fa5453299878411e127748fb1c",
                "447fc07e1573afff7cb02803462b12b6c8ece7cf10e2cd78565fa6d7a1c0bf8d",
                "8a3e039a6a40ed976c491722b60e0ae4a4aa1a86057f540ee7a37a5d19ae9120",
                "4684dded7db60aa57cb118fa158630f5feade4af5782903b6053484bdf9bd129",
                "d29c934357e45642fda2f34c0b1f4025b4a6c01e15e4bf0016879d60078a142c",
                "a21713e629d3e5bdb2f5c2def1cc7f04f47fa8e1a7eb0571b4a28e1da64bc728",
                "7baa24ef5fc8e77aa93879e1d3f43754a01ae488e869f1ae30cf431afd4d2452",
                "a41aac6230aadfaa133bdfa1614488531ffa3e0837567ae04c0da2058a9c0f9e",
                "c2529fb5896d49115b0e9aa8f887490b34bbe76baf879ec2264ac59406869ce7",
                "41e3870277694177429b56406d65dfbdb2c2802c52b715edaf6a0b829c69d4ee",
                "8adbe4e58af167f767d7b2ba5e83c42e878350796cf78c2f5e14ab9903a92588",
                "0e1dbc2073624dee6618d41d0098690a7244654af746704b64759e12b6b6b385",
                "6bd40fec8cd4c8a3d4dd589bd8ad4e6320c13c29234159bfd959a40d529d597b",
                "ed1a252039200f57a59eebc063b54beabea67bfbaaca0eeaa7f54b5fbcda2284",
                "12b625c6cfba78d285d9f48eda5f053374f1e53cb10ef17342a383750db99161",
            },
            set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_text)),
        )

        workflow = (root / ".github/workflows/extended-quality.yml").read_text(
            encoding="utf-8",
        )
        self.assertIn("--require-hashes -r requirements-ci-ruff.txt", workflow)
        self.assertNotIn("ruff==0.16.9", workflow)
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
            {"bandit==1.9.4", "pip-audit==2.10.1"},
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
        self.assertNotIn(
            "pip install --disable-pip-version-check pip-audit bandit",
            workflow,
        )
        scanner_ignore = (root / ".oss-scanner/Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        self.assertIn("!.github/workflows/security-quickwins.yml", scanner_ignore)

    def test_browser_test_tools_are_hash_locked_in_consuming_workflows(self):
        root = Path(__file__).resolve().parents[1]
        source_text = (root / "requirements-ci-browser.in").read_text(encoding="utf-8")
        lock_text = (root / "requirements-ci-browser.txt").read_text(encoding="utf-8")
        packages = {
            "greenlet==3.3.2",
            "jinja2==3.1.6",
            "markupsafe==3.0.4",
            "playwright==1.55.0",
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
        for package in packages:
            with self.subTest(package=package):
                self.assertIn(package, lock_text)
        self.assertEqual(
            {
                "43e99d1749147ac21dde49b99c9abffcbc1e2d55c67501465ef0930d6e78e070",
                "85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67",
                "8e124f974786f831d6043728e38296969d3579db8896fe004682f5758e613581",
                "c6d4d8f6f8c66c483b0835569c7f0caa03230820af8e500c181c93509c92d831",
                "af2f8fede4171ef667dfded53f96e2ed0d6e6bd7ee3bb46437f77e3b57689228",
                "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
            },
            set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock_text)),
        )

        workflow_root = root / ".github" / "workflows"
        consumers = ("manual-browser-screenshots.yml", "ubuntu-26-compat.yml")
        scanner_ignore = (root / ".oss-scanner" / "Dockerfile.dockerignore").read_text(
            encoding="utf-8",
        )
        for name in consumers:
            workflow = (workflow_root / name).read_text(encoding="utf-8")
            with self.subTest(workflow=name):
                self.assertIn("requirements-ci-browser.txt", workflow)
                self.assertNotIn("playwright==1.55.0", workflow)
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
        self.assertIn("awscli==1.45.28", source_text)
        self.assertIn("awscli==1.45.28", lock_text)
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
        self.assertIn("pip==26.2.2", wheel_lock)
        self.assertNotIn("pip==26.1.2", wheel_lock)
        for package in ("pip==26.2.2", "cibuildwheel==4.3.3", "wheel==0.46.3"):
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
        self.assertNotIn("pip install 'cibuildwheel==4.3.3'", native_workflow)
        self.assertIn("requirements-ci-android-apk.txt", apk_workflow)
        self.assertNotIn("pip install 'Pillow==13.0.0'", apk_workflow)
        self.assertNotIn("pip install 'wheel==0.46.3'", native_workflow)

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
