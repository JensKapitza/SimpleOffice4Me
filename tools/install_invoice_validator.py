#!/usr/bin/env python3
"""Install the pinned, self-contained EN16931 validator used by SimpleOffice."""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


VERSION = "2.25.0"
BASE_URL = f"https://repo1.maven.org/maven2/org/mustangproject/Mustang-CLI/{VERSION}"
FILENAME = f"Mustang-CLI-{VERSION}.jar"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".runtime-tools" / FILENAME
CHECKSUM_FILE = TARGET.with_suffix(".jar.sha256")
MAVEN_HOST = "repo1.maven.org"
MAX_JAR_BYTES = 128 * 1024 * 1024
MAX_CHECKSUM_BYTES = 4096
UPSTREAM_SHA256 = "d68b9fd6a9948a0964b7c93ed06b7e903a6dbafcd0df581e992e3178bf016701"
UPSTREAM_URL = f"https://github.com/ZUGFeRD/mustangproject/releases/download/core-{VERSION}/{FILENAME}"
UPSTREAM_REDIRECT_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


class _GitHubAssetRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or parsed.hostname not in UPSTREAM_REDIRECT_HOSTS:
            raise RuntimeError("validator release redirected outside the GitHub asset hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_GITHUB_ASSET_OPENER = urllib.request.build_opener(_GitHubAssetRedirectHandler())


def _download(url: str, *, max_bytes: int) -> bytes:
    parsed = urllib.parse.urlsplit(str(url or ""))
    expected_prefix = f"/maven2/org/mustangproject/Mustang-CLI/{VERSION}/"
    if (
        parsed.scheme != "https"
        or parsed.hostname != MAVEN_HOST
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith(expected_prefix)
    ):
        raise RuntimeError("validator download URL is not allowed")
    request = urllib.request.Request(parsed.geturl(), headers={"User-Agent": "SimpleOffice4Me validator bootstrap"})
    with _OPENER.open(request, timeout=90) as response:
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > max_bytes:
                    raise RuntimeError("validator download exceeds size limit")
            except ValueError as exc:
                raise RuntimeError("validator download returned invalid Content-Length") from exc
        payload = response.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise RuntimeError("validator download exceeds size limit")
    return payload


def _published_checksum() -> str:
    """Return the SHA-256 published for the pinned upstream release asset."""
    return UPSTREAM_SHA256


def _download_upstream_release() -> bytes:
    request = urllib.request.Request(UPSTREAM_URL, headers={"User-Agent": "SimpleOffice4Me validator bootstrap"})
    with _GITHUB_ASSET_OPENER.open(request, timeout=90) as response:
        if urlsplit(response.geturl()).hostname not in UPSTREAM_REDIRECT_HOSTS:
            raise RuntimeError("validator release resolved outside the GitHub asset hosts")
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_JAR_BYTES:
                    raise RuntimeError("validator download exceeds size limit")
            except ValueError as exc:
                raise RuntimeError("validator download returned invalid Content-Length") from exc
        payload = response.read(MAX_JAR_BYTES + 1)
    if len(payload) > MAX_JAR_BYTES:
        raise RuntimeError("validator download exceeds size limit")
    return payload


def install() -> Path | None:
    if os.environ.get("SIMPLEOFFICE_SKIP_INVOICE_VALIDATOR_BOOTSTRAP", "").casefold() in {"1", "true", "yes"}:
        return None
    if not shutil.which("java"):
        print("WARNUNG: Java fehlt; der mitgelieferte EN16931-Validator kann nicht gestartet werden.", file=sys.stderr)
        return None
    if TARGET.is_file() and CHECKSUM_FILE.is_file():
        expected = CHECKSUM_FILE.read_text(encoding="ascii").strip().lower()
        if len(expected) == 64 and hashlib.sha256(TARGET.read_bytes()).hexdigest() == expected:
            return TARGET
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    print(f"Installiere EN16931-Standardvalidator Mustang {VERSION} …")
    checksum = _published_checksum()
    payload = _download_upstream_release()
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise RuntimeError("downloaded EN16931 validator SHA-256 checksum does not match")
    local_sha256 = hashlib.sha256(payload).hexdigest()
    with tempfile.NamedTemporaryFile(dir=TARGET.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    temporary.replace(TARGET)
    CHECKSUM_FILE.write_text(local_sha256 + "\n", encoding="ascii")
    return TARGET


if __name__ == "__main__":
    try:
        installed = install()
    except (OSError, RuntimeError) as exc:
        print(f"WARNUNG: EN16931-Standardvalidator konnte nicht installiert werden: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if installed:
        print(installed)
