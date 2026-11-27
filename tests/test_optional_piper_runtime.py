from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from app import optional_piper_runtime as runtime
from app.component_runtime import resolve_command


class _Response(BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


class PiperRuntimeManifestTests(unittest.TestCase):
    def test_manifest_is_fixed_to_pypi_linux_wheels_and_explicit_transitive_versions(self):
        wheels = runtime.manifest()
        self.assertEqual(7, len(wheels))
        self.assertEqual(len(wheels), len({wheel.filename for wheel in wheels}))
        self.assertIn("piper-tts==1.8.0", {wheel.requirement for wheel in wheels})
        self.assertIn("onnxruntime==1.32.0", {wheel.requirement for wheel in wheels})
        for wheel in wheels:
            self.assertEqual(64, len(wheel.digest))
            self.assertEqual(wheel.filename, wheel.url.rsplit("/", 1)[-1])
            self.assertEqual("https", urlsplit(wheel.url).scheme)
            self.assertEqual("files.pythonhosted.org", urlsplit(wheel.url).hostname)
            self.assertGreater(wheel.size, 0)

    def test_installer_refuses_unsupported_windows_runtime(self):
        with patch.object(runtime.platform, "system", return_value="Windows"):
            self.assertFalse(runtime.install_supported())


class PiperWheelDownloadTests(unittest.TestCase):
    def test_fixed_wheel_download_verifies_bytes_and_restricts_mode(self):
        data = b"trusted wheel bytes"
        wheel = runtime.Wheel(
            "fixture.whl", len(data), sha256(data).hexdigest(),
            "https://files.pythonhosted.org/packages/fixture.whl", "fixture==1",
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / wheel.filename
            response = _Response(data)
            runtime._download(wheel, target, runtime.time.monotonic() + 5,
                              opener=lambda *_args, **_kwargs: response)
            self.assertEqual(data, target.read_bytes())
            self.assertEqual(0o600, target.stat().st_mode & 0o777)

    def test_wrong_sha256_is_rejected(self):
        data = b"changed wheel bytes"
        wheel = runtime.Wheel(
            "fixture.whl", len(data), "0" * 64,
            "https://files.pythonhosted.org/packages/fixture.whl", "fixture==1",
        )
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / wheel.filename
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                runtime._download(wheel, target, runtime.time.monotonic() + 5,
                                  opener=lambda *_args, **_kwargs: _Response(data))

    def test_redirect_cannot_leave_fixed_pypi_wheel_host(self):
        from urllib.request import Request

        handler = runtime._TrustedRedirects()
        request = Request("https://files.pythonhosted.org/packages/fixture.whl")
        with self.assertRaisesRegex(ValueError, "Untrusted"):
            handler.redirect_request(request, None, 302, "Found", {},
                                     "https://attacker.example/fixture.whl")

    def test_cancel_before_download_does_not_open_network(self):
        from threading import Event

        wheel = runtime.manifest()[0]
        event = Event()
        event.set()
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(InterruptedError):
                runtime._download(wheel, Path(temporary) / wheel.filename,
                                  runtime.time.monotonic() + 5, cancel_event=event,
                                  opener=lambda *_args, **_kwargs: self.fail("unexpected network request"))


class ManagedPiperCommandTests(unittest.TestCase):
    def test_install_retry_recovers_crash_after_launcher_replace_before_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"
            components = root / ".simpleoffice-meta" / "components"
            runtime_dir = components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
            runtime_dir.mkdir(parents=True)
            (runtime_dir / runtime._INCOMPLETE_MARKER).write_text(runtime.VERSION, encoding="ascii")
            wrapper = components / "bin" / "piper"
            wrapper.parent.mkdir()
            wrapper.write_bytes(runtime._launcher_content(runtime_dir))
            wrapper.chmod(0o700)
            calls = []

            def download(_wheel, destination, _deadline, **_kwargs):
                destination.write_bytes(b"fixture wheel payload")

            def run(args, **_kwargs):
                calls.append(list(args))
                if args[1:3] == ["-m", "venv"]:
                    staged = Path(args[3])
                    (staged / "bin").mkdir(parents=True)
                    (staged / "bin" / "python").write_text("fixture python", encoding="utf-8")
                    (staged / "bin" / "piper").write_text("fixture cli", encoding="utf-8")

            with patch.object(runtime, "install_supported", return_value=True), \
                    patch.object(runtime, "_download", side_effect=download), \
                    patch.object(runtime, "_run", side_effect=run):
                self.assertEqual("ready", runtime.install(root))
            self.assertEqual(4, len(calls))
            self.assertTrue(runtime._managed_wrapper(components)[0])
            self.assertFalse((runtime_dir / runtime._INCOMPLETE_MARKER).exists())

    def test_install_promotes_only_after_offline_dependency_and_cli_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"
            components = root / ".simpleoffice-meta" / "components"
            orphan = components / ".piper-install-stage-abandoned"
            (orphan / "wheels").mkdir(parents=True)
            (orphan / "wheels" / "partial.whl").write_bytes(b"partial download")
            calls = []

            def download(wheel, destination, _deadline, **_kwargs):
                destination.write_bytes(b"fixture wheel payload")

            def run(args, **_kwargs):
                calls.append(list(args))
                if args[1:3] == ["-m", "venv"]:
                    runtime_dir = Path(args[3])
                    (runtime_dir / "bin").mkdir(parents=True)
                    (runtime_dir / "bin" / "python").write_text("fixture python", encoding="utf-8")
                    (runtime_dir / "bin" / "piper").write_text("fixture cli", encoding="utf-8")
                if "pip" in args and "install" in args:
                    self.assertIn("--no-index", args)
                    self.assertIn("--no-deps", args)
                    self.assertIn("--require-hashes", args)
                if args[-1:] == ["--help"]:
                    self.assertFalse((root / ".simpleoffice-meta" / "components" / "bin" / "piper").exists())

            with patch.object(runtime, "install_supported", return_value=True), \
                    patch.object(runtime, "_download", side_effect=download) as downloader, \
                    patch.object(runtime, "_run", side_effect=run):
                self.assertEqual("ready", runtime.install(root))

            self.assertEqual(len(runtime.manifest()), downloader.call_count)
            self.assertEqual(4, len(calls))
            self.assertFalse(orphan.exists(), "restart cleanup should reclaim abandoned staging files")
            wrapper, record = runtime._managed_wrapper(components)
            self.assertIsNotNone(wrapper)
            self.assertEqual(runtime.VERSION, record["version"])
            self.assertFalse((components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
                              / runtime._INCOMPLETE_MARKER).exists())

    def test_cancellation_during_offline_pip_install_does_not_promote_runtime(self):
        from threading import Event

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"
            event = Event()

            def download(_wheel, destination, _deadline, **_kwargs):
                destination.write_bytes(b"fixture wheel payload")

            def run(args, **_kwargs):
                if args[1:3] == ["-m", "venv"]:
                    runtime_dir = Path(args[3])
                    (runtime_dir / "bin").mkdir(parents=True)
                    (runtime_dir / "bin" / "python").write_text("fixture python", encoding="utf-8")
                elif "pip" in args and "install" in args:
                    event.set()
                    raise InterruptedError("cancelled during install")

            with patch.object(runtime, "install_supported", return_value=True), \
                    patch.object(runtime, "_download", side_effect=download), \
                    patch.object(runtime, "_run", side_effect=run):
                self.assertEqual("cancelled", runtime.install(root, cancel_event=event))
            components = root / ".simpleoffice-meta" / "components"
            self.assertFalse((components / f"piper-{runtime.VERSION}-cp312-linux-x86_64").exists())
            self.assertFalse((components / "bin" / "piper").exists())

    def test_component_resolver_accepts_only_hash_matched_managed_wrapper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"
            components = root / ".simpleoffice-meta" / "components"
            bindir = components / "bin"
            bindir.mkdir(parents=True)
            wrapper = bindir / "piper"
            wrapper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            wrapper.chmod(0o700)
            (components / "installed.json").write_text(
                '{"schema":1,"components":{"piper":{"filename":"piper",'
                '"sha256":"' + sha256(wrapper.read_bytes()).hexdigest() + '"}}}',
                encoding="utf-8",
            )
            with patch("app.component_runtime.shutil.which", return_value=None):
                self.assertEqual(str(wrapper), resolve_command("piper", root))
                wrapper.write_text("#!/bin/sh\nexit 5\n", encoding="utf-8")
                self.assertIsNone(resolve_command("piper", root))

    def test_managed_launcher_uses_relative_runtime_path_after_data_root_moves(self):
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / "old-documents"
            components = original / ".simpleoffice-meta" / "components"
            runtime_dir = components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
            binary_dir = runtime_dir / "bin"
            binary_dir.mkdir(parents=True)
            fake_python = binary_dir / "python"
            fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
            fake_python.chmod(0o700)
            (binary_dir / "piper").write_text("fixture cli\n", encoding="utf-8")
            wrapper = components / "bin" / "piper"
            runtime._write_manifest(components, wrapper, runtime_dir)
            new_root = Path(temporary) / "moved-documents"
            shutil.move(str(original), str(new_root))
            with patch("app.component_runtime.shutil.which", return_value=None):
                resolved = resolve_command("piper", new_root)
                output = subprocess.check_output([resolved, "--version"], text=True)
            self.assertIn(str(runtime_dir.name), output)
            self.assertIn("--version", output)

    def test_manifest_write_failure_rolls_back_new_wrapper(self):
        with tempfile.TemporaryDirectory() as temporary:
            components = Path(temporary) / "components"
            components.mkdir()
            runtime_dir = components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
            wrapper = components / "bin" / "piper"
            with patch.object(runtime, "_atomic_write_manifest", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    runtime._write_manifest(components, wrapper, runtime_dir)
            self.assertFalse(wrapper.exists())
            self.assertFalse((components / "installed.json").exists())

    def test_manifest_write_failure_restores_previous_managed_wrapper(self):
        with tempfile.TemporaryDirectory() as temporary:
            components = Path(temporary) / "components"
            components.mkdir()
            runtime_dir = components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
            wrapper = components / "bin" / "piper"
            runtime._write_manifest(components, wrapper, runtime_dir)
            previous = wrapper.read_bytes()
            with patch.object(runtime, "_atomic_write_manifest", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    runtime._write_manifest(components, wrapper, runtime_dir)
            self.assertEqual(previous, wrapper.read_bytes())
            self.assertTrue(runtime._managed_wrapper(components)[0])

    def test_dangling_manifest_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            components = Path(temporary) / "components"
            components.mkdir()
            (components / "installed.json").symlink_to(components / "missing.json")
            with self.assertRaisesRegex(ValueError, "Invalid managed component manifest"):
                runtime._manifest_state(components)

    def test_manifest_legacy_temp_symlink_cannot_change_its_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            components = Path(temporary) / "components"
            components.mkdir()
            protected = Path(temporary) / "protected.json"
            protected.write_text("keep this file", encoding="utf-8")
            (components / "installed.json.tmp").symlink_to(protected)
            runtime_dir = components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
            runtime._write_manifest(components, components / "bin" / "piper", runtime_dir)
            self.assertEqual("keep this file", protected.read_text(encoding="utf-8"))
            self.assertTrue((components / "installed.json.tmp").is_symlink())

    def test_only_installer_marked_runtime_can_be_discarded_for_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            components = Path(temporary) / "components"
            marked = components / f"piper-{runtime.VERSION}-cp312-linux-x86_64"
            marked.mkdir(parents=True)
            (marked / runtime._INCOMPLETE_MARKER).write_text(runtime.VERSION, encoding="ascii")
            self.assertTrue(runtime._discard_incomplete_runtime(marked))
            self.assertFalse(marked.exists())

            unmanaged = components / "unmanaged-runtime"
            unmanaged.mkdir()
            self.assertFalse(runtime._discard_incomplete_runtime(unmanaged))
            self.assertTrue(unmanaged.is_dir())


if __name__ == "__main__":
    unittest.main()
