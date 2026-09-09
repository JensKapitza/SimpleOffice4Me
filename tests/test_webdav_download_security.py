"""Regression checks for low-risk WebDAV download hardening."""

from pathlib import Path


SOURCE = Path("app/webdav.py")


def test_webdav_download_headers_block_active_content_execution():
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("def _download_response(")
    end = source.index("\ndef _resource_url(", start)
    function = source[start:end]
    assert '"X-Content-Type-Options": "nosniff"' in function
    assert '"Content-Security-Policy": "sandbox"' in function


def test_webdav_download_security_headers_are_shared_by_all_variants():
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("def _download_response(")
    end = source.index("\ndef _resource_url(", start)
    function = source[start:end]
    headers_start = function.index("headers = {")
    range_start = function.index("if range_header:")
    shared_headers = function[headers_start:range_start]
    assert '"X-Content-Type-Options": "nosniff"' in shared_headers
    assert '"Content-Security-Policy": "sandbox"' in shared_headers
