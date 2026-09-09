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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirectHandler())


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
    """Return the SHA-256 checksum published for the pinned artifact.

    Installation fails closed when Maven Central does not provide a SHA-256
    sidecar. Weak digest algorithms are not accepted for executable artifacts.
    """
    try:
        value = _download(f"{BASE_URL}/{FILENAME}.sha256", max_bytes=MAX_CHECKSUM_BYTES).decode("ascii").split()[0].lower()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("Maven Central did not publish a SHA-256 checksum for the validator") from exc
        raise
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("Maven Central returned an invalid SHA-256 checksum")
    return value


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
    payload = _download(f"{BASE_URL}/{FILENAME}", max_bytes=MAX_JAR_BYTES)
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
