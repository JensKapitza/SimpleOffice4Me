import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r'(?ms)^\[project\]\s.*?^version\s*=\s*["\x27]([^"\x27]+)["\x27]',
        text,
    )
    if not match:
        raise AssertionError("project version missing")
    return match.group(1)


class InstallableArtifactWorkflowTests(unittest.TestCase):
    def test_desktop_package_version_tracks_project(self):
        package = json.loads(
            (ROOT / "desktop" / "electron" / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(package["version"], project_version())
        self.assertIn("sync:version", package["scripts"])
        self.assertIn("sync_project_version.py", package["scripts"]["sync:version"])

    def test_desktop_ci_publishes_verified_appimage(self):
        text = (ROOT / ".github" / "workflows" / "desktop-build.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("simpleoffice4me-linux-appimage-installable", text)
        self.assertIn("SimpleOffice4Me-Linux-x86_64.AppImage", text)
        self.assertIn("--appimage-version", text)
        self.assertIn("sha256sum", text)
        self.assertIn("npm run sync:version", text)

    def test_server_ci_publishes_verified_deb(self):
        text = (ROOT / ".github" / "workflows" / "server-deb-build.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("bash packaging/build-server.sh", text)
        self.assertIn("simpleoffice4me-server-deb-installable", text)
        self.assertIn("SimpleOffice4Me-Server-", text)
        self.assertIn("dpkg-deb -f", text)
        self.assertIn("debian:12-slim", text)
        self.assertIn("Test offline Python payload on Debian 12", text)
        self.assertIn("apt-get -s install", text)
        self.assertIn("sha256sum", text)

    def test_server_deb_declares_complete_system_dependencies(self):
        text = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        required = [
            "poppler-utils",
            "tesseract-ocr",
            "tesseract-ocr-deu",
            "tesseract-ocr-eng",
            "imagemagick",
            "ghostscript",
            "default-jre-headless",
            "ffmpeg",
            "coturn",
            "clamav",
            "clamav-daemon",
            "libreoffice",
            "cups-client",
            "rsync",
            "openssh-client",
            "iproute2",
            "nftables",
        ]
        for dependency in required:
            with self.subTest(dependency=dependency):
                self.assertIn(dependency, text)
        self.assertIn('if [ "$BUILD_ROLE" = "server" ]', text)
        self.assertIn('FPM_DEPENDENCY_ARGS+=(--depends "$dependency")', text)
        self.assertIn('--depends "python3 (>= $PYTHON_SERIES)"', text)
        self.assertIn('--depends "python3 (<< $PYTHON_NEXT_SERIES)"', text)

    def test_server_deb_bundles_and_installs_python_feature_extras(self):
        build = (ROOT / "packaging" / "build-fpm.sh").read_text(encoding="utf-8")
        postinst = (ROOT / "packaging" / "postinst.sh").read_text(encoding="utf-8")
        self.assertIn('PYTHON_EXTRAS="ocr,sftp,banking,erasure"', build)
        self.assertIn(".install-extras", build)
        self.assertIn(".install-extras", postinst)
        self.assertIn('INSTALL_SPEC="simpleoffice4me[$INSTALL_EXTRAS]"', postinst)


if __name__ == "__main__":
    unittest.main()
