#!/usr/bin/env python3
"""Install the pinned, self-contained EN16931 validator used by SimpleOffice."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


VERSION = "2.25.0"
BASE_URL = f"https://repo1.maven.org/maven2/org/mustangproject/Mustang-CLI/{VERSION}"
FILENAME = f"Mustang-CLI-{VERSION}.jar"
ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".runtime-tools" / FILENAME
CHECKSUM_FILE = TARGET.with_suffix(".jar.sha256")


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "SimpleOffice4Me validator bootstrap"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def _published_checksum() -> tuple[str, str]:
    """Return the strongest checksum Maven Central publishes for the artifact.

    Maven Central does not consistently expose SHA-256 sidecar files.  Mustang
    2.25.0 currently publishes the traditional SHA-1 sidecar, so a missing
    SHA-256 sidecar must not make the default validator uninstallable.
    """
    for algorithm in ("sha256", "sha1"):
        try:
            value = _download(f"{BASE_URL}/{FILENAME}.{algorithm}").decode("ascii").split()[0].lower()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        expected_length = hashlib.new(algorithm).digest_size * 2
        if len(value) != expected_length or any(character not in "0123456789abcdef" for character in value):
            raise RuntimeError(f"Maven Central returned an invalid {algorithm.upper()} checksum")
        return algorithm, value
    raise RuntimeError("Maven Central did not publish a usable checksum for the validator")


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
    checksum_algorithm, checksum = _published_checksum()
    payload = _download(f"{BASE_URL}/{FILENAME}")
    if hashlib.new(checksum_algorithm, payload).hexdigest() != checksum:
        raise RuntimeError(f"downloaded EN16931 validator {checksum_algorithm.upper()} checksum does not match")
    local_sha256 = hashlib.sha256(payload).hexdigest()
    with tempfile.NamedTemporaryFile(dir=TARGET.parent, delete=False) as handle:
        temporary = Path(handle.name); handle.write(payload)
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
