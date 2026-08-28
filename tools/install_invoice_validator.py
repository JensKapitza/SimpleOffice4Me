#!/usr/bin/env python3
"""Install the pinned, self-contained EN16931 validator used by SimpleOffice."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
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
    checksum = _download(f"{BASE_URL}/{FILENAME}.sha256").decode("ascii").split()[0].lower()
    if len(checksum) != 64:
        raise RuntimeError("Maven Central returned an invalid validator checksum")
    payload = _download(f"{BASE_URL}/{FILENAME}")
    if hashlib.sha256(payload).hexdigest() != checksum:
        raise RuntimeError("downloaded EN16931 validator checksum does not match")
    with tempfile.NamedTemporaryFile(dir=TARGET.parent, delete=False) as handle:
        temporary = Path(handle.name); handle.write(payload)
    temporary.replace(TARGET)
    CHECKSUM_FILE.write_text(checksum + "\n", encoding="ascii")
    return TARGET


if __name__ == "__main__":
    try:
        installed = install()
    except (OSError, RuntimeError) as exc:
        print(f"WARNUNG: EN16931-Standardvalidator konnte nicht installiert werden: {exc}", file=sys.stderr)
        raise SystemExit(1)
    if installed:
        print(installed)
