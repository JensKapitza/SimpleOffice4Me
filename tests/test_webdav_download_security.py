"""Regression checks for WebDAV download hardening."""

import unittest
from pathlib import Path


APP_SOURCE = Path("app/__init__.py")
WEBDAV_SOURCES = [Path("app/webdav.py"), *sorted(Path("app").glob("webdav_part_*.py"))]


class WebDavDownloadSecurityTests(unittest.TestCase):
    def test_webdav_file_downloads_receive_nosniff_and_sandbox(self):
        source = APP_SOURCE.read_text(encoding="utf-8")
        self.assertIn('response.headers.setdefault("X-Content-Type-Options", "nosniff")', source)
        self.assertIn('request.endpoint in {"webdav.file_tree", "webdav.endpoint"}', source)
        self.assertIn('request.method in {"GET", "HEAD"}', source)
        self.assertIn('response.headers["Content-Security-Policy"] = "sandbox"', source)

    def test_webdav_downloads_share_one_response_path(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in WEBDAV_SOURCES)
        self.assertIn("return _download_response(", source)
        self.assertIn('if request.method in {"GET", "HEAD"}:', source)


if __name__ == "__main__":
    unittest.main()