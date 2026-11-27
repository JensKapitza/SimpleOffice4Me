"""Unit and integration checks for the pinned FFmpeg runtime package."""
from __future__ import annotations

import io
import hashlib
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from app import optional_ffmpeg_runtime as runtime
from app.component_runtime import managed_command, resolve_command


def _elf_fixture() -> bytes:
    header = bytearray(20)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = b"\x3e\x00"
    return bytes(header) + b"test binary"


def _archive(*, extra=()) -> bytes:
    buffer = io.BytesIO()
    prefix = runtime._RUNTIME_PREFIX
    files = {
        f"{prefix}/bin/ffmpeg": _elf_fixture(),
        f"{prefix}/bin/ffprobe": _elf_fixture(),
        f"{prefix}/bin/ffplay": _elf_fixture(),
        f"{prefix}/LICENSE.txt": b"LGPL fixture license",
        f"{prefix}/lib/libavcodec.so.1": b"fixture shared library",
    }
    with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
        root = tarfile.TarInfo(prefix)
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for folder in ("bin", "lib"):
            directory = tarfile.TarInfo(f"{prefix}/{folder}")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
        for name, payload in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(payload)
            archive.addfile(item, io.BytesIO(payload))
        link = tarfile.TarInfo(f"{prefix}/lib/libavcodec.so")
        link.type = tarfile.SYMTYPE
        link.linkname = "libavcodec.so.1"
        archive.addfile(link)
        for item in extra:
            archive.addfile(item)
    return buffer.getvalue()


class OptionalFfmpegRuntimeTests(unittest.TestCase):
    def test_support_is_limited_to_glibc_linux_x86_64(self):
        with patch.object(runtime.platform, "system", return_value="Linux"), \
                patch.object(runtime.platform, "machine", return_value="x86_64"), \
                patch.object(runtime.platform, "libc_ver", return_value=("glibc", "2.28")):
            self.assertTrue(runtime.install_supported())
        with patch.object(runtime.platform, "machine", return_value="aarch64"):
            self.assertFalse(runtime.install_supported())
        with patch.object(runtime.platform, "libc_ver", return_value=("musl", "1.2.4")):
            self.assertFalse(runtime.install_supported())

    def test_artifact_manifest_pins_release_size_and_digest(self):
        metadata = runtime.artifact()
        self.assertEqual(runtime._ARCHIVE_SIZE, metadata["size"])
        self.assertEqual(runtime._ARCHIVE_SHA256, metadata["sha256"])
        self.assertIn(runtime.RELEASE, metadata["url"])
        self.assertIn("lgpl-shared", metadata["name"])

    def test_safe_relative_rejects_traversal_and_windows_separators(self):
        for value in ("../outside", "/absolute", "bin/../outside", r"bin\ffmpeg", "bin//ffmpeg"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime._safe_relative(value)

    def test_download_rejects_content_that_does_not_match_the_pinned_digest(self):
        class Reply(io.BytesIO):
            status = 200
            headers = {"Content-Length": "3"}

            def geturl(self):
                return "https://github.com/release"

        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "ffmpeg.tar.xz"
            with patch.object(runtime, "_ARCHIVE_SIZE", 3), \
                    patch.object(runtime, "_ARCHIVE_SHA256", hashlib.sha256(b"good").hexdigest()), \
                    self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                runtime._download(destination, 10**12, opener=lambda *_args, **_kwargs: Reply(b"bad"))
            self.assertEqual(b"bad", destination.read_bytes(), "unverified bytes remain temporary, never promoted")

    def test_redirect_policy_allows_only_github_release_assets(self):
        from urllib.request import Request

        handler = runtime._TrustedRedirects()
        request = Request(runtime._ARCHIVE_URL)
        allowed = handler.redirect_request(request, None, 302, "Found", {},
                                           "https://release-assets.githubusercontent.com/object")
        self.assertEqual("release-assets.githubusercontent.com", allowed.host)
        with self.assertRaisesRegex(ValueError, "Untrusted FFmpeg download redirect"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.org/asset")

    def test_extract_keeps_only_binaries_libraries_and_license(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "fixture.tar.xz"
            archive_path.write_bytes(_archive())
            stage = Path(temporary) / "stage"
            stage.mkdir()
            files, links = runtime._extract(archive_path, stage)
            self.assertEqual({"bin/ffmpeg", "bin/ffprobe", "bin/ffplay", "LICENSE.txt",
                              "lib/libavcodec.so.1"}, set(files))
            self.assertEqual({"lib/libavcodec.so": "libavcodec.so.1"}, links)
            self.assertTrue((stage / "lib/libavcodec.so").is_symlink())
            self.assertTrue((stage / "bin/ffmpeg").stat().st_mode & 0o100)
            self.assertFalse((stage / "smoke.wav").exists())

    def test_extract_rejects_traversal_hardlinks_and_symlink_escape(self):
        cases = (
            tarfile.TarInfo(f"{runtime._RUNTIME_PREFIX}/bin/../escape"),
            tarfile.TarInfo(f"{runtime._RUNTIME_PREFIX}/lib/extra.so"),
        )
        cases[1].type = tarfile.LNKTYPE
        cases[1].linkname = f"{runtime._RUNTIME_PREFIX}/bin/ffmpeg"
        with tempfile.TemporaryDirectory() as temporary:
            for index, extra in enumerate(cases):
                archive_path = Path(temporary) / f"bad-{index}.tar.xz"
                archive_path.write_bytes(_archive(extra=(extra,)))
                stage = Path(temporary) / f"stage-{index}"
                stage.mkdir()
                with self.subTest(index=index), self.assertRaises(ValueError):
                    runtime._extract(archive_path, stage)

            escape = tarfile.TarInfo(f"{runtime._RUNTIME_PREFIX}/lib/escape.so")
            escape.type = tarfile.SYMTYPE
            escape.linkname = "../../outside"
            bad_link = Path(temporary) / "bad-link.tar.xz"
            bad_link.write_bytes(_archive(extra=(escape,)))
            stage = Path(temporary) / "stage-link"
            stage.mkdir()
            with self.assertRaises(ValueError):
                runtime._extract(bad_link, stage)

    def test_install_promotes_verified_runtime_and_resolver_checks_shared_libraries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "documents"
            archive_path = Path(temporary) / "verified-fixture.tar.xz"
            archive_path.write_bytes(_archive())

            def download(_destination, _deadline, **_kwargs):
                _destination.write_bytes(archive_path.read_bytes())

            with patch.object(runtime, "install_supported", return_value=True), \
                    patch.object(runtime, "_download", side_effect=download), \
                    patch.object(runtime, "_smoke_test"):
                self.assertEqual("ready", runtime.install(root))

            for name in runtime._EXECUTABLES:
                self.assertTrue(managed_command(name, root).endswith(f"/bin/{name}"))
                with patch("app.component_runtime.shutil.which", return_value=None):
                    self.assertEqual(managed_command(name, root), resolve_command(name, root))
            self.assertEqual(runtime.VERSION, runtime.installed_version(root))
            self.assertEqual("ready", runtime.install(root), "a verified install should be idempotent")

            library = root / ".simpleoffice-meta" / "components" / f"ffmpeg-{runtime.VERSION}-linux-x86_64" / "lib/libavcodec.so.1"
            library.write_bytes(b"tampered shared library")
            with patch("app.component_runtime.shutil.which", return_value=None):
                self.assertIsNone(managed_command("ffmpeg", root))
                self.assertIsNone(resolve_command("ffmpeg", root))


if __name__ == "__main__":
    unittest.main()
