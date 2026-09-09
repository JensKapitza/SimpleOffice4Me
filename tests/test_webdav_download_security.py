"""Regression checks for low-risk WebDAV download hardening."""

import unittest
from pathlib import Path


SOURCE = Path("app/webdav.py")


class WebDavDownloadSecurityTests(unittest.TestCase):
    @staticmethod
    def _download_response_source() -> str:
        source = SOURCE.read_text(encoding="utf-8")
        start = source.index("def _download_response(")
        end = source.index("\ndef _resource_url(", start)
        return source[start:end]

    def test_webdav_download_headers_block_active_content_execution(self):
        function = self._download_response_source()
        self.assertIn('"X-Content-Type-Options": "nosniff"', function)
        self.assertIn('"Content-Security-Policy": "sandbox"', function)

    def test_webdav_download_security_headers_are_shared_by_all_variants(self):
        function = self._download_response_source()
        headers_start = function.index("headers = {")
        range_start = function.index("if range_header:")
        shared_headers = function[headers_start:range_start]
        self.assertIn('"X-Content-Type-Options": "nosniff"', shared_headers)
        self.assertIn('"Content-Security-Policy": "sandbox"', shared_headers)


if __name__ == "__main__":
    unittest.main()
